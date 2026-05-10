"""v3.7.0 客户反馈 bug 锁定: 期望序列里 label 多次出现应被允许。

客户场景: 期望 A-B-C-B-D, 工人正确做 A-B-C-B-D → 现在系统会标"步骤回退/重复" NG.
预期: 应该 OK (合法序列)。

测试不依赖完整的 VSM 状态机, 直接构造 stub host + SequenceLabels 验证
判断函数 + 仿真 settle 里的 expected_counter 数学.
"""
from __future__ import annotations

from collections import Counter

import pytest

from backend.api.source_sequence_labels import SequenceLabels


class _StubHost:
    """模拟 VideoSourceManager, 只暴露 sequence_labels 用到的字段."""
    def __init__(self, expected_labels_via_seq_order):
        self.project_config = {
            "logic_mode": "sequential",
            "steps_config": [
                {"id": 1, "label": "A", "enabled": True},
                {"id": 2, "label": "B", "enabled": True},
                {"id": 3, "label": "C", "enabled": True},
                {"id": 4, "label": "D", "enabled": True},
            ],
            "pipeline_config": {
                "sequence_order": [{"step_id": sid} for sid in expected_labels_via_seq_order],
            },
        }
        self.current_cycle_steps = []


# ===== 期望序列 A-B-C-B-D =====
def _stub_abcbd():
    return _StubHost([1, 2, 3, 2, 4])


def test_is_legitimate_next_first_B_when_no_prev_seen():
    """A 之后, label=B → 是 expected[1]=B, 合法."""
    host = _stub_abcbd()
    sl = SequenceLabels(host)
    host.current_cycle_steps = ["A"]
    assert sl.is_legitimate_next_in_sequence("B") is True


def test_is_legitimate_next_C_after_AB():
    host = _stub_abcbd()
    sl = SequenceLabels(host)
    host.current_cycle_steps = ["A", "B"]
    assert sl.is_legitimate_next_in_sequence("C") is True


def test_is_legitimate_next_second_B_after_ABC_is_legit():
    """A-B-C 之后, 第二个 B 触发 → expected[3]=B, 合法重复, 不该被拒."""
    host = _stub_abcbd()
    sl = SequenceLabels(host)
    host.current_cycle_steps = ["A", "B", "C"]
    assert sl.is_legitimate_next_in_sequence("B") is True


def test_is_legitimate_next_third_B_is_not_legit():
    """A-B-C-B 之后, 再来第三个 B → 期望此时 D, 不合法."""
    host = _stub_abcbd()
    sl = SequenceLabels(host)
    host.current_cycle_steps = ["A", "B", "C", "B"]
    assert sl.is_legitimate_next_in_sequence("B") is False
    assert sl.is_legitimate_next_in_sequence("D") is True


def test_is_legitimate_overshoot_returns_false():
    host = _stub_abcbd()
    sl = SequenceLabels(host)
    host.current_cycle_steps = ["A", "B", "C", "B", "D"]
    assert sl.is_legitimate_next_in_sequence("E") is False


def test_is_legitimate_no_config_returns_false():
    host = _stub_abcbd()
    host.project_config = None
    sl = SequenceLabels(host)
    assert sl.is_legitimate_next_in_sequence("A") is False


# ===== Settle 数学: A-B-C-B-D 正确执行不应被 expected_counter 判 duplicated/missing =====

def test_settle_math_legitimate_repeat_not_flagged_as_duplicated():
    expected = ["A", "B", "C", "B", "D"]
    actual = ["A", "B", "C", "B", "D"]
    expected_counter = Counter(expected)
    step_counter = Counter(actual)
    duplicated = [s for s, cnt in step_counter.items()
                  if cnt > expected_counter.get(s, 1)]
    missing = []
    for lbl, exp_cnt in expected_counter.items():
        act_cnt = step_counter.get(lbl, 0)
        if act_cnt < exp_cnt:
            missing.extend([lbl] * (exp_cnt - act_cnt))
    assert duplicated == []
    assert missing == []


def test_settle_math_overcount_still_flagged():
    expected = ["A", "B", "C", "B", "D"]
    actual = ["A", "B", "B", "C", "B", "D"]  # B 出现 3 次, 期望 2 次
    expected_counter = Counter(expected)
    step_counter = Counter(actual)
    duplicated = [s for s, cnt in step_counter.items()
                  if cnt > expected_counter.get(s, 1)]
    assert "B" in duplicated


def test_settle_math_undercount_flagged_as_missing():
    expected = ["A", "B", "C", "B", "D"]
    actual = ["A", "B", "C", "D"]  # 只做了 1 次 B, 期望 2 次
    expected_counter = Counter(expected)
    step_counter = Counter(actual)
    missing = []
    for lbl, exp_cnt in expected_counter.items():
        act_cnt = step_counter.get(lbl, 0)
        if act_cnt < exp_cnt:
            missing.extend([lbl] * (exp_cnt - act_cnt))
    assert missing == ["B"]
