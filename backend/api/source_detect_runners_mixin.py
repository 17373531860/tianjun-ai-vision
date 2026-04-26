"""推理执行 (_detect_only/_detect_and_track/_detect_segment, v2.7.16 P6 阶段一第九刀)。

包含 3 个推理引擎封装方法 (合计 ~290 行):
  _detect_only      : 只检测, 走 model.predict, 带 ThreadPoolExecutor 超时保护 (130L)
  _detect_and_track : 检测+ByteTrack 跟踪 (87L)
  _detect_segment   : 实例分割 (75L)

依赖宿主 (VideoSourceManager):
  - 状态: model / inference_size / inference_conf / inference_iou /
          inference_device / _inference_executor / _inference_timeout_sec /
          allowed_classes / project_config 等
  - 方法: _get_inference_executor / _apply_rod_filters / _broadcast_progress
"""
import time
import traceback
import cv2
import numpy as np


class DetectRunnersMixin:
    def _detect_only(self, frame: np.ndarray) -> list:
        """只执行检测，返回检测结果（不绘制检测框）- 带超时保护"""
        detections = []
        t_func_start = time.time()
        
        try:
            device = self.current_device_info.get('device', 'cpu') if self.current_device_info else 'cpu'
            
            from concurrent.futures import TimeoutError as FuturesTimeoutError
            
            _half = self.use_half and device.startswith('cuda') and self._is_native_pytorch
            _frame = frame if frame.flags['C_CONTIGUOUS'] else np.ascontiguousarray(frame)
            
            def run_inference():
                t_predict_start = time.time()
                result = list(self.model.predict(
                    _frame, 
                    conf=self.conf_threshold, 
                    iou=self.iou_threshold, 
                    imgsz=self._model_imgsz, 
                    verbose=False, 
                    device=device,
                    stream=True,
                    half=_half
                ))
                t_predict_end = time.time()
                predict_time = (t_predict_end - t_predict_start) * 1000
                if predict_time > 150:
                    debug_log(f"model.predict内部耗时: {predict_time:.1f}ms", "DETECT")
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
                    debug_log(f"future.result等待耗时: {wait_time:.1f}ms", "DETECT")
                self._inference_timeout_count = 0
                self._consecutive_detect_errors = 0
                self._last_successful_inference = time.time()
            except FuturesTimeoutError:
                self._inference_timeout_count += 1
                debug_log(f"!!! 推理超时 ({self._inference_timeout}秒)，连续超时次数: {self._inference_timeout_count}", "DETECT")
                print(f"[警告] 推理超时 ({self._inference_timeout}秒)，连续超时次数: {self._inference_timeout_count}")
                
                if self._inference_timeout_count >= self._max_consecutive_timeouts:
                    debug_log(f"!!! 连续 {self._max_consecutive_timeouts} 次推理超时，尝试重置 GPU...", "DETECT")
                    print(f"[错误] 连续 {self._max_consecutive_timeouts} 次推理超时，尝试重置 GPU...")
                    self._shutdown_inference_executor()
                    self._emergency_gpu_reset()
                    self._inference_timeout_count = 0
                
                return []
            finally:
                del future
            t_pool_end = time.time()
            pool_time = (t_pool_end - t_pool_start) * 1000
            if pool_time > 300:
                debug_log(f"推理总耗时: {pool_time:.1f}ms", "DETECT")
            
            h, w = frame.shape[:2]
            
            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                
                # 获取启用的步骤标签（用于过滤禁用的步骤）
                enabled_labels = set()
                if self.project_config:
                    for step in self.project_config.get('steps_config', []):
                        if step.get('enabled', True):
                            step_label = step.get('label', '')
                            if step_label:
                                enabled_labels.add(step_label)
                
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
                    confidence = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())
                    
                    # 获取类别名
                    if hasattr(self.model, 'names') and class_id in self.model.names:
                        class_name = self.model.names[class_id]
                    else:
                        class_name = f"class_{class_id}"
                    
                    # 跳过禁用的步骤（从推理层面就忽略，不参与任何逻辑）
                    if enabled_labels and class_name not in enabled_labels:
                        continue
                    
                    # 应用步骤特定的置信度阈值（低于阈值的检测不显示也不参与任何逻辑）
                    if self.step_conf_thresholds:
                        step_threshold = self.step_conf_thresholds.get(class_name)
                        if step_threshold is not None and confidence < step_threshold:
                            continue
                    
                    # 记录检测结果（归一化坐标）
                    det = {
                        'x': float(x1 / w),
                        'y': float(y1 / h),
                        'w': float((x2 - x1) / w),
                        'h': float((y2 - y1) / h),
                        'confidence': confidence,
                        'class_id': class_id,
                        'label': class_name
                    }
                    if class_name in self.step_display_names:
                        det['display_name'] = self.step_display_names[class_name]
                    if class_name in self.step_backup_map:
                        det['hidden'] = True
                        det['backup_for'] = self.step_backup_map[class_name]
                    detections.append(det)
        except Exception as e:
            self._consecutive_detect_errors = getattr(self, '_consecutive_detect_errors', 0) + 1
            if self._consecutive_detect_errors <= 3:
                print(f"检测错误: {e}")
                import traceback
                traceback.print_exc()
            elif self._consecutive_detect_errors == 4:
                print(f"[警告] 检测持续报错，后续相同错误将被抑制 (已连续 {self._consecutive_detect_errors} 次)")
            if self._consecutive_detect_errors > 2:
                time.sleep(0.5)
        
        return self._apply_rod_filters(detections)
    
    def _detect_and_track(self, frame: np.ndarray) -> list:
        """Execute model.track() — works for both detect and segment models.
        Returns detections with track_id.  Segment models also get 'mask' (polygon)."""
        detections = []
        try:
            device = self.current_device_info.get('device', 'cpu') if self.current_device_info else 'cpu'
            from concurrent.futures import TimeoutError as FuturesTimeoutError
            
            _tracker_cfg = self._custom_tracker_yaml or "bytetrack.yaml"
            _half = self.use_half and device.startswith('cuda') and self._is_native_pytorch
            _frame = frame if frame.flags['C_CONTIGUOUS'] else np.ascontiguousarray(frame)
            def run_tracking():
                return list(self.model.track(
                    _frame, conf=self.conf_threshold, iou=self.iou_threshold,
                    imgsz=self._model_imgsz, verbose=False, device=device,
                    stream=True, persist=True, tracker=_tracker_cfg,
                    half=_half
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
            is_seg = (getattr(self, 'model_task', 'detect') == 'segment')
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
                    class_name = self.model.names[class_id] if hasattr(self.model, 'names') and class_id in self.model.names else f"class_{class_id}"
                    
                    if enabled_labels and class_name not in enabled_labels:
                        continue
                    if self.step_conf_thresholds:
                        thr = self.step_conf_thresholds.get(class_name)
                        if thr is not None and confidence < thr:
                            continue
                    
                    det = {
                        'x': float(x1 / w), 'y': float(y1 / h),
                        'w': float((x2 - x1) / w), 'h': float((y2 - y1) / h),
                        'confidence': confidence, 'class_id': class_id,
                        'label': class_name, 'track_id': track_id
                    }
                    if has_masks:
                        try:
                            mask_xy = result.masks.xyn[i]
                            det['mask'] = mask_xy.tolist()
                        except Exception:
                            pass
                    if class_name in self.step_display_names:
                        det['display_name'] = self.step_display_names[class_name]
                    detections.append(det)
        except Exception as e:
            self._consecutive_detect_errors = getattr(self, '_consecutive_detect_errors', 0) + 1
            if self._consecutive_detect_errors <= 3:
                print(f"tracking error: {e}")
                import traceback; traceback.print_exc()
            elif self._consecutive_detect_errors == 4:
                print(f"[警告] 跟踪持续报错，后续相同错误将被抑制 (已连续 {self._consecutive_detect_errors} 次)")
            if self._consecutive_detect_errors > 2:
                time.sleep(0.5)
        return self._apply_rod_filters(detections)
    
    def _detect_segment(self, frame: np.ndarray) -> list:
        """Segmentation predict (no tracking) — for seg models in non-tracking logic modes."""
        detections = []
        try:
            device = self.current_device_info.get('device', 'cpu') if self.current_device_info else 'cpu'
            from concurrent.futures import TimeoutError as FuturesTimeoutError
            _half = self.use_half and device.startswith('cuda') and self._is_native_pytorch
            _frame = frame if frame.flags['C_CONTIGUOUS'] else np.ascontiguousarray(frame)
            
            def run_inference():
                return list(self.model.predict(
                    _frame, conf=self.conf_threshold, iou=self.iou_threshold,
                    imgsz=self._model_imgsz, verbose=False, device=device, stream=True,
                    half=_half
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
                    class_name = self.model.names[class_id] if hasattr(self.model, 'names') and class_id in self.model.names else f"class_{class_id}"
                    
                    if enabled_labels and class_name not in enabled_labels:
                        continue
                    if self.step_conf_thresholds:
                        thr = self.step_conf_thresholds.get(class_name)
                        if thr is not None and confidence < thr:
                            continue
                    
                    det = {
                        'x': float(x1 / w), 'y': float(y1 / h),
                        'w': float((x2 - x1) / w), 'h': float((y2 - y1) / h),
                        'confidence': confidence, 'class_id': class_id, 'label': class_name
                    }
                    if has_masks:
                        try:
                            det['mask'] = result.masks.xyn[i].tolist()
                        except Exception:
                            pass
                    if class_name in self.step_display_names:
                        det['display_name'] = self.step_display_names[class_name]
                    if class_name in self.step_backup_map:
                        det['hidden'] = True
                        det['backup_for'] = self.step_backup_map[class_name]
                    detections.append(det)
        except Exception as e:
            print(f"segment error: {e}")
            import traceback; traceback.print_exc()
        return self._apply_rod_filters(detections)
    
