"""周期结算 + 步骤处理 (v2.7.16 P6 阶段一第八刀, 从 source.py 整体搬出)。

包含 8 个方法 (合计 ~890 行):
  _settle_custom_cycle           : 自定义模式周期结算 (228L, 最大单方法)
  _settle_detection_cycle        : 检测模式周期结算 (76L)
  _settle_sequential_cycle       : 内置顺序模式周期结算 (171L)
  _process_simultaneous_groups   : 处理 simultaneous 组步骤 (96L)
  _process_single_step           : 处理单一步骤的添加/超时/重置 (173L)
  _inject_backup_steps           : 把备用步骤注入到 cycle 末尾 (20L)
  _filter_cycle_by_duration      : 按 min_duration_sec 过滤 cycle 步骤 (42L)
  _check_static_step_conditions  : 静态触发条件检查 (84L)

依赖宿主 (VideoSourceManager):
  - 状态: project_config / steps_config / current_cycle_steps / backup_steps_seen_in_cycle /
          step_first_seen_time / sequential_states / cycle_start_time / boxes / 等
  - 方法: _trigger_event / _add_step_record / _broadcast_* / _rebuild_checklist / 等
"""
import time
import traceback
import cv2
import numpy as np


class SettlementMixin:
    def _settle_custom_cycle(self):
        """结算自定义模式的当前周期（在第一步重新出现且不匹配任何条件前缀时调用）"""
        if not self.project_config:
            return
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        custom_based_on = pipeline_config.get('custom_based_on')
        
        # 创建步骤ID到标签的映射，并获取启用的步骤ID集合
        id_to_label = {}
        enabled_step_ids = set()
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                if step.get('enabled', True):
                    enabled_step_ids.add(step_id)
        
        # 获取启用的步骤标签
        enabled_step_labels = [s.get('label') for s in steps_config if s.get('enabled', True)]
        
        self._supplement_step_durations()
        
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        print(f"自定义模式结算: 当前序列={self.current_cycle_steps}")
        
        if not self.current_cycle_steps:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return
        
        # 先检查自定义条件（只包含启用步骤的条件）
        custom_conditions = pipeline_config.get('custom_conditions', [])
        if custom_conditions:
            sorted_conditions = sorted(custom_conditions, key=lambda c: c.get('priority', 999))
            
            for cond in sorted_conditions:
                cond_sequence = cond.get('sequence', [])
                cond_event_id = cond.get('event_id')
                
                if not cond_sequence or not cond_event_id:
                    continue
                
                # 只包含启用的步骤
                cond_labels = [id_to_label.get(sid) for sid in cond_sequence if sid in id_to_label and sid in enabled_step_ids]
                
                if self.current_cycle_steps == cond_labels:
                    print(f"  → 条件匹配！触发事件 {cond_event_id}")
                    self._reconcile_step_records()
                    self._trigger_event(cond_event_id, f'自定义条件匹配: {cond_labels}')
                    self.current_cycle_steps = []
                    self.backup_steps_seen_in_cycle = set()
                    self.last_added_step = None
                    self.step_last_seen.clear()
                    self.step_start_time.clear()
                    self.step_consecutive_frames.clear()
                    self.step_frame_confirmed.clear()
                    self._step_gap_count.clear()
                    if hasattr(self, '_step_raw_start'):
                        self._step_raw_start.clear()
                    self.last_step_completed_time = None
                    return
        
        # 没有条件匹配，回退到基础模式判定
        if custom_based_on == 'sequential':
            # 使用自定义模式独立的顺序配置
            sequence_order = pipeline_config.get('custom_sequence_order', [])
            
            if not sequence_order:
                self.current_cycle_steps = []
                self.backup_steps_seen_in_cycle = set()
                self.last_added_step = None
                self.step_last_seen.clear()
                self.step_start_time.clear()
                self.step_consecutive_frames.clear()
                self.step_frame_confirmed.clear()
                self._step_gap_count.clear()
                if hasattr(self, '_step_raw_start'):
                    self._step_raw_start.clear()
                self.last_step_completed_time = None
                return
            
            expected_labels = []
            for item in sequence_order:
                step_id = item.get('step_id')
                if step_id in id_to_label and step_id in enabled_step_ids:
                    expected_labels.append(id_to_label[step_id])
            
            if not expected_labels:
                self.current_cycle_steps = []
                self.backup_steps_seen_in_cycle = set()
                self.last_added_step = None
                self.step_last_seen.clear()
                self.step_start_time.clear()
                self.step_consecutive_frames.clear()
                self.step_frame_confirmed.clear()
                self._step_gap_count.clear()
                if hasattr(self, '_step_raw_start'):
                    self._step_raw_start.clear()
                self.last_step_completed_time = None
                return
            
            self.current_cycle_steps = self._inject_backup_steps(
                self.current_cycle_steps, expected_labels)
            self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
            
            if not self.current_cycle_steps:
                self._discard_empty_cycle()
                self.current_cycle_steps = []
                self.backup_steps_seen_in_cycle = set()
                self.last_added_step = None
                self.step_last_seen.clear()
                self.step_start_time.clear()
                self.step_consecutive_frames.clear()
                self.step_frame_confirmed.clear()
                self._step_gap_count.clear()
                if hasattr(self, '_step_raw_start'):
                    self._step_raw_start.clear()
                self.last_step_completed_time = None
                return
            
            self._reconcile_step_records()
            
            print(f"  期望序列({len(expected_labels)}步): {expected_labels}")
            print(f"  实际序列({len(self.current_cycle_steps)}步): {self.current_cycle_steps}")
            
            from collections import Counter
            expected_set = set(expected_labels)
            expected_counter = Counter(expected_labels)
            step_counter = Counter(self.current_cycle_steps)
            unexpected = [s for s in self.current_cycle_steps if s not in expected_set]
            # v3.7.0: 期望序列里允许的多次出现不算重复 (例 A-B-C-B-D 里 B 合法 2 次)
            duplicated = [s for s, cnt in step_counter.items()
                          if cnt > expected_counter.get(s, 1)]

            if self._cycle_regression or unexpected or duplicated:
                reasons = []
                if self._cycle_regression:
                    reasons.append(f'步骤回退: {[s for s, c in step_counter.items() if c > 1]}')
                if unexpected:
                    reasons.append(f'多余步骤: {list(dict.fromkeys(unexpected))}')
                if duplicated and not self._cycle_regression:
                    reasons.append(f'重复步骤: {duplicated}')
                reason_str = ', '.join(reasons)
                print(f"  → {reason_str} → NG")
                self._trigger_event(2, reason_str)
            elif self.current_cycle_steps == expected_labels:
                print(f"  → 序列完全匹配 → OK")
                self._trigger_event(1, '顺序正确完成')
            elif len(self.current_cycle_steps) < len(expected_labels):
                missing = [l for l in expected_labels if l not in self.current_cycle_steps]
                print(f"  → 周期不完整，缺少: {missing} → NG")
                self._trigger_event(2, f'周期不完整，缺少: {missing}')
            else:
                # v3.7.x: actual 与 expected multiset 相同 (前面 unexpected/duplicated 都已 NG),
                # 直接逐位比较. 旧实现用 unique_steps + zip 截断, 在 sequence_order
                # 含重复元素时把正确序列误判 NG.
                mismatch_idx = -1
                for k in range(min(len(self.current_cycle_steps), len(expected_labels))):
                    if self.current_cycle_steps[k] != expected_labels[k]:
                        mismatch_idx = k
                        break
                if mismatch_idx >= 0:
                    print(f"  → 第{mismatch_idx+1}步顺序错误: 期望[{expected_labels[mismatch_idx]}], 实际[{self.current_cycle_steps[mismatch_idx]}] → NG")
                    self._trigger_event(2, f'第{mismatch_idx+1}步顺序错误')
                else:
                    print(f"  → 顺序错误 → NG")
                    self._trigger_event(2, '顺序错误')
        
        elif custom_based_on == 'detection':
            detection_step_ids = pipeline_config.get('custom_detection_steps', [])
            if detection_step_ids:
                detection_labels = [id_to_label.get(sid) for sid in detection_step_ids
                                    if sid in id_to_label and sid in enabled_step_ids]
            else:
                detection_labels = enabled_step_labels
            
            self.current_cycle_steps = self._inject_backup_steps(
                self.current_cycle_steps, detection_labels)
            
            self._reconcile_step_records()

            from collections import Counter
            expected_counter = Counter(detection_labels)
            step_counts = Counter(self.current_cycle_steps)
            # v3.7.0: detection_labels 同 label 多次配置也合法, 按 expected_counter 判.
            missing = []
            for lbl, exp_cnt in expected_counter.items():
                act_cnt = step_counts.get(lbl, 0)
                if act_cnt < exp_cnt:
                    missing.extend([lbl] * (exp_cnt - act_cnt))
            duplicated = [s for s, cnt in step_counts.items()
                          if cnt > expected_counter.get(s, 1)]

            ng_reasons = []
            if missing:
                ng_reasons.append(f'缺少步骤: {missing}')
            if duplicated:
                ng_reasons.append(f'重复步骤: {duplicated}')
            
            print(f"  自定义(基于检测)结算: 需要={detection_labels}, 本周期={self.current_cycle_steps}, 缺少={missing}, 重复={duplicated}")
            
            if not ng_reasons:
                print(f"  → 全部检测到，无重复 → OK")
                self._trigger_event(1, '检测完成')
            else:
                reason = '；'.join(ng_reasons)
                print(f"  → {reason} → NG")
                self._trigger_event(2, reason)
        
        # 重置周期
        self._capture_post_settle_ignore_labels()
        self._cycle_regression = False
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        
        self.last_step_completed_time = None
    
    def _settle_detection_cycle(self):
        """结算检测模式的当前周期
        
        判定逻辑（无序）：
        - 第一步和最后一步固定，中间步骤不要求顺序
        - 所有需检测步骤都出现过 → OK (事件1)
        - 缺少步骤 → NG (事件2)，报告缺少的步骤列表
        - 重复步骤（accept_once=OFF的步骤） → NG (事件2)
        """
        if not self.project_config:
            return
        
        detection_labels = self._get_detection_step_labels()
        if not detection_labels:
            return
        
        self._supplement_step_durations()
        
        self.current_cycle_steps = self._inject_backup_steps(
            self.current_cycle_steps, detection_labels)
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        if not self.current_cycle_steps:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self._last_step_added_time = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            if hasattr(self, '_step_raw_start'):
                self._step_raw_start.clear()
            self.last_step_completed_time = None
            return
        
        self._reconcile_step_records()
        
        from collections import Counter
        expected_counter = Counter(detection_labels)
        step_counts = Counter(self.current_cycle_steps)

        # v3.7.0: 若 detection_labels 里配置同一 label 多次 (不常见但合法),
        # 按 expected_counter 判 missing/duplicated, 避免合法重复被误判 NG.
        missing = []
        for lbl, exp_cnt in expected_counter.items():
            act_cnt = step_counts.get(lbl, 0)
            if act_cnt < exp_cnt:
                missing.extend([lbl] * (exp_cnt - act_cnt))
        duplicated = [s for s, cnt in step_counts.items()
                      if cnt > expected_counter.get(s, 1)]

        ng_reasons = []
        if missing:
            ng_reasons.append(f'缺少步骤: {missing}')
        if duplicated:
            ng_reasons.append(f'重复步骤: {duplicated}')
        
        print(f"检测模式结算: 需要={detection_labels}, 本周期={self.current_cycle_steps}, 缺少={missing}, 重复={duplicated}")
        
        if not ng_reasons:
            print(f"  → 全部检测到，无重复 → OK")
            self._trigger_event(1, '检测完成')
        else:
            reason = '；'.join(ng_reasons)
            print(f"  → {reason} → NG")
            self._trigger_event(2, reason)
        
        self._capture_post_settle_ignore_labels()
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self._last_step_added_time = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        if hasattr(self, '_step_raw_start'):
            self._step_raw_start.clear()
        self.last_step_completed_time = None
    
    def _settle_sequential_cycle(self):
        """结算纯顺序模式的当前周期（在新周期开始前调用）
        
        与自定义模式（基于顺序）的判定逻辑一致：
        1. 检查序列长度是否超过预期（有重复步骤）
        2. 检查是否包含所有预期步骤
        3. 检查顺序是否正确
        """
        if not self.project_config:
            return
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
        # 创建步骤ID到标签的映射，并获取启用的步骤ID集合
        id_to_label = {}
        enabled_step_ids = set()
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                if step.get('enabled', True):
                    enabled_step_ids.add(step_id)
        
        # 顺序模式的结算逻辑
        sequence_order = pipeline_config.get('sequence_order', [])
        
        if not sequence_order or not steps_config:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return
        
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label and step_id in enabled_step_ids:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return
        
        self._supplement_step_durations()
        
        self.current_cycle_steps = self._inject_backup_steps(
            self.current_cycle_steps, expected_labels)
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        if not self.current_cycle_steps:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return
        
        self._reconcile_step_records()
        
        print(f"顺序模式结算: 期望={expected_labels}, 实际={self.current_cycle_steps}, 回退={self._cycle_regression}")
        
        from collections import Counter
        expected_set = set(expected_labels)
        expected_counter = Counter(expected_labels)
        step_counter = Counter(self.current_cycle_steps)

        unexpected = [s for s in self.current_cycle_steps if s not in expected_set]
        # v3.7.0: 期望序列允许的重复 (A-B-C-B-D 里 B×2) 不算 duplicated;
        #         label 在 actual 里少于 expected 次数算 missing.
        duplicated = [s for s, cnt in step_counter.items()
                      if cnt > expected_counter.get(s, 1)]
        missing = []
        for lbl, exp_cnt in expected_counter.items():
            act_cnt = step_counter.get(lbl, 0)
            if act_cnt < exp_cnt:
                missing.extend([lbl] * (exp_cnt - act_cnt))

        if self._cycle_regression or unexpected or duplicated:
            reasons = []
            if self._cycle_regression:
                reasons.append(f'步骤回退: {[s for s, c in step_counter.items() if c > 1]}')
            if unexpected:
                reasons.append(f'多余步骤: {list(dict.fromkeys(unexpected))}')
            if duplicated and not self._cycle_regression:
                reasons.append(f'重复步骤: {duplicated}')
            reason_str = ', '.join(reasons)
            print(f"  → {reason_str} → NG")
            self._trigger_event(2, reason_str)
            self._cycle_regression = False
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return

        if missing:
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
            self._cycle_regression = False
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            
            self.last_step_completed_time = None
            return
        
        # v3.7.x: 到这里 unexpected/duplicated/missing 都已过滤,
        # actual 与 expected 是 multiset 完全相同的两个序列,
        # 直接逐位比较即可. 旧实现用 unique_steps.index() 总返首次位置,
        # 在 sequence_order 含重复元素时 (例 A-B-C-B-D 里 B×2)
        # 会把"完全正确的序列"误判为"顺序错误", 客户感知为 0% OK.
        if self.current_cycle_steps == expected_labels:
            print(f"  → 顺序正确 → OK")
            self._trigger_event(1, '顺序正确完成')
        else:
            mismatch_idx = -1
            for k in range(min(len(self.current_cycle_steps), len(expected_labels))):
                if self.current_cycle_steps[k] != expected_labels[k]:
                    mismatch_idx = k
                    break
            if mismatch_idx >= 0:
                order_error_labels = [expected_labels[mismatch_idx],
                                      self.current_cycle_steps[mismatch_idx]]
            else:
                order_error_labels = ['?', '?']
            print(f"  → 顺序错误 → NG: 第{mismatch_idx+1}步 期望[{order_error_labels[0]}] 实际[{order_error_labels[1]}]")
            self._trigger_event(2, f'顺序错误，期望[{order_error_labels[0]}]在前 实际[{order_error_labels[1]}]在前')
        
        # 重置周期
        self._capture_post_settle_ignore_labels()
        self._cycle_regression = False
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        
        self.last_step_completed_time = None
    
    def _capture_post_settle_ignore_labels(self):
        """v3.7.x (FIX-鬼周期):
        上一周期"最后一步"动作 (如"放置产品") 可能延续到本次结算之后还在被模型识别.
        旧实现 step_last_seen.clear() / step_frame_confirmed.clear() 后下一帧再次识别
        就被当作"新出现"启动 ghost cycle. cycle_steps 只含这一个 step, 客户后续动作
        (拿取配件1 ...) 因 strict_order 缺前置而"闪一下没反应", 直到下一个"放置产品"
        再来触发 settle -> NG.
        修复: 记录结算时仍处于"已确认中"的 label, 要求它们必须先彻底 disappear 一次
        才能再触发 add-to-cycle. 由 source_step_stats_mixin.py 的消失处理负责清理.
        """
        if hasattr(self, 'step_frame_confirmed'):
            self._post_settle_ignore_labels = set(
                lbl for lbl, conf in self.step_frame_confirmed.items() if conf
            )
        else:
            self._post_settle_ignore_labels = set()
    
    def _process_simultaneous_groups(self, frame_detected_labels: set, detected_labels: set, current_time: float):
        """
        同时出现组缓冲排序层。
        
        当检测到某个组的成员时，开始收集。在时间窗口内收集到的成员按用户
        配置的优先顺序排序后输出。不区分跨周期/同周期，统一处理。
        
        返回:
            pending_labels: set  -- 正在缓冲中、本帧不应处理的标签
            ready_ordered: list  -- 缓冲完成、按配置顺序输出的标签列表
        """
        pending_labels = set()
        ready_ordered = []
        
        if not self._simultaneous_groups:
            return pending_labels, ready_ordered
        
        for idx, group in enumerate(self._simultaneous_groups):
            if not group.get('enabled', True):
                continue
            
            group_labels = set(group.get('labels', []))
            if len(group_labels) < 2:
                continue
            
            time_window = group.get('time_window', 2.0)
            priority_order = group.get('priority_order') or list(group.get('labels', [])) or list(group_labels)
            
            present_members = group_labels & frame_detected_labels
            
            buf = self._sim_group_buffers.get(idx)
            if buf is None:
                buf = {
                    'collecting': False,
                    'start_time': None,
                    'collected_labels': set(),
                    'group_labels': group_labels,
                    'time_window': time_window,
                    'priority_order': priority_order,
                }
            
            if not buf['collecting']:
                if present_members:
                    # 只在当前周期已有步骤时才启动缓冲
                    if len(self.current_cycle_steps) == 0:
                        pass
                    else:
                        # 只在有"潜在新出现"的成员时才缓冲，避免连续检测被误缓冲
                        has_potential_new = False
                        for lbl in present_members:
                            if lbl not in self.step_last_seen:
                                has_potential_new = True
                                break
                            tc = self.step_time_config.get(lbl, {})
                            mi = tc.get('max_interval') or 1.0
                            if current_time - self.step_last_seen[lbl] > mi:
                                has_potential_new = True
                                break
                        
                        if not has_potential_new:
                            pass  # 都是连续检测，不缓冲
                        elif present_members >= group_labels:
                            ordered = [l for l in priority_order if l in group_labels]
                            ready_ordered.extend(ordered)
                            print(f"[同时出现组 {idx}] 全员同帧到齐，按序输出: {ordered}")
                        else:
                            buf['collecting'] = True
                            buf['start_time'] = current_time
                            buf['collected_labels'] = set(present_members)
                            pending_labels.update(present_members)
            else:
                buf['collected_labels'].update(present_members)
                elapsed = current_time - buf['start_time']
                
                if buf['collected_labels'] >= group_labels:
                    ordered = [l for l in priority_order if l in group_labels]
                    ready_ordered.extend(ordered)
                    buf['collecting'] = False
                    buf['start_time'] = None
                    buf['collected_labels'] = set()
                    print(f"[同时出现组 {idx}] 全员在 {elapsed:.2f}s 内到齐，按序输出: {ordered}")
                elif elapsed > time_window:
                    collected = buf['collected_labels']
                    ordered = [l for l in priority_order if l in collected]
                    ready_ordered.extend(ordered)
                    buf['collecting'] = False
                    buf['start_time'] = None
                    buf['collected_labels'] = set()
                    print(f"[同时出现组 {idx}] 超时 {elapsed:.2f}s，输出已收集: {ordered}")
                else:
                    pending_labels.update(buf['collected_labels'])
            
            self._sim_group_buffers[idx] = buf
        
        return pending_labels, ready_ordered
    
    def _process_single_step(self, label, current_time, enabled_labels, is_seq_like,
                             should_update_screenshot, original_frame, det_info,
                             just_confirmed_labels=None):
        """处理单个标签的步骤逻辑：新出现判定、周期结算触发、周期记录、截图更新。
        
        从 _update_step_stats 的 for 循环体中提取，供缓冲层输出和普通标签共用。
        """
        import base64
        
        if label not in enabled_labels:
            return
        
        # v3.7.x (FIX-鬼周期): 上一周期 settle 时仍处于"已确认中"的 label
        # 必须先彻底消失一次, 才能再次参与新 cycle 的启动 / 累计.
        # 否则模型对"放置产品"的连续识别会立即在 cycle_steps=[] 时启动 ghost cycle,
        # 导致后续步骤被 strict_order 拦截"闪一下没反应".
        # disappear handler (source_step_stats_mixin.py) 负责在 label 消失时把它从
        # ignore set 里移除.
        ignore = getattr(self, '_post_settle_ignore_labels', None)
        if ignore and label in ignore:
            return
        
        if self.step_strict_order.get(label):
            expected = self._get_expected_sequence_labels()
            if label in expected:
                idx = expected.index(label)
                predecessors = expected[:idx]
                cycle_set = set(self.current_cycle_steps)
                for pred in predecessors:
                    if pred in cycle_set:
                        continue
                    backup = self.step_primary_to_backup.get(pred)
                    if backup and backup in self.backup_steps_seen_in_cycle:
                        continue
                    return
        
        logic_mode = self.project_config.get('logic_mode') if self.project_config else 'detection'
        pipeline_config = self.project_config.get('pipeline_config', {}) if self.project_config else {}
        custom_based_on = pipeline_config.get('custom_based_on')
        
        # ── 截图：在任何 return 之前执行，确保 SOP 卡片始终有图 ──
        force_screenshot = just_confirmed_labels and label in just_confirmed_labels
        if (should_update_screenshot or force_screenshot) and det_info:
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
        
        # Save previous step_last_seen BEFORE any logic, needed by both accept_once
        # and is_new_appearance calculations below.
        old_last_seen = self.step_last_seen.get(label)

        # ── accept_once 拦截 ──
        # 在 first_step 结算模式下，第一步即使设了 accept_once，真正消失后重现
        # 也必须放行以触发结算；只有连续检测（未消失）才拦截。
        #
        # v3.7.x (FIX-客户工艺 cycle 内 N 次同 label):
        # 旧逻辑: `label in self.current_cycle_steps` 即拦截 -> 周期内只允许 1 次.
        # 但当 sequence_order 含重复元素 (如 "检查外观" × 2), accept_once 把模型
        # 第 2 次识别拦下, 客户感知"框冒蓝色但不变绿". 现按 sequence_order 期望次数
        # 放行: 已入 cycle 次数 < 期望次数时不拦截.
        if self.step_accept_once.get(label) and label in self.current_cycle_steps:
            expected_count = 0
            if is_seq_like:
                expected_seq = self._get_expected_sequence_labels()
                if expected_seq:
                    expected_count = expected_seq.count(label)
            current_count = self.current_cycle_steps.count(label)
            quota_reached = current_count >= max(1, expected_count)

            allow_through = False
            if not quota_reached:
                # 期望多次出现, 名额未满 -> 放行让 add-to-cycle 走 [期望重复] 分支
                allow_through = True
            elif self.settlement_mode == 'first_step' and is_seq_like and len(self.current_cycle_steps) > 1:
                first_step_label = self._get_first_sequence_step_label()
                if first_step_label and label == first_step_label and old_last_seen is not None:
                    gap = current_time - old_last_seen
                    dedup_interval = (self.step_time_config.get(label, {}).get('max_interval')) or 1.0
                    if gap > dedup_interval:
                        allow_through = True
            if not allow_through:
                self.step_last_seen[label] = current_time
                if label not in self.step_start_time:
                    raw_start = getattr(self, '_step_raw_start', {}).get(label, current_time)
                    self.step_start_time[label] = raw_start
                return
        
        # ── 第一步重现结算（仅 first_step 结算模式） ──
        _just_settled_by_first_step = False
        if self.settlement_mode == 'first_step' and is_seq_like \
                and label in self.current_cycle_steps and len(self.current_cycle_steps) > 1:
            first_step_label = self._get_first_sequence_step_label()
            if first_step_label and label == first_step_label:
                first_start = self.step_start_time.get(label) or getattr(self, '_step_raw_start', {}).get(label)
                first_min_dur = (self.step_time_config.get(label, {}).get('min_duration')) or 0
                first_duration = (current_time - first_start) if first_start else 0
                if first_duration >= first_min_dur:
                    print(f"[第一步结算] [{label}] 再次检测到 (持续{first_duration:.2f}s >= {first_min_dur}s)，结算当前周期 (步骤数={len(self.current_cycle_steps)})")
                    if logic_mode == 'custom' and custom_based_on == 'sequential':
                        self._settle_custom_cycle()
                    elif logic_mode == 'sequential':
                        self._settle_sequential_cycle()
                    old_last_seen = None
                    _just_settled_by_first_step = True
                    if hasattr(self, '_step_raw_start'):
                        if first_min_dur and first_min_dur > 0:
                            self._step_raw_start[label] = current_time - first_min_dur
                        else:
                            self._step_raw_start.pop(label, None)
        
        # ── 检测模式：第一步重现结算 ──
        if logic_mode == 'detection' and len(self.current_cycle_steps) > 1:
            first_det_label = self._get_first_detection_step_label()
            if first_det_label and label == first_det_label and label in self.current_cycle_steps:
                first_start = self.step_start_time.get(label) or getattr(self, '_step_raw_start', {}).get(label)
                first_min_dur = (self.step_time_config.get(label, {}).get('min_duration')) or 0
                first_duration = (current_time - first_start) if first_start else 0
                if first_duration >= first_min_dur:
                    print(f"[检测模式结算] [{label}] 第一步再次出现 (持续{first_duration:.2f}s >= {first_min_dur}s)，结算当前周期 (步骤={self.current_cycle_steps})")
                    self._settle_detection_cycle()
                    old_last_seen = None
                    _just_settled_by_first_step = True
                    if hasattr(self, '_step_raw_start'):
                        if first_min_dur and first_min_dur > 0:
                            self._step_raw_start[label] = current_time - first_min_dur
                        else:
                            self._step_raw_start.pop(label, None)
        
        # Always update step_last_seen so duration calculations reflect actual last detection time
        self.step_last_seen[label] = current_time
        
        time_config = self.step_time_config.get(label, {})
        max_interval = time_config.get('max_interval') or 1.0
        
        if old_last_seen is not None:
            time_since_last = current_time - old_last_seen
            if is_seq_like:
                is_new_appearance = time_since_last > max_interval
            elif self.last_added_step is not None and self.last_added_step != label:
                is_new_appearance = True
            else:
                is_new_appearance = time_since_last > max_interval
        else:
            is_new_appearance = True
        
        if is_new_appearance:
            # v3.7.5: 早于 FIX-381 拦截把保养类 trigger 记进旁路账本.
            # 顺序模式里 expected_seq 之外的步骤会被下方 return 拦掉, 永远进不了
            # cycle.step_sequence — 但周期性强制动作的 trigger_step 本来就在序列外,
            # 拦掉了就清不了零. 这里独立记一笔, 让 _check_periodic_actions 看得见.
            try:
                self._observe_periodic_trigger(label)
            except Exception as _e:
                print(f"[PeriodicActions] _observe_periodic_trigger 失败: {_e}")

            # v3.7.2 (FIX-381): 顺序 / 自定义-基于顺序 模式下,
            # 仅"勾进序列"的步骤参与周期生命周期 (开 cycle / 入 cycle 累计).
            # 不在序列里的启用步骤仍可被画检测框、更新 step_last_seen、刷新截图,
            # 客户能在 SOP 卡片上看到出现, 但不会让周期被判 NG.
            # 老行为 (E/F 入 cycle 触发"重复步骤: []") 仅在 expected 非空时屏蔽,
            # 避免序列未配置时把所有步骤都误挡掉.
            if logic_mode == 'sequential' or (
                logic_mode == 'custom' and custom_based_on == 'sequential'
            ):
                expected_seq = self._get_expected_sequence_labels()
                if expected_seq and label not in expected_seq:
                    return

            # 检测模式：只有第一步能开启新周期
            if len(self.current_cycle_steps) == 0 and logic_mode == 'detection':
                first_det_label = self._get_first_detection_step_label()
                if first_det_label and label != first_det_label:
                    return
            
            raw_start = getattr(self, '_step_raw_start', {}).get(label, current_time)
            self.step_start_time[label] = raw_start
            self.step_detection_times[label] = raw_start
            
            if len(self.current_cycle_steps) == 0:
                self.cycle_start_time = current_time
                self.start_cycle()
        
        if is_new_appearance and label in enabled_labels:
            should_join_cycle = True
            if self.step_detection_type.get(label) == 'static':
                static_config = self.step_static_config.get(label, {})
                should_join_cycle = static_config.get('join_cycle', True)
            
            if should_join_cycle:
                logic_mode = self.project_config.get('logic_mode') if self.project_config else 'detection'
                if logic_mode == 'custom' or logic_mode == 'sequential':
                    if len(self.current_cycle_steps) == 0:
                        self._first_step_had_gap = False
                        self._first_step_reconfirmed = False
                        self._first_step_disappeared_at = None
                        self._cycle_regression = False
                    if self.last_added_step == label:
                        pass
                    elif label in self.current_cycle_steps:
                        # v3.7.0 客户反馈: 期望序列里允许同一 label 多次出现
                        # (例如 A-B-C-B-D 里 B 出现 2 次)。
                        # 旧逻辑只看 "label 是否在 current_cycle_steps 里",
                        # 第二个 B 也被当成"回退"NG。
                        # 修复: 只有当 current_cycle_steps 里 label 的出现次数
                        # >= 期望序列里 label 的总数, 才视为真正的回退/重复.
                        if self._is_legitimate_next_in_sequence(label):
                            self.current_cycle_steps.append(label)
                            self.last_added_step = label
                            self._last_step_added_time = current_time
                            print(f"[期望重复] {label} 是期望序列里的合法重复 (当前序列: {self.current_cycle_steps})")
                        else:
                            self._cycle_regression = True
                            self.current_cycle_steps.append(label)
                            self.last_added_step = label
                            self._last_step_added_time = current_time
                            print(f"[步骤回退] {label} 已在周期中出现过且非期望重复，标记回退 (当前序列: {self.current_cycle_steps})")
                    else:
                        self.current_cycle_steps.append(label)
                        self.last_added_step = label
                        self._last_step_added_time = current_time
                else:
                    if not self.step_accept_once.get(label) or label not in self.current_cycle_steps:
                        self.current_cycle_steps.append(label)
                        self.last_added_step = label
                        self._last_step_added_time = current_time
    
    def _inject_backup_steps(self, this_cycle: list, expected_labels: list) -> list:
        """Inject primary step labels into this_cycle when their backup was seen but
        the primary itself is missing. Returns a new list with injections applied."""
        if not self.step_backup_map or not self.backup_steps_seen_in_cycle:
            return this_cycle
        
        for backup_label, primary_label in self.step_backup_map.items():
            if (backup_label in self.backup_steps_seen_in_cycle
                    and primary_label not in this_cycle
                    and primary_label in expected_labels):
                expected_idx = expected_labels.index(primary_label)
                insert_pos = 0
                for i, lbl in enumerate(this_cycle):
                    if lbl in expected_labels and expected_labels.index(lbl) < expected_idx:
                        insert_pos = i + 1
                this_cycle.insert(insert_pos, primary_label)
                print(f"替补注入: {backup_label} -> {primary_label} at position {insert_pos}")
        
        return this_cycle
    
    def _filter_cycle_by_duration(self, cycle_steps: list) -> list:
        """Remove steps whose duration falls outside [min_duration, max_duration].

        Only evaluates steps still tracked in step_last_seen (not yet validated
        by the normal disappearance handler).  Steps already removed from
        step_last_seen passed validation earlier; backup-injected steps have no
        tracking entry and are always kept.
        """
        filtered = []
        for label in cycle_steps:
            if label not in self.step_last_seen:
                filtered.append(label)
                continue

            if self.step_accept_once.get(label):
                filtered.append(label)
                continue

            time_config = self.step_time_config.get(label, {})
            min_dur = time_config.get('min_duration')
            max_dur = time_config.get('max_duration')

            if min_dur is None and max_dur is None:
                filtered.append(label)
                continue

            start_time = self.step_start_time.get(label, self.step_last_seen[label])
            duration = self.step_last_seen[label] - start_time

            is_valid = True
            if min_dur is not None and duration < min_dur:
                is_valid = False
            if max_dur is not None and duration > max_dur:
                is_valid = False

            if is_valid:
                filtered.append(label)
            else:
                print(f"[duration filter] {label}: {duration:.2f}s not in "
                      f"[{min_dur}, {max_dur}], removed from cycle")
        return filtered
    
    def _check_static_step_conditions(self, static_label: str):
        """静态步骤达到触发帧数后，检查自定义条件
        
        当静态步骤（如"工件堆积"）达到配置的触发帧数时，
        直接检查自定义条件中是否有匹配这个步骤的条件并触发对应事件。
        这样即使静态步骤设置为不参与周期（join_cycle=False），
        也能正确触发自定义条件中配置的事件（如NG）。
        
        Args:
            static_label: 触发的静态步骤标签
        """
        if not self.project_config:
            return
        
        logic_mode = self.project_config.get('logic_mode', 'detection')
        if logic_mode != 'custom':
            return  # 只在自定义模式下生效
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        custom_conditions = pipeline_config.get('custom_conditions', [])
        steps_config = self.project_config.get('steps_config', [])
        events_config = self.project_config.get('events_config', [])
        
        if not custom_conditions:
            return
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        label_to_id = {}
        enabled_step_ids = set()
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                label_to_id[label] = step_id
                if step.get('enabled', True):
                    enabled_step_ids.add(step_id)
        
        static_step_id = label_to_id.get(static_label)
        if not static_step_id:
            return
        
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        print(f"检查静态步骤 [{static_label}] 的自定义条件...")
        
        # 按优先级排序自定义条件
        sorted_conditions = sorted(custom_conditions, key=lambda c: c.get('priority', 999))
        
        for cond in sorted_conditions:
            cond_sequence = cond.get('sequence', [])
            cond_event_id = cond.get('event_id')
            
            if not cond_sequence or not cond_event_id:
                continue
            
            # 将条件中的步骤ID转换为标签（只包含启用的步骤）
            cond_labels = [id_to_label.get(sid) for sid in cond_sequence 
                          if sid in id_to_label and sid in enabled_step_ids]
            
            # 检查条件是否只包含这个静态步骤
            # 支持两种情况：
            # 1. 条件只有一个步骤，且就是这个静态步骤
            # 2. 条件的最后一个步骤是这个静态步骤（用于组合条件）
            if len(cond_labels) == 1 and cond_labels[0] == static_label:
                # 单步骤条件，直接触发
                print(f"  → 匹配单步骤自定义条件: [{static_label}]，触发事件 ID: {cond_event_id}")
                self._trigger_event(cond_event_id, f'静态步骤自定义条件触发: {static_label}')
                return  # 匹配后不再检查其他条件
            elif cond_labels and cond_labels[-1] == static_label:
                # 组合条件，检查前面的步骤是否都在当前周期中
                prefix_labels = cond_labels[:-1]
                if all(pl in self.current_cycle_steps for pl in prefix_labels):
                    print(f"  → 匹配组合自定义条件: {cond_labels}，触发事件 ID: {cond_event_id}")
                    self._trigger_event(cond_event_id, f'静态步骤自定义条件触发: {static_label}')
                    return  # 匹配后不再检查其他条件
        
        print(f"  → 未找到匹配的自定义条件")
    
    # ================================================================
    # Counting Mode (物品清点模式) — stats / cycle logic
    # ================================================================
    
