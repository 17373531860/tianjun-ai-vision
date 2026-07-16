"""v3.19.x 顺序模式"连续相同步骤"单元测试.

覆盖 4 个改动点:
  1. _split_cycle_at_last_label   — 周期切分点按末步第 N 次出现 (sequential_mixin)
  2. _last_step_repeat_quota_reached — 末步消失结算的重复次数守门 (events_check_mixin)
  3. _process_single_step          — A-A 连续重复的去重放行 + 首步重现结算守门 (settlement_mixin)
  4. _apply_pipeline_config        — 连续重复步骤 disappear_delay 强制清 0 (project_config_apply)

只验证状态机单点行为, 不接触主推理循环 / DB / 报警.
"""
import time

import pytest

from backend.api.source_sequential_mixin import _split_cycle_at_last_label


# ============================================================
# 公共构造器
# ============================================================
def _make_vsm(seq_labels, logic_mode='sequential', custom_based_on=None,
              steps_extra=None, settlement_mode='first_step'):
    """构造 VSM 并注入期望序列 (seq_labels 可含重复 label).

    steps_config 每个唯一 label 一条; sequence_order 按 seq_labels 引用
    (同一 step_id 可出现多次, 模拟前端重复选择).
    steps_extra: {label: {字段覆盖}} 注入 disappear_delay 等步骤级配置.
    """
    from backend.api.source import VideoSourceManager

    unique_labels = list(dict.fromkeys(seq_labels))
    label_to_id = {lbl: i + 1 for i, lbl in enumerate(unique_labels)}

    steps_config = []
    for lbl in unique_labels:
        step = {'id': label_to_id[lbl], 'label': lbl, 'enabled': True, 'min_frames': 1}
        if steps_extra and lbl in steps_extra:
            step.update(steps_extra[lbl])
        steps_config.append(step)

    pipeline = {
        'sequence_order': [{'step_id': label_to_id[lbl]} for lbl in seq_labels],
        'settlement_mode': settlement_mode,
    }
    if custom_based_on:
        pipeline['custom_based_on'] = custom_based_on
        pipeline['custom_sequence_order'] = pipeline.pop('sequence_order')

    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        'id': 99190,
        'name': '连续重复步骤单测项目',
        'task_type': 'detection',
        'logic_mode': logic_mode,
        'steps_config': steps_config,
        'events_config': [
            {'id': 1, 'name': 'OK', 'actions': [], 'show_notification': False},
            {'id': 2, 'name': 'NG', 'actions': [], 'show_notification': False},
        ],
        'counters_config': [],
        'pipeline_config': pipeline,
    })

    # 隔离外设/DB: 事件只记录, 周期开启为空操作, 结算打标记
    vsm._test_events = []
    vsm._trigger_event = lambda eid, reason='': vsm._test_events.append((eid, reason))
    vsm.start_cycle = lambda: None
    vsm._test_settles = []
    vsm._settle_sequential_cycle = lambda: vsm._test_settles.append('sequential')
    vsm._settle_custom_cycle = lambda: vsm._test_settles.append('custom')
    return vsm


def _appear(vsm, label, t):
    """模拟某 label 在 t 时刻的一次新出现 (走 _process_single_step)."""
    enabled = set(vsm.step_conf_thresholds.keys()) or {label}
    vsm._process_single_step(label, t, enabled, True, False, None, None)


def _settle_disappear(vsm, label):
    """模拟该 label 已走完消失结算 (delay=0 立即结算路径的最终效果)."""
    vsm.step_last_seen.pop(label, None)
    vsm.step_start_time.pop(label, None)
    vsm.step_frame_confirmed[label] = False
    vsm.step_consecutive_frames[label] = 0


# ============================================================
# 1. 周期切分点 (纯函数)
# ============================================================
class TestSplitCycleAtLastLabel:
    def test_last_label_single_old_behavior(self):
        # 末步只出现一次 → 与旧 index() 行为一致
        assert _split_cycle_at_last_label(['A', 'B', 'X'], 'B', 1) == (['A', 'B'], ['X'])

    def test_last_label_absent(self):
        assert _split_cycle_at_last_label(['A', 'B'], 'D', 1) == (['A', 'B'], [])

    def test_last_label_duplicated_full(self):
        # 末步期望 2 次, 凑满后按第 2 次出现切分
        assert _split_cycle_at_last_label(['A', 'D', 'D', 'E'], 'D', 2) == (['A', 'D', 'D'], ['E'])

    def test_last_label_duplicated_not_enough(self):
        # 凑不满 → 整个列表算本周期, 无残留 (按"末步未到"处理)
        assert _split_cycle_at_last_label(['A', 'D'], 'D', 2) == (['A', 'D'], [])

    def test_expected_count_zero_treated_as_one(self):
        assert _split_cycle_at_last_label(['A', 'D', 'X'], 'D', 0) == (['A', 'D'], ['X'])


# ============================================================
# 2. 末步消失结算守门
# ============================================================
class TestLastStepRepeatQuota:
    def test_single_last_label_always_true(self):
        vsm = _make_vsm(['A', 'B'])
        vsm.current_cycle_steps = ['A', 'B']
        assert vsm._last_step_repeat_quota_reached('B') is True

    def test_duplicated_last_label_not_enough(self):
        vsm = _make_vsm(['A', 'B', 'B'])
        vsm.current_cycle_steps = ['A', 'B']
        assert vsm._last_step_repeat_quota_reached('B') is False

    def test_duplicated_last_label_reached(self):
        vsm = _make_vsm(['A', 'B', 'B'])
        vsm.current_cycle_steps = ['A', 'B', 'B']
        assert vsm._last_step_repeat_quota_reached('B') is True


# ============================================================
# 3. A-A 连续重复入周期 + 首步重现结算守门
# ============================================================
class TestConsecutiveRepeatAppend:
    def test_expected_consecutive_repeat_appended(self):
        # 期望 [A,A,B]: 第二个 A (消失结算后重现) 必须入周期
        vsm = _make_vsm(['A', 'A', 'B'])
        t = time.time()
        _appear(vsm, 'A', t)
        assert vsm.current_cycle_steps == ['A']
        _settle_disappear(vsm, 'A')
        _appear(vsm, 'A', t + 1)
        assert vsm.current_cycle_steps == ['A', 'A']
        assert vsm.last_added_step == 'A'

    def test_unexpected_consecutive_repeat_still_deduped(self):
        # 期望 [A,B]: 第二个 A 不是期望重复 → A-A 去重硬规则不变
        vsm = _make_vsm(['A', 'B'])
        t = time.time()
        _appear(vsm, 'A', t)
        _settle_disappear(vsm, 'A')
        _appear(vsm, 'A', t + 1)
        assert vsm.current_cycle_steps == ['A']
        assert vsm._test_settles == []  # cur=[A] len=1, 不触发首步重现结算

    def test_full_consecutive_sequence(self):
        # 期望 [A,A,A,B]: 三连 A 全部入周期
        vsm = _make_vsm(['A', 'A', 'A', 'B'])
        t = time.time()
        for i in range(3):
            _appear(vsm, 'A', t + i)
            _settle_disappear(vsm, 'A')
        _appear(vsm, 'B', t + 3)
        assert vsm.current_cycle_steps == ['A', 'A', 'A', 'B']

    def test_first_step_repeat_no_premature_settle(self):
        # 期望 [A,A,B] + first_step 结算: 第二个 A 是合法重复, 不能当"新周期开始"提前结算
        vsm = _make_vsm(['A', 'A', 'B'], settlement_mode='first_step')
        t = time.time()
        _appear(vsm, 'A', t)
        _settle_disappear(vsm, 'A')
        _appear(vsm, 'A', t + 1)
        assert vsm._test_settles == []
        assert vsm.current_cycle_steps == ['A', 'A']

    def test_first_step_reappear_after_quota_settles(self):
        # 期望 [A,B] + first_step 结算: 周期完整后 A 重现 → 正常触发结算 (老行为不回归)
        vsm = _make_vsm(['A', 'B'], settlement_mode='first_step')
        t = time.time()
        _appear(vsm, 'A', t)
        _settle_disappear(vsm, 'A')
        _appear(vsm, 'B', t + 1)
        _settle_disappear(vsm, 'B')
        assert vsm.current_cycle_steps == ['A', 'B']
        _appear(vsm, 'A', t + 2)
        assert vsm._test_settles == ['sequential']

    def test_mid_sequence_first_label_repeat_appended(self):
        # 期望 [A,B,A] (首步在中途合法重复): 第二个 A 入周期而非提前结算
        vsm = _make_vsm(['A', 'B', 'A'], settlement_mode='first_step')
        t = time.time()
        _appear(vsm, 'A', t)
        _settle_disappear(vsm, 'A')
        _appear(vsm, 'B', t + 1)
        _settle_disappear(vsm, 'B')
        _appear(vsm, 'A', t + 2)
        assert vsm._test_settles == []
        assert vsm.current_cycle_steps == ['A', 'B', 'A']


# ============================================================
# 5. v3.40 川南反馈: 周期跑偏后位置指针失效, 末步余像不得误判"合法重复"
# ============================================================
class TestDeviatedCycleNoFalseRepeat:
    def test_missing_step_shifts_pointer_no_dup_join(self):
        # 期望 [A,B,C,D], 实际漏做 B → 周期 [A,C,D] (长度3)。
        # 老 bug: 位置指针指到 expected[3]='D', 滞留画面的成品 D 每次闪现都被
        # 当"合法连续重复"重新入周期 → [A,C,D,D,...] → 前端末步 OK/NG 闪烁 +
        # 结算多报"重复步骤 D"。修复后: 周期已偏离期望前缀, D 余像一律去重。
        vsm = _make_vsm(['A', 'B', 'C', 'D'], settlement_mode='first_step')
        t = time.time()
        for i, lbl in enumerate(['A', 'C', 'D']):
            _appear(vsm, lbl, t + i)
            _settle_disappear(vsm, lbl)
        assert vsm.current_cycle_steps == ['A', 'C', 'D']
        # 成品滞留画面, D 反复闪现 (每次都走完消失结算再重现)
        for k in range(3):
            _appear(vsm, 'D', t + 10 + k)
            _settle_disappear(vsm, 'D')
        assert vsm.current_cycle_steps == ['A', 'C', 'D'], \
            "跑偏周期中末步余像不得重复入周期"
        assert vsm._test_settles == []

    def test_legit_repeat_on_clean_prefix_still_works(self):
        # 对照组: 周期是期望的严格前缀时, 期望内的重复照常放行 (v3.19.x 行为不回归)
        vsm = _make_vsm(['A', 'B', 'B', 'C'], settlement_mode='first_step')
        t = time.time()
        _appear(vsm, 'A', t)
        _settle_disappear(vsm, 'A')
        _appear(vsm, 'B', t + 1)
        _settle_disappear(vsm, 'B')
        _appear(vsm, 'B', t + 2)  # 期望内第二个 B → 合法重复
        assert vsm.current_cycle_steps == ['A', 'B', 'B']


# ============================================================
# 4. 连续重复步骤 disappear_delay 强制清 0
# ============================================================
class TestDisappearDelayEnforcement:
    def test_consecutive_dup_forced_zero(self):
        vsm = _make_vsm(['A', 'A', 'B'],
                        steps_extra={'A': {'disappear_delay': 1.5},
                                     'B': {'disappear_delay': 0.8}})
        assert vsm.step_time_config['A']['disappear_delay'] == 0
        assert vsm.step_time_config['B']['disappear_delay'] == 0.8

    def test_non_consecutive_dup_keeps_delay(self):
        # 非连续重复 (A-B-A) 不受限制
        vsm = _make_vsm(['A', 'B', 'A'],
                        steps_extra={'A': {'disappear_delay': 1.5}})
        assert vsm.step_time_config['A']['disappear_delay'] == 1.5

    def test_custom_sequential_also_enforced(self):
        vsm = _make_vsm(['A', 'A', 'B'], logic_mode='custom', custom_based_on='sequential',
                        steps_extra={'A': {'disappear_delay': 2.0}})
        assert vsm.step_time_config['A']['disappear_delay'] == 0

    def test_detection_mode_untouched(self):
        # 检测模式没有顺序语义, 不做强制
        vsm = _make_vsm(['A', 'A', 'B'], logic_mode='detection',
                        steps_extra={'A': {'disappear_delay': 1.5}})
        assert vsm.step_time_config['A']['disappear_delay'] == 1.5
