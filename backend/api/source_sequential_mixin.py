"""顺序模式 / 自定义顺序 / 自定义检测 模式判定 (从 CheckModesMixin 拆出)"""
import time
import traceback
import cv2
import numpy as np

from backend.api.source_custom_mix import compose_settle_event


def _split_cycle_at_last_label(cycle_steps: list, last_label: str, expected_count: int):
    """以末步第 N 次出现为界切分周期 (N = 期望序列里末步的总次数).

    v3.19.x: 期望序列支持连续相同步骤后, 末步本身可能重复 (如 [..., D, D]),
    旧实现固定按"末步首次出现" (list.index) 切分, 会把第二个 D 切进下周期残留.
    凑不满 N 次时按"末步未到"处理 (整个列表都算本周期, 无残留), 与旧行为一致.
    """
    need = max(1, expected_count)
    seen = 0
    for i, s in enumerate(cycle_steps):
        if s == last_label:
            seen += 1
            if seen >= need:
                return cycle_steps[:i + 1], cycle_steps[i + 1:]
    return list(cycle_steps), []


class SequentialMixin:
    def _check_sequential_mode(self, pipeline_config: dict, id_to_label: dict):
        """检查顺序模式（由 last_step 消失触发）
        
        关键设计：在 current_cycle_steps 中以 last_step 首次出现为界拆分，
        拆分后的前半部分为本周期判定依据，后半部分保留给下一周期。
        """
        sequence_order = pipeline_config.get('sequence_order', [])
        if not sequence_order:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            # 周期结算后重置步骤时序状态，确保下一轮的相同步骤可被视为“新出现”
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self.last_step_completed_time = None
            return
        
        # 获取启用的步骤ID集合 (v3.19.x: 物品行归混合子状态机, 不算步骤)
        steps_config = self.project_config.get('steps_config', []) if self.project_config else []
        enabled_step_ids = {s.get('id') for s in steps_config
                            if s.get('enabled', True) and s.get('detect_role') != 'item'}
        
        # 将步骤ID转换为标签名（只包含启用的步骤）
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label and step_id in enabled_step_ids:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            # 周期结算后重置步骤时序状态，确保下一轮的相同步骤可被视为“新出现”
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self.last_step_completed_time = None
            return
        
        last_step_label = expected_labels[-1]
        
        # v3.19.x: 以末步第 N 次出现为界切分 (N=期望次数), 支持末步连续重复
        this_cycle, next_carry = _split_cycle_at_last_label(
            self.current_cycle_steps, last_step_label,
            expected_labels.count(last_step_label))
        
        this_cycle = self._inject_backup_steps(this_cycle, expected_labels)
        this_cycle = self._filter_cycle_by_duration(this_cycle)
        self.current_cycle_steps = this_cycle
        
        if not this_cycle:
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
        
        print(f"顺序模式检查: 期望={expected_labels}, 本周期={this_cycle}, 下周期残留={next_carry}")
        
        # ── 判定 (v3.7.x: 与 _settle_sequential_cycle 对齐, 用 Counter 区分多次出现) ──
        from collections import Counter
        expected_counter = Counter(expected_labels)
        step_counter = Counter(this_cycle)
        unexpected = [s for s in this_cycle if s not in set(expected_labels)]
        duplicated = [s for s, cnt in step_counter.items()
                      if cnt > expected_counter.get(s, 1)]
        missing = []
        for lbl, exp_cnt in expected_counter.items():
            act_cnt = step_counter.get(lbl, 0)
            if act_cnt < exp_cnt:
                missing.extend([lbl] * (exp_cnt - act_cnt))
        
        if unexpected or duplicated:
            reasons = []
            if unexpected:
                reasons.append(f'多余步骤: {list(dict.fromkeys(unexpected))}')
            if duplicated:
                reasons.append(f'重复步骤: {duplicated}')
            print(f"  → {', '.join(reasons)} → NG")
            self._trigger_event(2, ', '.join(reasons))
        elif missing:
            # 旧实现用 `not all(lbl in this_cycle for lbl in expected_labels)` 的存在性判,
            # 在 expected 含重复时 (例 A-B-C-B-D) 漏判 second-B 的 missing,
            # 使本应报"缺少 B"的 case 错走顺序判定误报"顺序错误", 客户困惑.
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
        else:
            # v3.7.x: this_cycle 与 expected_labels 长度相同且每个 expected 标签都在
            # (前面分支已过滤). multiset 相同 → 直接逐位比较.
            # 旧实现用 this_cycle.index(lbl) 总返首次位置, 在 sequence_order
            # 含重复元素时把正确序列误判为顺序错误.
            if this_cycle == expected_labels:
                print(f"  → 顺序正确 → OK")
                self._trigger_event(1, '顺序正确完成')
            else:
                mismatch_idx = -1
                for k in range(min(len(this_cycle), len(expected_labels))):
                    if this_cycle[k] != expected_labels[k]:
                        mismatch_idx = k
                        break
                if mismatch_idx >= 0:
                    order_error_labels = [expected_labels[mismatch_idx], this_cycle[mismatch_idx]]
                else:
                    order_error_labels = ['?', '?']
                print(f"  → 顺序错误 → NG: {order_error_labels}")
                self._trigger_event(2, f'顺序错误，期望[{order_error_labels[0]}]在前 实际[{order_error_labels[1]}]在前')
        
        # ── 补计：对本周期中尚未被计数的步骤进行补计 ──
        for label in this_cycle:
            if label in self.step_last_seen:
                # v3.9.x: 严格顺序 PT 起点夹到 max(start, 上一步完成时刻).
                raw_start = self.step_start_time.get(label, self.step_last_seen[label])
                start_time = self._resolve_step_pt_anchor(label, raw_start)
                last_time = self.step_last_seen[label]
                # v3.10.x B方案v2: 视频源用帧号差/fps 算耗时
                _raw_start_fr = self.step_start_frame_pos.get(label, 0)
                _start_fr = self._resolve_step_pt_anchor_frame_pos(label, _raw_start_fr)
                _last_fr = self.step_last_frame_pos.get(label, 0)
                duration = self._compute_duration_sec(
                    _start_fr, _last_fr,
                    fallback_start_wall=start_time, fallback_end_wall=last_time,
                )
                
                time_config = self.step_time_config.get(label, {})
                min_duration = time_config.get('min_duration')
                max_duration = time_config.get('max_duration')
                
                is_valid = True
                if min_duration is not None and duration < min_duration:
                    is_valid = False
                if max_duration is not None and duration > max_duration:
                    is_valid = False
                
                if is_valid:
                    if label not in self.step_counts:
                        self.step_counts[label] = 0
                    self.step_counts[label] += 1
                    rounded_dur = round(duration, 2)
                    self.step_durations[label] = rounded_dur
                    self.step_durations_history.setdefault(label, []).append(rounded_dur)
                    # v3.7.x: 与主路径对齐，补写本周期 SUM。
                    # 漏写会导致前端 PT 列在 cycle 末尾被补计的步骤（典型: 周期最后一步）显示 --，
                    # 但 step_counts 已 +1 → 前端仍标 OK，造成 "OK 出了 PT 没出" 的语义错位。
                    # 守门：补计场景下 label 一定在 current_cycle_steps（否则 step_counts 不该 +1），
                    # 加守门是为了对齐主路径，防止顺序错误的标签也被无脑塞进 SUM 字典污染下个周期。
                    if label in self.current_cycle_steps:
                        self._accumulate_step_pt(label, rounded_dur)
                    print(f"  顺序判定补计: {label}, 耗时 {duration:.2f}s, 累计: {self.step_counts[label]}")
                
                del self.step_last_seen[label]
                if label in self.step_start_time:
                    del self.step_start_time[label]
        
        # ── 重置 / 承接下一周期 ──
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        # v3.8.x: 残留传染守门 —— 残留必须是期望序列的**合法前缀**才传给下周期。
        # 客户报障 "一次 NG 后正常做的也全 NG"：旧实现无脑把 next_carry 塞进下周期，
        # 而 NG 周期下 next_carry 常是切割算法误判产生的乱序残渣（比如期望 [A,B,C,D]
        # 实际 [C,D,A,B,D] → 按 D 第一次出现切 → 本周期[C,D] 残留[A,B,D]）。
        # 残留[A,B,D] 不是期望前缀 [A,B,C]，下周期起手就带垃圾，用户再正常做也变 NG，
        # 残留滚雪球 → 永远 NG。
        # 合法前缀场景（保留传递）：典型为"上周期末尾步骤画面没消失就被新周期识别"，
        # next_carry 是期望从头开始的连续步骤（[A] / [A,B] / [A,B,C,D] 等）。
        if next_carry and next_carry == expected_labels[:len(next_carry)]:
            self.current_cycle_steps = next_carry
            self.last_added_step = next_carry[-1]
            self.cycle_start_time = self.step_start_time.get(next_carry[0], time.time())
            # v3.10.x B方案v2: 同步 cycle_start_frame_pos 与首步 step_start_frame_pos
            self.cycle_start_frame_pos = self.step_start_frame_pos.get(next_carry[0], self._video_frame_pos())
            self._last_step_added_time = time.time()
            self.start_cycle()
        else:
            if next_carry:
                print(f"  → 残留 {next_carry} 不是期望前缀 {expected_labels[:len(next_carry)]}, 丢弃避免 NG 滚雪球")
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self._last_step_added_time = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None

    def _check_custom_sequential_mode(self, pipeline_config: dict, id_to_label: dict):
        """检查自定义模式（基于顺序模式）的判定
        
        关键设计：在 current_cycle_steps 中以 last_step 首次出现为界拆分。
        使用独立的 custom_sequence_order 配置进行判定。
        """
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
            self.last_step_completed_time = None
            return
        
        # 获取启用的步骤ID集合 (v3.19.x: 物品行归混合子状态机, 不算步骤)
        steps_config = self.project_config.get('steps_config', []) if self.project_config else []
        enabled_step_ids = {s.get('id') for s in steps_config
                            if s.get('enabled', True) and s.get('detect_role') != 'item'}
        
        # 将步骤ID转换为标签名（只包含启用的步骤）
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
        
        last_step_label = expected_labels[-1]
        
        # v3.19.x: 以末步第 N 次出现为界切分 (N=期望次数), 支持末步连续重复
        this_cycle, next_carry = _split_cycle_at_last_label(
            self.current_cycle_steps, last_step_label,
            expected_labels.count(last_step_label))
        
        this_cycle = self._inject_backup_steps(this_cycle, expected_labels)
        this_cycle = self._filter_cycle_by_duration(this_cycle)
        self.current_cycle_steps = this_cycle
        
        if not this_cycle:
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
        
        print(f"自定义模式（基于顺序）检查: 期望={expected_labels}, 本周期={this_cycle}, 下周期残留={next_carry}")
        
        # ── 判定 (v3.7.x: 与 _settle_sequential_cycle 对齐, 同 _check_sequential_mode) ──
        from collections import Counter
        expected_counter = Counter(expected_labels)
        step_counter = Counter(this_cycle)
        unexpected = [s for s in this_cycle if s not in set(expected_labels)]
        duplicated = [s for s, cnt in step_counter.items()
                      if cnt > expected_counter.get(s, 1)]
        missing = []
        for lbl, exp_cnt in expected_counter.items():
            act_cnt = step_counter.get(lbl, 0)
            if act_cnt < exp_cnt:
                missing.extend([lbl] * (exp_cnt - act_cnt))
        
        if unexpected or duplicated:
            reasons = []
            if unexpected:
                reasons.append(f'多余步骤: {list(dict.fromkeys(unexpected))}')
            if duplicated:
                reasons.append(f'重复步骤: {duplicated}')
            print(f"  → {', '.join(reasons)} → NG")
            self._trigger_event(*compose_settle_event(self, 2, ', '.join(reasons)))
        elif missing:
            # v3.44 收尾防呆: 纯缺步 → 可选挂起等视觉补做 (补齐自动判 OK), 不判 NG.
            # 默认关 = 零差异; 有下周期残留时不挂 (边界模糊).
            if self._maybe_enter_settle_hold(missing, expected_labels, next_carry):
                return
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(*compose_settle_event(self, 2, f'周期不完整，缺少: {missing}'))
        else:
            # v3.7.x: 同上, 直接逐位比较, 修 sequence_order 含重复元素时的误判.
            if this_cycle == expected_labels:
                # v3.44.1 少装挂起: 步骤全对但箱内数量不足 → 摘收尾步骤挂起等
                # 补数量 (断点重做), 不判 NG 不清账. 默认关 = 零差异.
                if not next_carry and self._maybe_enter_short_count_hold(expected_labels):
                    return
                print(f"  → 顺序正确 → OK")
                self._trigger_event(*compose_settle_event(self, 1, '顺序正确完成'))
            else:
                mismatch_idx = -1
                for k in range(min(len(this_cycle), len(expected_labels))):
                    if this_cycle[k] != expected_labels[k]:
                        mismatch_idx = k
                        break
                if mismatch_idx >= 0:
                    order_error_labels = [expected_labels[mismatch_idx], this_cycle[mismatch_idx]]
                else:
                    order_error_labels = ['?', '?']
                print(f"  → 顺序错误 → NG: {order_error_labels}")
                self._trigger_event(*compose_settle_event(
                    self, 2, f'顺序错误，期望[{order_error_labels[0]}]在前 实际[{order_error_labels[1]}]在前'))
        
        # ── 补计：对本周期中尚未被计数的步骤进行补计 ──
        for label in this_cycle:
            if label in self.step_last_seen:
                # v3.9.x: 严格顺序 PT 起点夹到 max(start, 上一步完成时刻).
                raw_start = self.step_start_time.get(label, self.step_last_seen[label])
                start_time = self._resolve_step_pt_anchor(label, raw_start)
                last_time = self.step_last_seen[label]
                # v3.10.x B方案v2: 视频源用帧号差/fps 算耗时
                _raw_start_fr = self.step_start_frame_pos.get(label, 0)
                _start_fr = self._resolve_step_pt_anchor_frame_pos(label, _raw_start_fr)
                _last_fr = self.step_last_frame_pos.get(label, 0)
                duration = self._compute_duration_sec(
                    _start_fr, _last_fr,
                    fallback_start_wall=start_time, fallback_end_wall=last_time,
                )
                
                time_config = self.step_time_config.get(label, {})
                min_duration = time_config.get('min_duration')
                max_duration = time_config.get('max_duration')
                
                is_valid = True
                if min_duration is not None and duration < min_duration:
                    is_valid = False
                if max_duration is not None and duration > max_duration:
                    is_valid = False
                
                if is_valid:
                    if label not in self.step_counts:
                        self.step_counts[label] = 0
                    self.step_counts[label] += 1
                    rounded_dur = round(duration, 2)
                    self.step_durations[label] = rounded_dur
                    self.step_durations_history.setdefault(label, []).append(rounded_dur)
                    # v3.7.x: 与主路径对齐，补写本周期 SUM（同顺序模式补计）
                    # 守门见同文件 line 160 处说明。
                    if label in self.current_cycle_steps:
                        self._accumulate_step_pt(label, rounded_dur)
                    print(f"  自定义顺序判定补计: {label}, 耗时 {duration:.2f}s, 累计: {self.step_counts[label]}")
                
                del self.step_last_seen[label]
                if label in self.step_start_time:
                    del self.step_start_time[label]
        
        # ── 重置 / 承接下一周期 ──
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        # v3.8.x: 残留传染守门 —— 与 _check_sequential_mode 同, 详见上方注释。
        if next_carry and next_carry == expected_labels[:len(next_carry)]:
            self.current_cycle_steps = next_carry
            self.last_added_step = next_carry[-1]
            self.cycle_start_time = self.step_start_time.get(next_carry[0], time.time())
            # v3.10.x B方案v2: 同步 cycle_start_frame_pos 与首步 step_start_frame_pos
            self.cycle_start_frame_pos = self.step_start_frame_pos.get(next_carry[0], self._video_frame_pos())
            self._last_step_added_time = time.time()
            self.start_cycle()
        else:
            if next_carry:
                print(f"  → 残留 {next_carry} 不是期望前缀 {expected_labels[:len(next_carry)]}, 丢弃避免 NG 滚雪球")
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self._last_step_added_time = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None

    def _check_custom_detection_mode(self, pipeline_config: dict, id_to_label: dict, enabled_step_labels: list):
        """检查自定义模式（基于检测模式）
        
        使用独立的 custom_detection_steps 配置
        """
        detection_step_ids = pipeline_config.get('custom_detection_steps', [])
        
        # 将步骤ID转换为标签名
        if detection_step_ids:
            detection_labels = [id_to_label.get(sid) for sid in detection_step_ids if sid in id_to_label]
        else:
            detection_labels = enabled_step_labels
        
        if not detection_labels:
            return
        
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        print(f"自定义模式（基于检测）检查: 需要={detection_labels}, 当前周期={self.current_cycle_steps}")
        
        if all(label in self.current_cycle_steps for label in detection_labels):
            self._trigger_event(*compose_settle_event(self, 1, '检测完成'))  # 事件1: 合格
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
