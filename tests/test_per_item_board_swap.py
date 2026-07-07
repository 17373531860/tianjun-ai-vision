"""per_item 「换板兜底结算」— 单元测试 (修覆盖态泄漏到新板 bug).

现场 bug: finish_label 单一结算模式下, 若换板动作没被检到"拿取结算", 上一板周期
永不结算, 其逐颗 covered 会经 update_item_positions 按位置泄漏到新板 —— 新板还没打,
螺丝检测框直接变绿, 计数器却是 0.

修复: workpiece_absent_settle_frames > 0 时, 全部 item 标签连续消失该帧数 → 判工件
已取走/换板 → 按真实覆盖兜底结算 + reset, 新板从零重新锁定.

直接构造 VideoSourceManager, 逐帧喂 detections, 与 test_per_item_duplicate_screw.py 同套路.
"""
from __future__ import annotations

import numpy as np
import pytest

import backend.api.alarm as alarm_mod
from backend.api.source import VideoSourceManager


# 3 颗螺丝固定位置 (归一化, 互不重叠)
SCREWS = [
    (0.10, 0.50, 0.05, 0.05),
    (0.40, 0.50, 0.05, 0.05),
    (0.70, 0.50, 0.05, 0.05),
]
_DUMMY_FRAME = np.zeros((100, 100, 3), dtype=np.uint8)


def _det(label, x, y, w, h, conf=0.9):
    return {'label': label, 'x': x, 'y': y, 'w': w, 'h': h,
            'confidence': conf, 'class_name': label}


def _screws_only():
    return [_det('螺丝', *p) for p in SCREWS]


def _screws_with_action(idx):
    base = _screws_only()
    base.append(_det('打螺丝', *SCREWS[idx]))
    return base


def _empty():
    return []


def _make_config(*, absent_settle=0):
    per_item = {
        'stability_window_frames': 3,
        'stability_iou_threshold': 0.5,
        'lock_count_on_start': True,
        # 无 finish_label / 无 leave 模式 / 无超时 → 唯一结算路径就是换板兜底 (若开启)
        'workpiece_absent_settle_frames': absent_settle,
    }
    return {
        'id': 9998, 'name': 'SWAP_TEST', 'task_type': 'detection',
        'logic_mode': 'per_item',
        'steps_config': [{
            'id': 1, 'label': '打螺丝', 'displayLabel': '打螺丝',
            'enabled': True, 'threshold': 50,
            'per_item': {
                'item_label': '螺丝', 'action_label': '打螺丝',
                'item_tracking_iou': 0.3, 'coverage_iou': 0.3,
                'sustain_frames': 3, 'completion': 'all_covered',
                'min_item_count': 'auto',
            },
        }],
        'events_config': [],
        'pipeline_config': {'per_item': per_item},
    }


def _make_vsm(**kwargs):
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config(_make_config(**kwargs))
    vsm._trigger_event = lambda eid, reason: True   # 避免触碰 end_cycle 等重逻辑
    return vsm


@pytest.fixture
def alarm_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        alarm_mod.alarm_router, 'trigger_alarm',
        lambda event_type, channel_id=0: calls.append((event_type, channel_id)),
    )
    return calls


def _feed(vsm, dets, repeat=1):
    for _ in range(repeat):
        vsm._update_step_stats(list(dets), _DUMMY_FRAME)


def _start_cycle(vsm):
    _feed(vsm, _screws_only(), repeat=4)
    assert vsm._per_item_session.cycle_active
    assert len(vsm._per_item_steps[0].items) == 3


def _cover(vsm, idx, frames=5):
    _feed(vsm, _screws_with_action(idx), repeat=frames)


def _step(vsm):
    return vsm._per_item_steps[0]


# ==================== 1. 关闭时复现 bug: 覆盖态泄漏到新板 ====================
def test_leak_reproduces_when_disabled(alarm_calls):
    """absent_settle=0 (关) + 无其它结算路径 → 上一板永不结算, 覆盖泄漏到新板."""
    vsm = _make_vsm(absent_settle=0)
    _start_cycle(vsm)
    _cover(vsm, 0)                       # 打第 0 颗
    _cover(vsm, 1)                       # 打第 1 颗 (漏第 2 颗)
    assert _step(vsm).covered_count() == 2

    # 换板: 旧板拿走 + 新板放上 (中间没有被检到"拿取结算")
    _feed(vsm, _empty(), repeat=6)       # 工件短暂消失
    _feed(vsm, _screws_only(), repeat=4) # 新板放上

    # 未修复行为: 周期仍是旧的, 旧覆盖态泄漏 → 新板 2 颗仍显示 covered
    assert vsm._per_item_session.cycle_active, "无兜底 → 旧周期一直挂着"
    assert _step(vsm).covered_count() == 2, "复现 bug: 覆盖态泄漏到新板"


# ==================== 2. 开启换板兜底 → 消失确认后结算 + reset ====================
def test_absent_settle_resets_stale_cycle(alarm_calls):
    """工件整体消失达阈值 → 兜底结算 + reset, 周期归零."""
    vsm = _make_vsm(absent_settle=5)
    _start_cycle(vsm)
    _cover(vsm, 0)
    _cover(vsm, 1)
    assert _step(vsm).covered_count() == 2
    assert vsm._per_item_session.cycle_active

    # 工件被拿走: 全标签连续消失 5 帧 → 兜底结算
    _feed(vsm, _empty(), repeat=5)
    assert not vsm._per_item_session.cycle_active, "达阈值应兜底结算, 周期结束"
    assert _step(vsm).items == {}, "结算后个体表应清空"


def test_new_board_starts_fresh_after_swap(alarm_calls):
    """换板兜底结算后, 新板重新锁定, 全部未覆盖 (无泄漏)."""
    vsm = _make_vsm(absent_settle=5)
    _start_cycle(vsm)
    _cover(vsm, 0)
    _cover(vsm, 1)
    assert _step(vsm).covered_count() == 2

    # 换板: 旧板消失 5 帧触发兜底结算 → 新板放上重新开周期
    _feed(vsm, _empty(), repeat=5)
    _feed(vsm, _screws_only(), repeat=4)

    assert vsm._per_item_session.cycle_active, "新板应重新开周期"
    assert len(_step(vsm).items) == 3, "新板重新锁定 3 颗"
    assert _step(vsm).covered_count() == 0, "新板全部未覆盖, 无泄漏 (修复目标)"


# ==================== 3. 手拧单颗时不会误触发 (只有整板消失才算) ====================
def test_partial_occlusion_does_not_trigger(alarm_calls):
    """打螺丝过程画面里始终有工件标签 → absent 计数不累积 → 不误结算."""
    vsm = _make_vsm(absent_settle=5)
    _start_cycle(vsm)
    # 连续打三颗, 全程都有 item 标签在画面 → 绝不触发换板兜底
    for i in range(3):
        _cover(vsm, i, frames=8)
    assert vsm._per_item_session.cycle_active, "全程有工件 → 不应误触发兜底结算"
    assert vsm._per_item_session.workpiece_absent_frames == 0


def test_brief_disappear_below_threshold_no_settle(alarm_calls):
    """工件短暂消失 (少于阈值) 又出现 → 计数清零, 不结算 (防遮挡误判)."""
    vsm = _make_vsm(absent_settle=5)
    _start_cycle(vsm)
    _cover(vsm, 0)
    _feed(vsm, _empty(), repeat=3)       # 短暂消失 3 帧 (< 5)
    _feed(vsm, _screws_only(), repeat=2) # 又出现 → 计数清零
    assert vsm._per_item_session.cycle_active, "未达阈值不应结算"
    assert vsm._per_item_session.workpiece_absent_frames == 0
    assert _step(vsm).items[1].covered, "同板未离场, 已打的螺丝覆盖态保留"
