"""推理执行 (_detect_only/_detect_and_track/_detect_segment).

Step 4 (feat/multi-model-roi-link) 改造要点
==========================================
3 个 runner 接受可选 `mi: ModelInstance` 参数:
  - 不传时默认从 self._router.get('main') 取 (老链路 100% 兼容)
  - 传时使用 mi 的 model / conf / iou / imgsz / use_half / class_filter / roi
  - 推理前: apply_roi_mask(frame, mi) 把 ROI 外区域置黑
  - 推理后: is_bbox_center_in_roi 后置过滤 (mask 边缘可能伪检测, 双保险)
  - 注入到每条 detection: model_name = mi.name, display_color = mi.display_color
  - class_filter (mi 上) 与 enabled_labels (项目步骤) 协同过滤
  - model.predict 在 self._router.gpu_lock 下调用 (Step 5 多模型并发派发时才会真用)

向后兼容
========
- 老调用 self._detect_only(frame) 等同 self._detect_only(frame, mi=None)
- mi=None → 自动从 router 取 main (Step 3 双写保证 main 字段已与 host 同步)
- main 是 ModelInstance 实例时用其字段, 否则 fallback 读 self.X (极端情况防御)

历史依赖 (mi 不可用时的 fallback)
================================
  self.model / self.conf_threshold / self.iou_threshold / self._model_imgsz /
  self.use_half / self._is_native_pytorch / self.current_device_info / self.model_task
"""
from __future__ import annotations

import time
from typing import Optional

import cv2
import numpy as np

from backend.api.source_sdk_loader import debug_log
from backend.api.source_geometry import clip_bbox_normalized
from backend.api.source_roi import apply_roi_mask, ensure_roi_mask, is_bbox_center_in_roi


def _resolve_mi(host, mi):
    """统一入口: mi=None 时取 main; main 也无效时返回 None (调用方应早退)"""
    if mi is not None:
        return mi
    router = getattr(host, '_router', None)
    if router is None:
        return None
    return router.get('main')


def _resolve_inference_params(host, mi):
    """从 mi 读推理参数; mi 不存在时 fallback 到 host 老字段 (向后兼容防御)."""
    if mi is None:
        # 没 mi 也没 main, 这种情况理论上不会到这里 (_inference_loop 会先 check
        # self.model is not None), 但为防御写一个 fallback
        return {
            'model': host.model,
            'conf': host.conf_threshold,
            'iou': host.iou_threshold,
            'imgsz': host._model_imgsz,
            'use_half': host.use_half,
            'is_native_pytorch': host._is_native_pytorch,
            'device': (host.current_device_info or {}).get('device', 'cpu'),
            'model_task': getattr(host, 'model_task', 'detect'),
            'class_filter': None,
            'model_name': 'main',
            'display_color': '#10b981',
        }
    device_info = mi.current_device_info or {}
    return {
        'model': mi.model,
        'conf': mi.conf,
        'iou': mi.iou,
        'imgsz': mi._model_imgsz,
        'use_half': mi.use_half,
        'is_native_pytorch': mi._is_native_pytorch,
        'device': device_info.get('device', 'cpu'),
        'model_task': mi.model_task,
        'class_filter': mi.class_filter,
        'model_name': mi.name,
        'display_color': mi.display_color,
    }


def _annotate_detection(det, params, mi):
    """把 model_name / display_color 注入 detection (供前端按色画框)"""
    det['model_name'] = params['model_name']
    det['display_color'] = params['display_color']
    return det


def _passes_class_filter(class_name: str, params: dict) -> bool:
    """class_filter (per-mi 类别白名单) 过滤. None 表示不限."""
    cf = params['class_filter']
    if cf is None:
        return True
    return class_name in cf


def _passes_box_size_limit(host, class_name: str, nw: float, nh: float) -> bool:
    """v3.10+ 步骤级 box 尺寸过滤.

    host.step_box_size_limits = {label: (max_w, max_h)} (归一化 0~1).
    用途: 模型把"工件整体形态"误识别为某个 label 时 box 异常宽/高,
    通过此过滤在 detection 出口直接丢弃 (任意 logic_mode 通用).

    返回 True = 通过 (保留 detection), False = 丢弃.
    """
    limits = getattr(host, 'step_box_size_limits', None)
    if not limits:
        return True
    lim = limits.get(class_name)
    if lim is None:
        return True
    max_w, max_h = lim
    if max_w > 0 and nw > max_w:
        return False
    if max_h > 0 and nh > max_h:
        return False
    return True


def _gpu_lock_ctx(host):
    """返回一个上下文管理器: 多模型场景下用 router.gpu_lock 串行 GPU 调用.

    单模型场景下 (router 不存在 / lock 不存在) 返回一个 no-op 上下文.
    """
    router = getattr(host, '_router', None)
    if router is None:
        return _NoopContext()
    return router.gpu_lock


class _NoopContext:
    def __enter__(self): return None
    def __exit__(self, *a): return False


class DetectRunnersMixin:
    def _handle_inference_timeout(self, model_name: str):
        """C1: 推理超时统一处理。每次超时都重建推理池, 连续超时升级 GPU 重置+重载模型。

        卡死的 worker 线程留在旧池里被丢弃, 下一帧 _get_inference_executor() 会建
        一个全新单线程池, 不会再排在卡死任务后面级联超时。只在"本来就超时丢帧"的
        异常路径触发, 正常推理一次都不进, 高收益低风险。
        """
        self._inference_timeout_count += 1
        debug_log(f"!!! [{model_name}] 推理超时 ({self._inference_timeout}秒), 连续超时次数: {self._inference_timeout_count}", "DETECT")
        print(f"[警告] [{model_name}] 推理超时 ({self._inference_timeout}秒), 连续超时次数: {self._inference_timeout_count}")

        # C1: 每次超时都重建推理池(丢弃卡死 worker)
        self._shutdown_inference_executor()

        if self._inference_timeout_count >= self._max_consecutive_timeouts:
            debug_log(f"!!! [{model_name}] 连续 {self._max_consecutive_timeouts} 次推理超时, 尝试重置 GPU...", "DETECT")
            print(f"[错误] [{model_name}] 连续 {self._max_consecutive_timeouts} 次推理超时, 尝试重置 GPU...")
            self._emergency_gpu_reset()
            self._inference_timeout_count = 0

    def _detect_only(self, frame: np.ndarray, mi: Optional["ModelInstance"] = None) -> list:
        """只执行检测 (predict, 无 tracking, 无 segmentation).

        Step 4: 接受可选 mi 参数; mi=None 时取 main; ROI 裁剪 + class_filter +
        model_name/display_color 注入. 行为对老调用 (mi 默认) 保持一致.
        """
        detections = []
        mi = _resolve_mi(self, mi)
        params = _resolve_inference_params(self, mi)
        if params['model'] is None:
            return []

        try:
            from concurrent.futures import TimeoutError as FuturesTimeoutError

            device = params['device']
            _half = params['use_half'] and device.startswith('cuda') and params['is_native_pytorch']

            # Step 4: ROI 裁剪 (mi.roi 不可用时直接返回原 frame)
            if mi is not None:
                ensure_roi_mask(mi, frame.shape[:2])  # 缓存 mask + 顶点
            roi_frame = apply_roi_mask(frame, mi) if mi is not None else frame
            _frame = roi_frame if roi_frame.flags['C_CONTIGUOUS'] else np.ascontiguousarray(roi_frame)

            def run_inference():
                t_predict_start = time.time()
                with _gpu_lock_ctx(self):
                    result = list(params['model'].predict(
                        _frame,
                        conf=params['conf'],
                        iou=params['iou'],
                        imgsz=params['imgsz'],
                        verbose=False,
                        device=device,
                        stream=True,
                        half=_half,
                    ))
                t_predict_end = time.time()
                predict_time = (t_predict_end - t_predict_start) * 1000
                if predict_time > 150:
                    debug_log(f"[{params['model_name']}] model.predict内部耗时: {predict_time:.1f}ms", "DETECT")
                return result

            t_pool_start = time.time()
            executor = self._get_inference_executor()
            future = executor.submit(run_inference)
            try:
                t_wait_start = time.time()
                results = future.result(timeout=self._inference_timeout)
                t_wait_end = time.time()
                wait_time = (t_wait_end - t_wait_start) * 1000
                if wait_time > 200:
                    debug_log(f"[{params['model_name']}] future.result等待耗时: {wait_time:.1f}ms", "DETECT")
                self._inference_timeout_count = 0
                self._consecutive_detect_errors = 0
                self._last_successful_inference = time.time()
            except FuturesTimeoutError:
                self._handle_inference_timeout(params['model_name'])
                return []
            finally:
                del future
            t_pool_end = time.time()
            pool_time = (t_pool_end - t_pool_start) * 1000
            if pool_time > 300:
                debug_log(f"[{params['model_name']}] 推理总耗时: {pool_time:.1f}ms", "DETECT")

            h, w = frame.shape[:2]

            # 启用步骤标签 (项目级过滤, 与 mi.class_filter 协同)
            enabled_labels = self._get_enabled_labels()

            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue

                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
                    confidence = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())

                    model_obj = params['model']
                    if hasattr(model_obj, 'names') and class_id in model_obj.names:
                        class_name = model_obj.names[class_id]
                    else:
                        class_name = f"class_{class_id}"

                    # mi.class_filter (per-mi 类别白名单) 过滤
                    if not _passes_class_filter(class_name, params):
                        continue

                    # 项目步骤过滤 (从推理层面忽略禁用的步骤)
                    if enabled_labels and class_name not in enabled_labels:
                        continue

                    # 步骤特定置信度阈值
                    if self.step_conf_thresholds:
                        step_threshold = self.step_conf_thresholds.get(class_name)
                        if step_threshold is not None and confidence < step_threshold:
                            continue

                    nx, ny, nw, nh = clip_bbox_normalized(x1, y1, x2, y2, w, h)
                    # v3.10+ 步骤级 box 尺寸过滤
                    if not _passes_box_size_limit(self, class_name, nw, nh):
                        continue
                    det = {
                        'x': nx, 'y': ny, 'w': nw, 'h': nh,
                        'confidence': confidence,
                        'class_id': class_id,
                        'label': class_name,
                    }

                    # ROI 后置过滤 (中心点是否在 ROI 多边形内)
                    if mi is not None and mi.roi is not None and not is_bbox_center_in_roi(det, mi):
                        continue

                    # Step 4: getattr 防御 - 项目未 apply_project_config 时
                    # step_display_names/step_backup_map 可能没初始化, 老代码会 crash
                    sdn = getattr(self, 'step_display_names', None) or {}
                    sbm = getattr(self, 'step_backup_map', None) or {}
                    if class_name in sdn:
                        det['display_name'] = sdn[class_name]
                    if class_name in sbm:
                        det['hidden'] = True
                        det['backup_for'] = sbm[class_name]
                    _annotate_detection(det, params, mi)
                    detections.append(det)
        except Exception as e:
            self._consecutive_detect_errors = getattr(self, '_consecutive_detect_errors', 0) + 1
            if self._consecutive_detect_errors <= 3:
                print(f"[{params['model_name']}] 检测错误: {e}")
                import traceback
                traceback.print_exc()
            elif self._consecutive_detect_errors == 4:
                print(f"[警告] [{params['model_name']}] 检测持续报错, 后续相同错误将被抑制 (已连续 {self._consecutive_detect_errors} 次)")
            if self._consecutive_detect_errors > 2:
                time.sleep(0.5)

        return self._apply_rod_filters(detections)

    def _detect_and_track(self, frame: np.ndarray, mi: Optional["ModelInstance"] = None) -> list:
        """Track + (optional) Seg. mi 用法见 _detect_only docstring."""
        detections = []
        mi = _resolve_mi(self, mi)
        params = _resolve_inference_params(self, mi)
        if params['model'] is None:
            return []

        try:
            from concurrent.futures import TimeoutError as FuturesTimeoutError

            device = params['device']
            _half = params['use_half'] and device.startswith('cuda') and params['is_native_pytorch']
            _tracker_cfg = self._custom_tracker_yaml or "bytetrack.yaml"

            if mi is not None:
                ensure_roi_mask(mi, frame.shape[:2])
            roi_frame = apply_roi_mask(frame, mi) if mi is not None else frame
            _frame = roi_frame if roi_frame.flags['C_CONTIGUOUS'] else np.ascontiguousarray(roi_frame)

            def run_tracking():
                with _gpu_lock_ctx(self):
                    return list(params['model'].track(
                        _frame, conf=params['conf'], iou=params['iou'],
                        imgsz=params['imgsz'], verbose=False, device=device,
                        stream=True, persist=True, tracker=_tracker_cfg,
                        half=_half,
                    ))

            executor = self._get_inference_executor()
            future = executor.submit(run_tracking)
            try:
                results = future.result(timeout=self._inference_timeout)
                self._inference_timeout_count = 0
                self._consecutive_detect_errors = 0
                self._last_successful_inference = time.time()
            except FuturesTimeoutError:
                self._inference_timeout_count += 1
                if self._inference_timeout_count >= self._max_consecutive_timeouts:
                    self._shutdown_inference_executor()
                    self._emergency_gpu_reset()
                    self._inference_timeout_count = 0
                return []
            finally:
                del future

            enabled_labels = self._get_enabled_labels()
            is_seg = (params['model_task'] == 'segment')
            h, w = frame.shape[:2]

            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                has_track_ids = boxes.id is not None
                has_masks = is_seg and result.masks is not None

                for i, box in enumerate(boxes):
                    x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
                    confidence = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())
                    track_id = int(boxes.id[i].cpu().numpy()) if has_track_ids else -1
                    model_obj = params['model']
                    class_name = (model_obj.names[class_id]
                                  if hasattr(model_obj, 'names') and class_id in model_obj.names
                                  else f"class_{class_id}")

                    if not _passes_class_filter(class_name, params):
                        continue
                    if enabled_labels and class_name not in enabled_labels:
                        continue
                    if self.step_conf_thresholds:
                        thr = self.step_conf_thresholds.get(class_name)
                        if thr is not None and confidence < thr:
                            continue

                    nx, ny, nw, nh = clip_bbox_normalized(x1, y1, x2, y2, w, h)
                    if not _passes_box_size_limit(self, class_name, nw, nh):
                        continue
                    det = {
                        'x': nx, 'y': ny, 'w': nw, 'h': nh,
                        'confidence': confidence, 'class_id': class_id,
                        'label': class_name, 'track_id': track_id,
                    }
                    if mi is not None and mi.roi is not None and not is_bbox_center_in_roi(det, mi):
                        continue
                    if has_masks:
                        try:
                            det['mask'] = result.masks.xyn[i].tolist()
                        except Exception:
                            pass
                    sdn = getattr(self, 'step_display_names', None) or {}
                    if class_name in sdn:
                        det['display_name'] = sdn[class_name]
                    _annotate_detection(det, params, mi)
                    detections.append(det)
        except Exception as e:
            self._consecutive_detect_errors = getattr(self, '_consecutive_detect_errors', 0) + 1
            if self._consecutive_detect_errors <= 3:
                print(f"[{params['model_name']}] tracking error: {e}")
                import traceback
                traceback.print_exc()
            elif self._consecutive_detect_errors == 4:
                print(f"[警告] [{params['model_name']}] 跟踪持续报错, 后续相同错误将被抑制 (已连续 {self._consecutive_detect_errors} 次)")
            if self._consecutive_detect_errors > 2:
                time.sleep(0.5)
        return self._apply_rod_filters(detections)

    def _detect_segment(self, frame: np.ndarray, mi: Optional["ModelInstance"] = None) -> list:
        """Segmentation predict (无 tracking). mi 用法见 _detect_only docstring."""
        detections = []
        mi = _resolve_mi(self, mi)
        params = _resolve_inference_params(self, mi)
        if params['model'] is None:
            return []

        try:
            from concurrent.futures import TimeoutError as FuturesTimeoutError

            device = params['device']
            _half = params['use_half'] and device.startswith('cuda') and params['is_native_pytorch']

            if mi is not None:
                ensure_roi_mask(mi, frame.shape[:2])
            roi_frame = apply_roi_mask(frame, mi) if mi is not None else frame
            _frame = roi_frame if roi_frame.flags['C_CONTIGUOUS'] else np.ascontiguousarray(roi_frame)

            def run_inference():
                with _gpu_lock_ctx(self):
                    return list(params['model'].predict(
                        _frame, conf=params['conf'], iou=params['iou'],
                        imgsz=params['imgsz'], verbose=False, device=device, stream=True,
                        half=_half,
                    ))

            executor = self._get_inference_executor()
            future = executor.submit(run_inference)
            try:
                results = future.result(timeout=self._inference_timeout)
                self._inference_timeout_count = 0
                self._last_successful_inference = time.time()
            except FuturesTimeoutError:
                self._inference_timeout_count += 1
                if self._inference_timeout_count >= self._max_consecutive_timeouts:
                    self._shutdown_inference_executor()
                    self._emergency_gpu_reset()
                    self._inference_timeout_count = 0
                return []
            finally:
                del future

            enabled_labels = self._get_enabled_labels()
            h, w = frame.shape[:2]

            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                has_masks = result.masks is not None

                for i, box in enumerate(boxes):
                    x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
                    confidence = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())
                    model_obj = params['model']
                    class_name = (model_obj.names[class_id]
                                  if hasattr(model_obj, 'names') and class_id in model_obj.names
                                  else f"class_{class_id}")

                    if not _passes_class_filter(class_name, params):
                        continue
                    if enabled_labels and class_name not in enabled_labels:
                        continue
                    if self.step_conf_thresholds:
                        thr = self.step_conf_thresholds.get(class_name)
                        if thr is not None and confidence < thr:
                            continue

                    nx, ny, nw, nh = clip_bbox_normalized(x1, y1, x2, y2, w, h)
                    if not _passes_box_size_limit(self, class_name, nw, nh):
                        continue
                    det = {
                        'x': nx, 'y': ny, 'w': nw, 'h': nh,
                        'confidence': confidence, 'class_id': class_id, 'label': class_name,
                    }
                    if mi is not None and mi.roi is not None and not is_bbox_center_in_roi(det, mi):
                        continue
                    if has_masks:
                        try:
                            det['mask'] = result.masks.xyn[i].tolist()
                        except Exception:
                            pass
                    sdn = getattr(self, 'step_display_names', None) or {}
                    sbm = getattr(self, 'step_backup_map', None) or {}
                    if class_name in sdn:
                        det['display_name'] = sdn[class_name]
                    if class_name in sbm:
                        det['hidden'] = True
                        det['backup_for'] = sbm[class_name]
                    _annotate_detection(det, params, mi)
                    detections.append(det)
        except Exception as e:
            print(f"[{params['model_name']}] segment error: {e}")
            import traceback
            traceback.print_exc()
        return self._apply_rod_filters(detections)
