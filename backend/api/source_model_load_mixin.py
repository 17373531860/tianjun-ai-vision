"""模型加载 (load_model) — Step 3 (feat/multi-model-roi-link) 多模型重构。

设计原则
========
本 mixin 同时承担两个责任：

1. **向后兼容老链路** (`load_model(path)` / `_release_model()`):
   - 接口签名不变, 行为不变
   - 内部仍然写 host 老字段 (self.model / self.model_path / 等)
   - 在 load/release 完成后调 `_mirror_host_to_main()` 把 main slot 同步成镜像
   - 这样 Step 4 的 DetectRunnersMixin 改写为"通过 mi 操作"时, main slot
     已经是 host 真状态的镜像, 老链路 0 改动也能跑

2. **新公共 API** (`load_model_into_slot(name, path, **kwargs)` /
   `release_all_models()` / `_release_model_from(mi)`):
   - 直接操作 `self._router.models[name]` 中的 ModelInstance
   - name == 'main' 时同时镜像到 host (双向)
   - 副模型 (name != 'main') 完全独立, 不动 host 老字段
   - warmup 在 `self._router.warmup_lock` 下串行, 防 3050 OOM 峰值

修复 (Step 3 顺手修)
====================
- **NameError 静默退化 bug**: 老代码 `_read_engine_metadata_imgsz(model_path)`
  在本 mixin 内被调, 但本文件没 import, 触发 NameError 被外层 try/except 吞掉,
  每次加载 .engine 模型都跳过最权威的 metadata 检测. Step 3 加 import 修复.

宿主必需属性 (来自 VSM 主类 / source_state_init):
  self.model, self.model_path, self.model_task, self._original_pt_path,
  self._is_native_pytorch, self._model_imgsz, self.current_device_info,
  self.use_half, self.device, self._router, self.models
"""
from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Optional

import numpy as np

from backend.core import debug_center

if TYPE_CHECKING:
    from backend.api.source_inference_router import ModelInstance


def _get_engine_metadata_imgsz_reader():
    """延迟 import (避免与 source.py 循环).

    Step 3 修复: 老代码在本文件直接调用 `_read_engine_metadata_imgsz(...)`,
    但本文件没 import, 触发 NameError 被外层 try/except 静默吞掉, 每次加载
    .engine 都跳过此最权威的 imgsz 检测路径. 现在延迟 import 修复.
    """
    from backend.api.source import _read_engine_metadata_imgsz
    return _read_engine_metadata_imgsz


class ModelLoadMixin:
    # ============================================================
    # 老签名 (向后兼容): load_model + _release_model
    # ============================================================
    def load_model(self, model_path: str, original_pt_path: str = None) -> bool:
        """加载 YOLO 模型 (主模型). 转换模型加载失败时自动回退到原始 .pt。

        本方法保持 v3.5.x 之前的行为完全不变 — 老调用路径 (start_detection /
        ChannelManager.load_model_for_channel / projects.activate / main 启动恢复
        / source_routes.gpu/set) 全部沿用此接口, 0 改动。

        改动只有一处: 末尾调 `_mirror_host_to_main()`, 把 host 字段同步到
        main slot, 让新链路 (Step 4 起的 DetectRunnersMixin / InferenceLoopMixin)
        可以从 mi 读到等价状态。
        """
        debug_center.dbg("backend.detection", "load_model 开始", f"path={model_path} original_pt={original_pt_path}")
        try:
            from ultralytics import YOLO
            import torch

            if original_pt_path:
                self._original_pt_path = original_pt_path
            elif model_path.endswith('.pt') or model_path.endswith('.pth'):
                self._original_pt_path = model_path

            # 先释放旧模型
            if self.model is not None:
                print(f"[ModelLoad] releasing old model: {self.model_path}")
                self._release_model()

            try:
                self.model = YOLO(model_path)
            except Exception as e:
                if self._original_pt_path and model_path != self._original_pt_path:
                    print(f"[ModelLoad] converted model load failed ({e}), fallback to original model: {self._original_pt_path}")
                    debug_center.dbg("backend.detection", "转换模型加载失败回退", f"path={model_path} err={e} → {self._original_pt_path}")
                    self.model = YOLO(self._original_pt_path)
                    model_path = self._original_pt_path
                else:
                    raise
            self.model_path = model_path
            self.model_task = getattr(self.model, 'task', 'detect')

            is_native_pytorch = (model_path.endswith('.pt') or model_path.endswith('.pth'))
            self._is_native_pytorch = is_native_pytorch

            # 设置推理设备
            if self.device == 'auto':
                device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
            else:
                device = self.device

            # .to(device) 仅对原生 PyTorch 模型有效; 导出格式在 predict 时通过 device 参数指定
            if is_native_pytorch:
                self.model.to(device)

            # 记录当前设备信息
            if device.startswith('cuda'):
                gpu_idx = int(device.split(':')[1]) if ':' in device else 0
                gpu_name = torch.cuda.get_device_name(gpu_idx)
                self.current_device_info = {'type': 'GPU', 'name': gpu_name, 'device': device}
                print(f"[ModelLoad] success: {model_path} -> GPU: {gpu_name}")
            else:
                self.current_device_info = {'type': 'CPU', 'name': 'CPU', 'device': 'cpu'}
                print(f"[ModelLoad] success: {model_path} -> CPU")

            if hasattr(self.model, 'names'):
                print(f"[ModelLoad] classes: {list(self.model.names.values())}")
            print(f"[ModelLoad] task type: {self.model_task}")

            # imgsz 多路径检测
            self._model_imgsz = self._detect_model_imgsz(self.model, model_path, is_native_pytorch)
            print(f"[ModelLoad] inference resolution imgsz={self._model_imgsz}")

            # CUDA warm-up (Step 3: 走 router.warmup_lock 串行)
            warmup_ok = True
            if device.startswith('cuda'):
                warmup_ok = self._warmup_model_cuda(self.model, device, self._model_imgsz,
                                                   self.use_half, is_native_pytorch,
                                                   log_tag='主模型',
                                                   update_imgsz_cb=lambda sz: setattr(self, '_model_imgsz', sz))

            # v3.8.x: 转换格式 (.engine / .onnx / .torchscript) 预热失败 → 自动 fallback 到原始 PyTorch 模型.
            # 触发场景: TRT 版本不兼容 / engine 文件损坏 / ONNX runtime 缺失 等.
            # 老行为是吞掉异常, 留下空壳模型, 每帧推理 deserialize 失败, 客户看到"画面在跑但检测数一直 0".
            # 新行为: 检测到失败 + 有原始 .pt 路径 → 释放当前坏模型, 重新加载 .pt 走 PyTorch 推理.
            if (not warmup_ok) and (not is_native_pytorch) \
                    and self._original_pt_path and model_path != self._original_pt_path:
                print(f"[ModelLoad] converted model warm-up failed ({model_path}), auto fallback to original PyTorch model: {self._original_pt_path}")
                debug_center.dbg("backend.detection", "预热失败 fallback", f"path={model_path} → {self._original_pt_path}")
                try:
                    self._release_model()
                except Exception as _e:
                    print(f"[ModelLoad] release bad model before fallback error (ignored): {_e}")
                self.model = YOLO(self._original_pt_path)
                model_path = self._original_pt_path
                self.model_path = model_path
                self.model_task = getattr(self.model, 'task', 'detect')
                is_native_pytorch = True
                self._is_native_pytorch = True
                self.model.to(device)
                self._model_imgsz = self._detect_model_imgsz(self.model, model_path, True)
                print(f"[ModelLoad] after fallback imgsz={self._model_imgsz}")
                if device.startswith('cuda'):
                    self._warmup_model_cuda(self.model, device, self._model_imgsz,
                                            self.use_half, True,
                                            log_tag='主模型(fallback)',
                                            update_imgsz_cb=lambda sz: setattr(self, '_model_imgsz', sz))

            # warm-up 之后 AutoBackend 已实例化, 用权威 imgsz 做最终修正
            authoritative = self._read_authoritative_imgsz(self.model)
            if authoritative and authoritative > 0 and authoritative != self._model_imgsz:
                print(f"[ModelLoad] AutoBackend authoritative imgsz={authoritative} (corrected {self._model_imgsz} -> {authoritative})")
                self._model_imgsz = authoritative

            # Step 3: 把 host 状态镜像到 main slot, 让 router/mi 能读到等价值
            self._mirror_host_to_main()
            debug_center.dbg("backend.detection", "load_model 成功", f"path={model_path} device={self.current_device_info} imgsz={self._model_imgsz}")
            return True
        except Exception as e:
            print(f"[ModelLoad] failed: {e}")
            debug_center.dbg("backend.detection", "load_model 失败", f"path={model_path} err={e}")
            traceback.print_exc()
            self.model = None
            self.current_device_info = None
            # 失败也同步到 main (清空状态)
            self._mirror_host_to_main()
            return False

    def _release_model(self):
        """释放主模型和 GPU 资源 (向后兼容老接口).

        老调用方: source_lifecycle_mixin.py:303 / load_model 内部 / main.py:887
        Step 3: 末尾镜像到 main, 保持一致性
        """
        try:
            import torch
            import gc

            # 先关闭推理线程池 (兼容层路由到 inference_exec.shutdown())
            self._shutdown_inference_executor()

            if self.model is not None:
                print("[ModelRelease] releasing model resources...")

                # 1. 等待 CUDA 操作完成
                if torch.cuda.is_available():
                    torch.cuda.synchronize()

                del self.model
                self.model = None
                self.model_task = 'detect'

                # 2. Python 垃圾回收
                gc.collect()

                # 3. 清理 CUDA 缓存
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    allocated = torch.cuda.memory_allocated() / 1024**2
                    cached = torch.cuda.memory_reserved() / 1024**2
                    print(f"[ModelRelease] VRAM: allocated={allocated:.1f}MB, cached={cached:.1f}MB")

                print("[ModelRelease] model resources released")
        except Exception as e:
            print(f"[ModelRelease] error releasing model: {e}")
        finally:
            # Step 3: 把 host 清空状态镜像到 main, 让 router 数据一致
            self._mirror_host_to_main()

    # ============================================================
    # 新公共 API: 多模型加载 / 释放 / 设备切换
    # ============================================================
    def load_model_into_slot(
        self,
        name: str,
        model_path: str,
        *,
        conf: Optional[float] = None,
        iou: Optional[float] = None,
        roi: Optional[list] = None,
        schedule: Optional[dict] = None,
        class_filter: Optional[list] = None,
        priority: Optional[int] = None,
        display_color: Optional[str] = None,
        use_half: Optional[bool] = None,
        device: Optional[str] = None,
        original_pt_path: Optional[str] = None,
    ) -> bool:
        """加载 YOLO 模型到指定 slot (新签名, Step 3+ 多模型场景使用).

        - name='main' 时: 等价于走 load_model() 的逻辑 + 把 conf/iou/roi/schedule
          等额外参数写入 main slot, 同时双向同步到 host 老字段
        - name != 'main' 时: 完全独立 slot, 不动 host 老字段
        - mi 不存在时自动创建 (默认 priority=50, every_frame, 主模型 priority=100)
        """
        from backend.api.source_inference_router import ModelInstance, Schedule

        mi = self._router.get(name)
        if mi is None:
            mi = ModelInstance(name=name, priority=(100 if name == 'main' else 50))
            self._router.add_model(mi)

        # 配置参数 (允许只更新部分)
        if conf is not None:
            mi.conf = conf
        if iou is not None:
            mi.iou = iou
        if roi is not None:
            mi.roi = list(roi) if roi else None
        if schedule is not None:
            if isinstance(schedule, dict):
                mi.schedule = Schedule(**schedule)
            elif isinstance(schedule, Schedule):
                mi.schedule = schedule
            else:
                raise TypeError(f"schedule 必须是 dict 或 Schedule, 不是 {type(schedule)}")
        if class_filter is not None:
            mi.class_filter = set(class_filter) if class_filter else None
        if priority is not None:
            mi.priority = priority
        if display_color is not None:
            mi.display_color = display_color
        if use_half is not None:
            mi.use_half = use_half
        elif name == 'main':
            mi.use_half = self.use_half  # main 默认继承 host
        # device: mi 自带 current_device_info, 这里只是写入预期设备 (实际由 _load_model_into 解析)
        if device is None:
            device = self.device

        ok = self._load_model_into(mi, model_path, device=device, original_pt_path=original_pt_path)

        # name='main' 时双向同步: mi 的状态镜像到 host 老字段
        if ok and name == 'main':
            self._mirror_main_to_host()
        return ok

    def _release_model_from(self, mi: "ModelInstance"):
        """释放指定 slot 的模型实例 (不动 executor, 不动其他 slot).

        用于副模型独立释放 (例如 stop_detection 时只释放副模型保留主模型),
        或 release_all_models 内部循环调用.
        """
        try:
            import torch
            import gc

            if mi.model is None:
                return
            print(f"[ModelRelease] [{mi.name}] releasing model resources...")

            if torch.cuda.is_available():
                torch.cuda.synchronize()

            del mi.model
            mi.model = None
            mi.model_task = 'detect'
            mi.current_device_info = None

            gc.collect()

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                allocated = torch.cuda.memory_allocated() / 1024**2
                cached = torch.cuda.memory_reserved() / 1024**2
                print(f"[ModelRelease] [{mi.name}] VRAM: allocated={allocated:.1f}MB cached={cached:.1f}MB")

            print(f"[ModelRelease] [{mi.name}] model resources released")

            # main slot 释放完, 同步到 host 老字段
            if mi.name == 'main':
                self._mirror_main_to_host()
        except Exception as e:
            print(f"[资源释放] [{mi.name}] 释放出错: {e}")

    def release_all_models(self):
        """释放所有 slot 的模型 + 关闭推理线程池 (切项目 / 关机 用).

        - 不删除 ModelInstance 壳 (main 永远存在的契约保留)
        - 调用顺序: 先 shutdown executor (停止派发) → 再逐个 _release_model_from
        """
        try:
            self._shutdown_inference_executor()
            for mi in list(self._router.models.values()):
                self._release_model_from(mi)
            self._mirror_main_to_host()
        except Exception as e:
            print(f"[ModelRelease] release_all_models error: {e}")

    # ============================================================
    # 双向同步桥 (Step 3 关键): host ↔ main slot
    # ============================================================
    def _mirror_host_to_main(self):
        """把 host 老字段镜像写入 main slot.

        在 load_model() / _release_model() 末尾调用, 保证老链路写完后
        new-style 链路也能从 mi 读到等价状态.
        """
        if not hasattr(self, '_router'):
            return
        main = self._router.get('main')
        if main is None:
            return
        main.model = self.model
        main.model_path = self.model_path
        main.model_task = self.model_task
        main._original_pt_path = self._original_pt_path
        main._is_native_pytorch = self._is_native_pytorch
        main._model_imgsz = self._model_imgsz
        main.current_device_info = self.current_device_info
        # use_half 不镜像: 它是用户全局配置 (use_half 在 host 上由 _load_device_config 读取),
        # 而 main.use_half 是 per-slot 开关. main 默认继承 host.use_half (在 load_model 时同步).
        main.use_half = self.use_half

    def _mirror_main_to_host(self):
        """把 main slot 字段镜像写回 host 老字段.

        在 load_model_into_slot('main', ...) / _release_model_from(main) 末尾调用,
        保证新接口操作 main slot 后, 老链路读 host 老字段也能看到最新状态.
        """
        if not hasattr(self, '_router'):
            return
        main = self._router.get('main')
        if main is None:
            return
        self.model = main.model
        self.model_path = main.model_path
        self.model_task = main.model_task
        self._original_pt_path = main._original_pt_path
        self._is_native_pytorch = main._is_native_pytorch
        self._model_imgsz = main._model_imgsz
        self.current_device_info = main.current_device_info

    # ============================================================
    # 内部核心: 按 mi 加载 (新链路 + 老链路共享)
    # ============================================================
    def _load_model_into(
        self,
        mi: "ModelInstance",
        model_path: str,
        *,
        device: str = "auto",
        original_pt_path: Optional[str] = None,
    ) -> bool:
        """把模型加载到指定 ModelInstance, 副模型加载入口."""
        debug_center.dbg("backend.detection", "slot 模型加载开始", f"slot={mi.name} path={model_path}")
        try:
            from ultralytics import YOLO
            import torch

            if original_pt_path:
                mi._original_pt_path = original_pt_path
            elif model_path.endswith('.pt') or model_path.endswith('.pth'):
                mi._original_pt_path = model_path

            if mi.model is not None:
                print(f"[ModelLoad] [{mi.name}] releasing old model: {mi.model_path}")
                self._release_model_from(mi)

            try:
                mi.model = YOLO(model_path)
            except Exception as e:
                if mi._original_pt_path and model_path != mi._original_pt_path:
                    print(f"[ModelLoad] [{mi.name}] converted model load failed ({e}), fallback: {mi._original_pt_path}")
                    mi.model = YOLO(mi._original_pt_path)
                    model_path = mi._original_pt_path
                else:
                    raise
            mi.model_path = model_path
            mi.model_task = getattr(mi.model, 'task', 'detect')
            mi._is_native_pytorch = (model_path.endswith('.pt') or model_path.endswith('.pth'))

            # 设备解析
            if device == 'auto':
                resolved_device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
            else:
                resolved_device = device

            if mi._is_native_pytorch:
                mi.model.to(resolved_device)

            if resolved_device.startswith('cuda'):
                gpu_idx = int(resolved_device.split(':')[1]) if ':' in resolved_device else 0
                gpu_name = torch.cuda.get_device_name(gpu_idx)
                mi.current_device_info = {'type': 'GPU', 'name': gpu_name, 'device': resolved_device}
                print(f"[ModelLoad] [{mi.name}] success: {model_path} -> GPU: {gpu_name}")
            else:
                mi.current_device_info = {'type': 'CPU', 'name': 'CPU', 'device': 'cpu'}
                print(f"[ModelLoad] [{mi.name}] success: {model_path} -> CPU")

            if hasattr(mi.model, 'names'):
                print(f"[ModelLoad] [{mi.name}] classes: {list(mi.model.names.values())}")
            print(f"[ModelLoad] [{mi.name}] task type: {mi.model_task}")

            mi._model_imgsz = self._detect_model_imgsz(mi.model, model_path, mi._is_native_pytorch)
            print(f"[ModelLoad] [{mi.name}] inference resolution imgsz={mi._model_imgsz}")

            if resolved_device.startswith('cuda'):
                self._warmup_model_cuda(
                    mi.model, resolved_device, mi._model_imgsz, mi.use_half, mi._is_native_pytorch,
                    log_tag=f"[{mi.name}]",
                    update_imgsz_cb=lambda sz: setattr(mi, '_model_imgsz', sz),
                )

            authoritative = self._read_authoritative_imgsz(mi.model)
            if authoritative and authoritative > 0 and authoritative != mi._model_imgsz:
                print(f"[ModelLoad] [{mi.name}] AutoBackend authoritative imgsz={authoritative} (corrected {mi._model_imgsz} -> {authoritative})")
                mi._model_imgsz = authoritative

            debug_center.dbg("backend.detection", "slot 模型加载成功", f"slot={mi.name} path={model_path} imgsz={mi._model_imgsz}")
            return True
        except Exception as e:
            print(f"[ModelLoad] [{mi.name}] failed: {e}")
            debug_center.dbg("backend.detection", "slot 模型加载失败", f"slot={mi.name} path={model_path} err={e}")
            traceback.print_exc()
            mi.model = None
            mi.current_device_info = None
            return False

    # ============================================================
    # 工具: imgsz 检测 / warm-up (从老 load_model 内联代码抽出)
    # ============================================================
    def _detect_model_imgsz(self, model, model_path: str, is_native_pytorch: bool) -> int:
        """多路径检测模型推理 imgsz.

        优先级:
          1) .engine 文件头部嵌入的 Ultralytics JSON metadata (最权威, 无需触发 AutoBackend)
          2) AutoBackend 已实例化时的 bindings / input_shape / context.get_tensor_shape
          3) .pt 模型 ckpt 元数据 (model.overrides / model.model.args)
          4) 默认 640 fallback
        """
        detected_imgsz = 640
        engine_imgsz = None

        # 方法 0 (v2.7.3): 直接解析 .engine 文件头部 metadata
        # Step 3 修复: 老代码漏 import 静默退化, 现在延迟 import (避免循环 import)
        if not is_native_pytorch and model_path.endswith('.engine'):
            try:
                _read_engine_metadata_imgsz = _get_engine_metadata_imgsz_reader()
                engine_imgsz = _read_engine_metadata_imgsz(model_path)
                if engine_imgsz:
                    print(f"[ModelLoad] detected imgsz from engine header metadata={engine_imgsz}")
            except Exception as e:
                print(f"[ModelLoad] engine metadata parse failed: {e}")

        try:
            if engine_imgsz is None and not is_native_pytorch and hasattr(model, 'model'):
                inner = model.model
                # 方法1: 从 bindings 读取
                if hasattr(inner, 'bindings') and inner.bindings:
                    iter_b = inner.bindings.values() if isinstance(inner.bindings, dict) else inner.bindings
                    for b in iter_b:
                        shape = getattr(b, 'shape', None)
                        if shape and len(shape) == 4:
                            engine_imgsz = max(shape[2], shape[3])
                            print(f"[ModelLoad] detected imgsz from engine bindings={engine_imgsz}")
                            break
                # 方法2: 从 input_shape 读取
                if engine_imgsz is None and hasattr(inner, 'input_shape'):
                    s = inner.input_shape
                    if isinstance(s, (list, tuple)) and len(s) >= 3:
                        engine_imgsz = max(s[-2], s[-1])
                        print(f"[ModelLoad] detected imgsz from input_shape={engine_imgsz}")
                # 方法3: AutoBackend 已读取的 imgsz 属性
                if engine_imgsz is None and hasattr(inner, 'imgsz'):
                    s = inner.imgsz
                    if isinstance(s, (list, tuple)) and len(s) >= 1:
                        engine_imgsz = max(s)
                        print(f"[ModelLoad] detected imgsz from AutoBackend.imgsz={engine_imgsz}")
                    elif isinstance(s, int):
                        engine_imgsz = s
                        print(f"[ModelLoad] detected imgsz from AutoBackend.imgsz={engine_imgsz}")
                # 方法4: TensorRT context/engine 读取 binding shape
                if engine_imgsz is None:
                    for attr_name in ('context', 'engine', 'runtime'):
                        ctx = getattr(inner, attr_name, None)
                        if ctx is None:
                            continue
                        if hasattr(ctx, 'get_tensor_shape'):
                            try:
                                for i in range(10):
                                    name = ctx.get_tensor_name(i) if hasattr(ctx, 'get_tensor_name') else None
                                    if name is None:
                                        break
                                    s = ctx.get_tensor_shape(name)
                                    if len(s) == 4 and s[1] == 3:
                                        engine_imgsz = max(s[2], s[3])
                                        print(f"[ModelLoad] detected imgsz from TRT {attr_name}.get_tensor_shape={engine_imgsz}")
                                        break
                            except Exception:
                                pass
                        if engine_imgsz:
                            break
            if engine_imgsz and engine_imgsz > 0:
                detected_imgsz = engine_imgsz
            elif hasattr(model, 'overrides') and 'imgsz' in model.overrides:
                raw = model.overrides['imgsz']
                detected_imgsz = raw if isinstance(raw, int) else max(raw)
            elif hasattr(model, 'model') and hasattr(model.model, 'args'):
                args = model.model.args
                if isinstance(args, dict) and 'imgsz' in args:
                    raw = args['imgsz']
                    detected_imgsz = raw if isinstance(raw, int) else max(raw)
        except Exception as e:
            print(f"[ModelLoad] detect imgsz failed, using default 640: {e}")
        debug_center.dbg("backend.detection", "imgsz 探测结果", f"path={model_path} imgsz={detected_imgsz} engine_imgsz={engine_imgsz}")
        return detected_imgsz

    def _warmup_model_cuda(self, model, device: str, imgsz: int, use_half: bool,
                           is_native_pytorch: bool, log_tag: str = '',
                           update_imgsz_cb=None) -> bool:
        """CUDA warm-up + AssertionError 时自动修正 imgsz.

        Step 3: 在 router.warmup_lock 下串行 — 多模型场景下避免同时初始化
        TensorRT context / 申请 workspace 内存, 防 3050 OOM 峰值.

        Returns:
            True  → 预热成功 (或 imgsz 修正后成功).
            False → 预热失败. 调用方可据此决定是否 fallback 到原始 PyTorch 模型.
                    历史上本方法吞掉所有异常返回 None, 导致 .engine 文件失效时
                    (TRT 版本不兼容 / engine 损坏) 模型加载假装成功, 实际每帧
                    推理都 deserialize 失败, 客户感知"画面在跑但检测结果一直 0".
        """
        warmup_lock = getattr(getattr(self, '_router', None), 'warmup_lock', None)

        def _do_warmup(_imgsz: int):
            _half = use_half if is_native_pytorch else False
            print(f"[ModelWarmup] {log_tag} CUDA warm-up (half={_half}, imgsz={_imgsz})...")
            model.predict(
                np.zeros((_imgsz, _imgsz, 3), dtype=np.uint8),
                conf=0.5, imgsz=_imgsz, verbose=False, device=device, half=_half
            )

        try:
            if warmup_lock is not None:
                with warmup_lock:
                    _do_warmup(imgsz)
            else:
                _do_warmup(imgsz)
            print(f"[ModelWarmup] {log_tag} warm-up done")
            return True
        except AssertionError as ae:
            import re
            m = re.search(r'model size \(1, 3, (\d+), (\d+)\)', str(ae))
            if m:
                correct_sz = max(int(m.group(1)), int(m.group(2)))
                print(f"[ModelWarmup] {log_tag} TensorRT engine actually needs imgsz={correct_sz}, auto-correcting")
                debug_center.dbg("backend.detection", "TRT imgsz 自动修正", f"{log_tag} {imgsz} → {correct_sz}")
                if update_imgsz_cb:
                    update_imgsz_cb(correct_sz)
                try:
                    if warmup_lock is not None:
                        with warmup_lock:
                            _do_warmup(correct_sz)
                    else:
                        _do_warmup(correct_sz)
                    print(f"[ModelWarmup] {log_tag} warm-up done (corrected)")
                    return True
                except Exception as e2:
                    print(f"[ModelWarmup] {log_tag} warm-up still failed after correction: {e2}")
                    return False
            else:
                print(f"[ModelWarmup] {log_tag} warm-up failed: {ae}")
                return False
        except Exception as e:
            print(f"[ModelWarmup] {log_tag} warm-up failed: {e}")
            return False

    @staticmethod
    def _read_authoritative_imgsz(model) -> Optional[int]:
        """warm-up 之后从 AutoBackend 读权威 imgsz (TensorRT engine metadata).

        warm-up 之后 Ultralytics 才创建 AutoBackend; 此时 AutoBackend.imgsz 是
        从 engine metadata 读出来的最权威值, 用它做最终修正, 避免任何上游路径
        漏检导致 imgsz 停在 640 默认值.
        """
        try:
            predictor = getattr(model, 'predictor', None)
            inner = getattr(predictor, 'model', None) if predictor is not None else None
            if inner is None:
                return None
            if hasattr(inner, 'imgsz'):
                s = inner.imgsz
                if isinstance(s, (list, tuple)) and len(s) >= 1:
                    return max(s)
                elif isinstance(s, int):
                    return s
            if hasattr(inner, 'bindings') and inner.bindings:
                iter_b = inner.bindings.values() if isinstance(inner.bindings, dict) else inner.bindings
                for b in iter_b:
                    shape = getattr(b, 'shape', None)
                    if shape and len(shape) == 4 and shape[1] == 3:
                        return max(shape[2], shape[3])
        except Exception as e:
            print(f"[ModelLoad] read AutoBackend authoritative imgsz failed: {e}")
        return None
