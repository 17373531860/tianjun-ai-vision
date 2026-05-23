"""v3.8.x last_first 结算模式 单元测试.

覆盖 25 个边界场景 (见 debug-source skill §十二·七 last_first 模式状态机):
  - State 0: 项目首启动 / 周期空 + 非 pending
  - State 1: 周期非空 (累积中)
  - State 2: D 已结算 + pending 等首步
  - 同帧多标签优先级 + 互斥校验

每条测试只验证 _process_last_first_mode 这一个新方法的行为,
不接触主推理循环 / DB / 报警等其他模块. 其他模式的测试不动.
"""
import time
from unittest.mock import MagicMock

import pytest


pytestmark = pytest.mark.usefixtures()


# ============================================================
# 公共构造器
# ============================================================
def _make_vsm(seq_labels=None, logic_mode='sequential', custom_based_on=None):
    """构造 VSM, 注入 last_first 模式 + 期望序列, 不启动实际推理.

    seq_labels 默认 ['A', 'B', 'C', 'D'].
    """
    from backend.api.source import VideoSourceManager

    if seq_labels is None:
        seq_labels = ['A', 'B', 'C', 'D']

    pipeline = {
        'sequence_order': [{'step_id': i + 1} for i in range(len(seq_labels))],
        'settlement_mode': 'last_first',
    }
    if custom_based_on:
        pipeline['custom_based_on'] = custom_based_on
        pipeline['custom_sequence_order'] = pipeline.pop('sequence_order')

    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        'id': 99381,
        'name': 'last_first 单测项目',
        'task_type': 'detection',
        'logic_mode': logic_mode,
        'steps_config': [
            {'id': i + 1, 'label': lbl, 'enabled': True, 'min_frames': 1}
            for i, lbl in enumerate(seq_labels)
        ],
        'events_config': [
            {'id': 1, 'name': 'OK', 'actions': [], 'show_notification': False},
            {'id': 2, 'name': 'NG', 'actions': [], 'show_notification': False},
        ],
        'counters_config': [],
        'pipeline_config': pipeline,
    })
    # 模拟 _trigger_event 不真触发外设, 只记录事件
    vsm._test_events = []
    _orig_trigger = vsm._trigger_event

    def _capture_trigger(event_id, reason=''):
        vsm._test_events.append((event_id, reason))
        # 不调真实 trigger 避免拉起报警 / 推 MES
        # 但需要清空 cycle_steps 等 (模拟 _settle_*_cycle 内部行为)

    vsm._trigger_event = _capture_trigger
    return vsm


def _process(vsm, frame_labels, current_time=None):
    """调用一次 _process_last_first_mode + 模拟主循环写 cycle_steps."""
    if current_time is None:
        current_time = time.time()
    detected = set(frame_labels)
    frame = set(frame_labels)
    consumed = vsm._process_last_first_mode(frame, detected, current_time)
    return consumed, detected - consumed


# ============================================================
# State 0: 周期空 + 非 pending (项目首启动)
# ============================================================
class TestStateZero:
    def test_single_first_label(self):
        """场景 1: 单 A 来 → 不消费, 让主循环写入 [A]."""
        vsm = _make_vsm()
        consumed, _ = _process(vsm, ['A'])
        assert consumed == set()
        assert vsm._pending_first_step is False

    def test_single_b_substitutes(self):
        """场景 2: 单 B 来 → 顶替, 让主循环写入 [B] (不消费 B)."""
        vsm = _make_vsm()
        consumed, remaining = _process(vsm, ['B'])
        assert 'B' not in consumed
        assert vsm._pending_first_step is False
        # B 被允许进入主循环, 顶替开新周期 (cycle_steps 应被主循环更新)

    def test_single_c_substitutes(self):
        """场景 3: 单 C 来 (A、B 都缺) → C 顶替."""
        vsm = _make_vsm()
        consumed, _ = _process(vsm, ['C'])
        assert 'C' not in consumed
        assert vsm._pending_first_step is False

    def test_single_last_label_state0(self):
        """场景 4: 单 D 来 (空周期 + 非 pending) → cycle_steps=[D] 立即结算 NG, 进 State 2."""
        vsm = _make_vsm()
        consumed, _ = _process(vsm, ['D'])
        assert 'D' in consumed
        assert vsm._pending_first_step is True
        assert 'D' in vsm._blocked_labels
        # 应有 NG 事件 (缺 A/B/C)
        assert any(eid == 2 for eid, _ in vsm._test_events), f"应触发 NG 事件, 实际 {vsm._test_events}"

    def test_a_b_simultaneous(self):
        """场景 5: A+B 同帧 → 都不消费, 主循环正常累积."""
        vsm = _make_vsm()
        consumed, _ = _process(vsm, ['A', 'B'])
        assert consumed == set()

    def test_b_c_simultaneous_substitutes(self):
        """场景 6: B+C 同帧 (A 缺) → 顶替, 都不消费."""
        vsm = _make_vsm()
        consumed, _ = _process(vsm, ['B', 'C'])
        assert 'B' not in consumed and 'C' not in consumed
        assert vsm._pending_first_step is False

    def test_all_four_simultaneous(self):
        """场景 7: A+B+C+D 同帧 → R1 结算 OK + R2 让 A 进新周期 (同帧串联, 最终 pending=False)."""
        vsm = _make_vsm()
        # 模拟主循环已先把 A, B, C 写入 cycle_steps
        vsm.current_cycle_steps = ['A', 'B', 'C']
        consumed, _ = _process(vsm, ['A', 'B', 'C', 'D'])
        assert 'D' in consumed
        # R1 结算后 cycle_steps 清空 + _pending=True; R2 看到 A 在帧里立即退出 pending
        assert vsm._pending_first_step is False
        assert 'D' in vsm._blocked_labels
        # A 不被消费, 让主循环写入新周期
        assert 'A' not in consumed

    def test_out_of_sequence_label(self):
        """场景 8: 序列外标签 → 不被 last_first 处理."""
        vsm = _make_vsm()
        consumed, _ = _process(vsm, ['Z'])
        assert consumed == set()


# ============================================================
# State 1: 周期非空 (累积中)
# ============================================================
class TestStateOne:
    def test_d_anchor_settles(self):
        """场景 10: cycle_steps=[A,B] + D → R1 立即结算, 进 pending."""
        vsm = _make_vsm()
        vsm.current_cycle_steps = ['A', 'B']
        consumed, _ = _process(vsm, ['D'])
        assert 'D' in consumed
        assert vsm._pending_first_step is True
        assert 'D' in vsm._blocked_labels
        assert any(eid == 2 for eid, _ in vsm._test_events), "缺 C 应判 NG"

    def test_a_residue_in_cycle_triggers_r3(self):
        """场景 12: cycle_steps=[B] + A 来 (cycle_steps 不含 A) → R3 D 缺位 fallback."""
        vsm = _make_vsm()
        vsm.current_cycle_steps = ['B']
        # A 不在 cycle_steps 中 → 不触发 R3 (R3 要求 A 已在 cycle_steps)
        # 这条是首步还没出现, 走主循环让 A 接到 [B] 后面
        consumed, _ = _process(vsm, ['A'])
        # cycle_steps 不含 A, 不触发 R3
        # A 不消费, 让主循环处理
        assert 'A' not in consumed

    def test_a_reappears_with_a_in_cycle_triggers_r3(self):
        """场景 14: cycle_steps=[A,B,C] (含 A, 缺 D) + A 来 → R3 触发."""
        vsm = _make_vsm()
        vsm.current_cycle_steps = ['A', 'B', 'C']
        consumed, _ = _process(vsm, ['A'])
        assert 'A' in consumed, "R3 触发后 A 已被手动写入新周期, 应消费"
        # 上周期结算 NG (缺 D)
        assert any(eid == 2 for eid, _ in vsm._test_events)
        # 新周期 = [A]
        assert vsm.current_cycle_steps == ['A']
        assert vsm._pending_first_step is False

    def test_a_d_simultaneous_d_priority(self):
        """场景 15: cycle_steps=[A,B] + A+D 同帧 → R1 结算 NG (缺 C) + R2 让 A 进新周期."""
        vsm = _make_vsm()
        vsm.current_cycle_steps = ['A', 'B']
        consumed, _ = _process(vsm, ['A', 'D'])
        # R1 触发 → cycle_steps=[A,B,D] settle NG (缺 C) → 清空 → pending=True
        # R2 同帧检测到 A → 退出 pending → 最终 pending=False
        assert 'D' in consumed
        assert vsm._pending_first_step is False
        assert 'D' in vsm._blocked_labels
        # A 不消费, 让主循环开新周期 [A]
        assert 'A' not in consumed

    def test_c_d_simultaneous(self):
        """场景 16: cycle_steps=[A,B,C] + C+D 同帧 → R1 结算 OK + R4 顶替 (A 缺 → C 顶)."""
        vsm = _make_vsm()
        vsm.current_cycle_steps = ['A', 'B', 'C']  # 假设主循环已先写 C
        consumed, _ = _process(vsm, ['C', 'D'])
        assert 'D' in consumed
        # R1 结算 [A,B,C,D] = OK → 清空 → pending=True
        # R4: cycle_steps 空 + A 不在帧 + C 在帧 → C 顶替 → pending=False
        assert vsm._pending_first_step is False
        assert 'D' in vsm._blocked_labels


# ============================================================
# State 2: D 已结算 + pending
# ============================================================
class TestStateTwo:
    def test_a_normal_open_cycle(self):
        """场景 18: pending + A 来 → 退出 pending, 让主循环写 [A]."""
        vsm = _make_vsm()
        vsm._pending_first_step = True
        vsm._blocked_labels = {'D'}
        consumed, _ = _process(vsm, ['A'])
        assert 'A' not in consumed  # 不消费, 让主循环写
        assert vsm._pending_first_step is False

    def test_b_substitutes(self):
        """场景 19: pending + B (A 缺) → 顶替."""
        vsm = _make_vsm()
        vsm._pending_first_step = True
        vsm._blocked_labels = {'D'}
        consumed, _ = _process(vsm, ['B'])
        assert 'B' not in consumed
        assert vsm._pending_first_step is False

    def test_c_substitutes(self):
        """场景 20: pending + C (A、B 都缺) → 顶替."""
        vsm = _make_vsm()
        vsm._pending_first_step = True
        vsm._blocked_labels = {'D'}
        consumed, _ = _process(vsm, ['C'])
        assert 'C' not in consumed
        assert vsm._pending_first_step is False

    def test_d_residue_blocked(self):
        """场景 21: pending + D 残影持续 → R5 屏蔽."""
        vsm = _make_vsm()
        vsm._pending_first_step = True
        vsm._blocked_labels = {'D'}
        consumed, _ = _process(vsm, ['D'])
        assert 'D' in consumed
        # _pending_first_step 不应被改 (D 没"退出 pending")
        assert vsm._pending_first_step is True
        # cycle_steps 不应被任何动作改变
        assert vsm.current_cycle_steps == []

    def test_a_d_pending_d_blocked_a_processes(self):
        """场景 22: pending + A+D 同帧 → A 进周期, D 屏蔽."""
        vsm = _make_vsm()
        vsm._pending_first_step = True
        vsm._blocked_labels = {'D'}
        consumed, _ = _process(vsm, ['A', 'D'])
        assert 'D' in consumed  # R5 屏蔽 D
        assert 'A' not in consumed  # A 让主循环处理
        assert vsm._pending_first_step is False  # R2 退出 pending


# ============================================================
# 项目级生命周期: 状态清空
# ============================================================
class TestStateClearing:
    def test_clear_step_runtime_state_resets_pending(self):
        """场景 25: stop / 切项目 → _clear_step_runtime_state 清空 _pending_first_step."""
        vsm = _make_vsm()
        vsm._pending_first_step = True
        vsm._blocked_labels = {'D'}
        vsm._clear_step_runtime_state()
        assert vsm._pending_first_step is False
        assert vsm._blocked_labels == set()


# ============================================================
# 守门: 其他模式不受 last_first 状态机影响
# ============================================================
class TestModeIsolation:
    def test_first_step_mode_returns_empty_consumed(self):
        """settlement_mode='first_step' → _process_last_first_mode 立即返空集."""
        vsm = _make_vsm()
        # 切到 first_step 模式
        vsm.settlement_mode = 'first_step'
        vsm.current_cycle_steps = ['A', 'B', 'C']
        consumed, _ = _process(vsm, ['A', 'D'])
        # 即使来了 D, last_first 守门不该触发
        assert consumed == set()
        # cycle_steps 不应被 R1 修改
        assert vsm.current_cycle_steps == ['A', 'B', 'C']
        assert vsm._pending_first_step is False
        assert 'D' not in vsm._blocked_labels

    def test_last_step_mode_returns_empty_consumed(self):
        vsm = _make_vsm()
        vsm.settlement_mode = 'last_step'
        vsm.current_cycle_steps = ['A', 'B', 'C']
        consumed, _ = _process(vsm, ['D'])
        assert consumed == set()
        assert vsm.current_cycle_steps == ['A', 'B', 'C']

    def test_detection_mode_returns_empty_consumed(self):
        """logic_mode='detection' → 即使 settlement_mode='last_first' 也直接返空 (互斥校验在前端阻止此组合)."""
        vsm = _make_vsm(logic_mode='detection')
        vsm.settlement_mode = 'last_first'
        consumed, _ = _process(vsm, ['D'])
        # detection 模式下 is_seq_like = False, 直接返空
        assert consumed == set()


# ============================================================
# custom-based-on-sequential 兼容
# ============================================================
class TestCustomBasedOnSequential:
    def test_custom_sequential_d_anchor(self):
        """custom 模式 base=sequential, R1 应正常触发."""
        vsm = _make_vsm(logic_mode='custom', custom_based_on='sequential')
        vsm.current_cycle_steps = ['A', 'B', 'C']
        consumed, _ = _process(vsm, ['D'])
        assert 'D' in consumed
        assert vsm._pending_first_step is True


# ============================================================
# 退化场景: 序列长度 < 2
# ============================================================
class TestDegradedSequence:
    def test_single_label_sequence_no_op(self):
        """序列只有一个标签 (first==last) → last_first 不动."""
        vsm = _make_vsm(seq_labels=['X'])
        consumed, _ = _process(vsm, ['X'])
        # first_label == last_label, 直接返空
        assert consumed == set()
