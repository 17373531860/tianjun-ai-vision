"""per_item 「整板快速拖动重配准兜底」— 单元测试 (v3.57).

现场 bug (2026-09 六和螺丝锁付): 滚筒线上工人拖动工装, 单帧位移达 box 尺寸数倍
→ 整板位移校准 (IoU 配对) 全断 → 逻辑框滞留原地成幽灵 → 之后整盘永远红。

修复: 关联大面积崩溃连续 4 帧 且 检测数量仍在 → 质心平移初值 + 两轮最近邻
中位位移细化 (mini-ICP) + 一对一验证 ≥60% 槽位重新对上 → 提交刚性平移,
覆盖状态随逻辑 ID 完整保留。验证不过不动状态。

开关: pipeline_config.per_item.board_rereg_enabled (默认 False = 关, 老项目
零差异)。滚筒线/可滑动工装现场开启; 固定工装现场保持关闭。

直接构造 VideoSourceManager, 逐帧喂 detections, 与 test_per_item_board_swap.py 同套路。
"""
from __future__ import annotations

import numpy as np
import pytest

import backend.api.alarm as alarm_mod
from backend.api.source import VideoSourceManager


# 6 颗螺丝固定位置 (归一化, 两行三列, 互不重叠)
SCREWS = [
    (0.10, 0.30, 0.05, 0.05),
    (0.40, 0.30, 0.05, 0.05),
    (0.70, 0.30, 0.05, 0.05),
    (0.10, 0.60, 0.05, 0.05),
    (0.40, 0.60, 0.05, 0.05),
    (0.70, 0.60, 0.05, 0.05),
]
_DUMMY_FRAME = np.zeros((100, 100, 3), dtype=np.uint8)


def _det(label, x, y, w, h, conf=0.9):
    return {'label': label, 'x': x, 'y': y, 'w': w, 'h': h,
            'confidence': conf, 'class_name': label}


def _screws(offset=(0.0, 0.0)):
    dx, dy = offset
    return [_det('螺丝', x + dx, y + dy, w, h) for x, y, w, h in SCREWS]


def _screws_with_action(idx, offset=(0.0, 0.0)):
    base = _screws(offset)
    dx, dy = offset
    x, y, w, h = SCREWS[idx]
    base.append(_det('打螺丝', x + dx, y + dy, w, h))
    return base


def _make_config(rereg=True):
    return {
        'id': 9997, 'name': 'DRAG_REREG_TEST', 'task_type': 'detection',
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
        'pipeline_config': {'per_item': {
            'stability_window_frames': 3,
            'stability_iou_threshold': 0.5,
            'lock_count_on_start': True,
            'board_rereg_enabled': rereg,
        }},
    }


def _make_vsm(rereg=True):
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config(_make_config(rereg))
    vsm._trigger_event = lambda eid, reason: True
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


def _step(vsm):
    return vsm._per_item_steps[0]


def _start_and_cover_two(vsm):
    """开周期 + 打完前 2 颗 (covered id 1/2)。"""
    _feed(vsm, _screws(), repeat=4)
    assert vsm._per_item_session.cycle_active
    assert len(_step(vsm).items) == 6
    for idx in (0, 1):
        _feed(vsm, _screws_with_action(idx), repeat=5)
    assert _step(vsm).covered_count() == 2


# ==================== 1. 快速拖动 → 重配准恢复关联 + 覆盖态保留 ====================
def test_fast_drag_recovers_via_rereg(alarm_calls):
    """瞬移 0.15 (3 倍 box 宽) → 关联全断 → 4 帧后重配准 → 恢复关联且覆盖态保留."""
    vsm = _make_vsm()
    _start_and_cover_two(vsm)

    # 工装被瞬间拖走 0.15 (单帧位移远超 IoU 跟踪能力)
    _feed(vsm, _screws(offset=(0.15, 0.02)), repeat=3)
    step = _step(vsm)
    assert sum(1 for st in step.items.values() if st.associated) == 0, \
        "拖动瞬间关联应全断 (前置条件)"

    # 第 4 帧崩溃计数达标 → 重配准 → 关联恢复
    _feed(vsm, _screws(offset=(0.15, 0.02)), repeat=2)
    assert sum(1 for st in step.items.values() if st.associated) == 6, \
        "重配准后全部槽位应重新关联"
    covered_ids = {iid for iid, st in step.items.items() if st.covered}
    assert covered_ids == {1, 2}, "覆盖状态必须随逻辑 ID 保留, 不泄漏不丢失"

    # 拖动后继续打第 3 颗 (新位置) → 正常盖绿
    _feed(vsm, _screws_with_action(2, offset=(0.15, 0.02)), repeat=5)
    assert step.covered_count() == 3, "重配准后覆盖判定继续正常工作"


# ==================== 1b. 开关默认关: 快拖后不重配准 (老行为零差异) ====================
def test_default_off_no_rereg(alarm_calls):
    """board_rereg_enabled 缺省/False → 快拖后逻辑框滞留原地 (老行为), 不得重配准."""
    vsm = _make_vsm(rereg=False)
    _start_and_cover_two(vsm)
    before = {iid: st.bbox for iid, st in _step(vsm).items.items()}

    _feed(vsm, _screws(offset=(0.15, 0.02)), repeat=10)
    step = _step(vsm)
    assert sum(1 for st in step.items.values() if st.associated) == 0, \
        "开关关闭时关联应保持崩溃状态 (老行为)"
    after = {iid: st.bbox for iid, st in step.items.items()}
    assert before == after, "开关关闭时逻辑框必须原地不动 (零差异)"


# ==================== 2. 工件被拿走 (无检测) 不误触发 ====================
def test_removal_does_not_trigger_rereg(alarm_calls):
    """工件整体拿走 → 检测数不足 50% → 崩溃计数不累积, 不动逻辑框."""
    vsm = _make_vsm()
    _start_and_cover_two(vsm)
    before = {iid: st.bbox for iid, st in _step(vsm).items.items()}

    _feed(vsm, [], repeat=10)  # 全部消失
    after = {iid: st.bbox for iid, st in _step(vsm).items.items()}
    assert before == after, "无检测时不得触发重配准 (那是拿走/遮挡, 不是拖动)"
    assert _step(vsm)._rereg_lost_streak == 0


# ==================== 3. 位置对不上 (换了不同布局的板) 验证拒绝 ====================
def test_layout_mismatch_rejected(alarm_calls):
    """检测数量够但空间布局对不上 → 验证 <60% → 拒绝提交, 逻辑框不动."""
    vsm = _make_vsm()
    _start_and_cover_two(vsm)
    before = {iid: st.bbox for iid, st in _step(vsm).items.items()}

    # 6 个检测但挤成一列 (与 2×3 网格刚性平移后仍对不上); 放在常规关联
    # 容差 (1.5×box) 之外, 单独考核重配准的验证门
    weird = [_det('螺丝', 0.90, 0.05 + i * 0.13, 0.05, 0.05) for i in range(6)]
    _feed(vsm, weird, repeat=8)
    after = {iid: st.bbox for iid, st in _step(vsm).items.items()}
    assert before == after, "布局验证不过必须不动任何状态"


# ==================== 4. 慢漂移走原校准路径, 不触发重配准 ====================
def test_slow_drift_uses_translation_not_rereg(alarm_calls):
    """每帧 0.005 慢漂移 → IoU 配对健在, 整板位移校准跟住 → 崩溃计数恒 0."""
    vsm = _make_vsm()
    _start_and_cover_two(vsm)
    step = _step(vsm)

    for i in range(1, 11):
        _feed(vsm, _screws(offset=(0.005 * i, 0.0)))
        assert step._rereg_lost_streak == 0, "慢漂移不应累积崩溃计数"
    assert sum(1 for st in step.items.values() if st.associated) == 6
    covered_ids = {iid for iid, st in step.items.items() if st.covered}
    assert covered_ids == {1, 2}, "慢漂移全程覆盖态保留 (老路径回归)"
