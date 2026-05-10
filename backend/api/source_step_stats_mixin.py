"""步骤统计 (_update_step_stats, v2.7.16 P6 阶段一从 source.py 整体搬出)。

把 330 行的 _update_step_stats 直接整体搬到独立文件, 不在 mixin 内做 P5b 拆分
(若后续需要细分, 可以在本文件继续按职责拆 _step_collect_*/_step_check_*/...)。

宿主必须提供的属性: self.step_conf_thresholds / step_consecutive_frames /
                  step_frame_confirmed / step_min_frames / step_gap_tolerance /
                  step_static_triggered / step_static_config / step_detection_type /
                  step_time_config / step_display_names / step_screenshots /
                  step_last_seen / step_start_time / step_counts / step_durations /
                  step_intervals / step_backup_map / backup_steps_seen_in_cycle /
                  current_cycle_steps / cycle_start_time / cycle_max_duration /
                  idle_timeout_seconds / project_config 等
宿主必须提供的方法: self.start_step_recording / stop_step_recording / record_step /
                  self._process_simultaneous_groups / _process_single_step /
                  _check_static_step_conditions / _force_timeout_ng / _check_events /
                  _settle_custom_cycle / _settle_sequential_cycle / _settle_detection_cycle /
                  _trigger_event / _get_first_sequence_step_label
"""
from __future__ import annotations

import time
import base64

import cv2
import numpy as np

from backend.api.source_roi import is_normalized_bbox_center_in_polygon


class StepStatsMixin:
    def _update_step_stats(self, detections: list, original_frame: np.ndarray):
        """更新步骤统计和截图
        
        注意：置信度阈值过滤已在 _detect_only 方法中完成，
        此处收到的 detections 都是通过阈值的有效检测
        """
        import base64
        current_time = time.time()
        detected_labels = set()  # 用于统计的标签（通过阈值的）
        frame_detected_labels = set()  # 本帧通过置信度阈值的标签（用于帧数过滤）
        
        # Screenshot throttle: at most once per second to save CPU
        if not hasattr(self, '_last_screenshot_time'):
            self._last_screenshot_time = 0
        should_update_screenshot = (current_time - self._last_screenshot_time) >= 1.0
        just_confirmed_labels = set()
        
        for det in detections:
            label = det.get('label', '')
            confidence = det.get('confidence', 0)
            if not label:
                continue
            
            if self.step_conf_thresholds:
                threshold = self.step_conf_thresholds.get(label)
                if threshold is not None and confidence < threshold:
                    continue

            # 逐步骤 ROI: 仅框中心在配置多边形内才计入该步骤 (顺序/检测/自定义/共用路径)
            _poly_map = getattr(self, 'step_roi_polygons', None) or {}
            _poly = _poly_map.get(label)
            if _poly and len(_poly) >= 3:
                if not is_normalized_bbox_center_in_polygon(det, _poly):
                    continue

            frame_detected_labels.add(label)
        
        if not hasattr(self, '_step_raw_start'):
            self._step_raw_start = {}
        
        for label in frame_detected_labels:
            self._step_gap_count[label] = 0
            prev_count = self.step_consecutive_frames.get(label, 0)
            if prev_count == 0:
                self._step_raw_start[label] = current_time
                self.start_step_recording(label)
            self.step_consecutive_frames[label] = prev_count + 1
            
            min_frames = self.step_min_frames.get(label, 1)
            if self.step_consecutive_frames[label] >= min_frames:
                detected_labels.add(label)
                if not self.step_frame_confirmed.get(label):
                    self.step_frame_confirmed[label] = True
                    just_confirmed_labels.add(label)
                    first_seq_lbl = self._get_first_sequence_step_label() if self.current_cycle_steps else None
                    if label == first_seq_lbl and label in self.current_cycle_steps:
                        self._first_step_reconfirmed = True
        
        # Backup step processing: mark seen, then remove from detected_labels
        if self.step_backup_map:
            backup_in_detected = detected_labels & set(self.step_backup_map.keys())
            for b_label in backup_in_detected:
                self.backup_steps_seen_in_cycle.add(b_label)
            detected_labels -= backup_in_detected
        
        # 对于本帧没有检测到的标签，根据 gap_tolerance 决定是否重置连续帧计数
        all_configured_labels = set(self.step_conf_thresholds.keys()) if self.step_conf_thresholds else set()
        first_seq_label = self._get_first_sequence_step_label() if self.current_cycle_steps else None
        for label in all_configured_labels:
            if label not in frame_detected_labels:
                gap_tolerance = self.step_gap_tolerance.get(label, 0)
                current_gap = self._step_gap_count.get(label, 0) + 1
                self._step_gap_count[label] = current_gap

                if current_gap > gap_tolerance:
                    was_tracking = self.step_consecutive_frames.get(label, 0) > 0
                    was_confirmed = self.step_frame_confirmed.get(label, False)
                    self.step_consecutive_frames[label] = 0
                    self.step_frame_confirmed[label] = False
                    self._step_gap_count[label] = 0
                    if was_tracking and not was_confirmed:
                        self.stop_step_recording(label)
                    if was_confirmed and label == first_seq_label and label in self.current_cycle_steps:
                        self._first_step_disappeared_at = time.time()
                # 重置静态步骤的触发状态（标签消失后可以再次触发）
                if label in self.step_static_triggered:
                    self.step_static_triggered[label] = False
        
        # 检查静态步骤是否达到触发条件
        for label in frame_detected_labels:
            if self.step_detection_type.get(label) == 'static':
                static_config = self.step_static_config.get(label, {})
                trigger_frames = static_config.get('trigger_frames', 30)
                trigger_event = static_config.get('trigger_event')
                
                # 检查是否达到静态触发帧数且未触发过
                if (self.step_consecutive_frames.get(label, 0) >= trigger_frames 
                    and not self.step_static_triggered.get(label, False)):
                    
                    self.step_static_triggered[label] = True
                    print(f"静态步骤 [{label}] 达到触发条件（{trigger_frames}帧）")
                    
                    # 触发配置的事件（如果有）
                    if trigger_event:
                        self._trigger_event(trigger_event, f'静态步骤触发: {label}')
                    
                    # 检查自定义条件中是否有匹配这个静态步骤的条件
                    self._check_static_step_conditions(label)
        
        # 获取启用的步骤标签
        enabled_labels = set()
        if self.project_config:
            steps_config = self.project_config.get('steps_config', [])
            for step in steps_config:
                if step.get('enabled', True):
                    step_label = step.get('label', '')
                    if step_label:
                        enabled_labels.add(step_label)
        
        # 构建 label -> detection_info 映射，供截图使用
        det_by_label = {}
        for det in detections:
            label = det.get('label', '')
            if label:
                det_by_label[label] = det
        
        # 存储当前帧检测到的标签（供周期结算时清理 step_last_seen）
        self._current_detected_labels = detected_labels
        
        # 调用缓冲排序层
        pending_labels, ready_ordered = self._process_simultaneous_groups(
            frame_detected_labels, detected_labels, current_time)
        ready_ordered_set = set(ready_ordered)
        
        # 缓冲中的标签：不更新 step_last_seen（保留原始值以便释放时正确判断 is_new），
        # 但仍更新截图。消失检测通过 _currently_pending_labels 跳过。
        self._currently_pending_labels = pending_labels
        for label in pending_labels:
            if (should_update_screenshot or label in just_confirmed_labels) and label in det_by_label:
                det_info = det_by_label[label]
                x, y, w, h = det_info['x'], det_info['y'], det_info['w'], det_info['h']
                img_h, img_w = original_frame.shape[:2]
                pad = 20
                cx1 = max(0, int(x * img_w) - pad)
                cy1 = max(0, int(y * img_h) - pad)
                cx2 = min(img_w, int((x + w) * img_w) + pad)
                cy2 = min(img_h, int((y + h) * img_h) + pad)
                if cx2 > cx1 and cy2 > cy1:
                    crop = original_frame[cy1:cy2, cx1:cx2]
                    _, buffer = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    self.step_screenshots[label] = base64.b64encode(buffer).decode('utf-8')
        
        _logic_mode_for_sort = self.project_config.get('logic_mode') if self.project_config else 'detection'
        _pipeline_for_sort = self.project_config.get('pipeline_config', {}) if self.project_config else {}
        _is_seq_like = (_logic_mode_for_sort == 'sequential' or
                        (_logic_mode_for_sort == 'custom' and _pipeline_for_sort.get('custom_based_on') == 'sequential'))

        for label in list(detected_labels):
            if label not in self.step_start_time:
                continue
            time_cfg = self.step_time_config.get(label, {})
            _max_dur = time_cfg.get('max_duration')
            if _max_dur and (current_time - self.step_start_time[label]) > _max_dur:
                if time_cfg.get('timeout_ng') and self.current_cycle_steps:
                    elapsed = current_time - self.step_start_time[label]
                    display = self.step_display_names.get(label, label)
                    print(f"[步骤超时NG] 步骤 [{display}] 持续 {elapsed:.1f}s > {_max_dur}s，触发NG")
                    self._force_timeout_ng(f'步骤 [{display}] 超时 ({elapsed:.1f}s > {_max_dur}s)')
                    return
                print(f"[超时重置] 步骤 [{label}] 持续 {current_time - self.step_start_time[label]:.1f}s > max_duration {_max_dur}s，模拟再次出现")
                if label in self.step_last_seen:
                    del self.step_last_seen[label]
                del self.step_start_time[label]
                self.step_consecutive_frames[label] = 0
                self.step_frame_confirmed[label] = False
                self._step_gap_count[label] = 0
                if label in getattr(self, '_step_raw_start', {}):
                    del self._step_raw_start[label]

        for label in ready_ordered:
            # min_duration gate: step must be continuously present for min_duration
            # before it can enter any cycle logic. Detection box still shows.
            _min_dur_cfg = self.step_time_config.get(label, {}).get('min_duration')
            if _min_dur_cfg and _min_dur_cfg > 0:
                _raw_st = self._step_raw_start.get(label)
                if _raw_st and (current_time - _raw_st) < _min_dur_cfg:
                    continue
            self._process_single_step(label, current_time, enabled_labels, _is_seq_like,
                                      should_update_screenshot, original_frame,
                                      det_by_label.get(label), just_confirmed_labels)
        
        for label in detected_labels:
            if label in pending_labels:
                continue
            if label in ready_ordered_set:
                continue
            _min_dur_cfg2 = self.step_time_config.get(label, {}).get('min_duration')
            if _min_dur_cfg2 and _min_dur_cfg2 > 0:
                _raw_st2 = self._step_raw_start.get(label)
                if _raw_st2 and (current_time - _raw_st2) < _min_dur_cfg2:
                    continue
            self._process_single_step(label, current_time, enabled_labels, _is_seq_like,
                                      should_update_screenshot, original_frame,
                                      det_by_label.get(label), just_confirmed_labels)
        
        if should_update_screenshot:
            self._last_screenshot_time = current_time
        
        # 检查消失的步骤（完成计数）
        # 使用延迟判定机制：先记录所有消失的步骤，再统一进行事件判定
        # 这样可以确保所有步骤都被正确记录到当前周期，避免因判定触发 end_cycle 导致后续步骤记录失败
        pending_event_checks = []  # 收集需要检查事件的步骤
        
        _pending = getattr(self, '_currently_pending_labels', set())
        for label, last_time in list(self.step_last_seen.items()):
            if label in _pending:
                continue
            if label not in detected_labels:
                # 获取步骤时间配置
                time_config = self.step_time_config.get(label, {})
                disappear_delay = time_config.get('disappear_delay') or 0
                
                if current_time - last_time > disappear_delay:
                    # 计算持续时间
                    start_time = self.step_start_time.get(label, last_time)
                    duration = last_time - start_time
                    
                    # 检查持续时间是否在有效范围内
                    min_duration = time_config.get('min_duration')
                    max_duration = time_config.get('max_duration')
                    
                    is_valid = True
                    if min_duration is not None and duration < min_duration:
                        is_valid = False
                        print(f"步骤 {label} 持续时间 {duration:.2f}s 低于最短时间 {min_duration}s，忽略")
                    if max_duration is not None and duration > max_duration:
                        is_valid = False
                        print(f"步骤 {label} 持续时间 {duration:.2f}s 超过最大时间 {max_duration}s，忽略")
                    
                    # 如果持续时间无效，从周期中移除该步骤（影响周期判定）
                    # But never remove accept_once steps that are already in the cycle:
                    # they were validated during their first appearance.
                    if not is_valid:
                        if self.step_accept_once.get(label) and label in self.current_cycle_steps:
                            print(f"  → 步骤 {label} (accept_once) 本次检测无效但保留在周期中")
                        else:
                            self.current_cycle_steps = [s for s in self.current_cycle_steps if s != label]
                            print(f"  → 已从当前周期中移除步骤 {label}")
                    
                    # 清理状态
                    del self.step_last_seen[label]
                    if label in self.step_start_time:
                        del self.step_start_time[label]
                    
                    # 只有有效的检测才计数
                    if is_valid:
                        if label not in self.step_counts:
                            self.step_counts[label] = 0
                        self.step_counts[label] += 1
                        
                        rounded_dur = round(duration, 2)
                        self.step_durations[label] = rounded_dur
                        self.step_durations_history.setdefault(label, []).append(rounded_dur)
                        
                        # 计算与上一步骤的间隔时间
                        if self.last_step_completed_time is not None:
                            interval = start_time - self.last_step_completed_time
                            self.step_intervals[label] = round(interval, 2)
                        else:
                            self.step_intervals[label] = 0
                        
                        # 更新上一个步骤完成时间为当前步骤的结束时间
                        self.last_step_completed_time = last_time
                        
                        print(f"步骤完成: {label}, 耗时 {duration:.2f}s, 间隔 {self.step_intervals.get(label, 0):.2f}s, 累计: {self.step_counts[label]}")
                        
                        # ========== 记录步骤到数据库 ==========
                        # 获取步骤显示名称
                        step_name = self.step_display_names.get(label, label)
                        
                        # 停止步骤视频录制并获取视频信息
                        step_video_info = self.stop_step_recording(label)
                        
                        self.record_step(
                            step_label=label,
                            step_name=step_name,
                            start_time=start_time,
                            end_time=last_time,
                            duration=duration,
                            interval=self.step_intervals.get(label),
                            confidence=None,
                            is_valid=True,
                            video_info=step_video_info
                        )

                        # v3.5.2: 周期性强制动作 — 开机首检在第一个步骤完成时清算
                        # (做了 trigger_step 静默 reset, 否则立即提醒). 一次性判定.
                        try:
                            if hasattr(self, '_check_periodic_actions_on_first_step'):
                                self._check_periodic_actions_on_first_step(label)
                        except Exception as _e:
                            print(f"[PeriodicActions] on_first_step 触发失败: {_e}")

                        # 记录该步骤消失时的结束时间，供顺序模式结算时补写未“消失”的步骤记录
                        if not hasattr(self, '_last_disappeared_step_times'):
                            self._last_disappeared_step_times = {}
                        self._last_disappeared_step_times[label] = last_time
                        
                        # 收集需要检查事件的步骤（延迟判定）
                        pending_event_checks.append(label)
        
        # ========== 延迟判定阶段 ==========
        # 所有步骤记录完成后，再统一进行事件判定
        # 这样即使判定触发 end_cycle，也不会影响其他步骤的记录
        for completed_label in pending_event_checks:
            self._check_events(completed_label)
        
        # ========== 周期总时长超时NG ==========
        if (self.cycle_max_duration > 0
                and self.cycle_start_time is not None
                and self.current_cycle_steps):
            cycle_elapsed = current_time - self.cycle_start_time
            if cycle_elapsed > self.cycle_max_duration:
                print(f"[周期超时NG] 周期总时长 {cycle_elapsed:.1f}s > {self.cycle_max_duration}s，强制NG")
                self._force_timeout_ng(f'周期总时长超时 ({cycle_elapsed:.1f}s > {self.cycle_max_duration}s)')
                return

        # ========== 空闲超时结算 ==========
        if (self.idle_timeout_seconds > 0
                and self.current_cycle_steps
                and self._last_step_added_time is not None):
            idle_elapsed = current_time - self._last_step_added_time
            if idle_elapsed > self.idle_timeout_seconds:
                print(f"[空闲超时] {idle_elapsed:.1f}s > {self.idle_timeout_seconds}s，强制结算当前周期 (步骤={self.current_cycle_steps})")
                _lm = self.project_config.get('logic_mode') if self.project_config else 'detection'
                _pc = self.project_config.get('pipeline_config', {}) if self.project_config else {}
                _cbo = _pc.get('custom_based_on')
                if _lm == 'custom' and _cbo == 'sequential':
                    self._settle_custom_cycle()
                elif _lm == 'sequential':
                    self._settle_sequential_cycle()
                elif _lm == 'detection':
                    self._settle_detection_cycle()
