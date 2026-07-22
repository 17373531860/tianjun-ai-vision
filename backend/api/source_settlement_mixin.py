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

from backend.core import debug_center
from backend.api.source_custom_mix import compose_settle_event


class SettlementMixin:
    def _settle_custom_cycle(self):
        """结算自定义模式的当前周期（在第一步重新出现且不匹配任何条件前缀时调用）"""
        if not self.project_config:
            return
        
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        custom_based_on = pipeline_config.get('custom_based_on')
        
        # 创建步骤ID到标签的映射，并获取启用的步骤ID集合
        # v3.19.x: 物品行 (detect_role='item') 归混合子状态机管, 永不算步骤
        id_to_label = {}
        enabled_step_ids = set()
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                if step.get('enabled', True) and step.get('detect_role') != 'item':
                    enabled_step_ids.add(step_id)
        
        # 获取启用的步骤标签
        enabled_step_labels = [s.get('label') for s in steps_config
                               if s.get('enabled', True) and s.get('detect_role') != 'item']
        
        self._supplement_step_durations()
        
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        # v3.8.x: 结算前对同时出现组成员按优先顺序兜底重排
        self._reorder_simultaneous_groups_in_cycle()
        
        print(f"[Settle/Custom] current sequence={self.current_cycle_steps}")
        if debug_center.is_on("backend.settlement"):
            debug_center.dbg("backend.settlement", "自定义模式结算入口", f"channel={self.channel_id} steps={self.current_cycle_steps}")
        
        if not self.current_cycle_steps:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
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
                    print(f"  -> condition matched! trigger event {cond_event_id}")
                    if debug_center.is_on("backend.settlement"):
                        debug_center.dbg("backend.settlement", "自定义条件匹配结算", f"event_id={cond_event_id} seq={cond_labels}")
                    self._reconcile_step_records()
                    self._trigger_event(*compose_settle_event(
                        self, cond_event_id, f'自定义条件匹配: {cond_labels}'))
                    self.current_cycle_steps = []
                    self.backup_steps_seen_in_cycle = set()
                    self.last_added_step = None
                    self.step_last_seen.clear()
                    self.step_start_time.clear()
                    self.step_consecutive_frames.clear()
                    self.step_frame_confirmed.clear()
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
                if hasattr(self, '_step_raw_start'):
                    self._step_raw_start.clear()
                self.last_step_completed_time = None
                return
            
            self._reconcile_step_records()
            
            print(f"  expected sequence ({len(expected_labels)} steps): {expected_labels}")
            print(f"  actual sequence ({len(self.current_cycle_steps)} steps): {self.current_cycle_steps}")
            
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
                self._trigger_event(*compose_settle_event(self, 2, reason_str))
            elif self.current_cycle_steps == expected_labels:
                print("  -> sequence fully matched -> OK")
                self._trigger_event(*compose_settle_event(self, 1, '顺序正确完成'))
            elif len(self.current_cycle_steps) < len(expected_labels):
                missing = [l for l in expected_labels if l not in self.current_cycle_steps]
                # v3.44 收尾防呆: 纯缺步可选挂起等视觉补做 (与末步消失结算同口径)
                if self._maybe_enter_settle_hold(missing, expected_labels, None):
                    return
                print(f"  -> cycle incomplete, missing: {missing} -> NG")
                self._trigger_event(*compose_settle_event(self, 2, f'周期不完整，缺少: {missing}'))
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
                    print(f"  -> step {mismatch_idx+1} order error: expected[{expected_labels[mismatch_idx]}], actual[{self.current_cycle_steps[mismatch_idx]}] -> NG")
                    self._trigger_event(*compose_settle_event(self, 2, f'第{mismatch_idx+1}步顺序错误'))
                else:
                    print("  -> order error -> NG")
                    self._trigger_event(*compose_settle_event(self, 2, '顺序错误'))
        
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
            
            print(f"  [Settle/Custom-Detection] required={detection_labels}, this_cycle={self.current_cycle_steps}, missing={missing}, duplicated={duplicated}")
            
            if not ng_reasons:
                print("  -> all detected, no duplicates -> OK")
                self._trigger_event(*compose_settle_event(self, 1, '检测完成'))
            else:
                reason = '；'.join(ng_reasons)
                print(f"  → {reason} → NG")
                self._trigger_event(*compose_settle_event(self, 2, reason))
        
        # 重置周期
        self._cycle_regression = False
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
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
        # v3.8.x: 结算前对同时出现组成员按优先顺序兜底重排
        self._reorder_simultaneous_groups_in_cycle()
        
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
        
        print(f"[Settle/Detection] required={detection_labels}, this_cycle={self.current_cycle_steps}, missing={missing}, duplicated={duplicated}")
        if debug_center.is_on("backend.settlement"):
            debug_center.dbg("backend.settlement", "检测模式结算", f"channel={self.channel_id} is_good={not ng_reasons} missing={missing} duplicated={duplicated}")
        
        if not ng_reasons:
            print("  -> all detected, no duplicates -> OK")
            self._trigger_event(1, '检测完成')
        else:
            reason = '；'.join(ng_reasons)
            print(f"  → {reason} → NG")
            self._trigger_event(2, reason)
        
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self._last_step_added_time = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
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
            # 静默丢弃周期的高危分支: 项目缺 sequence_order 时周期无 OK/NG 直接蒸发,
            # 客户感知"做完一圈什么都没发生" — 至少让调试日志说出原因
            if debug_center.is_on("backend.settlement"):
                debug_center.dbg("backend.settlement", "顺序模式结算被跳过",
                                 f"channel={self.channel_id} 项目缺 sequence_order 配置(或无步骤), 周期 {self.current_cycle_steps} 被静默丢弃, 不产生 OK/NG")
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
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
            self.last_step_completed_time = None
            return
        
        self._supplement_step_durations()
        
        self.current_cycle_steps = self._inject_backup_steps(
            self.current_cycle_steps, expected_labels)
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        # v3.8.x: 结算前对同时出现组成员按优先顺序兜底重排
        self._reorder_simultaneous_groups_in_cycle()

        if not self.current_cycle_steps:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self.last_step_completed_time = None
            return
        
        self._reconcile_step_records()
        
        print(f"[Settle/Sequential] expected={expected_labels}, actual={self.current_cycle_steps}, regression={self._cycle_regression}")
        if debug_center.is_on("backend.settlement"):
            debug_center.dbg("backend.settlement", "顺序模式结算入口", f"channel={self.channel_id} expected={expected_labels} actual={self.current_cycle_steps}")
        
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
            if debug_center.is_on("backend.settlement"):
                debug_center.dbg("backend.settlement", "顺序模式结算 NG", f"is_good=False reason={reason_str}")
            self._trigger_event(2, reason_str)
            self._cycle_regression = False
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self.last_step_completed_time = None
            return

        if missing:
            print(f"  -> cycle incomplete, missing: {missing} -> NG")
            if debug_center.is_on("backend.settlement"):
                debug_center.dbg("backend.settlement", "顺序模式结算 NG", f"is_good=False 缺少={missing}")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
            self._cycle_regression = False
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self.last_step_completed_time = None
            return
        
        # v3.7.x: 到这里 unexpected/duplicated/missing 都已过滤,
        # actual 与 expected 是 multiset 完全相同的两个序列,
        # 直接逐位比较即可. 旧实现用 unique_steps.index() 总返首次位置,
        # 在 sequence_order 含重复元素时 (例 A-B-C-B-D 里 B×2)
        # 会把"完全正确的序列"误判为"顺序错误", 客户感知为 0% OK.
        if debug_center.is_on("backend.settlement"):
            debug_center.dbg("backend.settlement", "顺序模式结算判定", f"is_good={self.current_cycle_steps == expected_labels}")
        if self.current_cycle_steps == expected_labels:
            print("  -> order correct -> OK")
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
            print(f"  -> order error -> NG: step {mismatch_idx+1} expected[{order_error_labels[0]}] actual[{order_error_labels[1]}]")
            self._trigger_event(2, f'顺序错误，期望[{order_error_labels[0]}]在前 实际[{order_error_labels[1]}]在前')
        
        # 重置周期
        self._cycle_regression = False
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self.last_step_completed_time = None

    def _handle_blocked_labels_release(self, detected_labels: set):
        """v3.8.x (类二): 检查是否解除被屏蔽集合.

        解除条件: 本帧已过帧确认的启用步骤里, 出现了任何"非任何跨周期组成员"的标签
        → 一次性清空 _blocked_labels.

        语义: 客户已经推进到跨周期组外的步骤, 说明上一周期残影窗口已经过去,
        屏蔽不再必要.
        """
        if not getattr(self, '_blocked_labels', None):
            return
        # 收集所有跨周期组的成员标签
        cross_cycle_members = set()
        for group in getattr(self, '_simultaneous_groups', []) or []:
            if group.get('enabled', True) and group.get('cross_cycle'):
                cross_cycle_members.update(group.get('labels', []))
        # 本帧有非跨周期组成员的有意义步骤 → 解除屏蔽
        if detected_labels - cross_cycle_members:
            print(f"[CrossCycle/Unblock] out-of-group step this frame, clearing blocked set: {self._blocked_labels}")
            self._blocked_labels = set()

    def _process_cross_cycle_groups(self, frame_detected_labels: set, detected_labels: set, current_time: float):
        """v3.8.x (类二): 跨周期同时出现组路由.

        语义: 跨周期同时出现组配置 (cross_cycle=true) 指"上一周期某些成员和下一周期
        某些成员实际会几乎同时入场". 状态机为这两组成员互相等待: 任一侧先到 → 进入
        等待 → 等待中另一侧到来一起结算上周期并启动下周期; 等待中出现组外步骤 / 等待
        超时 → 立即结算上周期 + 把所有组员加入被屏蔽集合.

        被屏蔽集合 (_blocked_labels) 的作用: 上一周期残影 (例如已结算后的 E 持续被
        识别) 不再触发新周期 / 不再写入 cycle_steps. 屏蔽解除靠 _handle_blocked_labels_release.

        组配置 schema:
          - cross_cycle: true
          - labels: 全部成员
          - prev_cycle_labels: 上周期成员标签列表 (子集)
          - next_cycle_labels: 下周期成员标签列表 (子集)
          - time_window: 等待超时秒数 (默认 3.0)
          - priority_order: 输出顺序 (仅影响下周期成员入序的排列)

        返回:
            consumed: set -- 本帧被跨周期路由"消费"掉的标签, 应该从 detected_labels /
                            frame_detected_labels 里去掉, 不让后续主循环再处理.
        """
        consumed = set()
        if not self._simultaneous_groups:
            return consumed

        # 应用被屏蔽集合: 本帧若识别到屏蔽中的标签, 直接消费掉
        if getattr(self, '_blocked_labels', None):
            blocked_in_frame = frame_detected_labels & self._blocked_labels
            if blocked_in_frame:
                consumed.update(blocked_in_frame)

        for idx, group in enumerate(self._simultaneous_groups):
            if not group.get('enabled', True):
                continue
            if not group.get('cross_cycle'):
                continue

            group_labels = set(group.get('labels', []))
            if len(group_labels) < 2:
                continue
            prev_labels = set(group.get('prev_cycle_labels', []))
            next_labels = set(group.get('next_cycle_labels', []))
            if not prev_labels or not next_labels:
                # 配置不完整, 跳过 (前端应该禁止保存这种组)
                continue
            time_window = group.get('time_window', 3.0)

            # 本帧组内成员 (排除已屏蔽的)
            present = (group_labels & frame_detected_labels) - self._blocked_labels
            present_in_detected = (group_labels & detected_labels) - self._blocked_labels

            wait_state = self._cross_cycle_waiting.get(idx)

            if wait_state is None or wait_state.get('phase') != 'waiting':
                # idle 状态: 等待第一个成员到来
                if not present_in_detected:
                    continue

                # 选一个本帧已确认的成员作为"先到者"
                # 优先 prev 成员 (上周期结算步骤通常先到), 否则取 next 成员
                first_member = None
                first_role = None
                for lbl in present_in_detected:
                    if lbl in prev_labels:
                        first_member = lbl
                        first_role = 'prev'
                        break
                if first_member is None:
                    for lbl in present_in_detected:
                        if lbl in next_labels:
                            first_member = lbl
                            first_role = 'next'
                            break
                if first_member is None:
                    continue

                # 关键差异化处理:
                # - prev 成员先到: 它属于上周期, 应加入 current_cycle_steps (情况乙),
                #   但不立即触发结算, 进入等待 next 成员
                # - next 成员先到: 它属于下周期, 暂不加入 current_cycle_steps
                #   (current_cycle_steps 必须非空, 否则没什么可等的)
                if first_role == 'prev':
                    # prev 成员先到的等待: 防御"本周期还在中间, prev 成员 (例如 E) 因为
                    # 模型误检提前出现"的误触发 — 严格顺序模式下, 序列里 prev 成员之前的
                    # 步骤必须已经在 cycle_steps 里, 否则放任主循环按"步骤回退/重复"逻辑处理.
                    expected_seq = self._get_expected_sequence_labels() if hasattr(self, '_get_expected_sequence_labels') else None
                    if expected_seq and first_member in expected_seq:
                        prev_member_idx = expected_seq.index(first_member)
                        required_predecessors = expected_seq[:prev_member_idx]
                        if required_predecessors and not all(p in self.current_cycle_steps for p in required_predecessors):
                            # 前驱步骤还没齐, 不视为"上周期收尾", 让主循环正常处理
                            continue
                    if first_member not in self.current_cycle_steps:
                        self.current_cycle_steps.append(first_member)
                        self.last_added_step = first_member
                        self._last_step_added_time = current_time
                        # 同步更新 step_start_time / step_last_seen
                        if first_member not in self.step_start_time:
                            self.step_start_time[first_member] = current_time
                            self.step_start_frame_pos[first_member] = self._video_frame_pos()
                        self.step_last_seen[first_member] = current_time
                        self.step_last_frame_pos[first_member] = self._video_frame_pos()
                        print(f"[CrossCycle/Wait] prev-cycle member {first_member} arrived, adding to cycle_steps "
                              f"(当前序列: {self.current_cycle_steps}), 进入等待")
                elif first_role == 'next':
                    # next 成员先到的等待: 必须 cycle_steps 里已经含至少一个 prev 成员,
                    # 这是"上一周期已经接近完成"的强信号. 否则 next 成员就是"本周期首步
                    # 刚出现"被误判, 会把 [A] 错当成"上一周期 [A] + 下一周期等 prev",
                    # 造成结算时实际 cycle_steps=[A] 与期望 [A,B,C,D,E] 不符判 NG.
                    if not self.current_cycle_steps:
                        # 没有上一周期, 跨周期等待没意义, 让 next 成员走正常路径开新周期
                        continue
                    if not (set(self.current_cycle_steps) & prev_labels):
                        # cycle_steps 里还没有任何 prev 成员 = 上一周期没完成 = 不是真正
                        # 跨周期场景, 让 next 成员走正常主循环 (例如它本身就是本周期首步)
                        continue
                    print(f"[CrossCycle/Wait] next-cycle member {first_member} arrived, not yet adding to cycle_steps "
                          f"(等上周期成员)")
                    # 仅更新 step_last_seen 让 disappear 路径正常工作
                    self.step_last_seen[first_member] = current_time
                    self.step_last_frame_pos[first_member] = self._video_frame_pos()

                self._cross_cycle_waiting[idx] = {
                    'phase': 'waiting',
                    'first_member': first_member,
                    'first_role': first_role,
                    'wait_start_time': current_time,
                    'time_window': time_window,
                    'group_labels': group_labels,
                    'prev_labels': prev_labels,
                    'next_labels': next_labels,
                }
                consumed.update({first_member})
                continue

            # phase == 'waiting'
            first_member = wait_state['first_member']
            first_role = wait_state['first_role']
            elapsed = current_time - wait_state['wait_start_time']

            other_role_labels = wait_state['next_labels'] if first_role == 'prev' else wait_state['prev_labels']
            other_arrived = present_in_detected & other_role_labels
            # 本帧"组外有意义步骤" (用于中断等待)
            other_meaningful = detected_labels - group_labels

            if other_arrived:
                # 路径 1: 另一侧成员到达 → 结算上周期 + 启动下周期 + 屏蔽所有组员
                arrived_label = next(iter(other_arrived))
                print(f"[CrossCycle/End-combo-complete] first {first_member}({first_role}) + "
                      f"另一侧 {arrived_label} → 结算上周期 + 启动下周期")
                self._settle_for_cross_cycle()

                # 启动下周期: 用 next 成员 (按优先顺序排好)
                if first_role == 'next':
                    new_cycle_first = first_member
                elif arrived_label in wait_state['next_labels']:
                    new_cycle_first = arrived_label
                else:
                    new_cycle_first = None

                if new_cycle_first is not None:
                    self.cycle_start_time = current_time
                    self.cycle_start_frame_pos = self._video_frame_pos()
                    self.start_cycle()
                    self.current_cycle_steps.append(new_cycle_first)
                    self.last_added_step = new_cycle_first
                    self._last_step_added_time = current_time
                    self.step_start_time[new_cycle_first] = current_time
                    self.step_start_frame_pos[new_cycle_first] = self._video_frame_pos()
                    self.step_last_seen[new_cycle_first] = current_time
                    self.step_last_frame_pos[new_cycle_first] = self._video_frame_pos()

                # 屏蔽所有组员, 直到出现组外有意义步骤
                self._blocked_labels |= group_labels
                self._cross_cycle_waiting.pop(idx, None)
                consumed.update(group_labels & frame_detected_labels)
                continue

            if other_meaningful:
                # 路径 2: 出现组外有意义步骤 → 立即结算上周期 + 屏蔽组员
                print(f"[CrossCycle/End-out-of-group] first {first_member}({first_role}), "
                      f"组外步骤 {other_meaningful} 到达 → 结算上周期 + 让组外步骤走正常路径")
                self._settle_for_cross_cycle()
                self._blocked_labels |= group_labels
                self._cross_cycle_waiting.pop(idx, None)
                # 注意: 不 consume 组外标签, 让它们继续走正常主循环
                consumed.update(group_labels & frame_detected_labels)
                continue

            if elapsed > time_window:
                # 路径 3: 等待超时 → 自动结算上周期 + 屏蔽组员
                print(f"[CrossCycle/End-timeout] first {first_member}({first_role}), "
                      f"等待 {elapsed:.2f}s > {time_window}s → 自动结算上周期")
                self._settle_for_cross_cycle()
                self._blocked_labels |= group_labels
                self._cross_cycle_waiting.pop(idx, None)
                consumed.update(group_labels & frame_detected_labels)
                continue

            # 等待中: 本帧组员被消费 (即使是同一个 first_member 再次出现, 也屏蔽)
            consumed.update(group_labels & frame_detected_labels)

        return consumed

    def _settle_for_cross_cycle(self):
        """跨周期路由触发的上周期结算 (按当前 logic_mode 选择 settle 函数)."""
        if not self.project_config:
            return
        if not self.current_cycle_steps:
            return
        logic_mode = self.project_config.get('logic_mode', 'detection')
        pipeline_config = self.project_config.get('pipeline_config', {})
        custom_based_on = pipeline_config.get('custom_based_on')
        if debug_center.is_on("backend.settlement"):
            debug_center.dbg("backend.settlement", "结算分发", f"channel={self.channel_id} logic_mode={logic_mode} based_on={custom_based_on} steps={self.current_cycle_steps}")
        if logic_mode == 'custom' and custom_based_on == 'sequential':
            self._settle_custom_cycle()
        elif logic_mode == 'sequential':
            self._settle_sequential_cycle()
        elif logic_mode == 'detection':
            self._settle_detection_cycle()
        elif logic_mode == 'custom':
            self._settle_custom_cycle()

    def _process_last_first_mode(self, frame_detected_labels: set,
                                 detected_labels: set, current_time: float) -> set:
        """v3.8.x last_first 结算模式状态机 (前置过滤器, 仅 last_first 模式生效).

        锚点:
          - 末步 D = 结算锚: 当前帧含 D 且 cycle_steps 非空 (或空 = State 0) → 立即结算 (R1)
          - 首步 A = 开周期锚: cycle_steps 含 A + 又一帧 A → D 缺位 fallback 立即结算 (R3)
          - pending 状态 + 首步 A → 退出 pending 让主循环写 cycle_steps (R2)
          - pending / 空 + A 缺 + 序列下一步在帧里 → 让主循环写它入 cycle_steps (R4)

        D 残影屏蔽:
          - R1 触发后 _blocked_labels.add(D), 后续帧 D 出现被消费 (R5)
          - 帧里出现序列内非 D 步骤 → _handle_blocked_labels_release 自动清空屏蔽 (R6)

        互斥保证 (前端 + apply_pipeline_config 双重校验):
          - 与 v3.8.x 类二跨周期同时出现组互斥 → _blocked_labels 不会被两边同时写
          - 与 per_item / 严格顺序互斥
          - 仅在 logic_mode ∈ {sequential, custom-based-on-sequential} 下激活

        Args:
            frame_detected_labels: 当前帧识别 (含未确认)
            detected_labels: 帧确认后的有效集合
            current_time: 当前时间

        Returns:
            consumed: 被本方法处理掉的标签集合, 主循环应从 detected_labels /
                      frame_detected_labels 中扣除 (避免后续 _process_single_step 重复处理).
        """
        consumed: set = set()

        # 守门 1: 仅 last_first 模式
        if self.settlement_mode != 'last_first':
            return consumed
        if not self.project_config:
            return consumed

        # 守门 2: 仅顺序型 (sequential / custom-based-on-sequential)
        logic_mode = self.project_config.get('logic_mode', 'detection')
        pipeline_config = self.project_config.get('pipeline_config', {})
        custom_based_on = pipeline_config.get('custom_based_on')
        is_seq_like = (
            logic_mode == 'sequential'
            or (logic_mode == 'custom' and custom_based_on == 'sequential')
        )
        if not is_seq_like:
            return consumed

        # 取首末步标签 + 完整序列
        first_label = self._get_first_sequence_step_label()
        last_label = self._get_last_sequence_step_label()
        if not first_label or not last_label or first_label == last_label:
            # 序列长度 < 2 时本模式没意义, 退化为不动
            return consumed
        seq_labels = self._get_expected_sequence_labels()
        if not seq_labels:
            return consumed

        # ─── R5: D 残影屏蔽 (优先级最高) ───
        # 前提: D 已在 _blocked_labels (上一次 R1 结算后置入).
        # 屏蔽集合的"出现非 D 步骤就清空"由 _handle_blocked_labels_release 在主循环里负责.
        if last_label in self._blocked_labels and last_label in detected_labels:
            consumed.add(last_label)

        # ─── R1: D 锚结算 (末步出现且不在屏蔽中) ───
        # State 1 (cycle_steps 非空) + D → 把 D 加进 cycle_steps 一起结算
        # State 0 (cycle_steps 空 + 非 pending) + D → 决策点②选 a: 结算空周期 [D] (NG 缺所有)
        # 注意 R3 触发后 cycle_steps 也会被清空, 此时 R1 不该再跑 (我们用 _r1_triggered 标志阻断)
        _r1_triggered = False
        if (last_label in detected_labels
                and last_label not in self._blocked_labels):
            if last_label not in self.current_cycle_steps:
                self.current_cycle_steps.append(last_label)
            self._settle_for_cross_cycle()
            self._pending_first_step = True
            self._blocked_labels.add(last_label)
            consumed.add(last_label)
            _r1_triggered = True
            if debug_center.is_on("backend.settlement"):
                debug_center.dbg("backend.settlement", "last_first R1 末步锚结算", f"channel={self.channel_id} last={last_label}")
            # _settle 内部已清空 cycle_steps + step_last_seen + step_start_time

        # ─── R3: D 缺位 fallback ───
        # cycle_steps 已含首步 + 当前帧又来首步 + cycle_steps 末尾非 D
        # (R1 触发后 cycle_steps 已空, 自然不会进 R3)
        if (not _r1_triggered
                and first_label in detected_labels
                and first_label in self.current_cycle_steps
                and self.current_cycle_steps
                and self.current_cycle_steps[-1] != last_label):
            self._settle_for_cross_cycle()
            # _settle 已清空所有运行时状态, 现在手动把首步当新周期首步写入,
            # 避免主循环 _process_single_step 与同帧其他标签产生竞态顺序.
            self.current_cycle_steps = [first_label]
            self.cycle_start_time = current_time
            self.cycle_start_frame_pos = self._video_frame_pos()
            try:
                self.start_cycle()
            except Exception as _e:
                print(f"[last_first R3] start_cycle error: {_e}")
            self.step_last_seen[first_label] = current_time
            self.step_last_frame_pos[first_label] = self._video_frame_pos()
            self.step_start_time[first_label] = current_time
            self.step_start_frame_pos[first_label] = self._video_frame_pos()
            self.last_added_step = first_label
            self._last_step_added_time = current_time
            min_frames_required = max(1, int(self.step_min_frames.get(first_label, 1)))
            self.step_consecutive_frames[first_label] = min_frames_required
            self.step_frame_confirmed[first_label] = True
            self._pending_first_step = False
            consumed.add(first_label)
            if debug_center.is_on("backend.settlement"):
                debug_center.dbg("backend.settlement", "last_first R3 末步缺位fallback", f"channel={self.channel_id} first={first_label} last={last_label}")
            print(f"[last_first R3] D missing fallback: prev cycle settled NG (missing last step) -> new cycle [{first_label}]")
            return consumed

        # ─── R2: pending 状态 + 首步正常开周期 ───
        # 不消费 first_label, 让主循环 _process_single_step 把它写入 cycle_steps.
        # 仅把 _pending_first_step 置 False 阻止 R4 顶替.
        if self._pending_first_step and first_label in detected_labels:
            self._pending_first_step = False

        # ─── R4: 首步缺位顶替 (State 0 / State 2 + 首步缺) ───
        # cycle_steps 为空 + 首步不在 detected_labels + 序列里下一个未出现的非末步在帧中
        # 决策点⑤选 a: 项目首启动 + B 直接来也允许顶替
        if (not self.current_cycle_steps
                and first_label not in detected_labels):
            for lbl in seq_labels:
                if lbl == last_label:
                    break  # D 不能顶替 (D 已被 R1 处理)
                if lbl == first_label:
                    continue
                if lbl in detected_labels and lbl not in consumed:
                    # 让主循环正常处理 lbl, 它会写入 cycle_steps
                    self._pending_first_step = False
                    print(f"[last_first R4] first step {first_label} missing, {lbl} supersedes to start new cycle")
                    break

        return consumed

    def _reorder_simultaneous_groups_in_cycle(self):
        """结算前对当前周期内的同时出现组成员按优先顺序回写到 cycle_steps.

        v3.8.x 新增. 兜底保险: 不管运行时 cycle_steps 是因为模型识别先后顺序、
        缓冲超时单独走、或缓冲被非组内步骤打断, 写入顺序怎么乱, 结算前一律按
        客户配的优先顺序回写组内成员对应的位置.

        例: 期望 A-B-C-D, B-C 是同时组(优先顺序 B-C), 运行时 cycle_steps =
        [A, C, B, D] (C 先入了周期) → 重排后 [A, B, C, D].

        策略:
        - 找出每个组内成员在 cycle_steps 中的所有索引位置
        - 收集这些位置上的成员实际值, 按优先顺序排序
        - 把排序后的成员填回原来的位置 (位置数 = 成员数)
        - 跨周期组在类二有独立逻辑, 此处跳过 (cross_cycle=True 的组)
        """
        if not self._simultaneous_groups or not self.current_cycle_steps:
            return
        for group in self._simultaneous_groups:
            if not group.get('enabled', True):
                continue
            if group.get('cross_cycle'):
                continue
            priority_order = group.get('priority_order') or list(group.get('labels', []))
            group_labels_set = set(priority_order)
            if len(group_labels_set) < 2:
                continue

            positions = [i for i, lbl in enumerate(self.current_cycle_steps) if lbl in group_labels_set]
            if len(positions) < 2:
                continue

            members_at_positions = [self.current_cycle_steps[p] for p in positions]
            # 多重集合: 同一标签可能在 cycle_steps 中出现多次, 重排要保留出现次数.
            from collections import Counter
            member_counter = Counter(members_at_positions)
            reordered = []
            for lbl in priority_order:
                if member_counter.get(lbl, 0) > 0:
                    reordered.extend([lbl] * member_counter[lbl])

            if reordered != members_at_positions:
                print(
                    f"[同时出现组结算重排] 原序 {members_at_positions} → "
                    f"新序 {reordered} (位置 {positions})"
                )
                for pos, member in zip(positions, reordered):
                    self.current_cycle_steps[pos] = member

    def _process_simultaneous_groups(self, frame_detected_labels: set, detected_labels: set, current_time: float):
        """
        同时出现组缓冲排序层 (v3.8.x 重构).

        语义:
          客户配置一组在期望序列里"必然几乎同时入场、但模型识别顺序不可靠"
          的标签 (例如 B-C). 缓冲层负责: 部分成员先到时挂起、全员到齐时按客户
          配置的优先顺序输出, 让 cycle_steps 写入顺序对客户可控.

        进入缓冲条件 (跨周期组在类二处理, 这里跳过):
          - 当前周期已有步骤
          - 本帧出现组内成员
          - 至少一个成员在"最后看见时间戳"里不存在 (即"潜在新出现")
          - 否则视为连续识别, 不缓冲

        缓冲终止 (按优先级判断):
          1) 全员到齐: 按优先顺序输出 (最佳路径)
          2) 出现非组内有意义步骤 (= detected_labels - group_labels 非空):
             立刻按"已收集成员的优先顺序"输出, 让那个非组内步骤接着走正常路径
             (后续可能因顺序错误被结算判 NG, 由结算逻辑处理)
          3) 时间窗口到: 按"已收集成员的优先顺序"输出, 缺的成员归结算时
             "缺步骤" NG 判定

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

            # 跨周期组在类二路径处理 (结算延迟 / 被屏蔽集合), 这里跳过.
            if group.get('cross_cycle'):
                continue

            group_labels = set(group.get('labels', []))
            if len(group_labels) < 2:
                continue

            time_window = group.get('time_window', 2.0)
            priority_order = group.get('priority_order') or list(group.get('labels', [])) or list(group_labels)

            present_members = group_labels & frame_detected_labels

            # 本帧"非组内的有意义步骤": 已通过帧确认的启用步骤集合, 去掉本组成员.
            # 这是缓冲提前释放的触发信号 (规则: 客户已经推进到组外步骤, 不再等组内成员).
            other_meaningful = detected_labels - group_labels

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
                    if len(self.current_cycle_steps) == 0:
                        # 周期空时不启动缓冲 (跨周期场景由类二处理)
                        pass
                    else:
                        # 只有"潜在新出现"的成员才进缓冲. step_last_seen 里还有
                        # 记录说明步骤尚未走完消失结算, 是连续识别, 不缓冲.
                        has_potential_new = any(
                            lbl not in self.step_last_seen for lbl in present_members
                        )

                        if not has_potential_new:
                            pass  # 连续检测, 不缓冲
                        elif present_members >= group_labels:
                            ordered = [l for l in priority_order if l in group_labels]
                            ready_ordered.extend(ordered)
                            print(f"[SimultaneousGroup {idx}] all arrived same frame, output in order: {ordered}")
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
                    print(f"[SimultaneousGroup {idx}] all arrived within {elapsed:.2f}s, output in order: {ordered}")
                elif other_meaningful:
                    # v3.8.x 新增: 缓冲中出现非组内有意义步骤 → 立即释放
                    # 客户已经推进到组外步骤, 不再等组内剩余成员; 已收集的按优先顺序
                    # 输出, 缺的部分由结算逻辑判 NG (顺序错误或缺步骤).
                    collected = buf['collected_labels']
                    ordered = [l for l in priority_order if l in collected]
                    ready_ordered.extend(ordered)
                    buf['collecting'] = False
                    buf['start_time'] = None
                    buf['collected_labels'] = set()
                    print(
                        f"[同时出现组 {idx}] 缓冲中出现非组内步骤 {other_meaningful}, "
                        f"提前释放已收集 {ordered}"
                    )
                elif elapsed > time_window:
                    collected = buf['collected_labels']
                    ordered = [l for l in priority_order if l in collected]
                    ready_ordered.extend(ordered)
                    buf['collecting'] = False
                    buf['start_time'] = None
                    buf['collected_labels'] = set()
                    print(f"[SimultaneousGroup {idx}] timeout {elapsed:.2f}s, output collected: {ordered}")
                else:
                    pending_labels.update(buf['collected_labels'])

            self._sim_group_buffers[idx] = buf

        return pending_labels, ready_ordered
    
    def _violation_throttle_pass(self, label) -> bool:
        """违规响应节流 (实时NG 与违序提示事件共用一本账): 同一 (标签, 周期进度)
        5 秒内只放行一次, 周期推进后可再报。守门/回退点每帧或每次新出现都会打到,
        不节流会刷屏 + 重复触发事件。返回 True = 放行 (并记账)。"""
        throttle = getattr(self, '_strict_violation_throttle', None)
        if throttle is None:
            throttle = {}
            self._strict_violation_throttle = throttle
        key = (label, tuple(self.current_cycle_steps))
        now_mono = time.monotonic()
        if now_mono - throttle.get(key, 0.0) < 5.0:
            return False
        throttle[key] = now_mono
        # 防止 dict 无限膨胀 (周期状态组合有限但保险起见)
        if len(throttle) > 256:
            throttle.clear()
            throttle[key] = now_mono
        return True

    def _fire_instant_ng(self, reason: str) -> bool:
        """v3.43 实时NG统一收口: 违规确认点当场触发 NG 事件(2) 走完整结算链路
        (end_cycle/计数/报警/MES)。全模式挂点共用 (严格守门违序 / 回退重复 /
        后续各模式超量等), 分流与档位只在这里存在一份。

        调用方约定:
          1. 先过 _violation_throttle_pass 节流再调本方法;
          2. 返回 True = 实时NG路径已消费本次违规 (已触发, 或被事件层守门抑制
             settle_dedup / ng_protect / 定格中 —— NG 语义下被抑制的违规不该
             绕道再报), 调用方不应再叠加提示类事件;
          3. 返回 False = 实时NG不适用 (开关关 / 周期未开 —— 空周期没有可结算
             对象, 落 NG 计数只会刷屏), 调用方自行决定兜底。

        提示档/斩立决由事件2自身配置分流:
          - 带「需人工确认」→ _trigger_event 内设定格标志, 此时不清运行时
            (_clear_step_runtime_state 会连定格一起抹掉; 清理交给确认端点:
            确认重做→清 / 保留周期→留, 与既有 ack 语义一致)
          - 不带 → 当场结算 + 清运行时开新周期 (同 _force_timeout_ng 语义)
        """
        if not getattr(self, 'instant_ng_on_violation', False):
            return False
        if not getattr(self, 'current_cycle_steps', None):
            return False
        fired = False
        try:
            fired = bool(self._trigger_event(2, f'实时NG: {reason}'))
        except Exception as e:
            print(f"[InstantNG] 实时NG触发失败: {e}")
        if fired:
            if not getattr(self, '_pending_ack', False) \
                    and hasattr(self, '_clear_step_runtime_state'):
                self._clear_step_runtime_state()
            print(f"[InstantNG] 违规即时结算已触发: {reason}")
        return True

    def _maybe_instant_ng_detection_duplicate(self, label):
        """v3.43 二期 实时NG · 检测模式重复超次: 标签入账后本周期出现次数一旦超过
        期望次数, _settle_detection_cycle 必报「重复步骤」NG (可证明性准绳) → 当场结。

        两个保守守门 (保证"提前结不改判定"严格成立):
          1. 仅纯 detection 模式 —— 基于检测的自定义有"末步消失齐了就 OK"早退路径
             (_check_custom_detection_mode 不数重复), 重复超次在那不必然 NG, 不做;
          2. 配了时长门 (min/max_duration) 的步骤跳过 —— 结算前
             _filter_cycle_by_duration 可能把时长不达标的出现滤出周期, 次数会回落,
             中途判会误杀; 交结算兜底。
        期望次数口径与 _settle_detection_cycle 完全同源 (expected_counter.get(s, 1))。
        """
        if not getattr(self, 'instant_ng_on_violation', False):
            return
        tc = self.step_time_config.get(label, {}) or {}
        if tc.get('min_duration') is not None or tc.get('max_duration') is not None:
            return
        from collections import Counter
        expected_counter = Counter(self._get_detection_step_labels() or [])
        if self.current_cycle_steps.count(label) <= expected_counter.get(label, 1):
            return
        if self._violation_throttle_pass(label):
            self._fire_instant_ng(f'重复步骤: [{label}] 超出期望次数')

    def _fire_strict_order_violation(self, label, reason: str):
        """v3.32 严格顺序违序即时事件 + v3.43 实时NG: 违序动作被守门拦下的当场响应。

        - 实时NG: pipeline_config.instant_ng_on_violation (False=关, 零差异),
          违规证据在这一刻已完整 (违序=提前出现; 漏步骤的最早可证明时刻=后继步骤
          已确认而前置缺失, 同一份证据) → 经 _fire_instant_ng 统一收口结算
        - 提示事件: pipeline_config.strict_order_violation_event_id (None=关, 零差异),
          当场触发所配事件但不动周期; 建议配警告/自定义类事件。
          实时NG不适用时 (开关关/空周期) 才走到这条
        """
        event_id = getattr(self, 'strict_order_violation_event_id', None)
        if not getattr(self, 'instant_ng_on_violation', False) and not event_id:
            return
        if not self._violation_throttle_pass(label):
            return
        if self._fire_instant_ng(reason):
            return
        if not event_id:
            return
        try:
            self._trigger_event(event_id, reason)
            print(f"[StrictOrder] 违序即时事件已触发: event_id={event_id} {reason}")
        except Exception as e:
            print(f"[StrictOrder] 违序即时事件触发失败: {e}")

    def _fire_closing_guard_alarm(self, reason: str, event_id=None) -> None:
        """v3.44 收尾防呆报警收口: 借所配事件的响应面 (灯/蜂鸣/Toast/语音),
        不结周期不动计数; 未配事件时仅日志 — 防呆本体 (拒收/挂起) 不依赖报警.
        v3.44: 数量门与缺步挂起各配各的事件 (话术不同), 由调用点传入."""
        if event_id:
            try:
                self.fire_external_event_response(event_id, reason, source='closing_guard')
                return
            except Exception as e:
                print(f"[ClosingGuard] 提示事件触发失败 (降级为日志): {e}")
        print(f"[ClosingGuard] {reason}")

    def _settle_hold_wants(self, label) -> bool:
        """v3.44: 缺步挂起中且该标签仍在缺失清单里 (含期望重复次数口径) → True.
        严格顺序守门/严格+单次守门/回退判定对这类步骤豁免放行 (断点补做语义)."""
        hold = getattr(self, '_settle_hold', None)
        if hold is None:
            return False
        from collections import Counter
        need = Counter(hold.get('expected') or []) - Counter(self.current_cycle_steps)
        return need.get(label, 0) > 0

    def _closing_guard_blocks(self, label) -> bool:
        """v3.44 收尾防呆入周期守门 (True = 本次新出现不计入周期).

        两个职责 (调用点在 _process_single_step 的新出现判定后):
          1. 缺步挂起吸收: 挂起中只接纳仍缺失的步骤, 其余新出现 (封箱余像 /
             工人再次封箱等) 一律吸收 — 防止挂起期间周期被塞成"重复步骤"死局;
          2. 数量门: 收尾步骤新出现时箱内已进数量未达目标 → 拒收 + 节流报警
             (工人当场补数量, 周期不打断; 补满后该步骤再出现自然放行).
        默认配置全关 → 前两个 getattr 即返回, 热路径零开销.
        """
        hold = getattr(self, '_settle_hold', None)
        if hold is not None:
            from collections import Counter
            need = Counter(hold.get('expected') or []) - Counter(self.current_cycle_steps)
            if need.get(label, 0) <= 0:
                self._dbg_step_rejected(label, "缺步挂起中: 非缺失步骤, 吸收不计入")
                return True
            return False
        if not getattr(self, '_closing_gate_enabled', False):
            return False
        if label not in (getattr(self, '_closing_gate_steps', None) or ()):
            return False
        # 周期还没开 (无任何步骤) → 不做数量门: 此时收尾步骤出现属违序问题,
        # 归严格顺序守门管; 也顺带吞掉结算后残像在新空周期上的"数量未满"误报警
        # (上银视频二实测: OK 结算瞬间封箱余像触发一次误报)。
        if not self.current_cycle_steps:
            return False
        mix = getattr(self, '_custom_mix', None)
        if mix is None:
            return False
        try:
            # ⚠️ 数量门用"已进箱记账"口径 (booked), 不用 verdict 的凑数口径 (settled):
            # 备盘区摆着整盘没进箱时 verdict 口径会凑成"已满", 数量门被骗过放行 —
            # 上银视频二 (漏装第四盘就放油嘴包, 期望当场报警) 实测漏报。
            total = mix.container_booked_item_total()
            target = mix.container_item_target()
        except Exception as e:
            print(f"[ClosingGuard] 读箱内数量失败 (放行不卡产线): {e}")
            return False
        if total is None or not target or int(total) >= int(target):
            return False
        print(f"[ClosingGuard] 收尾数量门拦下 [{label}]: 箱内已进 {int(total)}/{int(target)}")
        if self._violation_throttle_pass(f'@closing_gate:{label}'):
            self._fire_closing_guard_alarm(
                f'收尾防呆: [{label}] 出现但箱内数量 {int(total)}/{int(target)} 未满 — '
                f'请先补足数量再收尾',
                event_id=getattr(self, '_closing_gate_event_id', None))
        self._dbg_step_rejected(label, f"收尾数量门拦下 (箱内 {int(total)}/{int(target)})")
        return True

    def _maybe_enter_settle_hold(self, missing, expected_labels, next_carry) -> bool:
        """v3.44 缺步结算挂起入口 (True = 已挂起, 调用方不再触发 NG).

        仅在结算判定为"纯缺步骤" (无多余/重复/顺序错) 时由结算函数调用.
        挂起语义: 周期保持打开, 报警提示工人; 缺的步骤视觉补齐后自动按 OK
        结算 (_maybe_resolve_settle_hold); 超时 (_check_settle_hold_timeout)
        按原缺步 NG 落账. 有下周期残留时边界模糊, 不挂 (走原 NG).
        """
        if not getattr(self, '_settle_hold_enabled', False):
            return False
        if getattr(self, '_settle_hold', None) is not None:
            return False
        if next_carry:
            return False
        # 首步都缺 = 周期从没正经开始过 (典型: OK 结算后封箱余像自己开了个
        # 幽灵周期, 上银视频尾实测) → 不挂, 走原 NG 路径. 挂起语义只救
        # "开工了、缺了中间/收尾某步"的正经周期.
        if expected_labels and expected_labels[0] in (missing or []):
            return False
        self._settle_hold = {
            'missing': list(missing),
            'expected': list(expected_labels),
            'since': time.time(),
        }
        self._fire_closing_guard_alarm(
            f'收尾防呆: 缺少步骤 {list(missing)} — 周期挂起等补做, 补齐自动判合格',
            event_id=getattr(self, '_settle_hold_event_id', None))
        print(f"[ClosingGuard] 缺步结算挂起: missing={list(missing)} "
              f"expected={list(expected_labels)} timeout={getattr(self, '_settle_hold_timeout_s', 0)}s")
        if debug_center.is_on("backend.settlement"):
            debug_center.dbg("backend.settlement", "缺步结算挂起",
                             f"channel={self.channel_id} missing={list(missing)}")
        return True

    def _maybe_resolve_settle_hold(self) -> None:
        """v3.44 缺步挂起自动销结: 每次有步骤入周期后调用; multiset 补齐 →
        按期望顺序重排 (断点补做语义) 走标准结算路径判 OK + 补计 + 清理."""
        hold = getattr(self, '_settle_hold', None)
        if hold is None:
            return
        from collections import Counter
        expected = list(hold.get('expected') or [])
        if Counter(self.current_cycle_steps) != Counter(expected):
            return
        print(f"[ClosingGuard] 缺步已补齐 {hold.get('missing')} → 按期望顺序重排结算")
        self._settle_hold = None
        # 补做步骤 append 在末步之后, 顺序必然"错" — 断点补做语义下重排是正当的:
        # 工人确实把每一步都做了, 只是补做发生在收尾之后. 重排后走标准结算 → OK.
        self.current_cycle_steps = list(expected)
        if not self.project_config:
            return
        pipeline_config = self.project_config.get('pipeline_config', {}) or {}
        steps_config = self.project_config.get('steps_config', []) or []
        id_to_label = {s.get('id'): s.get('label', '') for s in steps_config
                       if s.get('id') and s.get('label')}
        try:
            self._check_custom_sequential_mode(pipeline_config, id_to_label)
        except Exception as e:
            print(f"[ClosingGuard] 挂起销结结算失败: {e}")

    def _check_settle_hold_timeout(self) -> None:
        """v3.44 缺步挂起超时兜底: 超时未补齐 → 按原缺步 NG 落账 + 清运行时.
        timeout=0 表示永等 (只手动处理). 由主循环每帧调用 (无挂起零开销)."""
        hold = getattr(self, '_settle_hold', None)
        if hold is None:
            return
        timeout = float(getattr(self, '_settle_hold_timeout_s', 0) or 0)
        if timeout <= 0:
            return
        since = float(hold.get('since') or 0)
        if since <= 0 or (time.time() - since) < timeout:
            return
        missing = list(hold.get('missing') or [])
        print(f"[ClosingGuard] 缺步挂起超时 ({timeout}s) 未补齐 → NG 落账: missing={missing}")
        self._settle_hold = None
        from backend.api.source_custom_mix import compose_settle_event
        # 挂起已是补做窗口, 超时落账不再进"补步骤延迟落账"二次挂起 (无人值守
        # 会变无限等待链); 事件自身的人工确认定格语义不受影响.
        self._skip_remediation_defer = True
        try:
            self._trigger_event(*compose_settle_event(
                self, 2, f'周期不完整，缺少: {missing} (挂起补做超时)'))
        finally:
            self._skip_remediation_defer = False
        if not getattr(self, '_pending_ack', False):
            self._clear_step_runtime_state()

    def _device_gate_hold(self, label, gate_cfg) -> bool:
        """v3.35 步骤外设门控查询: True = 门控未放行, 本步骤暂不入周期 (下帧重查)。

        - 门控已放行 → 消费实例 (下周期重新武装) 并放行入周期
        - 未武装 → 武装 (称重引擎开始拿秤读数推进) 并扣住
        - 引擎未登记本通道 (weighing 配置缺失/异常) → 直接放行, 绝不卡产线
        """
        try:
            from backend.services.weighing_engine import get_weighing_engine
            eng = get_weighing_engine()
            if eng.consume_gate_if_passed(self.channel_id, label):
                print(f"[DeviceGate] ch{self.channel_id} [{label}] 门控放行 → 入周期")
                return False
            if not eng.arm_step_gate(self.channel_id, label, gate_cfg):
                return False
            self._dbg_step_rejected(label, "等待外设门控 (秤条件未满足)")
            return True
        except Exception as e:
            print(f"[DeviceGate] ch{getattr(self, 'channel_id', '?')} [{label}] 检查失败(放行不卡产线): {e}")
            return False

    def _process_single_step(self, label, current_time, enabled_labels, is_seq_like,
                             should_update_screenshot, original_frame, det_info,
                             just_confirmed_labels=None):
        """处理单个标签的步骤逻辑：新出现判定、周期结算触发、周期记录、截图更新。
        
        从 _update_step_stats 的 for 循环体中提取，供缓冲层输出和普通标签共用。
        """
        import base64
        
        if label not in enabled_labels:
            return

        # v3.44 缺步挂起豁免: 挂起等补做时, 仍缺失的步骤是被明确期待的 —
        # 严格顺序/严格+单次守门放行它入周期 (断点补做语义, 顺序已由挂起兜底)
        if self.step_strict_order.get(label) and not self._settle_hold_wants(label):
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
                    # v3.32: 拦截照旧, 但支持当场报违序 (工人打错对角顺序立即报警,
                    # 不必等周期结算)。未配置事件时零差异。
                    self._fire_strict_order_violation(
                        label, f'违反严格顺序: [{label}] 过早出现, 前置步骤 [{pred}] 未完成')
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

        # 提前计算 is_new_appearance — 严格+单次守门只拦"多余位置的新出现",
        # 不拦画面里持续识别 (is_new_appearance=False). 旧实现在每帧都拦,
        # 把合法步骤的 step_last_seen 掐断 → PT 算出 0.00s.
        #
        # v3.8.x: 原"用 max_interval (去重间隔) 判定窗口"已合并入 disappear_delay
        # (消失等待时间). 现在判定逻辑统一为:
        # - step_last_seen 还在 → 说明上一次出现尚未走完消失结算 → 连续识别
        # - step_last_seen 已被 del (消失结算路径已清理) → 新出现
        # 检测模式下"步骤交替"(last_added_step != label) 这条独立的新出现信号保留,
        # 因为顺序无关时不同步骤交替本就该算一次"切换"而非"接续".
        if old_last_seen is None:
            is_new_appearance = True
        elif (not is_seq_like
              and self.last_added_step is not None
              and self.last_added_step != label):
            is_new_appearance = True
        else:
            is_new_appearance = False

        # ── v3.44 收尾防呆守门 (数量门拒收 + 缺步挂起吸收, 默认关零差异) ──
        # 位置有讲究: 在严格+单次守门之前 — 数量不足时报"箱内数量未满"比报"违序"
        # 对工人更可操作; 挂起中封箱余像等非缺失步骤也要在触发违序/实时NG前被吸收.
        if is_new_appearance and self._closing_guard_blocks(label):
            return

        # ── v3.8.x: 严格 + 单次接受 → 仅拦截"多余位置的新出现" ──
        # (v3.44: 挂起等补做的缺失步骤豁免, 见 _settle_hold_wants)
        if (is_new_appearance
                and is_seq_like
                and self.step_strict_order.get(label)
                and self.step_accept_once.get(label)
                and not self._settle_hold_wants(label)
                and not self._is_legitimate_next_in_sequence(label)):
            # Throttle: this gate fires every frame the surplus label is seen and
            # used to flood the log (thousands of identical lines/min, drowning real
            # errors + wasting CPU/IO). Only log once per (label, cycle-state) every 5s.
            _gate_throttle = getattr(self, "_strict_gate_log_throttle", None)
            if _gate_throttle is None:
                _gate_throttle = {}
                self._strict_gate_log_throttle = _gate_throttle
            _gate_key = (label, tuple(self.current_cycle_steps))
            _now = time.monotonic()
            _last = _gate_throttle.get(_gate_key, 0.0)
            if _now - _last >= 5.0:
                _gate_throttle[_gate_key] = _now
                print(f"[Gate/StrictOnce] '{label}' rejected: new appearance at wrong "
                      f"position (current={list(self.current_cycle_steps)})")
            # v3.32: 严格+单次守门支持当场报违序, 但只报"提前出现"(该步骤本周期
            # 还没做过)。已完成步骤的余像重现(补拧一下/标记笔迹持续在画面/工件
            # 横放中途被调整) 是现场常态 —— 静默拦截不入周期即可, 报违序是误伤
            # (真实视频验证: 收尾标记与横放二段出现均属此类)。
            if label not in self.current_cycle_steps:
                self._fire_strict_order_violation(
                    label, f'违反严格顺序: [{label}] 提前出现')
            return

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
                # v3.8.x: 改用"上一次出现已被消失结算清理"作为放行信号
                # (旧版用 max_interval 判时间窗已废弃, 合并入 disappear_delay).
                # old_last_seen is None 即首步彻底消失后再次出现 → 触发第一步重现结算.
                if first_step_label and label == first_step_label and old_last_seen is None:
                    allow_through = True
            if not allow_through:
                self.step_last_seen[label] = current_time
                self.step_last_frame_pos[label] = self._video_frame_pos()
                if label not in self.step_start_time:
                    raw_start = getattr(self, '_step_raw_start', {}).get(label, current_time)
                    self.step_start_time[label] = raw_start
                    # 帧位起点与 wall 起点同刻取原始出现时刻(口径见 1335 行注释)
                    self.step_start_frame_pos[label] = getattr(
                        self, '_step_raw_start_frame_pos', {}
                    ).get(label, self._video_frame_pos())
                return
        
        # ── 第一步重现结算（仅 first_step 结算模式） ──
        # v3.19.x: 若该 label 的本次重现恰好是期望序列的下一位 (首步在序列中
        # 合法重复, 如 [A,A,B] 的第二个 A), 这不是"新周期开始"的信号, 跳过结算
        # 让它走下方 append 路径作为期望重复入周期.
        _just_settled_by_first_step = False
        if self.settlement_mode == 'first_step' and is_seq_like \
                and label in self.current_cycle_steps and len(self.current_cycle_steps) > 1 \
                and not self._is_legitimate_next_in_sequence(label):
            first_step_label = self._get_first_sequence_step_label()
            # v3.34: 首步开了"消失等待不被打断"(disappear_uninterruptible) 时,
            # 持续可见 (step_last_seen 未被消失结算清理, old_last_seen 非 None)
            # 不算"重现"——首步工具驻留画面贯穿多个后续步骤是该开关的目标场景,
            # 只有真正走完消失结算后的再次出现才触发首步重现结算。
            # 开关默认 False → _held_visible 恒 False, 老项目行为零差异。
            _held_visible = (
                old_last_seen is not None
                and self.step_time_config.get(label, {}).get('disappear_uninterruptible')
            )
            if first_step_label and label == first_step_label and not _held_visible:
                first_start = self.step_start_time.get(label) or getattr(self, '_step_raw_start', {}).get(label)
                first_min_dur = (self.step_time_config.get(label, {}).get('min_duration')) or 0
                first_duration = (current_time - first_start) if first_start else 0
                if first_duration >= first_min_dur:
                    print(f"[FirstStepSettle] [{label}] detected again (held {first_duration:.2f}s >= {first_min_dur}s), settling current cycle (steps={len(self.current_cycle_steps)})")
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
                    print(f"[Settle/Detection] [{label}] first step reappeared (held {first_duration:.2f}s >= {first_min_dur}s), settling current cycle (steps={self.current_cycle_steps})")
                    self._settle_detection_cycle()
                    old_last_seen = None
                    _just_settled_by_first_step = True
                    if hasattr(self, '_step_raw_start'):
                        if first_min_dur and first_min_dur > 0:
                            self._step_raw_start[label] = current_time - first_min_dur
                        else:
                            self._step_raw_start.pop(label, None)
        
        # ── v3.35 步骤外设门控 (steps_config[].device_gate, 默认无配置零差异) ──
        # 视觉确认的新出现先"武装"外设门控 (如称重去皮/标准量判定), 门控放行前
        # 不写 step_last_seen / 不入周期 → 下一帧 is_new_appearance 仍为 True,
        # 放行后本 append 路径自然执行。融合模式核心接线点。
        if is_new_appearance and getattr(self, 'step_device_gates', None):
            _gate_cfg = self.step_device_gates.get(label)
            if _gate_cfg and self._device_gate_hold(label, _gate_cfg):
                return

        # Always update step_last_seen so duration calculations reflect actual last detection time
        self.step_last_seen[label] = current_time
        self.step_last_frame_pos[label] = self._video_frame_pos()
        
        if is_new_appearance:
            # v3.7.5: 早于 FIX-381 拦截把保养类 trigger 记进旁路账本.
            # 顺序模式里 expected_seq 之外的步骤会被下方 return 拦掉, 永远进不了
            # cycle.step_sequence — 但周期性强制动作的 trigger_step 本来就在序列外,
            # 拦掉了就清不了零. 这里独立记一笔, 让 _check_periodic_actions 看得见.
            try:
                self._observe_periodic_trigger(label)
            except Exception as _e:
                print(f"[PeriodicActions] _observe_periodic_trigger failed: {_e}")

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
                    # v3.42.1: 序列外步骤记入旁路账本 (不进 cycle, 不影响判定).
                    # 尾箱塞工单 gate 的"放工单"正是序列外检测步骤 — 被本 return
                    # 拦住导致 current_cycle_steps 永远看不见它, gate 永远不放行.
                    self._record_out_of_seq_step(label)
                    return

            # 检测模式：只有第一步能开启新周期
            if len(self.current_cycle_steps) == 0 and logic_mode == 'detection':
                first_det_label = self._get_first_detection_step_label()
                if first_det_label and label != first_det_label:
                    return
            
            raw_start = getattr(self, '_step_raw_start', {}).get(label, current_time)
            self.step_start_time[label] = raw_start
            # 帧位起点与 wall 起点(raw_start)保持同刻: 都取首次出现的原始时刻,
            # 不取"过完 min_duration 门才处理"的当前帧位, 否则视频源按帧号差
            # 算耗时会整体少掉门槛时长(与 1334 行 wall 路径口径不一致)。
            self.step_start_frame_pos[label] = getattr(
                self, '_step_raw_start_frame_pos', {}
            ).get(label, self._video_frame_pos())
            self.step_detection_times[label] = raw_start
            
            if len(self.current_cycle_steps) == 0:
                self.cycle_start_time = current_time
                self.cycle_start_frame_pos = self._video_frame_pos()
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
                        self._cycle_regression = False
                    if self._settle_hold_wants(label):
                        # v3.44 缺步挂起补做: 缺失步骤入周期 (不标回退/不报实时NG,
                        # 顺序由挂起销结时按期望重排兜底)
                        self.current_cycle_steps.append(label)
                        self.last_added_step = label
                        self._last_step_added_time = current_time
                        print(f"[ClosingGuard] 挂起补做步骤入周期: {label} "
                              f"(current: {self.current_cycle_steps})")
                    elif self.last_added_step == label:
                        # v3.19.x: 期望序列支持"连续相同步骤" (如 放托盘×4).
                        # 走到这里说明 is_new_appearance=True, 即上一次出现已经
                        # 走完消失结算 (step_last_seen 被删) 后重现 — 不是同一次
                        # 出现的延续. 若期望序列下一位正是该 label, 这是合法的
                        # 连续重复, 必须入周期; 否则维持 A-A 去重硬规则.
                        if self._is_legitimate_next_in_sequence(label):
                            self.current_cycle_steps.append(label)
                            self.last_added_step = label
                            self._last_step_added_time = current_time
                            print(f"[ExpectedConsecutiveRepeat] {label} is a legal consecutive repeat in expected sequence (current: {self.current_cycle_steps})")
                        else:
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
                            print(f"[ExpectedRepeat] {label} is a legal repeat in expected sequence (current: {self.current_cycle_steps})")
                        else:
                            self._cycle_regression = True
                            self.current_cycle_steps.append(label)
                            self.last_added_step = label
                            self._last_step_added_time = current_time
                            print(f"[StepRegression] {label} already appeared in cycle and not an expected repeat, marking regression (current: {self.current_cycle_steps})")
                            # v3.43 二期 实时NG: 回退/非法重复一经入账, 顺序型结算必判
                            # NG (可证明性准绳) → 当场结。守门只放顺序型: 回退标记仅被
                            # 顺序型结算分支消费; 基于检测的自定义按出现次数判且不看
                            # 回退标记, 此处提前结会误杀合法多次出现的周期。
                            _based_on = ((self.project_config.get('pipeline_config', {}) or {})
                                         .get('custom_based_on') if self.project_config else None)
                            if logic_mode == 'sequential' or _based_on == 'sequential':
                                if self._violation_throttle_pass(label):
                                    self._fire_instant_ng(
                                        f'步骤回退: [{label}] 在错误位置重复出现')
                    else:
                        self.current_cycle_steps.append(label)
                        self.last_added_step = label
                        self._last_step_added_time = current_time
                else:
                    if not self.step_accept_once.get(label) or label not in self.current_cycle_steps:
                        self.current_cycle_steps.append(label)
                        self.last_added_step = label
                        self._last_step_added_time = current_time
                        # v3.43 二期 实时NG: 纯检测模式重复超次即时结 (口径与
                        # _settle_detection_cycle 同源, 详见方法 docstring)
                        if logic_mode == 'detection':
                            self._maybe_instant_ng_detection_duplicate(label)

    def _record_out_of_seq_step(self, label):
        """v3.42.1 序列外步骤旁路账本 + 包装协调器即时通知.

        顺序/自定义-基于顺序模式下, 序列外启用步骤被 FIX-381 拦在周期外 —
        正确 (不该影响判定), 但「尾箱塞工单 gate」探测的"放工单"恰是序列外步骤,
        它的出现必须有处可查、有人可知:
          1. 记入 _oos_steps_seen (随周期结算轮转到 _last_oos_steps_seen,
             is_packaging_paper_order_covered 两代都查);
          2. 即时通知包装结算协调器 (尾箱挂起等放工单时, 出现即收尾,
             不必等下一次扫码/下一个周期结算)。
        无包装配置 / 通道不参与包装 → 协调器入口一层判断直接返回, 零差异。
        """
        try:
            if not hasattr(self, '_oos_steps_seen') or self._oos_steps_seen is None:
                self._oos_steps_seen = set()
            self._oos_steps_seen.add(label)
        except Exception:
            pass
        try:
            from backend.services.packaging_flow_coordinator import get_coordinator
            get_coordinator().on_step_detected(
                int(getattr(self, 'channel_id', 0) or 0), label)
        except Exception as e:
            print(f"[PackagingFlow] on_step_detected error (isolated, non-fatal): {e}")

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
                print(f"[BackupInject] {backup_label} -> {primary_label} at position {insert_pos}")
        
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
        
        print(f"[Settle] checking custom conditions for static step [{static_label}]...")
        
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
                print(f"  -> matched single-step custom condition: [{static_label}], trigger event ID: {cond_event_id}")
                self._trigger_event(cond_event_id, f'静态步骤自定义条件触发: {static_label}')
                return  # 匹配后不再检查其他条件
            elif cond_labels and cond_labels[-1] == static_label:
                # 组合条件，检查前面的步骤是否都在当前周期中
                prefix_labels = cond_labels[:-1]
                if all(pl in self.current_cycle_steps for pl in prefix_labels):
                    print(f"  -> matched combo custom condition: {cond_labels}, trigger event ID: {cond_event_id}")
                    self._trigger_event(cond_event_id, f'静态步骤自定义条件触发: {static_label}')
                    return  # 匹配后不再检查其他条件
        
        print("  -> no matching custom condition found")
    
    # ================================================================
    # Counting Mode (物品清点模式) — stats / cycle logic
    # ================================================================
    
