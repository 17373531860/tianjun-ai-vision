"""模型加载 (load_model, v2.7.16 P6 阶段一从 source.py 整体搬出)。

把 202 行的 load_model 直接整体搬到独立文件。后续如需细分,
可在 mixin 内继续按职责拆 _load_model_open_with_fallback /
_resolve_inference_device / _detect_model_imgsz / _warmup_cuda_model /
_apply_authoritative_imgsz。

宿主必须提供的属性: self.model / self._original_pt_path / self.current_device_info /
                  self.detection_enabled / self.imgsz / self.fp16 / self.target_device 等
"""
from __future__ import annotations

import traceback

import numpy as np


class ModelLoadMixin:
    def load_model(self, model_path: str, original_pt_path: str = None) -> bool:
        """加载 YOLO 模型。转换模型加载失败时自动回退到原始 .pt。"""
        try:
            from ultralytics import YOLO
            import torch
            
            if original_pt_path:
                self._original_pt_path = original_pt_path
            elif model_path.endswith('.pt') or model_path.endswith('.pth'):
                self._original_pt_path = model_path
            
            # 先释放旧模型
            if self.model is not None:
                print(f"[模型加载] 释放旧模型: {self.model_path}")
                self._release_model()
            
            try:
                self.model = YOLO(model_path)
            except Exception as e:
                if self._original_pt_path and model_path != self._original_pt_path:
                    print(f"[模型加载] 转换模型加载失败 ({e})，回退到原始模型: {self._original_pt_path}")
                    self.model = YOLO(self._original_pt_path)
                    model_path = self._original_pt_path
                else:
                    raise
            self.model_path = model_path
            self.model_task = getattr(self.model, 'task', 'detect')  # 'detect' or 'segment'
            
            is_native_pytorch = model_path.endswith('.pt') or model_path.endswith('.pth')
            self._is_native_pytorch = is_native_pytorch
            
            # 设置推理设备
            if self.device == 'auto':
                if torch.cuda.is_available():
                    device = 'cuda:0'
                else:
                    device = 'cpu'
            else:
                device = self.device
            
            # .to(device) 仅对原生 PyTorch 模型有效；导出格式在 predict 时通过 device 参数指定
            if is_native_pytorch:
                self.model.to(device)
            
            # 记录当前设备信息
            if device.startswith('cuda'):
                gpu_idx = int(device.split(':')[1]) if ':' in device else 0
                gpu_name = torch.cuda.get_device_name(gpu_idx)
                self.current_device_info = {'type': 'GPU', 'name': gpu_name, 'device': device}
                print(f"模型加载成功: {model_path} -> GPU: {gpu_name}")
            else:
                self.current_device_info = {'type': 'CPU', 'name': 'CPU', 'device': 'cpu'}
                print(f"模型加载成功: {model_path} -> CPU")
            
            if hasattr(self.model, 'names'):
                print(f"类别: {list(self.model.names.values())}")
            print(f"模型任务类型: {self.model_task}")
            
            # 自动检测模型的 imgsz
            # 优先级（v2.7.3）：
            #   1) .engine 文件头部嵌入的 Ultralytics JSON metadata（最权威，无需触发 AutoBackend 实例化）
            #   2) .pt 模型 ckpt 元数据（model.overrides / model.model.args）
            #   3) AutoBackend 已实例化时的 bindings / input_shape / context.get_tensor_shape
            #   4) 默认 640（fallback）
            detected_imgsz = 640
            engine_imgsz = None

            # 方法0（v2.7.3）：直接解析 .engine 文件头部 metadata，最可靠
            if not is_native_pytorch and model_path.endswith('.engine'):
                try:
                    engine_imgsz = _read_engine_metadata_imgsz(model_path)
                    if engine_imgsz:
                        print(f"[模型加载] 从 engine 文件头 metadata 检测到 imgsz={engine_imgsz}")
                except Exception as e:
                    print(f"[模型加载] engine metadata 解析失败: {e}")

            try:
                if engine_imgsz is None and not is_native_pytorch and hasattr(self.model, 'model'):
                    inner = self.model.model
                    # 方法1: 从 bindings 读取
                    if hasattr(inner, 'bindings') and inner.bindings:
                        for b in inner.bindings.values() if isinstance(inner.bindings, dict) else inner.bindings:
                            shape = getattr(b, 'shape', None)
                            if shape and len(shape) == 4:
                                engine_imgsz = max(shape[2], shape[3])
                                print(f"[模型加载] 从引擎 bindings 检测到 imgsz={engine_imgsz}")
                                break
                    # 方法2: 从 input_shape 读取
                    if engine_imgsz is None and hasattr(inner, 'input_shape'):
                        s = inner.input_shape
                        if isinstance(s, (list, tuple)) and len(s) >= 3:
                            engine_imgsz = max(s[-2], s[-1])
                            print(f"[模型加载] 从 input_shape 检测到 imgsz={engine_imgsz}")
                    # 方法3: 从 AutoBackend 已读取的 imgsz 属性（v2.7.3）
                    if engine_imgsz is None and hasattr(inner, 'imgsz'):
                        s = inner.imgsz
                        if isinstance(s, (list, tuple)) and len(s) >= 1:
                            engine_imgsz = max(s)
                            print(f"[模型加载] 从 AutoBackend.imgsz 检测到 imgsz={engine_imgsz}")
                        elif isinstance(s, int):
                            engine_imgsz = s
                            print(f"[模型加载] 从 AutoBackend.imgsz 检测到 imgsz={engine_imgsz}")
                    # 方法4: 从 context/engine 读取 TensorRT binding shape
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
                                            print(f"[模型加载] 从 TRT {attr_name}.get_tensor_shape 检测到 imgsz={engine_imgsz}")
                                            break
                                except Exception:
                                    pass
                            if engine_imgsz:
                                break
                if engine_imgsz and engine_imgsz > 0:
                    detected_imgsz = engine_imgsz
                elif hasattr(self.model, 'overrides') and 'imgsz' in self.model.overrides:
                    raw = self.model.overrides['imgsz']
                    detected_imgsz = raw if isinstance(raw, int) else max(raw)
                elif hasattr(self.model, 'model') and hasattr(self.model.model, 'args'):
                    args = self.model.model.args
                    if isinstance(args, dict) and 'imgsz' in args:
                        raw = args['imgsz']
                        detected_imgsz = raw if isinstance(raw, int) else max(raw)
            except Exception as e:
                print(f"[模型加载] 检测 imgsz 失败，使用默认 640: {e}")
            self._model_imgsz = detected_imgsz
            print(f"[模型加载] 推理分辨率 imgsz={self._model_imgsz}")
            
            if device.startswith('cuda'):
                try:
                    import numpy as np
                    _half = self.use_half if is_native_pytorch else False
                    sz = self._model_imgsz
                    print(f"[模型预热] CUDA warm-up (half={_half}, imgsz={sz})...")
                    self.model.predict(
                        np.zeros((sz, sz, 3), dtype=np.uint8),
                        conf=0.5, imgsz=sz, verbose=False, device=device,
                        half=_half
                    )
                    print("[模型预热] warm-up done")
                except AssertionError as ae:
                    import re
                    m = re.search(r'model size \(1, 3, (\d+), (\d+)\)', str(ae))
                    if m:
                        correct_sz = max(int(m.group(1)), int(m.group(2)))
                        print(f"[模型预热] TensorRT 引擎实际需要 imgsz={correct_sz}，自动修正")
                        self._model_imgsz = correct_sz
                        self.model.predict(
                            np.zeros((correct_sz, correct_sz, 3), dtype=np.uint8),
                            conf=0.5, imgsz=correct_sz, verbose=False, device=device,
                            half=_half
                        )
                        print("[模型预热] warm-up done (修正后)")
                    else:
                        print(f"[模型预热] warm-up failed: {ae}")
                except Exception as e:
                    print(f"[模型预热] warm-up failed: {e}")

            # v2.7.3: warm-up 之后 Ultralytics 才创建 AutoBackend；此时 AutoBackend.imgsz 是从 engine
            # metadata 读出来的最权威值，用它做最终修正，避免任何上游路径漏检导致 _model_imgsz 停在 640
            try:
                predictor = getattr(self.model, 'predictor', None)
                inner = getattr(predictor, 'model', None) if predictor is not None else None
                authoritative = None
                if inner is not None:
                    if hasattr(inner, 'imgsz'):
                        s = inner.imgsz
                        if isinstance(s, (list, tuple)) and len(s) >= 1:
                            authoritative = max(s)
                        elif isinstance(s, int):
                            authoritative = s
                    if authoritative is None and hasattr(inner, 'bindings') and inner.bindings:
                        for b in (inner.bindings.values() if isinstance(inner.bindings, dict) else inner.bindings):
                            shape = getattr(b, 'shape', None)
                            if shape and len(shape) == 4 and shape[1] == 3:
                                authoritative = max(shape[2], shape[3])
                                break
                if authoritative and authoritative > 0 and authoritative != self._model_imgsz:
                    print(f"[模型加载] AutoBackend 权威 imgsz={authoritative}（修正 {self._model_imgsz} → {authoritative}）")
                    self._model_imgsz = authoritative
            except Exception as e:
                print(f"[模型加载] 读取 AutoBackend 权威 imgsz 失败: {e}")

            return True
        except Exception as e:
            print(f"模型加载失败: {e}")
            import traceback
            traceback.print_exc()
            self.model = None
            self.current_device_info = None
            return False
