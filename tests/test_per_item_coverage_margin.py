"""per_item 「动作框扩边救援」coverage_margin — 单元测试 (v3.57).

现场问题 (2026-09 六和螺丝锁付): 电枪"已完成"检测框标注在枪头, 相对螺丝中心
系统性偏移 1~2 个框宽 → 个别螺丝的中心点/IoU 判定一帧都盖不到 → 永远红 →
整周期误 NG (32 颗打完仍 31/32)。

修复: coverage_margin > 0 时, 本帧原始判定没盖到任何"未覆盖"个体 → 动作框
按比例扩边, 只把"最近的一个未覆盖且有资格"的个体计入覆盖:
  - 一枪绝不同时绿两颗 (每动作框只取最近一个)
  - 原始判定已有未覆盖个体进账 → 救援完全不插手
  - 已覆盖个体不重复计
  - 默认 0 = 关, 老项目零差异

与 test_per_item_drag_rereg.py 同套路: 直接构造 VideoSourceManager 逐帧喂 detections。
"""
from __future__ import annotations

import numpy as np
import pytest

import backend.api.alarm as alarm_mod
from backend.api.source import VideoSourceManager


# 3 颗螺丝 (归一化, 互不重叠); 中心分别 x=0.125 / 0.425 / 0.725
SCREWS = [
    (0.10, 0.50, 0.05, 0.05),
    (0.40, 0.50, 0.05, 0.05),
    (0.70, 0.50, 0.05, 0.05),
]
_DUMMY_FRAME = np.zeros((100, 100, 3), dtype=np.uint8)


def _det(label, x, y, w, h, conf=0.9):
    return {'label': label, 'x': x, 'y': y, 'w': w, 'h': h,
            'confidence': conf, 'class_name': label}


def _screws():
    return [_det('螺丝', *p) for p in SCREWS]


def _make_config(margin=0.0):
    return {
        'id': 9996, 'name': 'MARGIN_TEST', 'task_type': 'detection',
        'logic_mode': 'per_item',
        'steps_config': [{
            'id': 1, 'label': '打螺丝', 'displayLabel': '打螺丝',
            'enabled': True, 'threshold': 50,
            'per_item': {
                'item_label': '螺丝', 'action_label': '打螺丝',
                'item_tracking_iou': 0.3, 'coverage_iou': 0.3,
                'coverage_use_center': True,      # 客户现场同款: 中心点判定
                'coverage_margin': margin,
                'sustain_frames': 3, 'completion': 'all_covered',
                'min_item_count': 'auto',
            },
        }],
        'events_config': [],
        'pipeline_config': {'per_item': {
            'stability_window_frames': 3,
            'stability_iou_threshold': 0.5,
            'lock_count_on_start': True,
        }},
    }


def _make_vsm(margin=0.0):
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config(_make_config(margin))
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


def _start(vsm):
    _feed(vsm, _screws(), repeat=4)
    assert vsm._per_item_session.cycle_active
    assert len(_step(vsm).items) == 3


# 偏移动作框: 中心点判定原始必 miss (螺丝1 中心 0.125 不在 0.16~0.21 内)
OFFSET_ACTION = ('打螺丝', 0.16, 0.50, 0.05, 0.05)


# ==================== 1. 默认关: 偏移动作永远盖不到 (老行为保留) ====================
def test_default_off_offset_action_never_covers(alarm_calls):
    vsm = _make_vsm(margin=0.0)
    _start(vsm)
    _feed(vsm, _screws() + [_det(*OFFSET_ACTION)], repeat=8)
    assert _step(vsm).covered_count() == 0, "margin=0 必须维持老行为: 偏移动作盖不到"


# ==================== 2. 开启后: 救援最近的未覆盖个体 ====================
def test_margin_rescues_nearest_uncovered(alarm_calls):
    vsm = _make_vsm(margin=1.0)
    _start(vsm)
    _feed(vsm, _screws() + [_det(*OFFSET_ACTION)], repeat=5)
    step = _step(vsm)
    assert step.items[1].covered, "扩边后应救援到最近的螺丝1"
    assert not step.items[2].covered and not step.items[3].covered, \
        "只救最近一颗, 其余不动"


# ==================== 3. 一枪绝不同时绿两颗 ====================
def test_rescue_covers_only_one_per_action(alarm_calls):
    """动作框落在两颗未覆盖螺丝之间, 扩边后两颗都在框内 → 只绿最近的一颗."""
    vsm = _make_vsm(margin=1.0)
    _start(vsm)
    # 动作框中心 x=0.235, 离螺丝1(0.125)/螺丝2(0.425); 扩边后两颗中心都在框内
    between = _det('打螺丝', 0.16, 0.48, 0.15, 0.09)
    _feed(vsm, _screws() + [between], repeat=5)
    step = _step(vsm)
    covered = [iid for iid, st in step.items.items() if st.covered]
    assert covered == [1], f"只应救最近的螺丝1, 实际 {covered}"


# ==================== 4. 原始判定已进账时救援不插手 ====================
def test_no_rescue_when_original_hit_exists(alarm_calls):
    """动作框正盖住螺丝1 (原始进账), 扩边范围内的螺丝2 不得被救援误绿."""
    vsm = _make_vsm(margin=2.0)
    _start(vsm)
    # 动作框包含螺丝1中心(0.125); margin=2 扩边后 x 范围 -0.26~0.46 覆盖螺丝2 中心(0.425)
    on_s1 = _det('打螺丝', 0.08, 0.46, 0.12, 0.12)
    _feed(vsm, _screws() + [on_s1], repeat=5)
    step = _step(vsm)
    assert step.items[1].covered, "螺丝1 走原始判定正常盖住"
    assert not step.items[2].covered, "原始已进账 → 救援不插手, 螺丝2 不得误绿"


# ==================== 5. 已覆盖个体不重复计, 救援转向下一颗 ====================
def test_rescue_skips_already_covered(alarm_calls):
    """螺丝1 已覆盖后, 同样的偏移动作再来 → 救援跳过它, 落到范围内下一颗未覆盖."""
    vsm = _make_vsm(margin=1.0)
    _start(vsm)
    _feed(vsm, _screws() + [_det(*OFFSET_ACTION)], repeat=5)
    assert _step(vsm).items[1].covered
    # 更大的偏移动作框: 扩边后同时罩住 螺丝1(已覆盖) 和 螺丝2(未覆盖)
    wide = _det('打螺丝', 0.20, 0.48, 0.12, 0.09)
    _feed(vsm, _screws() + [wide], repeat=5)
    step = _step(vsm)
    assert step.items[2].covered, "救援应跳过已覆盖的螺丝1, 救到螺丝2"


# ==================== 6. 救援同样吃 sustain_frames 门槛 ====================
def test_rescue_respects_sustain_frames(alarm_calls):
    vsm = _make_vsm(margin=1.0)
    _start(vsm)
    _feed(vsm, _screws() + [_det(*OFFSET_ACTION)], repeat=2)  # sustain=3, 只喂 2 帧
    assert not _step(vsm).items[1].covered, "未达 sustain 帧数不得翻转 covered"
    _feed(vsm, _screws() + [_det(*OFFSET_ACTION)], repeat=1)
    assert _step(vsm).items[1].covered, "第 3 帧达标翻转"
