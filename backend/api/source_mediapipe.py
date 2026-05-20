"""MediaPipeOverlay 组件 (v2.7.16 P7 第二刀, 组合优于继承).

把 MediaPipe 姿态/手部识别相关功能从 VideoSourceManager 搬出, 形成独立组件.

v3.8.0 二段 pipeline 升级 (feat/hand-skeleton):
    - 新增 hand-detector 槽位: 当 host.mediapipe_hand_detector_path 非空时, 走二段
      pipeline (YOLO 检框 -> 扩 ROI -> HandLandmarker 跑在 ROI 上), 解决工业场景
      MediaPipe PalmDetector 不识别戴手套/握工具姿态的问题.
    - 第二阶段 landmarker 优先级:
        1. backend/data/models/hand_landmarker.task 存在 -> MediaPipe Tasks API
           (新版, 更稳, 推荐)
        2. 文件不存在 -> 回退 mp.solutions.hands (legacy 单帧推理)
    - 配 hand_detector 但加载失败 -> 自动回退原 baseline, 不阻塞推理
    - hand-detector kind: 'v8'(ultralytics) / 'v5'(legacy, monkeypatch torch.load)

字段所有权 (8 + 5 个内部状态):
  老 baseline:
    _mp_pose / _mp_hands / _mp_landmarker_tasks  : lazy-loaded 模型句柄
    _mp_draw / _mp_draw_styles                   : drawing utils
    _mp_last_pose_results / _mp_last_hands_results : 帧间复用
    _mp_frame_counter / _mp_process_interval
  v3.8.0 新增 (二段 pipeline):
    _hand_detector                       : YOLO 检测器实例 (None=未启用)
    _hand_detector_path_loaded           : 已加载的路径 (热更新检测)
    _hand_detector_kind_loaded           : 已加载的 kind
    _last_two_stage_landmarks            : 帧间复用的 ROI 关键点 [(roi_offset, roi_size, landmarks), ...]

用户配置 (公共字段保留在 VSM, 通过 __setattr__ 转发):
  老 4 个: mediapipe_enabled / mediapipe_pose / mediapipe_hands / mediapipe_confidence
  新 6 个: mediapipe_hand_detector_path / _conf / _iou / _imgsz / _class / _kind
           mediapipe_hand_roi_pad

公共 API: init() / release() / apply_overlay(frame).
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import List, Optional, Tuple

import cv2

# HandLandmarker .task 默认路径 (跟项目同级分发, 客户可换)
DEFAULT_TASK_MODEL_REL = "backend/data/models/hand_landmarker.task"

# MediaPipe 标准 21 关键点连接表 (用于 Tasks API 渲染)
HAND_CONNECTIONS_21 = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


# ==================== 二段 pipeline: hand-detector 适配器 ====================

class _YOLOv8HandDetector:
    """ultralytics YOLOv8/v11 hand-detector. 内部使用, 失败抛 RuntimeError."""

    def __init__(self, model_path: str, conf: float, iou: float, imgsz: int,
                 device: str, class_filter: int):
        from ultralytics import YOLO
        self.model = YOLO(model_path)
        self.conf = max(0.05, min(0.95, conf))
        self.iou = max(0.1, min(0.9, iou))
        self.imgsz = max(160, min(1280, imgsz))
        # device: 'auto' -> ultralytics 默认; 否则原样
        self.device = device if device and device != "auto" else None
        # class_filter: -1 表示所有类, 否则只保留该类
        self.class_filter = int(class_filter) if class_filter is not None else -1
        self.names = getattr(self.model, "names", {}) or {}

    def predict(self, frame) -> List[Tuple[int, int, int, int]]:
        """返回 [(x1, y1, x2, y2), ...] (只输出 bbox, 不带 conf/cls)."""
        try:
            kwargs = dict(conf=self.conf, iou=self.iou, imgsz=self.imgsz, verbose=False)
            if self.device:
                kwargs["device"] = self.device
            results = self.model.predict(frame, **kwargs)
        except Exception as e:
            print(f"[MediaPipe two-stage] hand-detector 推理失败: {e}", file=sys.stderr)
            return []
        if not results:
            return []
        res = results[0]
        if res.boxes is None or len(res.boxes) == 0:
            return []
        xyxy = res.boxes.xyxy.cpu().numpy()
        clses = res.boxes.cls.cpu().numpy().astype(int)
        out = []
        for (x1, y1, x2, y2), cl in zip(xyxy, clses):
            if self.class_filter >= 0 and int(cl) != self.class_filter:
                continue
            out.append((int(x1), int(y1), int(x2), int(y2)))
        return out


class _YOLOv5HandDetector:
    """yolov5 legacy hand-detector (用于加载 hf 上的 .pt 旧权重).

    monkeypatch torch.load(weights_only=False) 绕开 torch 2.6+ 安全检查.
    """

    def __init__(self, model_path: str, conf: float, iou: float, imgsz: int,
                 device: str, class_filter: int):
        import torch
        _orig = torch.load
        def _patched(*args, **kwargs):
            kwargs['weights_only'] = False
            return _orig(*args, **kwargs)
        torch.load = _patched
        try:
            import yolov5  # noqa: F401
        except ImportError as exc:
            torch.load = _orig
            raise RuntimeError("yolov5 包未安装, 无法加载 yolov5 格式 .pt") from exc
        import yolov5
        dev = device if (device and device != "auto") else ("cuda:0" if torch.cuda.is_available() else "cpu")
        self.model = yolov5.load(model_path, device=dev)
        self.model.conf = max(0.05, min(0.95, conf))
        self.model.iou = max(0.1, min(0.9, iou))
        self.imgsz = max(160, min(1280, imgsz))
        self.class_filter = int(class_filter) if class_filter is not None else -1
        self.names = getattr(self.model, "names", {}) or {}
        if isinstance(self.names, list):
            self.names = {i: n for i, n in enumerate(self.names)}
        torch.load = _orig

    def predict(self, frame) -> List[Tuple[int, int, int, int]]:
        try:
            res = self.model(frame, size=self.imgsz)
        except Exception as e:
            print(f"[MediaPipe two-stage] yolov5 推理失败: {e}", file=sys.stderr)
            return []
        if len(res.xyxy) == 0:
            return []
        det = res.xyxy[0].cpu().numpy()
        out = []
        for row in det:
            x1, y1, x2, y2, cf, cl = row[:6]
            if self.class_filter >= 0 and int(cl) != self.class_filter:
                continue
            out.append((int(x1), int(y1), int(x2), int(y2)))
        return out


# ==================== 二段 pipeline: HandLandmarker Tasks API ====================

class _HandLandmarkerTasksAdapter:
    """MediaPipe Tasks API HandLandmarker, 跑在 RGB 图像上, 输出 21 关键点."""

    def __init__(self, task_path: str, num_hands: int, min_det_conf: float,
                 min_track_conf: float):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_py
        from mediapipe.tasks.python import vision as mp_vis

        self._mp = mp
        base_options = mp_py.BaseOptions(model_asset_path=task_path)
        opts = mp_vis.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vis.RunningMode.IMAGE,
            num_hands=max(1, min(4, int(num_hands))),
            min_hand_detection_confidence=max(0.05, min(0.9, min_det_conf)),
            min_hand_presence_confidence=max(0.05, min(0.9, min_det_conf)),
            min_tracking_confidence=max(0.05, min(0.9, min_track_conf)),
        )
        self._landmarker = mp_vis.HandLandmarker.create_from_options(opts)

    def detect(self, bgr_image):
        """返回 list[landmarks_per_hand], 空 list 表示未命中.

        每个 landmarks_per_hand 是 list[NormalizedLandmark], 长度 21.
        """
        rgb = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(mp_image)
        return list(result.hand_landmarks) if result.hand_landmarks else []

    def close(self):
        try:
            self._landmarker.close()
        except Exception:
            pass


# ==================== ROI 计算工具 ====================

def _expand_bbox(x1: int, y1: int, x2: int, y2: int, frame_w: int, frame_h: int,
                 pad_ratio: float, min_size: int = 96) -> Tuple[int, int, int, int]:
    """把 bbox 外扩 pad_ratio * max(w,h), 不超出帧, 不小于 min_size."""
    w = x2 - x1
    h = y2 - y1
    pad = int(max(w, h) * max(0.0, pad_ratio))
    nx1 = max(0, x1 - pad)
    ny1 = max(0, y1 - pad)
    nx2 = min(frame_w, x2 + pad)
    ny2 = min(frame_h, y2 + pad)
    if (nx2 - nx1) < min_size:
        cx = (nx1 + nx2) // 2
        nx1 = max(0, cx - min_size // 2)
        nx2 = min(frame_w, nx1 + min_size)
    if (ny2 - ny1) < min_size:
        cy = (ny1 + ny2) // 2
        ny1 = max(0, cy - min_size // 2)
        ny2 = min(frame_h, ny1 + min_size)
    return nx1, ny1, nx2, ny2


# ==================== MediaPipeOverlay 主类 ====================

class MediaPipeOverlay:
    """MediaPipe 叠加层, 支持两种模式:

    Mode A (baseline): host.mediapipe_hand_detector_path 为空
        - 跟 v2.7.16 行为完全一致: mp.solutions.hands 跑整帧
    Mode B (二段 pipeline): host.mediapipe_hand_detector_path 配了有效路径
        - 加载 YOLO hand-detector + HandLandmarker(Tasks API)
        - 每帧: YOLO 检框 -> 扩 ROI -> HandLandmarker on ROI -> 关键点变回全帧
        - 加载任意一步失败 -> 自动回退到 Mode A
    """

    def __init__(self, host=None):
        self._host = host
        # ---------- 老 baseline 字段 (向后兼容) ----------
        self._mp_pose = None
        self._mp_hands = None
        self._mp_draw = None
        self._mp_draw_styles = None
        self._mp_last_pose_results = None
        self._mp_last_hands_results = None
        self._mp_frame_counter = 0
        self._mp_process_interval = 2

        # ---------- v3.8.0 二段 pipeline 字段 ----------
        self._hand_detector = None
        self._hand_detector_path_loaded: Optional[str] = None
        self._hand_detector_kind_loaded: Optional[str] = None
        self._hand_landmarker_tasks: Optional[_HandLandmarkerTasksAdapter] = None
        # 帧间缓存的 ROI 关键点: [(offset_xy, roi_size_wh, landmarks_list), ...]
        self._last_two_stage_results: List[tuple] = []
        # 二段 pipeline 启用状态 (由 init() 决定)
        self._two_stage_active = False
        self._init_lock = threading.Lock()

    # ---------------- 公共 API ----------------

    def init(self):
        """Lazy-load: 根据 host 配置决定走 baseline 还是二段 pipeline."""
        host = self._host
        with self._init_lock:
            try:
                import mediapipe as mp
                self._mp_draw = mp.solutions.drawing_utils
                self._mp_draw_styles = mp.solutions.drawing_styles
                conf = max(0.05, min(1.0, getattr(host, "mediapipe_confidence", 0.7)))

                # ---------- pose 部分: 维持老逻辑 (不受二段影响) ----------
                if host.mediapipe_pose and self._mp_pose is None:
                    self._mp_pose = mp.solutions.pose.Pose(
                        static_image_mode=False,
                        model_complexity=0,
                        min_detection_confidence=conf,
                        min_tracking_confidence=0.5,
                    )
                    print(f"[MediaPipe] Pose 模型已加载 (confidence={conf})")

                # ---------- hands 部分: 二选一 ----------
                if host.mediapipe_hands:
                    self._init_hands_pipeline(conf)
                else:
                    self._two_stage_active = False

            except ImportError:
                print("[MediaPipe] 警告: mediapipe 未安装，pip install mediapipe")
                host.mediapipe_enabled = False
            except Exception as e:
                print(f"[MediaPipe] 初始化失败: {e}")
                host.mediapipe_enabled = False

    def release(self):
        """释放所有资源, 重置缓存."""
        if self._mp_pose is not None:
            try:
                self._mp_pose.close()
            except Exception:
                pass
            self._mp_pose = None
        if self._mp_hands is not None:
            try:
                self._mp_hands.close()
            except Exception:
                pass
            self._mp_hands = None
        if self._hand_landmarker_tasks is not None:
            try:
                self._hand_landmarker_tasks.close()
            except Exception:
                pass
            self._hand_landmarker_tasks = None
        # hand-detector (YOLO) 没有显式 close, 让 GC 收
        self._hand_detector = None
        self._hand_detector_path_loaded = None
        self._hand_detector_kind_loaded = None
        self._two_stage_active = False
        self._mp_last_pose_results = None
        self._mp_last_hands_results = None
        self._last_two_stage_results = []
        self._mp_frame_counter = 0
        print("[MediaPipe] 资源已释放")

    def apply_overlay(self, frame):
        """在帧上画 pose + hands 骨架. 二段或 baseline 自动选择."""
        host = self._host
        if not host.mediapipe_enabled:
            return frame

        if self._mp_draw is None:
            self.init()
            if not host.mediapipe_enabled:
                return frame

        # 配置热更新: hand-detector 路径变了, 重新初始化
        cur_path = (getattr(host, "mediapipe_hand_detector_path", "") or "").strip()
        cur_kind = (getattr(host, "mediapipe_hand_detector_kind", "v8") or "v8").strip()
        if cur_path != (self._hand_detector_path_loaded or "") or \
           cur_kind != (self._hand_detector_kind_loaded or ""):
            self._reload_hands_pipeline()

        self._mp_frame_counter += 1
        should_process = (self._mp_frame_counter %
                          max(self._mp_process_interval, 1)) == 0

        if should_process:
            self._run_inference(frame)

        # ---------- 渲染 ----------
        # pose (baseline 共用)
        if self._mp_last_pose_results and self._mp_last_pose_results.pose_landmarks:
            import mediapipe as mp
            self._mp_draw.draw_landmarks(
                frame,
                self._mp_last_pose_results.pose_landmarks,
                mp.solutions.pose.POSE_CONNECTIONS,
                landmark_drawing_spec=self._mp_draw_styles.get_default_pose_landmarks_style(),
            )

        # hands: 二段或 baseline 不同渲染路径
        if self._two_stage_active:
            self._draw_two_stage_hands(frame)
        else:
            self._draw_baseline_hands(frame)

        return frame

    # ---------------- 内部方法 ----------------

    def _init_hands_pipeline(self, conf: float):
        """根据 host.mediapipe_hand_detector_path 决定走 baseline 还是二段."""
        host = self._host
        cur_path = (getattr(host, "mediapipe_hand_detector_path", "") or "").strip()
        cur_kind = (getattr(host, "mediapipe_hand_detector_kind", "v8") or "v8").strip()

        if cur_path and os.path.exists(cur_path):
            ok = self._try_init_two_stage(cur_path, cur_kind, conf)
            if ok:
                self._two_stage_active = True
                self._hand_detector_path_loaded = cur_path
                self._hand_detector_kind_loaded = cur_kind
                print(f"[MediaPipe] 二段 pipeline 已启用 (detector={cur_path}, kind={cur_kind})")
                return
            else:
                print("[MediaPipe] 二段 pipeline 初始化失败, 回退 baseline")

        # baseline: mp.solutions.hands
        self._two_stage_active = False
        if self._mp_hands is None:
            import mediapipe as mp
            # v3.8.0: 暴露 model_complexity 与 track_confidence 给 host 控制
            # 朋友程序的"完美骨架"密码 = complexity=1 + det_conf=0.5 + track_conf=0.5
            mc = int(getattr(host, "mediapipe_model_complexity", 0))
            mc = max(0, min(1, mc))  # 老版 mp.solutions.hands 只支持 0/1
            track_conf = float(getattr(host, "mediapipe_track_confidence", 0.5))
            track_conf = max(0.05, min(0.95, track_conf))
            self._mp_hands = mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=2,
                model_complexity=mc,
                min_detection_confidence=conf,
                min_tracking_confidence=track_conf,
            )
            print(f"[MediaPipe] Hands 模型已加载 (baseline mp.solutions.hands, "
                  f"complexity={mc}, det_conf={conf}, track_conf={track_conf})")
        # baseline 也要把 path/kind 记下, 避免热更新比较时误以为"配置变了"
        self._hand_detector_path_loaded = cur_path
        self._hand_detector_kind_loaded = cur_kind

    def _try_init_two_stage(self, detector_path: str, kind: str, conf: float) -> bool:
        """尝试初始化二段 pipeline. 成功返回 True, 失败返回 False (调用方回退 baseline)."""
        try:
            det_conf = float(getattr(self._host, "mediapipe_hand_detector_conf", 0.25))
            det_iou = float(getattr(self._host, "mediapipe_hand_detector_iou", 0.45))
            det_imgsz = int(getattr(self._host, "mediapipe_hand_detector_imgsz", 640))
            det_class = int(getattr(self._host, "mediapipe_hand_detector_class", -1))
            det_device = (getattr(self._host, "device", "auto") or "auto")

            if kind == "v5":
                detector = _YOLOv5HandDetector(detector_path, det_conf, det_iou,
                                               det_imgsz, det_device, det_class)
            else:
                detector = _YOLOv8HandDetector(detector_path, det_conf, det_iou,
                                               det_imgsz, det_device, det_class)
            # 加载 HandLandmarker .task
            task_path = self._resolve_task_model_path()
            if not task_path or not os.path.exists(task_path):
                print(f"[MediaPipe] .task 模型不存在 ({task_path}), 二段 pipeline 不可用")
                return False
            self._hand_detector = detector
            self._hand_landmarker_tasks = _HandLandmarkerTasksAdapter(
                task_path,
                num_hands=2,
                min_det_conf=conf,
                min_track_conf=conf,
            )
            print(f"[MediaPipe] hand-detector ({kind}) 已加载: {detector_path}  "
                  f"类别={getattr(detector, 'names', {})}")
            print(f"[MediaPipe] HandLandmarker(Tasks) 已加载: {task_path}")
            return True
        except Exception as e:
            print(f"[MediaPipe] 二段 pipeline 初始化异常: {e}", file=sys.stderr)
            self._hand_detector = None
            if self._hand_landmarker_tasks is not None:
                try:
                    self._hand_landmarker_tasks.close()
                except Exception:
                    pass
                self._hand_landmarker_tasks = None
            return False

    def _resolve_task_model_path(self) -> Optional[str]:
        """找 hand_landmarker.task 模型路径.

        优先级:
            1. host.mediapipe_landmarker_task_path (显式配)
            2. backend/data/models/hand_landmarker.task (项目内置)
        """
        explicit = (getattr(self._host, "mediapipe_landmarker_task_path", "") or "").strip()
        if explicit and os.path.exists(explicit):
            return explicit
        # 项目内置位置 (相对 backend/api/source_mediapipe.py)
        here = Path(__file__).resolve()
        for ancestor in [here.parents[2], here.parents[1]]:  # tianjun 根 / backend
            candidate = ancestor / DEFAULT_TASK_MODEL_REL.split("backend/", 1)[-1] \
                if "backend/" in str(ancestor).lower() else ancestor / DEFAULT_TASK_MODEL_REL
            if candidate.exists():
                return str(candidate)
        # 兜底: 直接拼 backend/data/models/hand_landmarker.task
        backend_dir = here.parents[1]  # .../backend
        candidate = backend_dir / "data" / "models" / "hand_landmarker.task"
        return str(candidate) if candidate.exists() else None

    def _reload_hands_pipeline(self):
        """配置变更后重新加载 hands 部分 (pose 保留)."""
        if self._mp_hands is not None:
            try:
                self._mp_hands.close()
            except Exception:
                pass
            self._mp_hands = None
        if self._hand_landmarker_tasks is not None:
            try:
                self._hand_landmarker_tasks.close()
            except Exception:
                pass
            self._hand_landmarker_tasks = None
        self._hand_detector = None
        self._two_stage_active = False
        conf = max(0.05, min(1.0, getattr(self._host, "mediapipe_confidence", 0.7)))
        self._init_hands_pipeline(conf)

    def _run_inference(self, frame):
        """跑 pose + hands 推理 (二段或 baseline 自动选择)."""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False

        # pose 部分
        if self._mp_pose is not None and self._host.mediapipe_pose:
            try:
                self._mp_last_pose_results = self._mp_pose.process(rgb)
            except Exception:
                self._mp_last_pose_results = None
        else:
            self._mp_last_pose_results = None

        # hands 部分: 分支
        if not self._host.mediapipe_hands:
            self._mp_last_hands_results = None
            self._last_two_stage_results = []
            return

        if self._two_stage_active and self._hand_detector and self._hand_landmarker_tasks:
            self._run_two_stage_hands(frame)
        elif self._mp_hands is not None:
            try:
                self._mp_last_hands_results = self._mp_hands.process(rgb)
            except Exception:
                self._mp_last_hands_results = None

    def _run_two_stage_hands(self, frame):
        """二段 pipeline 推理: YOLO 检框 -> ROI -> HandLandmarker -> 缓存关键点."""
        h, w = frame.shape[:2]
        pad_ratio = float(getattr(self._host, "mediapipe_hand_roi_pad", 0.3))
        bboxes = self._hand_detector.predict(frame)
        results = []
        for (x1, y1, x2, y2) in bboxes:
            ex1, ey1, ex2, ey2 = _expand_bbox(x1, y1, x2, y2, w, h, pad_ratio)
            roi = frame[ey1:ey2, ex1:ex2]
            if roi.size == 0:
                continue
            try:
                hands_lm = self._hand_landmarker_tasks.detect(roi)
            except Exception as e:
                print(f"[MediaPipe two-stage] landmarker 跑 ROI 失败: {e}", file=sys.stderr)
                continue
            for lm in hands_lm:
                results.append(((ex1, ey1), (ex2 - ex1, ey2 - ey1), lm))
        self._last_two_stage_results = results

    def _draw_baseline_hands(self, frame):
        """老 baseline 渲染: mp.solutions.hands 结果."""
        if not self._mp_last_hands_results:
            return
        if not getattr(self._mp_last_hands_results, "multi_hand_landmarks", None):
            return
        import mediapipe as mp
        for hand_lm in self._mp_last_hands_results.multi_hand_landmarks:
            self._mp_draw.draw_landmarks(
                frame,
                hand_lm,
                mp.solutions.hands.HAND_CONNECTIONS,
                self._mp_draw_styles.get_default_hand_landmarks_style(),
                self._mp_draw_styles.get_default_hand_connections_style(),
            )

    def _draw_two_stage_hands(self, frame):
        """二段 pipeline 渲染: 把 ROI 内归一化关键点变回全帧坐标后画."""
        if not self._last_two_stage_results:
            return
        for (offset, roi_size, landmarks) in self._last_two_stage_results:
            ox, oy = offset
            rw, rh = roi_size
            pts = []
            for lm in landmarks:
                x = ox + int(lm.x * rw)
                y = oy + int(lm.y * rh)
                pts.append((x, y))
            for a, b in HAND_CONNECTIONS_21:
                if a < len(pts) and b < len(pts):
                    cv2.line(frame, pts[a], pts[b], (255, 100, 0), 2, cv2.LINE_AA)
            for (x, y) in pts:
                cv2.circle(frame, (x, y), 3, (0, 255, 255), -1, cv2.LINE_AA)
