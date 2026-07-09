"""步骤统计 (_update_step_stats, v2.7.16 P6 阶段一从 source.py 整体搬出)。

把 330 行的 _update_step_stats 直接整体搬到独立文件, 不在 mixin 内做 P5b 拆分
(若后续需要细分, 可以在本文件继续按职责拆 _step_collect_*/_step_check_*/...)。

宿主必须提供的属性: self.step_conf_thresholds / step_consecutive_frames /
                  step_frame_confirmed / step_min_frames /
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

v3.8.x 参数语义重整 (本次):
  - 删除「丢帧容忍 gap_tolerance」「去重间隔 max_interval」两个旧参数, 统一并入
    「消失等待时间 disappear_delay」.
  - 未确认阶段 (frame_confirmed=False) 本帧丢失 → 立即清零累计帧, 防止噪声闪烁
    被累积成假性确认.
  - 已确认阶段 (frame_confirmed=True) 本帧丢失 → 不清零, 由 step_last_seen +
    disappear_delay 路径正式判定消失.
  - 规则 B (消失等待被打断): 已确认 label 处于等待消失期间, 若本帧出现其他对
    周期有意义的步骤, 立即按"已消失"处理 — 等待时间只为本步骤的偶发漏帧而设,
    出现别的步骤说明操作已经推进, 不必继续干等.
"""
from __future__ import annotations

import time
import base64

import cv2
import numpy as np

from backend.api.source_roi import is_normalized_bbox_center_in_polygon
from backend.core import debug_center


class StepStatsMixin:
    # ──── 调试: 步骤被拒原因 (每 label 2s 节流, 答"为什么这一步一直没记上") ────
    def _dbg_step_rejected(self, label: str, reason: str):
        if not debug_center.is_on("backend.settlement"):
            return
        now = time.time()
        last_map = getattr(self, '_dbg_step_reject_ts', None)
        if last_map is None:
            last_map = {}
            self._dbg_step_reject_ts = last_map
        # 节流键带上原因前缀: 同一步骤的"时长门"与"离场清理"是两条独立线索, 互不遮蔽
        key = f"{label}|{reason[:6]}"
        if now - last_map.get(key, 0.0) < 2.0:
            return
        last_map[key] = now
        debug_center.dbg("backend.settlement", "步骤检出被拒",
                         f"channel={self.channel_id} 步骤[{label}] {reason} → 该步骤不计入周期")
    def _update_step_stats(self, detections: list, original_frame: np.ndarray):
        """更新步骤统计和截图
        
        注意：置信度阈值过滤已在 _detect_only 方法中完成，
        此处收到的 detections 都是通过阈值的有效检测
        """
        # ==================== v3.9.x 人工确认阻塞门 (优先级最高) ====================
        # 触发了 require_ack=True 的事件后, 状态机完全停摆, 等待 /ack-event 主动解除.
        # 在阻塞门内同步处理"超时自动确认": 客户离岗等场景下避免无限阻塞.
        # 注意: 即便 logic_mode=per_item, 也走这个守门 (在 per_item 分流之前判定).
        if getattr(self, '_pending_ack', False):
            timeout = getattr(self, '_pending_ack_timeout_sec', 0) or 0
            started = getattr(self, '_pending_ack_started_at', 0) or 0
            if timeout > 0 and started > 0 and (time.time() - started) >= timeout:
                ev_name = getattr(self, '_pending_ack_event_name', '?') or '?'
                # v3.34: 事件配了「确认后保留周期」→ 超时自动确认同样只解除定格
                if self._pending_ack_keeps_cycle():
                    print(f"[ack] 阻塞超时自动确认(保留周期): event={ev_name} 已阻塞 "
                          f"{time.time() - started:.1f}s >= {timeout}s (channel_id={self.channel_id})")
                    self._ack_release_keep_cycle()
                else:
                    print(f"[ack] 阻塞超时自动确认: event={ev_name} 已阻塞 {time.time() - started:.1f}s "
                          f">= {timeout}s, 自动解除并清运行时 (channel_id={self.channel_id})")
                    self._clear_step_runtime_state()
                # 阻塞态字段已重置, 下面正常往下走
            else:
                # 仍处于阻塞态: 直接 return, 不推进状态机
                return

        # ==================== per_item 模式分流 ====================
        # logic_mode='per_item' 走独立路径, 完全绕开 sequential/detection/custom
        # 的 cycle/step 状态机. 见 source_per_item_mixin.PerItemMixin.
        _pc = self.project_config or {}
        if _pc.get('logic_mode') == 'per_item' and hasattr(self, '_update_step_stats_per_item'):
            return self._update_step_stats_per_item(detections, original_frame)

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

            # v3.32 守门: 项目已配步骤时, 非步骤标签不进入步骤统计/录像。
            # 历史上 runner 层按 enabled_labels 过滤保证了这里只见步骤标签;
            # 同标签区域拆分引入后, 锚点标签 / unmatched=keep 保留的原始标签
            # 会流到这里, 必须在状态机入口还原这条不变量。
            if self.step_conf_thresholds and label not in self.step_conf_thresholds:
                continue

            if self.step_conf_thresholds:
                threshold = self.step_conf_thresholds.get(label)
                if threshold is not None and confidence < threshold:
                    self._dbg_step_rejected(label, f"置信度{confidence:.2f} < 步骤阈值{threshold}")
                    continue

            # 逐步骤 ROI: 仅框中心在配置多边形内才计入该步骤 (顺序/检测/自定义/共用路径)
            _poly_map = getattr(self, 'step_roi_polygons', None) or {}
            _poly = _poly_map.get(label)
            if _poly and len(_poly) >= 3:
                if not is_normalized_bbox_center_in_polygon(det, _poly):
                    self._dbg_step_rejected(label, "检测框中心在该步骤 ROI 区域外")
                    continue

            frame_detected_labels.add(label)
        
        if not hasattr(self, '_step_raw_start'):
            self._step_raw_start = {}
        
        if not hasattr(self, '_step_raw_last_seen'):
            self._step_raw_last_seen = {}

        if not hasattr(self, '_step_raw_start_frame_pos'):
            self._step_raw_start_frame_pos = {}

        for label in frame_detected_labels:
            prev_count = self.step_consecutive_frames.get(label, 0)
            if prev_count == 0:
                self._step_raw_start[label] = current_time
                # 视频源耗时走帧号差: 帧位起点必须与 wall 起点同刻记录, 否则
                # min_duration 时长门放行后才盖帧位起点章 → 耗时被扣掉整个
                # 门槛时长, 结算时再对比 min_duration = 双重惩罚(实测 1.0s 的
                # 真实步骤被量成 0.38s 直接判无效)。
                self._step_raw_start_frame_pos[label] = self._video_frame_pos()
                self.start_step_recording(label)
            self._step_raw_last_seen[label] = current_time
            self.step_consecutive_frames[label] = prev_count + 1
            
            min_frames = self.step_min_frames.get(label, 1)
            if self.step_consecutive_frames[label] >= min_frames:
                detected_labels.add(label)
                if not self.step_frame_confirmed.get(label):
                    self.step_frame_confirmed[label] = True
                    just_confirmed_labels.add(label)
        
        # Backup step processing: mark seen, then remove from detected_labels
        if self.step_backup_map:
            backup_in_detected = detected_labels & set(self.step_backup_map.keys())
            for b_label in backup_in_detected:
                self.backup_steps_seen_in_cycle.add(b_label)
            detected_labels -= backup_in_detected
        
        # 对于本帧没有检测到的标签:
        # - 未确认阶段 (frame_confirmed=False): 立即清零累计帧, 防止噪声闪烁假性累积成确认
        # - 已确认阶段 (frame_confirmed=True):  保持帧确认状态, 由 step_last_seen +
        #   disappear_delay 路径正式判定消失 (含规则 B "消失等待被打断")
        all_configured_labels = set(self.step_conf_thresholds.keys()) if self.step_conf_thresholds else set()
        for label in all_configured_labels:
            if label not in frame_detected_labels:
                was_confirmed = self.step_frame_confirmed.get(label, False)
                if not was_confirmed:
                    was_tracking = self.step_consecutive_frames.get(label, 0) > 0
                    self.step_consecutive_frames[label] = 0
                    if was_tracking:
                        self._dbg_step_rejected(
                            label, f"未确认阶段闪断清零 (已累计"
                                   f"{self.step_consecutive_frames.get(label, 0)}帧)")
                        self.stop_step_recording(label)
                elif label not in self.step_last_seen:
                    # v3.34: 已确认但从未进入周期逻辑就消失的"短暂滑过"
                    # (step_last_seen 只在正式处理时写入; min_duration 时长门/
                    # 严格顺序守门都会拦在写入之前)。不清的话确认态 + 原始起始
                    # 时刻永久残留, 该标签下一次真实出现会带着陈旧起点直接越过
                    # 时长门(真实模型 UAT 实测: 0.24s 的滑过误命中借尸还魂,
                    # 被当成"已持续 135s"触发第一步重现结算)。
                    # 清理必须等它离场超过本步骤的消失等待时间再做 —— 立即清会把
                    # 时长门内的检测闪烁也一并清掉, 连续在场时长永远攒不满。
                    _gone_for = current_time - self._step_raw_last_seen.get(label, current_time)
                    _dd = (self.step_time_config.get(label, {}).get('disappear_delay') or 0)
                    if _gone_for > _dd:
                        self._dbg_step_rejected(
                            label, f"时长门内离场清理 gone={_gone_for:.2f}s>{_dd}s")
                        self.step_frame_confirmed[label] = False
                        self.step_consecutive_frames[label] = 0
                        self.stop_step_recording(label)
                        self._step_raw_start.pop(label, None)
                        self._step_raw_last_seen.pop(label, None)
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
        # v3.19.x: 物品行 (detect_role='item') 永不算步骤 —— 即使混合子状态机
        # 未启用 (如配置残留), 物品标签也不进入 _process_single_step / 周期序列
        enabled_labels = set()
        if self.project_config:
            steps_config = self.project_config.get('steps_config', [])
            for step in steps_config:
                if step.get('enabled', True) and step.get('detect_role') != 'item':
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

        # ==================== v3.9.x D 方案: 累计标签在画面里的可见时长 ====================
        # 用稳定检测集 (已过 min_frames + ROI + 阈值守门) 累加, 跳过瞬态噪声.
        # 步骤完成时如果项目配了 pt_calc_mode='visible', PT 用本字典累计值;
        # 否则继续用现行 (last_seen - start_time) 跨度.
        # mode='span' (默认) 时数据仍在累加但不被读取, 客户切到 visible 立刻能用.
        # _dt 上限 1.0s, 防 FPS 极低 / 暂停恢复 / GPU 卡顿等单帧大跨度污染累计值.
        _last_ts = getattr(self, '_last_frame_ts_for_visible', None)
        if _last_ts is not None and current_time > _last_ts:
            _dt = current_time - _last_ts
            if _dt > 1.0:
                _dt = 1.0
            if not hasattr(self, 'step_visible_seconds') or self.step_visible_seconds is None:
                self.step_visible_seconds = {}
            for _lbl in detected_labels:
                self.step_visible_seconds[_lbl] = self.step_visible_seconds.get(_lbl, 0.0) + _dt
        self._last_frame_ts_for_visible = current_time

        # v3.19.x: 自定义模式混合子状态机 — 物品标签分流。
        # 物品标签由子状态机独占消费 (计数/唯一ID统计), 这里剥离后不再进入
        # 跨周期组 / last_first / 同时组 / _process_single_step / 消失检测,
        # 保证物品永远不会被当成步骤参与周期序列。
        _custom_mix = getattr(self, '_custom_mix', None)
        if _custom_mix is not None:
            try:
                _custom_mix.feed(self, detections, current_time, original_frame)
            except Exception as _mix_e:
                print(f"[CustomMix] feed 失败: {_mix_e}")
            # v3.29.x: 剥离集 = 物品标签 + 容器标签 + (启用时)动作标签。
            # 容器标签由累加器独占消费, 不再当普通步骤刷"完成"/频闪 (见 strip_labels)。
            _strip = getattr(_custom_mix, 'strip_labels', None) or _custom_mix.item_labels
            detected_labels -= _strip
            frame_detected_labels -= _strip
            just_confirmed_labels -= _strip

            # 频闪诊断 (常驻低开销 + 出事自动抓现场): 记录监视标签每帧在场/置信度,
            # 某标签 2s 内在场翻转过频 → 自动转储最近现场到文件, 供事后定位真因。
            try:
                self._diag_flicker_tick(det_by_label, detections, current_time)
            except Exception:
                pass

        # v3.8.x (类二): 跨周期同时出现组路由
        # 顺序:
        #   1. 先检查是否解除被屏蔽集合 (本帧出现组外有意义步骤 → 清空屏蔽)
        #   2. 调用跨周期路由处理: 维护等待状态机, 在 4 种终止路径上结算上周期 + 启动下周期 + 屏蔽组员
        #   3. 把被消费的标签 (含屏蔽中的、等待中的) 从 detected_labels / frame_detected_labels 移除,
        #      它们不再进入下面的同时组缓冲 / _process_single_step / 消失检测.
        if hasattr(self, '_handle_blocked_labels_release'):
            self._handle_blocked_labels_release(detected_labels)
        if hasattr(self, '_process_cross_cycle_groups'):
            consumed_by_cross_cycle = self._process_cross_cycle_groups(
                frame_detected_labels, detected_labels, current_time)
            if consumed_by_cross_cycle:
                detected_labels -= consumed_by_cross_cycle
                frame_detected_labels -= consumed_by_cross_cycle

        # v3.8.x: last_first 结算模式状态机 (互斥保证: 与跨周期同时出现组不并存,
        # 且方法内部 settlement_mode != 'last_first' 时立即返回空集 → 其他模式零影响)
        if hasattr(self, '_process_last_first_mode'):
            consumed_by_last_first = self._process_last_first_mode(
                frame_detected_labels, detected_labels, current_time)
            if consumed_by_last_first:
                detected_labels -= consumed_by_last_first
                frame_detected_labels -= consumed_by_last_first

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

        # v3.15 RFC 12: 步骤进行中计时广播 (通用基础设施, 节流 1Hz, 无插件时早退).
        # 主程序只广播 elapsed_sec + 步骤身份, 阈值/分档/实时报警策略全交给插件.
        # 见本类 _broadcast_step_ticks.
        self._broadcast_step_ticks(current_time)

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
                if label in getattr(self, '_step_raw_start', {}):
                    del self._step_raw_start[label]

        for label in ready_ordered:
            # min_duration gate: step must be continuously present for min_duration
            # before it can enter any cycle logic. Detection box still shows.
            _min_dur_cfg = self.step_time_config.get(label, {}).get('min_duration')
            if _min_dur_cfg and _min_dur_cfg > 0:
                _raw_st = self._step_raw_start.get(label)
                if _raw_st and (current_time - _raw_st) < _min_dur_cfg:
                    self._dbg_step_rejected(
                        label, f"时长门内 {current_time - _raw_st:.2f}/{_min_dur_cfg}s"
                               f" cons={self.step_consecutive_frames.get(label)}"
                               f" confirmed={self.step_frame_confirmed.get(label)}")
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
                    self._dbg_step_rejected(
                        label, f"时长门内 {current_time - _raw_st2:.2f}/{_min_dur_cfg2}s"
                               f" cons={self.step_consecutive_frames.get(label)}"
                               f" confirmed={self.step_frame_confirmed.get(label)}")
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

        # 规则 B 预计算: 本帧"对周期有意义"的其他步骤集合
        # — 用于打断 disappear_delay 等待 (消失等待时间只为同一步骤的偶发漏帧而设,
        #   出现别的有意义步骤说明操作已经推进, 不必继续干等)
        # 注意: detected_labels 已经过滤了 min_frames 且不含本帧未到的标签,
        # 取交集 enabled_labels 进一步限制在"项目配置启用"的步骤范围内.
        meaningful_in_frame = detected_labels & enabled_labels

        _pending = getattr(self, '_currently_pending_labels', set())
        for label, last_time in list(self.step_last_seen.items()):
            if label in _pending:
                continue
            if label not in detected_labels:
                # 获取步骤时间配置
                time_config = self.step_time_config.get(label, {})
                disappear_delay = time_config.get('disappear_delay') or 0

                # 规则 B: 等待期间出现其他对周期有意义的步骤 → 立即按消失处理
                # (排除自身: label not in detected_labels 已保证, 但保险起见显式排除)
                other_meaningful_present = bool(meaningful_in_frame - {label})
                effective_delay = 0 if other_meaningful_present else disappear_delay

                if current_time - last_time > effective_delay:
                    # 已确认过的 label 走完消失结算后, 清零帧确认状态
                    self.step_frame_confirmed[label] = False
                    self.step_consecutive_frames[label] = 0
                    # 计算持续时间
                    # v3.9.x: 严格顺序步骤的 PT 起点夹到 max(start, 上一步完成时刻),
                    # 防止"标签提前被识别"把起点拖到上一步骤还在进行的时刻
                    # (导致 PT 跨度比客户实际操作时间大很多, 间隔出现负值).
                    raw_start = self.step_start_time.get(label, last_time)
                    start_time = self._resolve_step_pt_anchor(label, raw_start)
                    # v3.10.x B方案v2: 视频源用帧号差/fps 算耗时
                    _raw_start_fr = self.step_start_frame_pos.get(label, 0)
                    _start_fr = self._resolve_step_pt_anchor_frame_pos(label, _raw_start_fr)
                    _last_fr = self.step_last_frame_pos.get(label, 0)
                    duration = self._compute_duration_sec(
                        _start_fr, _last_fr,
                        fallback_start_wall=start_time, fallback_end_wall=last_time,
                    )
                    
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
                        # v3.7.x: 当前周期内的 SUM 累加（PT 合并档使用）
                        # 守门：只有被本周期接纳的步骤才累加 SUM，避免两类污染：
                        #   1) 上一周期残留标签的 disappear_delay 在新周期触发 →
                        #      current_cycle_steps 已是新周期，旧 label 不在 → 不写。
                        #   2) 本周期"被识别但顺序错误未接纳"的标签 → 没进
                        #      current_cycle_steps → 不写。
                        # 客户报障："步骤还是『待检测』，PT 列却有时间" 由此修复。
                        if label in self.current_cycle_steps:
                            self._accumulate_step_pt(label, rounded_dur)
                        
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

    # ==================== 频闪诊断 (常驻低开销 + 出事自动抓现场) ====================
    # 背景: SY 容器项目偶发"托盘/滑块标签频闪 + 步骤完成刷屏", 同一视频时有时无,
    # 人不可能正好盯着。这里常驻一个低开销环形缓冲, 每帧记录监视标签(容器/物品/动作)
    # 的 在场/步骤后置信度/原始置信度(步骤阈值过滤前)/track_id; 一旦某标签 2s 内在场
    # 状态翻转过频(频闪特征), 自动把最近 ~8s 现场转储到文件 + 打 WARN, 供事后一锤定音:
    #   - 原始框出现 >> 在场 → 框存在但被步骤阈值/尺寸/ROI 过滤(边界抖) → 调阈值/迟滞
    #   - 原始框 ≈ 在场 且都低 → 模型真丢检(运动模糊/遮挡) → 提模型稳定性/迟滞
    #   - 推理fps << 采集fps → 推理跟不上采集(跳帧) → 降 imgsz/换格式/降采集

    def _diag_flicker_tick(self, det_by_label, detections, current_time):
        """每帧采样监视标签到环形缓冲, 并做频闪自动转储判定 (custom_mix 项目)。"""
        mix = getattr(self, '_custom_mix', None)
        if mix is None:
            return
        # 监视标签 = 物品 + 容器 + 动作 (随 mix 实例变化重算)
        if getattr(self, '_diag_watched_token', None) is not id(mix):
            w = set(getattr(mix, 'item_labels', set()) or set())
            eng = getattr(mix, '_engine', None)
            cont = getattr(eng, '_container', None)
            if cont is not None:
                if getattr(cont, 'container_label', ''):
                    w.add(cont.container_label)
                if getattr(cont, 'action_label', ''):
                    w.add(cont.action_label)
            self._diag_watched = w
            self._diag_watched_token = id(mix)
        watched = getattr(self, '_diag_watched', None)
        if not watched:
            return
        self._diag_flicker_sample(watched, det_by_label, detections, current_time)

    def _diag_flicker_sample(self, watched, det_by_label, detections, current_time):
        """频闪诊断公共采样体 (custom_mix 与 region_events 共用)。"""
        from collections import deque
        if not hasattr(self, '_diag_ring'):
            self._diag_ring = deque(maxlen=200)     # ~8s @ 25fps
            self._diag_seq = 0
            self._diag_last_present = {}
            self._diag_flips = {}
            self._diag_last_dump = {}
        self._diag_seq += 1
        raw = getattr(self, '_diag_raw_conf', None) or {}
        lab = {}
        for L in watched:
            info = det_by_label.get(L)
            present = info is not None
            lab[L] = [
                1 if present else 0,
                round(float(info.get('confidence', 0)), 3) if info else None,
                (round(float(raw.get(L, 0)), 3) or None),
                (info.get('track_id') if info else None),
            ]
            prev = self._diag_last_present.get(L)
            if prev is not None and prev != present:
                fl = self._diag_flips.setdefault(L, deque())
                fl.append(current_time)
                while fl and current_time - fl[0] > 2.0:
                    fl.popleft()
            self._diag_last_present[L] = present
        self._diag_ring.append({
            'seq': self._diag_seq,
            't': round(current_time, 3),
            'fps_i': round(float(getattr(self, 'fps_inference', 0) or 0), 1),
            'fps_a': round(float(getattr(self, 'fps_actual', 0) or 0), 1),
            'lat': int(getattr(self, 'latency', 0) or 0),
            'n_det': len(detections) if detections else 0,
            'L': lab,
        })
        # 频闪判定: 2s 内在场翻转 >= 8 次 (>=4 亮灭循环) → 转储 (每标签 30s 节流)
        for L, fl in self._diag_flips.items():
            if len(fl) >= 8 and (current_time - self._diag_last_dump.get(L, 0)) > 30.0:
                self._diag_last_dump[L] = current_time
                try:
                    self._diag_flicker_dump(L, current_time, f"2s内在场翻转{len(fl)}次")
                except Exception as _e:
                    print(f"[FLICKER] 转储失败(已隔离): {_e}")

    def _diag_flicker_dump(self, label, current_time, reason):
        """把环形缓冲现场写文件 + 打 WARN + 进调试中心, 供事后定位频闪真因。"""
        import os
        import json
        import time as _t
        from backend.core.config import DATA_DIR
        d = os.path.join(DATA_DIR, 'diag_flicker')
        os.makedirs(d, exist_ok=True)
        ch = getattr(self, 'channel_id', 0)
        ts = _t.strftime('%Y%m%d_%H%M%S')
        fn = os.path.join(d, f"flicker_ch{ch}_{label}_{ts}.json")
        ring = list(getattr(self, '_diag_ring', []))
        fps_i = round(float(getattr(self, 'fps_inference', 0) or 0), 1)
        fps_a = round(float(getattr(self, 'fps_actual', 0) or 0), 1)
        proj = (self.project_config or {}).get('name') if getattr(self, 'project_config', None) else None
        with open(fn, 'w', encoding='utf-8') as f:
            json.dump({
                'channel_id': ch, 'label': label, 'reason': reason, 'wall_time': ts,
                'project': proj, 'fps_inference': fps_i, 'fps_actual': fps_a,
                'watched': sorted(getattr(self, '_diag_watched', []) or []),
                'legend': 'L[label]=[present(0/1), filtered_conf, raw_conf(步骤阈值过滤前), track_id]',
                'frames': ring,
            }, f, ensure_ascii=False, indent=1)
        recent = ring[-50:]
        on = sum(1 for r in recent if (r['L'].get(label) or [0])[0])
        raw_seen = sum(1 for r in recent if (r['L'].get(label) or [0, None, None])[2])
        msg = (f"[FLICKER] ch{ch} 标签[{label}] {reason}; 近{len(recent)}帧: 在场{on} "
               f"原始框出现{raw_seen} 推理{fps_i}fps 采集{fps_a}fps → 现场已存 {fn}")
        print(msg)
        try:
            from backend.core import debug_center
            debug_center.dbg('backend.detection', '频闪自动转储', msg)
        except Exception:
            pass

    # ==================== v3.15 RFC 12: 步骤进行中计时广播 ====================

    def _broadcast_step_ticks(self, current_time: float):
        """对当前正在计时的每个步骤, 按 ~1Hz 节流广播已持续时长.

        客户级 "步骤耗时分档 / 实时警告 / 实时超时报警" 需求的平台基础设施:
          - 主程序**不内嵌**任何阈值策略, 只广播 ``elapsed_sec`` + 步骤身份;
          - 阈值判定、分档颜色、实时报警动作全部交给插件 (插件经
            ``PluginHost.trigger_alarm`` 自行联动报警, 经 ``pre_cycle_end`` 改写结算);
          - 这是**只读 observe hook** (不在 ``RETURNABLE_HOOK_FIELDS`` 白名单),
            handler 返回值一律丢弃, 不影响主程序状态机.

        节流: 每个步骤 label 最多 1Hz, 避免每帧 fire 拖慢推理热路径; 无 active 插件
        时 ``fire_plugin_hook`` 自身 O(1) 早退. ctx 字段集合是契约 (改字段 = 升 SDK),
        见 tests/plugin_system/test_step_tick_hook.py.
        """
        last_map = getattr(self, '_step_tick_last', None)
        if last_map is None:
            if not self.step_start_time:
                return  # 快路径: 从未计时过且当前无步骤, 不建 dict
            last_map = {}
            self._step_tick_last = last_map

        # 清理已结束步骤的节流记录, 防止 dict 随 label 残留 (即使本帧无活动步骤也清).
        if last_map:
            for stale in [k for k in last_map if k not in self.step_start_time]:
                del last_map[stale]

        if not self.step_start_time:
            return

        try:
            from backend.plugin_system.hook_dispatch import fire_plugin_hook
        except Exception:
            return

        for label, start in list(self.step_start_time.items()):
            if current_time - last_map.get(label, 0.0) < 1.0:
                continue
            last_map[label] = current_time
            time_cfg = self.step_time_config.get(label, {})
            try:
                fire_plugin_hook("step_tick", "step_in_progress", "post", {
                    "channel_id": self.channel_id,
                    "cycle_id": getattr(self, 'current_cycle_id', None),
                    "step_label": label,
                    "step_name": self.step_display_names.get(label, label),
                    "elapsed_sec": round(current_time - start, 3),
                    "min_duration": time_cfg.get('min_duration'),
                    "max_duration": time_cfg.get('max_duration'),
                })
            except Exception as e:
                # fire_plugin_hook 内部已 swallow, 这里兜 import/属性层异常, 绝不抛回热路径
                print(f"[Plugin] step_tick hook 触发异常 (已隔离, 主流程继续): {e}")
