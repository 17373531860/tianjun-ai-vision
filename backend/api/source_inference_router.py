"""多模型推理调度器 (Step 1, feat/multi-model-roi-link)。

职责
====
把"单 VSM 单模型"扩展为"单 VSM N 模型"。每个 ModelInstance 自带：
  - YOLO 模型实例 + 路径 + 任务类型
  - 推理参数（conf / iou / imgsz / use_half / device）
  - ROI（None 表示全画面；非空时归一化多边形坐标）
  - 调度策略（every_frame / every_n_frames / on_event）
  - class_filter（per-model 可识别类别白名单）
  - display_color（前端画框颜色）
  - 独立 fps_inference / latency 统计

InferenceRouter
===============
所有模型共用一个 GPU（3050 上不能并行），所以：
  - 每模型一个独立的 ThreadPoolExecutor (max_workers=1) 用于异步发起推理
  - 但所有 executor 共享一把 `gpu_lock`，保证 GPU 上一次只跑一个模型
  - 启动时所有模型按 priority 串行 warmup（避免 OOM 峰值打满）
  - 调度器（schedule_models_for_frame）按 schedule 配置决定本帧跑哪些模型
  - 高 priority 模型总是先派发（保证主模型 FPS 稳定）

Step 1 只做骨架 + 调度逻辑 + 单测；
真正的 model.predict / 坐标映射 / detection 合并放到 Step 4 (DetectRunnersMixin) 接入。

设计原则
========
1. 这个文件不依赖 VSM (host) — 自洽 + 可单独单测
2. ModelInstance 是数据载体 + 状态字段，不持有 GPU 推理逻辑
3. dispatch() 接收 frame + 让宿主提供的 runner_fn 真正调推理；router 只负责"跑哪个 + 何时跑 + 串行锁"
4. on_event 调度通过 trigger_event(name) 入队，下次 dispatch 时由该事件触发
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ============================================================
# 数据结构：调度策略
# ============================================================
@dataclass
class Schedule:
    """模型调度策略。

    type:
      - "every_frame"      : 每帧都跑（主模型必选）
      - "every_n_frames"   : 每 N 帧跑一次（副模型常用，n 必须 ≥ 1）
      - "on_event"         : 只在外部触发指定事件时跑一次（QC/抽检场景）
    """
    type: str = "every_frame"
    n: int = 1                      # every_n_frames 用
    events: List[str] = field(default_factory=list)  # on_event 用：监听的事件名列表

    def __post_init__(self):
        if self.type not in ("every_frame", "every_n_frames", "on_event"):
            raise ValueError(f"未知调度类型: {self.type!r}")
        if self.type == "every_n_frames" and self.n < 1:
            raise ValueError(f"every_n_frames 的 n 必须 ≥ 1, 当前: {self.n}")


# ============================================================
# 数据结构：单个模型实例（运行时状态全集）
# ============================================================
@dataclass
class ModelInstance:
    """单个 YOLO 模型的全部运行时状态。

    一个 VSM (per channel) 持有 OrderedDict[name, ModelInstance]，
    'main' 是约定的主模型 key（兼容层把 self.model / self.conf_threshold 等
    路由到 models['main']）。
    """
    name: str                                 # 唯一标识，'main' 是主模型
    model: Any = None                         # YOLO 实例（Step 3 由 ModelLoadMixin 写入）
    model_path: Optional[str] = None
    model_task: str = "detect"                # 'detect' / 'segment'
    _original_pt_path: Optional[str] = None   # 转换模型加载失败时回退的 .pt
    _is_native_pytorch: bool = True
    _model_imgsz: int = 640
    use_half: bool = False
    current_device_info: Optional[Dict[str, Any]] = None

    # 推理参数
    conf: float = 0.25
    iou: float = 0.45

    # ROI（None = 全画面；归一化多边形 [(x,y), ...]，至少 3 点）
    roi: Optional[List[List[float]]] = None

    # 调度
    schedule: Schedule = field(default_factory=Schedule)

    # 类别白名单（None = 不限）；与 step_conf_thresholds 二次过滤独立
    class_filter: Optional[set] = None

    # 优先级 0-100（高优先先派发，主模型推荐 100）
    priority: int = 50

    # 前端显示色（CSS 颜色字符串）
    display_color: str = "#10b981"

    # ============== 运行时统计 ==============
    fps_inference: float = 0.0
    latency: int = 0
    _fps_inference_counter: int = 0
    _fps_inference_time: float = field(default_factory=time.time)
    _last_run_frame_id: Optional[int] = None
    _last_seen_frame_id: Optional[int] = None  # 最近一次 schedule 见过的帧 (幂等用)
    _frames_since_last_run: int = 0            # every_n_frames 调度用

    # ============== ROI mask 缓存 (Step 4) ==============
    # 把 mi.roi (归一化多边形) 转成像素 mask 是开销不小的操作 (cv2.fillPoly),
    # 帧大小不变就缓存. roi 变化或 frame_shape 变化时由 _ensure_roi_mask 重建.
    _roi_mask_cache: Any = None                # np.ndarray (h, w) uint8, ROI 内 255 / 外 0
    _roi_mask_shape: Optional[tuple] = None    # 缓存对应的 (h, w)
    _roi_polygon_pixels: Any = None            # np.ndarray (N, 2) int32, ROI 顶点像素坐标·原图坐标系 (cv2.pointPolygonTest 用)
    _roi_mask_transform_sig: Optional[tuple] = None  # 缓存对应的视频变换签名 (rot, flip_h, flip_v), 2026-07 缺陷 B 修复

    def tick_fps(self, loop_start: float) -> None:
        """每跑完一次推理调用一次，每秒聚合一次 fps_inference"""
        self._fps_inference_counter += 1
        if loop_start - self._fps_inference_time >= 1.0:
            self.fps_inference = float(self._fps_inference_counter)
            self._fps_inference_counter = 0
            self._fps_inference_time = loop_start

    def reset_fps(self) -> None:
        self.fps_inference = 0.0
        self._fps_inference_counter = 0
        self._fps_inference_time = time.time()


# ============================================================
# 调度器：决定每帧跑哪些模型
# ============================================================
class InferenceRouter:
    """N 模型推理路由器。

    Step 1 仅实现：
      - models 注册 / 注销 / 查找
      - schedule_models_for_frame(frame_id) 返回应跑的 ModelInstance 列表
      - on_event(name) 触发事件队列
      - GPU 串行锁 + warmup 串行锁（暴露给宿主用）

    Step 4 在此基础上接入：
      - dispatch(frame, frame_id, runner_fn) 串行调 runner_fn(mi, frame) → 收集 detections
      - detection 合并 + 注入 model_name + display_color
    """

    def __init__(self):
        self.models: "OrderedDict[str, ModelInstance]" = OrderedDict()
        self.gpu_lock = threading.Lock()        # 同一 GPU 上一次只跑一个模型
        self.warmup_lock = threading.Lock()     # 多模型加载时串行 warmup
        self._event_queue: List[str] = []       # 待消费的事件名（on_event 调度用）
        self._event_lock = threading.Lock()
        self._dispatch_counter = 0              # 已调度的帧总数（every_n_frames 用）

    # ------------- 模型注册 -------------
    def add_model(self, mi: ModelInstance) -> None:
        if not mi.name:
            raise ValueError("ModelInstance.name 不能为空")
        if mi.name in self.models:
            raise ValueError(f"模型 {mi.name!r} 已注册")
        self.models[mi.name] = mi

    def remove_model(self, name: str) -> Optional[ModelInstance]:
        return self.models.pop(name, None)

    def get(self, name: str) -> Optional[ModelInstance]:
        return self.models.get(name)

    def main(self) -> Optional[ModelInstance]:
        """返回主模型（约定 name='main'，否则取第一个）"""
        if "main" in self.models:
            return self.models["main"]
        if self.models:
            return next(iter(self.models.values()))
        return None

    def clear(self) -> None:
        self.models.clear()
        self._event_queue.clear()
        self._dispatch_counter = 0

    def names(self) -> List[str]:
        return list(self.models.keys())

    # ------------- 事件队列 (on_event 调度) -------------
    def trigger_event(self, event_name: str) -> None:
        """外部触发一个事件，下次 schedule 时让监听该事件的模型跑一次"""
        with self._event_lock:
            self._event_queue.append(event_name)

    def _drain_events(self) -> List[str]:
        with self._event_lock:
            events, self._event_queue = self._event_queue, []
            return events

    # ------------- 调度核心 -------------
    def schedule_models_for_frame(self, frame_id: int) -> List[ModelInstance]:
        """决定本帧应该跑哪些模型，按 priority 降序。

        策略：
          - every_frame    : 每帧都进
          - every_n_frames : 累计 _frames_since_last_run >= n 时进，进了就清零
          - on_event       : 仅当 schedule.events 中任一事件被本帧 drain 出来时进

        重要：
          - frame_id 应单调递增，每帧调用一次
          - 同一 frame_id 多次调用幂等 — 通过 _last_seen_frame_id 去重，
            既不重复决策也不重复累加 _frames_since_last_run（避免 every_n_frames 计数错乱）
        """
        # 幂等检查放最前面：如果所有 mi 都已经见过这个 frame_id，整体跳过
        # （甚至不消耗事件队列 — 事件保留给下一个 "新" 帧消费）
        if self.models and all(
            mi._last_seen_frame_id == frame_id for mi in self.models.values()
        ):
            return []

        events = self._drain_events()
        events_set = set(events)
        chosen: List[ModelInstance] = []

        for mi in self.models.values():
            if mi._last_seen_frame_id == frame_id:
                continue  # 已为本帧决策过，跳过（理论上不会发生，因上面整体返回了）
            mi._last_seen_frame_id = frame_id

            sched = mi.schedule
            should_run = False

            if sched.type == "every_frame":
                should_run = True
            elif sched.type == "every_n_frames":
                mi._frames_since_last_run += 1
                if mi._frames_since_last_run >= sched.n:
                    should_run = True
                    mi._frames_since_last_run = 0
            elif sched.type == "on_event":
                if events_set & set(sched.events):
                    should_run = True

            if should_run:
                mi._last_run_frame_id = frame_id
                chosen.append(mi)

        # priority 降序（同 priority 保持注册顺序，OrderedDict 已保证稳定）
        chosen.sort(key=lambda m: -m.priority)
        self._dispatch_counter += 1
        return chosen

    # ------------- 工具 -------------
    def stats_snapshot(self) -> List[Dict[str, Any]]:
        """快照所有模型的运行时统计 (供 /detection/results 注入)"""
        out = []
        for mi in self.models.values():
            out.append({
                "name": mi.name,
                "model_path": mi.model_path,
                "model_task": mi.model_task,
                "model_loaded": mi.model is not None,
                "conf": mi.conf,
                "iou": mi.iou,
                "imgsz": mi._model_imgsz,
                "use_half": mi.use_half,
                "device": (mi.current_device_info or {}).get("device"),
                "roi": mi.roi,
                "schedule": {"type": mi.schedule.type, "n": mi.schedule.n,
                             "events": list(mi.schedule.events)},
                "class_filter": (sorted(mi.class_filter)
                                 if mi.class_filter is not None else None),
                "priority": mi.priority,
                "display_color": mi.display_color,
                "fps_inference": mi.fps_inference,
                "latency": mi.latency,
            })
        return out


__all__ = ["Schedule", "ModelInstance", "InferenceRouter"]
