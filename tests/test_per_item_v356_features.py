"""per_item v3.56 四个新能力单元测试 (西门子装配 demo 需求沉淀).

覆盖:
  F-A action_label 数组 OR: 一行配对 产品[1..N] ⟶ 动作[1..N], 任一动作标签按位置覆盖
  F-B absorb_new_items_sec 自由吸收窗: auto 步骤周期开始后窗口内无上限吸收新个体;
      窗口外不吸收; 默认 0 = 关 (老行为回归)
  F-C warn_uncovered_after_sec 单件超时未覆盖警告: 超时报一次 (借事件响应面,
      不结周期); 已覆盖不报; 每件每周期只报一次
  F-D item_count_counter_name 按件累计计数: 周期判 OK 时把本周期件数累加进指定
      计数器; NG 落账不加
  F-E remediation_event_notify: 进待补态借事件完整响应面 (remind_only=True)

设计同 test_per_item_leave_judgement: 不依赖摄像头/真实推理, 直接喂 detections,
虚拟时钟推进时间, _trigger_event / fire_external_event_response 被 mock 捕获.
"""
from __future__ import annotations

import pytest
import numpy as np

from backend.api.source import VideoSourceManager
import backend.api.source_per_item_mixin as per_item_mixin


# ==================== 测试工具 ====================
# 5 个"产品"位置 (左→右一排)
POS_PRODUCTS = [(0.05 + i * 0.18, 0.40, 0.12, 0.30) for i in range(5)]
_DUMMY_FRAME = np.zeros((100, 100, 3), dtype=np.uint8)


def _det(label, x, y, w, h, conf=0.9):
    return {'label': label, 'x': x, 'y': y, 'w': w, 'h': h,
            'confidence': conf, 'class_name': label}


def _capture_events(vsm):
    captured = []

    def fake_trigger(event_id, reason):
        captured.append((event_id, reason))
        return True

    vsm._trigger_event = fake_trigger
    return captured


def _capture_external(vsm):
    captured = []

    def fake_fire(event_id, reason, source="external", remind_only=False):
        captured.append({'event_id': event_id, 'reason': reason,
                         'source': source, 'remind_only': remind_only})
        return True

    vsm.fire_external_event_response = fake_fire
    return captured


class VirtualClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, sec):
        self.now += sec


@pytest.fixture
def vclock(monkeypatch):
    clock = VirtualClock()
    monkeypatch.setattr(per_item_mixin.time, 'time', clock)
    return clock


def _feed(vsm, dets, repeat=1, vclock=None, frame_dt=0.05):
    for _ in range(repeat):
        vsm._update_step_stats(list(dets), _DUMMY_FRAME)
        if vclock is not None:
            vclock.advance(frame_dt)


# ==================== 配置工厂 ====================
def _cfg(n_products=5, absorb_sec=0.0, warn_sec=0.0, warn_event=0,
         counter_name='', judge_leave=True, ng_hold=True,
         remediation_event_id=0, remediation_notify=False,
         counters_config=None):
    """一行多标签配对: 产品[1..N] ⟶ 打钉[1..N] (中心点覆盖判定)."""
    return {
        'id': 9997, 'name': 'TEST_V356',
        'task_type': 'detection', 'logic_mode': 'per_item',
        'steps_config': [
            {
                'id': 1, 'label': '打钉', 'displayLabel': '打钉校验',
                'enabled': True, 'threshold': 50,
                'per_item': {
                    'item_label': [f'产品{i + 1}' for i in range(n_products)],
                    'action_label': [f'打钉{i + 1}' for i in range(n_products)],
                    'item_tracking_iou': 0.3,
                    'coverage_iou': 0.3,
                    'coverage_use_center': True,
                    'sustain_frames': 2,
                    'warn_uncovered_after_sec': warn_sec,
                    'warn_event_id': warn_event,
                },
            },
        ],
        'events_config': [],
        'counters_config': counters_config or [],
        'pipeline_config': {
            'per_item': {
                'stability_window_frames': 3,
                'stability_iou_threshold': 0.5,
                'item_timeout_seconds': 0,
                'lock_count_on_start': True,
                'finish_label': '',
                'settle_after_all_done_sec': 0,
                'lock_lookahead_seconds': 0,
                'cycle_max_duration_sec': 0,
                'idle_timeout_sec': 0,
                'judge_on_workpiece_leave': judge_leave,
                'leave_confirm_frames': 3,
                'ng_hold_for_remediation': ng_hold,
                'remediation_event_id': remediation_event_id,
                'absorb_new_items_sec': absorb_sec,
                'item_count_counter_name': counter_name,
                'remediation_event_notify': remediation_notify,
            },
        },
    }


def _make_vsm(config):
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config(config)
    return vsm


def _products_frame(idx_list):
    """指定编号的产品在场 (idx 从 0 开始)."""
    return [_det(f'产品{i + 1}', *POS_PRODUCTS[i]) for i in idx_list]


def _actions_frame(idx_list):
    """指定编号的打钉动作在对应产品位置出现."""
    return [_det(f'打钉{i + 1}', *POS_PRODUCTS[i]) for i in idx_list]


# ==================== F-A: action_label 数组 OR ====================
def test_FA_action_label_array_coverage(vclock):
    vsm = _make_vsm(_cfg())
    events = _capture_events(vsm)

    # 5 个产品全在场, 稳定 → 周期开始锁 5
    _feed(vsm, _products_frame(range(5)), repeat=4, vclock=vclock)
    assert vsm._per_item_session.cycle_active
    step = vsm._per_item_steps[0]
    assert len(step.items) == 5

    # 逐个位置用"各自的"动作标签覆盖 (sustain=2)
    _feed(vsm, _products_frame(range(5)) + _actions_frame(range(5)),
          repeat=3, vclock=vclock)
    assert step.completed, "5 个动作标签应各自按位置覆盖对应产品"

    # 离场 → OK
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert any(e[0] == 1 for e in events), f"应判 OK, events={events}"


def test_FA_action_label_array_partial_ng(vclock):
    """只打 4 个位置的动作 → 第 5 件不覆盖 → 步骤不完成."""
    vsm = _make_vsm(_cfg(ng_hold=False))
    events = _capture_events(vsm)
    _feed(vsm, _products_frame(range(5)), repeat=4, vclock=vclock)
    _feed(vsm, _products_frame(range(5)) + _actions_frame(range(4)),
          repeat=3, vclock=vclock)
    step = vsm._per_item_steps[0]
    assert not step.completed
    assert step.covered_count() == 4

    _feed(vsm, [], repeat=5, vclock=vclock)
    assert any(e[0] == 2 for e in events), f"漏 1 件应判 NG, events={events}"


def test_FA_state_export_serialization(vclock):
    """action_label 数组在 state 导出为 list; 单标签仍导出 str (老前端兼容)."""
    vsm = _make_vsm(_cfg(n_products=5))
    st = vsm.get_per_item_state()['steps'][0]
    assert st['action_label'] == [f'打钉{i + 1}' for i in range(5)]

    vsm2 = _make_vsm(_cfg(n_products=1))
    st2 = vsm2.get_per_item_state()['steps'][0]
    assert st2['action_label'] == '打钉1'
    assert st2['item_label'] == '产品1'


# ==================== F-B: absorb_new_items_sec 自由吸收窗 ====================
def test_FB_absorb_window_absorbs_late_items(vclock):
    vsm = _make_vsm(_cfg(absorb_sec=10.0))
    _capture_events(vsm)

    # 先只放 2 个产品 → 稳定 → 周期开始锁 2
    _feed(vsm, _products_frame([0, 1]), repeat=4, vclock=vclock)
    step = vsm._per_item_steps[0]
    assert vsm._per_item_session.cycle_active
    assert len(step.items) == 2

    # 2 秒后工人补放 3 个 (窗口 10s 内) → 应吸收到 5
    vclock.advance(2.0)
    _feed(vsm, _products_frame(range(5)), repeat=2, vclock=vclock)
    assert len(step.items) == 5, "窗口内新位置应被吸收"

    # 窗口外再放新位置 → 不吸收
    vclock.advance(20.0)
    extra = _products_frame(range(5)) + [_det('产品1', 0.05, 0.05, 0.10, 0.10)]
    _feed(vsm, extra, repeat=2, vclock=vclock)
    assert len(step.items) == 5, "窗口关闭后不应再吸收新个体"


def test_FB_default_off_no_absorb(vclock):
    """默认 absorb_new_items_sec=0: 锁定后新位置不吸收 (老行为回归)."""
    vsm = _make_vsm(_cfg(absorb_sec=0.0))
    _capture_events(vsm)
    _feed(vsm, _products_frame([0, 1]), repeat=4, vclock=vclock)
    step = vsm._per_item_steps[0]
    assert len(step.items) == 2
    _feed(vsm, _products_frame(range(5)), repeat=3, vclock=vclock)
    assert len(step.items) == 2, "默认关时不应吸收新个体"


# ==================== F-C: warn_uncovered_after_sec 单件超时警告 ====================
def test_FC_warn_fires_once_per_item(vclock):
    vsm = _make_vsm(_cfg(warn_sec=2.0, warn_event=5))
    _capture_events(vsm)
    warns = _capture_external(vsm)

    _feed(vsm, _products_frame(range(5)), repeat=4, vclock=vclock)
    step = vsm._per_item_steps[0]
    assert vsm._per_item_session.cycle_active

    # 立刻覆盖前 4 件, 第 5 件不覆盖
    _feed(vsm, _products_frame(range(5)) + _actions_frame(range(4)),
          repeat=3, vclock=vclock)
    assert step.covered_count() == 4
    assert not warns, "未到超时不应报警"

    # 推进 3 秒 (> warn_sec=2) → 只有未覆盖的第 5 件报一次警
    _feed(vsm, _products_frame(range(5)), repeat=30, vclock=vclock, frame_dt=0.1)
    assert len(warns) == 1, f"应只报 1 次, 实际 {warns}"
    assert warns[0]['event_id'] == 5
    assert warns[0]['source'] == 'per_item_warn'
    assert '#5' in warns[0]['reason']

    # 继续喂 → 不重复报
    _feed(vsm, _products_frame(range(5)), repeat=10, vclock=vclock, frame_dt=0.1)
    assert len(warns) == 1

    # 补上第 5 件动作 → 覆盖 → 面板恢复 (warned 状态与覆盖共存, 不再报)
    _feed(vsm, _products_frame(range(5)) + _actions_frame([4]),
          repeat=3, vclock=vclock)
    assert step.completed
    assert len(warns) == 1


def test_FC_default_off_no_warn(vclock):
    """warn_sec=0 (默认): 永不报单件超时警告."""
    vsm = _make_vsm(_cfg(warn_sec=0.0, warn_event=5))
    _capture_events(vsm)
    warns = _capture_external(vsm)
    _feed(vsm, _products_frame(range(5)), repeat=4, vclock=vclock)
    _feed(vsm, _products_frame(range(5)), repeat=40, vclock=vclock, frame_dt=0.2)
    assert not warns


# ==================== F-D: item_count_counter_name 按件累计计数 ====================
def _mute_persist(vsm, monkeypatch):
    vsm._persist_counters = lambda: None
    import backend.services.counter_daily as cd
    monkeypatch.setattr(cd, 'record_for_host', lambda *a, **k: None)


def test_FD_ok_settle_adds_item_count(vclock, monkeypatch):
    vsm = _make_vsm(_cfg(
        counter_name='组装数量',
        counters_config=[{'name': '组装数量', 'value': 0}],
    ))
    _capture_events(vsm)
    _mute_persist(vsm, monkeypatch)
    assert vsm.counters.get('组装数量') == 0

    # 第 1 轮: 5 件全覆盖 → 离场 OK → +5
    _feed(vsm, _products_frame(range(5)), repeat=4, vclock=vclock)
    _feed(vsm, _products_frame(range(5)) + _actions_frame(range(5)),
          repeat=3, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert vsm.counters['组装数量'] == 5

    # 第 2 轮: 只有 2 件 → OK → 累计 7
    _feed(vsm, _products_frame([1, 3]), repeat=4, vclock=vclock)
    _feed(vsm, _products_frame([1, 3]) + _actions_frame([1, 3]),
          repeat=3, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert vsm.counters['组装数量'] == 7


def test_FD_ng_settle_does_not_add(vclock, monkeypatch):
    vsm = _make_vsm(_cfg(
        counter_name='组装数量', ng_hold=False,
        counters_config=[{'name': '组装数量', 'value': 0}],
    ))
    _capture_events(vsm)
    _mute_persist(vsm, monkeypatch)
    _feed(vsm, _products_frame(range(5)), repeat=4, vclock=vclock)
    _feed(vsm, _products_frame(range(5)) + _actions_frame(range(4)),
          repeat=3, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)   # 漏 1 → NG 落账
    assert vsm.counters['组装数量'] == 0, "NG 落账不应累计按件计数"


def test_FD_default_off_no_count(vclock, monkeypatch):
    vsm = _make_vsm(_cfg(
        counter_name='',
        counters_config=[{'name': '组装数量', 'value': 0}],
    ))
    _capture_events(vsm)
    _mute_persist(vsm, monkeypatch)
    _feed(vsm, _products_frame(range(5)), repeat=4, vclock=vclock)
    _feed(vsm, _products_frame(range(5)) + _actions_frame(range(5)),
          repeat=3, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert vsm.counters['组装数量'] == 0


# ==================== F-E: remediation_event_notify ====================
def test_FE_remediation_notify_uses_event_response(vclock):
    vsm = _make_vsm(_cfg(remediation_event_id=4, remediation_notify=True))
    _capture_events(vsm)
    fired = _capture_external(vsm)

    _feed(vsm, _products_frame(range(5)), repeat=4, vclock=vclock)
    _feed(vsm, _products_frame(range(5)) + _actions_frame(range(4)),
          repeat=3, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)   # 离场漏 1 → 待补
    assert vsm._per_item_session.awaiting_remediation
    assert len(fired) == 1
    assert fired[0]['event_id'] == 4
    assert fired[0]['source'] == 'per_item_remediation'
    assert fired[0]['remind_only'] is True


def test_FE_default_off_only_alarm_router(vclock, monkeypatch):
    """默认 remediation_event_notify=False: 只点灯不借事件响应面 (老行为回归)."""
    vsm = _make_vsm(_cfg(remediation_event_id=4, remediation_notify=False))
    _capture_events(vsm)
    fired = _capture_external(vsm)
    alarms = []
    import backend.api.alarm as alarm_mod
    monkeypatch.setattr(alarm_mod.alarm_router, 'trigger_alarm',
                        lambda et, channel_id=None: alarms.append(et))

    _feed(vsm, _products_frame(range(5)), repeat=4, vclock=vclock)
    _feed(vsm, _products_frame(range(5)) + _actions_frame(range(4)),
          repeat=3, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert vsm._per_item_session.awaiting_remediation
    assert not fired, "默认关不应借事件响应面"
    assert 'event4' in alarms, f"应走 alarm_router 点灯, 实际 {alarms}"


# ==================== 待补 → 放回补打 → 撤红转 OK + 按件计数 (端到端串联) ====================
def test_full_remediation_flow_with_item_count(vclock, monkeypatch):
    """demo 主剧本缩影: 漏 1 件离场 → 待补 → 放回补打 → OK + 按件计数 +5."""
    vsm = _make_vsm(_cfg(
        counter_name='组装数量', remediation_event_id=4, remediation_notify=True,
        counters_config=[{'name': '组装数量', 'value': 0}],
    ))
    events = _capture_events(vsm)
    _mute_persist(vsm, monkeypatch)
    _capture_external(vsm)

    _feed(vsm, _products_frame(range(5)), repeat=4, vclock=vclock)
    _feed(vsm, _products_frame(range(5)) + _actions_frame([0, 1, 2, 4]),
          repeat=3, vclock=vclock)   # 位置 4 (#4) 不打
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert vsm._per_item_session.awaiting_remediation
    assert vsm.counters['组装数量'] == 0

    # 只放回第 4 个产品并补打 → 撤红转 OK → +5
    _feed(vsm, _products_frame([3]) + _actions_frame([3]),
          repeat=3, vclock=vclock)
    assert any(e[0] == 1 for e in events), f"补满应判 OK, events={events}"
    assert not any(e[0] == 2 for e in events)
    assert not vsm._per_item_session.awaiting_remediation
    assert vsm.counters['组装数量'] == 5
