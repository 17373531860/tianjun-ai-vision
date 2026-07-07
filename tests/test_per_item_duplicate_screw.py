"""per_item 「防止重复打同一颗螺丝」— 单元测试.

覆盖需求:
  1. 已打过的螺丝, 螺丝刀移开后又压回来重打 → 报警灯 (复用 event2) + 黄条警告 (last_warning)
  2. 待补态 (离场判 NG + ng_hold_for_remediation) 下, 回头重打已打过的螺丝同样报警;
     补打漏掉的螺丝 (未覆盖) 不报警
  3. 正常逐颗打 (打完就移走) 不误报
  4. duplicate_screw_alarm 关闭 → 零行为 (老项目零差异)
  5. duplicate_alarm_interval_sec 报警节流

直接构造 VideoSourceManager, 逐帧喂 detections, 与 test_per_item_smoke.py 同套路.
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


def _make_config(*, duplicate=True, dup_sustain=2, dup_release=3, dup_interval=0.0,
                 display_sec=0.0, leave_mode=False):
    per_item = {
        'stability_window_frames': 3,
        'stability_iou_threshold': 0.5,
        'lock_count_on_start': True,
        'duplicate_screw_alarm': duplicate,
        'duplicate_sustain_frames': dup_sustain,
        'duplicate_release_frames': dup_release,
        'duplicate_alarm_interval_sec': dup_interval,
        'duplicate_warning_display_sec': display_sec,
    }
    if leave_mode:
        per_item.update({
            'judge_on_workpiece_leave': True,
            'leave_confirm_frames': 3,
            'ng_hold_for_remediation': True,
        })
    return {
        'id': 9999, 'name': 'DUP_TEST', 'task_type': 'detection',
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
    """稳定窗口 → 周期开始, 锁定 3 颗."""
    _feed(vsm, _screws_only(), repeat=4)
    assert vsm._per_item_session.cycle_active
    assert len(vsm._per_item_steps[0].items) == 3


def _cover(vsm, idx, frames=5, leave=4):
    """打第 idx 颗 frames 帧, 再移开 leave 帧 (>= dup_release 才真正打开 released 门)."""
    _feed(vsm, _screws_with_action(idx), repeat=frames)
    _feed(vsm, _screws_only(), repeat=leave)


def _step(vsm):
    return vsm._per_item_steps[0]


# ==================== 1. 正常逐颗打不误报 ====================
def test_normal_screwing_no_warning(alarm_calls):
    vsm = _make_vsm()
    _start_cycle(vsm)
    for i in range(3):
        _cover(vsm, i)
    step = _step(vsm)
    assert step.covered_count() == 3
    assert all(st.redup_count == 0 for st in step.items.values())
    assert getattr(vsm, '_per_item_last_warning', None) is None
    assert alarm_calls == []


# ==================== 2. 打完移开再重打 → 报警 ====================
def test_rescrew_after_leave_triggers_warning(alarm_calls):
    vsm = _make_vsm(dup_sustain=2, dup_interval=0.0)
    _start_cycle(vsm)
    _cover(vsm, 0)                       # 第 0 颗打完 + 移开 (released)
    assert list(_step(vsm).items.values())[0].covered

    _feed(vsm, _screws_with_action(0), repeat=3)   # 回头重打第 0 颗

    warn = getattr(vsm, '_per_item_last_warning', None)
    assert warn is not None, "重复打应设置 last_warning"
    assert 0 + 1 in warn['item_ids'] or 1 in warn['item_ids']
    assert _step(vsm).items[1].redup_count >= 1
    assert alarm_calls, "重复打应触发报警灯"
    assert alarm_calls[0][0] == 'event2'


# ==================== 2b. 拔枪卡顿/单帧闪断不误报 (本次修复的 bug) ====================
def test_withdrawal_hesitation_no_false_warning(alarm_calls):
    """拔螺丝枪时工人卡顿 → 动作框短暂离开一两帧又压回 (不足 dup_release) → 不算重复打."""
    vsm = _make_vsm(dup_sustain=2, dup_release=3, dup_interval=0.0)
    _start_cycle(vsm)
    _feed(vsm, _screws_with_action(0), repeat=5)   # 打第 0 颗 → covered

    # 拔枪卡顿: 离开 2 帧 (< dup_release=3) → 未真正移开
    _feed(vsm, _screws_only(), repeat=2)
    # 卡顿中又压回来 4 帧 (拔枪尾程) — 因为门没开, 不该判重复
    _feed(vsm, _screws_with_action(0), repeat=4)

    step = _step(vsm)
    assert step.items[1].redup_count == 0, "拔枪卡顿不应误判重复打"
    assert getattr(vsm, '_per_item_last_warning', None) is None
    assert alarm_calls == []

    # 对照: 真正移开 (>= dup_release) 后再压回来 → 才判重复
    _feed(vsm, _screws_only(), repeat=3)
    _feed(vsm, _screws_with_action(0), repeat=3)
    assert step.items[1].redup_count >= 1, "真正移开后回头重打仍应判重复"
    assert alarm_calls, "真正移开后回头重打应触发报警灯"


# ==================== 3. 待补态回头重打已打的螺丝 → 报警 ====================
def test_rescrew_during_remediation(alarm_calls):
    vsm = _make_vsm(leave_mode=True, dup_sustain=2, dup_interval=0.0)
    _start_cycle(vsm)
    _cover(vsm, 0)                       # 打第 0 颗
    _cover(vsm, 1)                       # 打第 1 颗, 漏第 2 颗

    _feed(vsm, _empty(), repeat=4)       # 工件离场 → 判 NG 进待补态
    assert vsm._per_item_session.awaiting_remediation

    alarm_calls.clear()
    # 工件放回, 但工人错误地回头重打已打的第 0 颗 (而非补打漏的第 2 颗)
    _feed(vsm, _screws_only(), repeat=2)          # released 门 (含离场帧其实已开)
    _feed(vsm, _screws_with_action(0), repeat=3)  # 重打第 0 颗

    assert _step(vsm).items[1].redup_count >= 1, "待补态重打已打螺丝应判重复"
    assert alarm_calls, "待补态重复打应触发报警灯"
    assert vsm._per_item_session.awaiting_remediation, "重复打不应结束待补态"


def test_supplement_missing_during_remediation_no_warning(alarm_calls):
    """待补态补打漏掉的螺丝 (未覆盖) 是合法操作, 不报重复打."""
    vsm = _make_vsm(leave_mode=True, dup_sustain=2, dup_interval=0.0)
    _start_cycle(vsm)
    _cover(vsm, 0)
    _cover(vsm, 1)                       # 漏第 2 颗
    _feed(vsm, _empty(), repeat=4)
    assert vsm._per_item_session.awaiting_remediation

    alarm_calls.clear()
    vsm._per_item_last_warning = None
    _feed(vsm, _screws_only(), repeat=2)
    _feed(vsm, _screws_with_action(2), repeat=5)   # 补打漏掉的第 2 颗

    # 补打漏件 → 全部覆盖 → 撤红转 OK 结算 (个体表随之 reset), 全程不应判重复打
    assert getattr(vsm, '_per_item_last_warning', None) is None, "补打漏件不应判重复"
    assert alarm_calls == [], "补打漏件不应触发报警灯"
    assert not vsm._per_item_session.awaiting_remediation, "补满后应撤红转 OK"


# ==================== 3b. 提示横幅按配置时间自动撤下 ====================
def test_warning_auto_expires(alarm_calls):
    """duplicate_warning_display_sec 到期 → last_warning 自动清空 (报警框存在时间可调)."""
    import time
    vsm = _make_vsm(dup_sustain=2, dup_release=3, dup_interval=0.0, display_sec=0.2)
    _start_cycle(vsm)
    _cover(vsm, 0)
    _feed(vsm, _screws_with_action(0), repeat=3)   # 触发重复打 → 横幅出现
    assert getattr(vsm, '_per_item_last_warning', None) is not None

    time.sleep(0.3)                                # 超过 display_sec=0.2
    _feed(vsm, _screws_only(), repeat=1)           # 再来一帧 → 到期撤下
    assert getattr(vsm, '_per_item_last_warning', None) is None, "到期后横幅应自动清空"


def test_warning_persists_when_display_zero(alarm_calls):
    """display_sec=0 → 不自动撤 (维持到周期结束/下次刷新)."""
    import time
    vsm = _make_vsm(dup_sustain=2, dup_release=3, dup_interval=0.0, display_sec=0.0)
    _start_cycle(vsm)
    _cover(vsm, 0)
    _feed(vsm, _screws_with_action(0), repeat=3)
    assert getattr(vsm, '_per_item_last_warning', None) is not None
    time.sleep(0.3)
    _feed(vsm, _screws_only(), repeat=1)
    assert getattr(vsm, '_per_item_last_warning', None) is not None, "display=0 不应自动撤"


# ==================== 4. 开关关闭 → 零行为 ====================
def test_toggle_off_zero_behavior(alarm_calls):
    vsm = _make_vsm(duplicate=False)
    _start_cycle(vsm)
    _cover(vsm, 0)
    _feed(vsm, _screws_with_action(0), repeat=6)   # 疯狂重打
    step = _step(vsm)
    assert all(st.redup_count == 0 for st in step.items.values())
    assert getattr(vsm, '_per_item_last_warning', None) is None
    assert alarm_calls == []


# ==================== 5. 报警节流 ====================
def test_alarm_throttle(alarm_calls):
    """interval 很大时, 短时间内多次重打只响一次报警灯, 但警告横幅持续刷新."""
    vsm = _make_vsm(dup_sustain=2, dup_release=3, dup_interval=100.0)
    _start_cycle(vsm)
    _cover(vsm, 0)
    _feed(vsm, _screws_with_action(0), repeat=3)   # 第 1 次重打 → 响
    n_after_first = len(alarm_calls)
    _feed(vsm, _screws_only(), repeat=4)           # 真正移开 (>= dup_release) 重开 released 门
    _feed(vsm, _screws_with_action(0), repeat=3)   # 第 2 次重打 (间隔内)

    assert n_after_first == 1
    assert len(alarm_calls) == 1, "节流间隔内报警灯只响一次"
    assert _step(vsm).items[1].redup_count >= 2, "但重复次数应累加"
