"""per_item 工件离场快照判定 + 待补/待确认态 单元测试 (打螺丝漏打场景).

覆盖 RFC `docs/逐件漏打检测_实时看板方案_内部RFC.md` 测试矩阵 T-A~T-G:
  T-A 18 颗全打 → 离场 → OK
  T-B 漏 1 颗 → 离场 → NG 待补态 (不落账, 等补打/确认)
  T-C 待补态 + 工件放回补满 → 撤红转 OK (无 NG 计数)
  T-D 待补态 + 人工确认 NG → 按真实覆盖落账 NG
  T-E 打到一半手遮全部螺丝 < leave_confirm_frames → 不误判离场
  T-F 工件长时间在场无动作 → 不误结算 (不再依赖停手)
  T-G 老项目 (judge_on_workpiece_leave=False) → 行为完全不变 (回归)

设计原则同 test_per_item_v39_features: 不依赖摄像头/真实推理, 直接喂 detections,
用虚拟时钟推进时间, _trigger_event 被 mock 捕获事件.
"""
from __future__ import annotations

import pytest
import numpy as np

from backend.api.source import VideoSourceManager
import backend.api.source_per_item_mixin as per_item_mixin


# ==================== 测试工具 ====================
POS_5N_14 = [(0.05 + i * 0.06, 0.50, 0.04, 0.04) for i in range(14)]
POS_7N_4 = [(0.10 + i * 0.15, 0.20, 0.05, 0.05) for i in range(4)]
_DUMMY_FRAME = np.zeros((100, 100, 3), dtype=np.uint8)


def _det(label, x, y, w, h, conf=0.85):
    return {'label': label, 'x': x, 'y': y, 'w': w, 'h': h,
            'confidence': conf, 'class_name': label}


def _capture_events(vsm):
    captured = []

    def fake_trigger(event_id, reason):
        captured.append((event_id, reason))
        return True

    vsm._trigger_event = fake_trigger
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


# ==================== 配置工厂 ====================
def _cfg_leave(judge=True, ng_hold=True, leave_confirm=3,
               remediation_timeout=0, cycle_max=0,
               settle_after_all_done=0, remediation_event_id=0,
               finish_label='',
               finish_requires_no_items=False,
               remediation_takeaway_ng=False,
               remediation_alarm_mode='once',
               remediation_alarm_interval_sec=2.0):
    """2 步 (5N×14 + 7N×4, 涂黑已取消) + 工件离场判定配置."""
    return {
        'id': 9998, 'name': 'TEST_LEAVE',
        'task_type': 'detection', 'logic_mode': 'per_item',
        'steps_config': [
            {
                'id': 1, 'label': '扭5N', 'displayLabel': '扭5N螺丝',
                'enabled': True, 'threshold': 50,
                'per_item': {
                    'item_label': '5N螺丝', 'action_label': '扭5N螺丝',
                    'expected_count': 14, 'item_tracking_iou': 0.3,
                    'coverage_iou': 0.3, 'sustain_frames': 2,
                },
            },
            {
                'id': 2, 'label': '扭7N', 'displayLabel': '扭7N螺丝',
                'enabled': True, 'threshold': 50,
                'per_item': {
                    'item_label': '7N螺丝', 'action_label': '扭7N螺丝',
                    'expected_count': 4, 'item_tracking_iou': 0.3,
                    'coverage_iou': 0.3, 'sustain_frames': 2,
                },
            },
        ],
        'events_config': [],
        'pipeline_config': {
            'per_item': {
                'stability_window_frames': 3,
                'stability_iou_threshold': 0.5,
                'stability_count_tolerance': 1,
                'stability_count_ratio': 0.85,
                'item_timeout_seconds': 0,
                'lock_count_on_start': True,
                'finish_label': finish_label,
                'finish_requires_no_items': finish_requires_no_items,
                'finish_sustain_frames': 3,
                'settle_after_all_done_sec': settle_after_all_done,
                'lock_lookahead_seconds': 0,
                'cycle_max_duration_sec': cycle_max,
                'idle_timeout_sec': 0,
                # 本特性
                'judge_on_workpiece_leave': judge,
                'leave_confirm_frames': leave_confirm,
                'ng_hold_for_remediation': ng_hold,
                'remediation_timeout_sec': remediation_timeout,
                'remediation_event_id': remediation_event_id,
                'remediation_takeaway_ng': remediation_takeaway_ng,
                'remediation_alarm_mode': remediation_alarm_mode,
                'remediation_alarm_interval_sec': remediation_alarm_interval_sec,
            },
        },
    }


def _finish_det():
    """拿取结算动作标签 (随便放个位置, 不参与覆盖)."""
    return _det('拿取结算', 0.5, 0.9, 0.1, 0.1)


def _make_vsm(config):
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config(config)
    return vsm


def _items_frame():
    """完整工件在场: 14 颗 5N + 4 颗 7N (仅工件标签, 无动作)."""
    return ([_det('5N螺丝', *p) for p in POS_5N_14]
            + [_det('7N螺丝', *p) for p in POS_7N_4])


def _cover_frame(n5=14, n7=4):
    """工件在场 + 动作框覆盖前 n5 颗 5N / 前 n7 颗 7N."""
    dets = _items_frame()
    dets += [_det('扭5N螺丝', *POS_5N_14[i]) for i in range(n5)]
    dets += [_det('扭7N螺丝', *POS_7N_4[i]) for i in range(n7)]
    return dets


def _feed(vsm, dets, repeat=1, vclock=None, frame_dt=0.05):
    for _ in range(repeat):
        vsm._update_step_stats(list(dets), _DUMMY_FRAME)
        if vclock is not None:
            vclock.advance(frame_dt)


def _start_cycle(vsm, vclock):
    """喂稳定窗口帧锁定 18 颗, 进入周期."""
    _feed(vsm, _items_frame(), repeat=4, vclock=vclock)
    assert vsm._per_item_session.cycle_active, "周期应已开始"


# ==================== T-A: 全打 → 离场 → OK ====================
def test_TA_all_done_leave_ok(vclock):
    vsm = _make_vsm(_cfg_leave())
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)

    # 全覆盖 (sustain=2, 多喂几帧确保翻 covered)
    _feed(vsm, _cover_frame(14, 4), repeat=5, vclock=vclock)
    assert all(s.completed for s in vsm._per_item_steps), "应全部覆盖完成"

    # 工件离场: 空帧持续 >= leave_confirm_frames(3)
    _feed(vsm, [], repeat=5, vclock=vclock)

    assert any(e[0] == 1 for e in events), f"离场应判 OK, events={events}"
    assert not vsm._per_item_session.cycle_active
    assert not vsm._per_item_session.awaiting_remediation


# ==================== T-B: 漏 1 → 离场 → NG 待补态 ====================
def test_TB_missing_one_enters_await(vclock):
    vsm = _make_vsm(_cfg_leave())
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)

    # 只覆盖 13 颗 5N (漏第 14 颗) + 4 颗 7N
    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)
    assert not vsm._per_item_steps[0].completed, "5N 步骤不应完成 (漏 1)"

    # 离场
    _feed(vsm, [], repeat=5, vclock=vclock)

    # 进入待补态: 不落账 (无任何 event), awaiting=True, 有漏点详情
    assert vsm._per_item_session.awaiting_remediation, "应进入待补态"
    assert not events, f"待补态不应落账任何事件, 实际 {events}"
    nd = vsm._per_item_last_ng_detail
    assert nd and nd.get('missing_total') == 1, f"应记 1 件漏点, 实际 {nd}"
    assert nd.get('awaiting_remediation') is True


# ==================== T-C: 待补 + 放回补满 → 撤红转 OK ====================
def test_TC_remediation_supplement_to_ok(vclock):
    vsm = _make_vsm(_cfg_leave())
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert vsm._per_item_session.awaiting_remediation

    # 工件放回 + 补打第 14 颗 (现在覆盖全部 14+4)
    # 注: 补满判 OK 后会 reset, 继续喂整帧会重新锁定下一个新周期 (正确行为),
    # 所以这里不断言 cycle_active, 只验证"补满判了 OK 且没误计 NG, 待补态已清".
    _feed(vsm, _cover_frame(14, 4), repeat=4, vclock=vclock)

    assert any(e[0] == 1 for e in events), f"补满应判 OK, events={events}"
    assert not any(e[0] == 2 for e in events), "补打成功不应计 NG"
    assert not vsm._per_item_session.awaiting_remediation


# ==================== T-D: 待补 + 人工确认 → NG 落账 ====================
def test_TD_confirm_ng_settles_ng(vclock):
    vsm = _make_vsm(_cfg_leave())
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert vsm._per_item_session.awaiting_remediation

    ret = vsm.per_item_confirm_ng()
    assert ret['ok'] is True
    assert any(e[0] == 2 for e in events), f"人工确认应落账 NG, events={events}"
    assert not vsm._per_item_session.awaiting_remediation
    assert not vsm._per_item_session.cycle_active


# ==================== T-E: 短暂遮挡不误判离场 ====================
def test_TE_short_obscuration_not_leave(vclock):
    vsm = _make_vsm(_cfg_leave(leave_confirm=10))
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(14, 4), repeat=5, vclock=vclock)

    # 手挡住全部螺丝 8 帧 (< leave_confirm=10) → 不应判离场
    _feed(vsm, [], repeat=8, vclock=vclock)
    assert vsm._per_item_session.cycle_active, "短暂遮挡不应误判离场"
    assert not events, "不应有任何结算事件"

    # 工件重现 → 计数清零
    _feed(vsm, _items_frame(), repeat=2, vclock=vclock)
    assert vsm._per_item_session.leave_consec_frames == 0


# ==================== T-F: 长时间在场无动作不误结算 ====================
def test_TF_long_idle_with_workpiece_no_settle(vclock):
    # 离场模式 + cycle_max=0 (关超时兜底) → 只要工件在场就绝不结算
    vsm = _make_vsm(_cfg_leave(cycle_max=0))
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)

    # 工件在场, 工人停手很久 (复刻视频里 54s 大停顿), 每秒一帧只有工件无动作
    for _ in range(60):
        _feed(vsm, _items_frame(), repeat=1, vclock=vclock, frame_dt=1.0)

    assert vsm._per_item_session.cycle_active, "工件在场期间不应因停手误结算"
    assert not events, f"不应有结算事件, 实际 {events}"


# ==== 红灯事件化: 待补态触发可配事件的报警 (不写死 event2) ====
def test_remediation_fires_configured_event_alarm(vclock, monkeypatch):
    """进待补态时触发 remediation_event_id 指定事件的报警 (灯由事件配置决定)."""
    calls = []
    import backend.api.alarm as alarm_mod
    monkeypatch.setattr(alarm_mod.alarm_router, 'trigger_alarm',
                        lambda event_type, channel_id=0: calls.append((event_type, channel_id)))

    # 配 remediation_event_id=7 → 待补态应触发 event7 报警
    vsm = _make_vsm(_cfg_leave(remediation_event_id=7))
    _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)   # 漏 1
    _feed(vsm, [], repeat=5, vclock=vclock)                    # 离场

    assert vsm._per_item_session.awaiting_remediation
    assert ('event7', 0) in calls, f"待补态应触发 event7 报警, 实际 {calls}"


def test_remediation_event_zero_no_alarm(vclock, monkeypatch):
    """remediation_event_id=0 (默认) → 不主动触发任何报警."""
    calls = []
    import backend.api.alarm as alarm_mod
    monkeypatch.setattr(alarm_mod.alarm_router, 'trigger_alarm',
                        lambda event_type, channel_id=0: calls.append((event_type, channel_id)))

    vsm = _make_vsm(_cfg_leave(remediation_event_id=0))
    _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)

    assert vsm._per_item_session.awaiting_remediation
    assert not calls, f"未配事件号不应触发报警, 实际 {calls}"


# ==================== T-G: 老项目 (离场模式关) 行为不变 ====================
def test_TG_legacy_settle_after_all_done_unchanged(vclock):
    # judge_on_workpiece_leave=False + settle_after_all_done_sec=1 → 走老 OK 路径
    vsm = _make_vsm(_cfg_leave(judge=False, ng_hold=False, settle_after_all_done=1.0))
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)

    # 全覆盖
    _feed(vsm, _cover_frame(14, 4), repeat=5, vclock=vclock)
    assert all(s.completed for s in vsm._per_item_steps)

    # 保持 > 1s → 老路径自动 OK 结算
    _feed(vsm, _cover_frame(14, 4), repeat=1, vclock=vclock, frame_dt=1.5)
    _feed(vsm, _cover_frame(14, 4), repeat=1, vclock=vclock, frame_dt=0.1)

    assert any(e[0] == 1 for e in events), f"老 settle_after_all_done 路径应判 OK, events={events}"
    assert not vsm._per_item_session.cycle_active


# ════════════════════════════════════════════════════════════════
# 新增可选特性: 双条件离场 / 红灯内取件NG / 待补持续报警
# ════════════════════════════════════════════════════════════════

# ── 双条件离场判定 (复用 finish_requires_no_items 开关) ──
def test_dual_gate_items_gone_without_finish_no_settle(vclock):
    """双条件开启 + 工件消失但本周期从未出现拿取结算动作 → 不判定 (防瞬时遮挡误判)."""
    vsm = _make_vsm(_cfg_leave(finish_label='拿取结算',
                               finish_requires_no_items=True, leave_confirm=3))
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(14, 4), repeat=5, vclock=vclock)   # 全覆盖

    # 工件消失很久, 但从未出现拿取结算动作 → 双条件不满足, 不结算
    _feed(vsm, [], repeat=20, vclock=vclock)
    assert vsm._per_item_session.cycle_active, "无拿取动作时离场不应触发判定"
    assert not events, f"双条件未满足不应落账, 实际 {events}"


def test_dual_gate_finish_then_leave_settles_ok(vclock):
    """双条件开启 + 本周期出现过拿取结算动作 + 工件离场 → 判定 OK."""
    vsm = _make_vsm(_cfg_leave(finish_label='拿取结算',
                               finish_requires_no_items=True, leave_confirm=3))
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(14, 4), repeat=5, vclock=vclock)   # 全覆盖

    # 拿取动作出现 (latch), 同时工件还在画面 — 此刻不算离场
    _feed(vsm, _cover_frame(14, 4) + [_finish_det()], repeat=3, vclock=vclock)
    assert vsm._per_item_session.leave_finish_seen, "应已记录本周期出现过拿取动作"
    assert vsm._per_item_session.cycle_active, "工件仍在画面, 不应结算"

    # 工件真离场 → 双条件齐 → 判 OK
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert any(e[0] == 1 for e in events), f"双条件齐应判 OK, events={events}"
    assert not vsm._per_item_session.cycle_active


# ── 红灯内取件算 NG (remediation_takeaway_ng) ──
def test_takeaway_in_await_settles_ng(vclock):
    """待补态(红灯)期间再次拿取且未补满 → 自动按 NG 落账."""
    vsm = _make_vsm(_cfg_leave(finish_label='拿取结算', remediation_takeaway_ng=True))
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)   # 漏 1
    _feed(vsm, [], repeat=5, vclock=vclock)                    # 离场 → 待补
    assert vsm._per_item_session.awaiting_remediation

    # 红灯内: 工人没补 (仍 13 颗), 再次拿取 → 自动 NG
    _feed(vsm, _cover_frame(13, 4) + [_finish_det()], repeat=2, vclock=vclock)
    assert any(e[0] == 2 for e in events), f"红灯内取件应落账 NG, events={events}"
    assert not vsm._per_item_session.awaiting_remediation


def test_takeaway_off_no_settle_in_await(vclock):
    """开关关 (默认): 待补态再次拿取不自动落账, 仍停在待补."""
    vsm = _make_vsm(_cfg_leave(finish_label='拿取结算', remediation_takeaway_ng=False))
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert vsm._per_item_session.awaiting_remediation

    _feed(vsm, _cover_frame(13, 4) + [_finish_det()], repeat=2, vclock=vclock)
    assert not events, f"开关关时不应自动落账, 实际 {events}"
    assert vsm._per_item_session.awaiting_remediation, "应仍停在待补态"


# ── 待补报警单次 / 持续 (remediation_alarm_mode) ──
def _patch_alarm(monkeypatch):
    calls = []
    import backend.api.alarm as alarm_mod
    monkeypatch.setattr(alarm_mod.alarm_router, 'trigger_alarm',
                        lambda event_type, channel_id=0: calls.append((event_type, channel_id)))
    return calls


def test_alarm_once_fires_single(vclock, monkeypatch):
    """单次模式: 进待补只触发一次, 后续等待期间不再重复触发."""
    calls = _patch_alarm(monkeypatch)
    vsm = _make_vsm(_cfg_leave(remediation_event_id=7, remediation_alarm_mode='once'))
    _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)                    # 离场 → 待补 (响 1 次)
    assert vsm._per_item_session.awaiting_remediation

    # 待补态等很久 (空帧推进时间) → 单次模式不应再响
    _feed(vsm, [], repeat=10, vclock=vclock, frame_dt=1.0)
    assert len(calls) == 1, f"单次模式应只响一次, 实际 {calls}"


def test_alarm_sustained_fires_repeatedly(vclock, monkeypatch):
    """持续模式: 待补期间按间隔重复触发报警."""
    calls = _patch_alarm(monkeypatch)
    vsm = _make_vsm(_cfg_leave(remediation_event_id=7,
                               remediation_alarm_mode='sustained',
                               remediation_alarm_interval_sec=2.0))
    _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)
    _feed(vsm, [], repeat=5, vclock=vclock)                    # 离场 → 待补 (响第 1 次)
    assert vsm._per_item_session.awaiting_remediation

    # 待补态持续 ~8s (每帧推进 1s) → 间隔 2s 应再响约 3~4 次
    _feed(vsm, [], repeat=8, vclock=vclock, frame_dt=1.0)
    assert len(calls) >= 3, f"持续模式应重复触发多次, 实际 {calls}"


# ── 清零复位: 待补态 + 逐颗覆盖 + 上次NG详情全清 ──
def test_reset_runtime_clears_await_and_coverage(vclock):
    """点「清零」应清掉待补态/逐颗覆盖/上次NG, 否则前端轮询会把待补横幅刷回来."""
    vsm = _make_vsm(_cfg_leave())
    _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)   # 漏 1
    _feed(vsm, [], repeat=5, vclock=vclock)                    # 离场 → 待补
    assert vsm._per_item_session.awaiting_remediation
    assert vsm._per_item_last_ng_detail is not None
    assert any(it.covered for s in vsm._per_item_steps for it in s.items.values())

    # 清零
    vsm._per_item_reset_runtime()

    assert not vsm._per_item_session.awaiting_remediation, "待补态应清"
    assert not vsm._per_item_session.cycle_active, "周期态应清"
    assert vsm._per_item_last_ng_detail is None, "上次NG详情应清"
    assert all(len(s.items) == 0 and not s.completed for s in vsm._per_item_steps), "逐颗覆盖应清"


# ════════════════════════════════════════════════════════════════
# 判定时机 ≠ 结算时机 (判定层): 判定先亮绿/红, 结算再落账
# ════════════════════════════════════════════════════════════════
def _cfg_judge(timing, judge_label='', judge_all_done_sec=0.0,
               judge_label_frames=2, judge_ok_event_id=77, **kw):
    """在离场结算(judge=True)基础上叠加独立判定时机."""
    cfg = _cfg_leave(**kw)
    pi = cfg['pipeline_config']['per_item']
    pi['judge_timing'] = timing
    pi['judge_label'] = judge_label
    pi['judge_all_done_sec'] = judge_all_done_sec
    pi['judge_label_frames'] = judge_label_frames
    pi['judge_ok_event_id'] = judge_ok_event_id
    return cfg


def _capture_green(vsm, vclock):
    fires = []
    vsm._per_item_fire_judge_ok_event = lambda: fires.append(vclock.now)
    return fires


# 默认 on_settle: 判定层不介入 (判定就在结算那刻, 老行为)
def test_judge_on_settle_default_no_layer(vclock):
    vsm = _make_vsm(_cfg_leave())
    assert vsm._per_item_config['judge_timing'] == 'on_settle'
    events = _capture_events(vsm)
    _start_cycle(vsm, vclock)
    _feed(vsm, _cover_frame(14, 4), repeat=5, vclock=vclock)
    assert not vsm._per_item_session.judged, "on_settle 不应启用判定层"
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert any(e[0] == 1 for e in events), "老行为: 离场仍判 OK"


# all_done 判定: 全打完即亮绿(不落账), 取走离场才落账 OK
def test_judge_all_done_green_then_leave_ok(vclock):
    vsm = _make_vsm(_cfg_judge('all_done', judge_all_done_sec=0.0))
    events = _capture_events(vsm)
    green = _capture_green(vsm, vclock)
    _start_cycle(vsm, vclock)

    _feed(vsm, _cover_frame(14, 4), repeat=5, vclock=vclock)
    assert vsm._per_item_session.judged and vsm._per_item_session.judged_ok, "全完成应判合格"
    assert green, "应亮绿(judge_ok_event)"
    assert vsm._per_item_session.cycle_active, "判定不落账, 周期仍在"
    assert not events, "判定层不应触发落账事件 1/2"

    _feed(vsm, [], repeat=5, vclock=vclock)
    assert any(e[0] == 1 for e in events), f"离场应落账 OK, events={events}"
    assert not vsm._per_item_session.cycle_active


# all_done 判定: 漏件时判定不触发(全完成才触发); 取走未补 → 离场判 NG 待补
def test_judge_all_done_incomplete_no_green_leave_ng(vclock):
    vsm = _make_vsm(_cfg_judge('all_done'))
    events = _capture_events(vsm)
    green = _capture_green(vsm, vclock)
    _start_cycle(vsm, vclock)

    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)   # 漏 1
    assert not vsm._per_item_session.judged, "未全完成, all_done 判定不触发"
    assert not green, "漏件不应亮绿"

    _feed(vsm, [], repeat=5, vclock=vclock)                    # 取走未补
    assert vsm._per_item_session.awaiting_remediation, "离场判 NG 进待补"


# label 判定: 判定标签出现 → 有漏亮红待补; 补满翻绿; 离场落账 OK
def test_judge_label_ng_then_supplement_green_then_ok(vclock):
    vsm = _make_vsm(_cfg_judge('label', judge_label='完成检查', judge_label_frames=2))
    events = _capture_events(vsm)
    green = _capture_green(vsm, vclock)
    _start_cycle(vsm, vclock)

    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)   # 漏 1
    # 判定标签连续出现 (工件仍在场)
    lbl_frame = _cover_frame(13, 4) + [_det('完成检查', 0.5, 0.95, 0.08, 0.08)]
    _feed(vsm, lbl_frame, repeat=3, vclock=vclock)
    assert vsm._per_item_session.judged and not vsm._per_item_session.judged_ok, "标签判定应判 NG"
    assert vsm._per_item_session.awaiting_remediation, "判 NG 应亮红待补"
    assert not green, "判 NG 不应亮绿"
    assert not events, "判定层不应落账"

    # 补打第 14 颗 → 翻绿
    _feed(vsm, _cover_frame(14, 4), repeat=4, vclock=vclock)
    assert vsm._per_item_session.judged_ok, "补满应翻绿"
    assert not vsm._per_item_session.awaiting_remediation
    assert green, "翻绿应触发绿灯事件"

    # 离场 → 落账 OK
    _feed(vsm, [], repeat=5, vclock=vclock)
    assert any(e[0] == 1 for e in events), f"离场应落账 OK, events={events}"


# label 判定: 全覆盖时标签出现 → 直接亮绿, 离场落账 OK
def test_judge_label_all_covered_green(vclock):
    vsm = _make_vsm(_cfg_judge('label', judge_label='完成检查', judge_label_frames=2))
    events = _capture_events(vsm)
    green = _capture_green(vsm, vclock)
    _start_cycle(vsm, vclock)

    _feed(vsm, _cover_frame(14, 4), repeat=5, vclock=vclock)   # 全覆盖
    lbl_frame = _cover_frame(14, 4) + [_det('完成检查', 0.5, 0.95, 0.08, 0.08)]
    _feed(vsm, lbl_frame, repeat=3, vclock=vclock)
    assert vsm._per_item_session.judged and vsm._per_item_session.judged_ok
    assert green and not vsm._per_item_session.awaiting_remediation
    assert vsm._per_item_session.cycle_active, "判定不落账"


# manual 判定: 自动不触发, 只由"手动判定"按钮(per_item_judge_now)拍快照
def test_judge_manual_only_on_button(vclock):
    vsm = _make_vsm(_cfg_judge('manual'))
    events = _capture_events(vsm)
    green = _capture_green(vsm, vclock)
    _start_cycle(vsm, vclock)

    _feed(vsm, _cover_frame(14, 4), repeat=5, vclock=vclock)   # 全覆盖
    assert not vsm._per_item_session.judged, "manual 模式不应自动判定"
    assert not green, "没点按钮不应亮绿"

    ret = vsm.per_item_judge_now()                              # 点「手动判定」
    assert ret['ok'] and ret['judged_ok'] is True
    assert vsm._per_item_session.judged and vsm._per_item_session.judged_ok
    assert green, "手动判合格应亮绿"
    assert vsm._per_item_session.cycle_active, "判定不落账"
    assert not events, "判定层不落账事件"

    _feed(vsm, [], repeat=5, vclock=vclock)                    # 取走离场 → 落账 OK
    assert any(e[0] == 1 for e in events), f"离场应落账 OK, events={events}"


# manual 判定: 漏件手动判 NG → 待补; 补打 → tick 自动翻绿
def test_judge_manual_ng_then_supplement_green(vclock):
    vsm = _make_vsm(_cfg_judge('manual'))
    _capture_events(vsm)
    green = _capture_green(vsm, vclock)
    _start_cycle(vsm, vclock)

    _feed(vsm, _cover_frame(13, 4), repeat=5, vclock=vclock)   # 漏 1
    ret = vsm.per_item_judge_now()
    assert ret['judged_ok'] is False, "漏件手动判应 NG"
    assert vsm._per_item_session.awaiting_remediation, "判 NG 应亮红待补"
    assert not green

    _feed(vsm, _cover_frame(14, 4), repeat=4, vclock=vclock)   # 补打第 14 颗
    assert vsm._per_item_session.judged_ok, "补满应翻绿"
    assert green


# manual 判定: 无周期时点按钮 → 拒绝 (不伪造)
def test_judge_now_no_cycle_rejected(vclock):
    vsm = _make_vsm(_cfg_judge('manual'))
    ret = vsm.per_item_judge_now()
    assert ret['ok'] is False, "无 active 周期不应判定"


# ════════════════════════════════════════════════════════════════
# 螺丝编号: 锁定时按空间序(上→下、左→右)分配 item_id
# ════════════════════════════════════════════════════════════════
def test_spatial_sort_row_major():
    from backend.api.source_per_item_mixin import _spatial_sort_boxes
    w = h = 0.05
    b = lambda x, y: (x, y, w, h)
    # 2 行 x 3 列, 乱序喂入
    boxes = [b(0.7, 0.5), b(0.1, 0.1), b(0.7, 0.1), b(0.4, 0.5), b(0.1, 0.5), b(0.4, 0.1)]
    out = _spatial_sort_boxes(boxes)
    got = [(round(o[0], 2), round(o[1], 2)) for o in out]
    assert got == [(0.1, 0.1), (0.4, 0.1), (0.7, 0.1),
                   (0.1, 0.5), (0.4, 0.5), (0.7, 0.5)], f"应行优先空间排序, 实际 {got}"


def test_spatial_sort_same_row_jitter_tolerant():
    from backend.api.source_per_item_mixin import _spatial_sort_boxes
    w = h = 0.05
    # 同一排但 y 有微抖 (±0.01 < 行高×1.5), 应纯按 x 排, 不被 y 抖乱序
    boxes = [(0.7, 0.105, w, h), (0.1, 0.095, w, h), (0.4, 0.10, w, h)]
    out = _spatial_sort_boxes(boxes)
    xs = [round(o[0], 2) for o in out]
    assert xs == [0.1, 0.4, 0.7], f"同排应按 x 左→右, 实际 {xs}"


def test_lock_item_ids_follow_space(vclock):
    # 锁定后 item_id 应随空间位置单调 (POS_5N_14 是一排, x 递增 → id 递增)
    vsm = _make_vsm(_cfg_leave())
    _start_cycle(vsm, vclock)
    step5 = vsm._per_item_steps[0]
    xs = [step5.items[i].bbox[0] for i in sorted(step5.items.keys())]
    assert xs == sorted(xs), f"item_id 顺序应与空间 x 单调一致, 实际 {xs}"
