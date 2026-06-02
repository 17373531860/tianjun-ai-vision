"""客户"开了严格+单次仍 NG"根因验证 + 配置解法回归.

背景 (金龙 GW1 顺序线): 期望 热水退泡 → 浃泡结束 → 甩干, 末步结算 (last_step).
现场日志这一周期实际序列 = [热水退泡,浃泡结束,热水退泡,浃泡结束,甩干]:
首步与第二步交替反复进出 (消失 24s 远超消失延迟 → 算"新出现"), 顺序模式判重复 NG.

用交替重现模式 (贴近现场) 验证三件事:
  1. baseline: 不开任何保护 → cycle 记入重复 (复现现场 NG).
  2. 只保护首步 → 首步重现被"严格+单次"守门直接拦; 配合 last_added 短路,
     第二步紧随其后也不再入 cycle → cycle 干净.
  3. 反复进出的两步都开保护 → cycle 干净 (最稳妥根治).
"""
from __future__ import annotations

import numpy as np

from backend.api.source import VideoSourceManager

_DUMMY_FRAME = np.zeros((10, 10, 3), dtype="uint8")
_SEQ = ["热水退泡", "浃泡结束", "甩干"]


def _make_vsm(*, protect_labels):
    """末步结算顺序线; protect_labels 中的步骤开 严格顺序+单次接受."""
    vsm = VideoSourceManager(channel_id=0)
    steps = []
    for sid, label in enumerate(_SEQ, start=1):
        s = {"id": sid, "label": label, "enabled": True}
        if label in protect_labels:
            s["strict_order"] = True
            s["accept_once"] = True
        steps.append(s)
    vsm.set_project_config({
        "id": 99500,
        "name": "首步守门验证",
        "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": steps,
        "events_config": [
            {"id": 1, "name": "OK", "actions": [], "show_notification": False},
            {"id": 2, "name": "NG", "actions": [], "show_notification": False},
        ],
        "counters_config": [],
        "pipeline_config": {
            "sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}],
            "settlement_mode": "last_step",
        },
    })
    return vsm


def _feed(vsm, label, t, enabled):
    vsm._step_raw_start.setdefault(label, t)
    vsm._process_single_step(
        label=label, current_time=t, enabled_labels=enabled,
        is_seq_like=True, should_update_screenshot=False,
        original_frame=_DUMMY_FRAME, det_info=None, just_confirmed_labels=set(),
    )


def _reentry(vsm, label, t, enabled):
    """模拟某步消失被结算清理后重新出现 (老 step_last_seen 已删 → 新出现)."""
    vsm.step_last_seen.pop(label, None)
    _feed(vsm, label, t, enabled)


def _run_alternating(vsm):
    """现场交替重现剧本: 热水退泡→浃泡结束→热水退泡(重现)→浃泡结束(重现)→甩干."""
    en = set(_SEQ)
    t = 1000.0
    _feed(vsm, "热水退泡", t, en)
    _feed(vsm, "浃泡结束", t + 1, en)
    _reentry(vsm, "热水退泡", t + 25, en)
    _reentry(vsm, "浃泡结束", t + 26, en)
    _feed(vsm, "甩干", t + 30, en)


def test_baseline_no_protection_reproduces_duplicate_ng():
    """不开任何保护: 交替重现把重复记入 cycle → 复现现场重复 NG."""
    vsm = _make_vsm(protect_labels=set())
    _run_alternating(vsm)
    assert vsm.current_cycle_steps.count("热水退泡") == 2, (
        f"无保护应记入重复 (复现 NG), 实际={vsm.current_cycle_steps}"
    )
    assert vsm._cycle_regression is True


def test_protect_first_step_keeps_cycle_clean():
    """只保护首步 + 未开打断开关: 守门拦首步重现, cycle 收敛到期望序列."""
    vsm = _make_vsm(protect_labels={"热水退泡"})
    _run_alternating(vsm)
    assert vsm.current_cycle_steps == ["热水退泡", "浃泡结束", "甩干"], (
        f"保护首步后 cycle 应干净, 实际={vsm.current_cycle_steps}"
    )


def test_protect_both_repeating_steps_keeps_cycle_clean():
    """两步都保护: 最稳妥根治, cycle 干净."""
    vsm = _make_vsm(protect_labels={"热水退泡", "浃泡结束"})
    _run_alternating(vsm)
    assert vsm.current_cycle_steps == ["热水退泡", "浃泡结束", "甩干"], (
        f"两步都保护时 cycle 应干净, 实际={vsm.current_cycle_steps}"
    )
