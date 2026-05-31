"""M1.2c 基线护栏: _trigger_event alarm 顺序重构前后行为等价

客户视角叙事:
  M1.2c 要把 alarm 触发从 event_fire hook 之前挪到之后, 让客户插件能用
  suppress_alarm=True 抑制本次报警 (客户需求 2 "步骤级 NG 不报红").

  但 _trigger_event 是项目 debug-mes skill 标的"61 条历史 bug 重灾区",
  改业务流程顺序最大风险是: **没用插件的客户机, alarm 行为变了**.

  本测试在重构 _trigger_event 之前先录 baseline:
    - 无插件场景下 alarm 调用次数 + 入参 + 与 router / _last_event_time 相对顺序
    - 抑制路径 (settle_dedup / ng_protect / _pending_ack) 不调 alarm
    - alarm 异常被 swallow 不上抛

  重构后这些测试**全部必须保持通过** — 这是"无插件场景字节级等价"的强契约.

  注: hook 位置不在基线断言里 — 因为无 active 插件时 fire_plugin_hook 返回空 dict,
  hook 在 alarm 前还是 alarm 后, 主程序可见的副作用完全一致.

测试隔离策略:
  _trigger_event 是 EventTriggerMixin 方法, 真跑要 self 含 30+ 属性. 这里造
  一个 minimal fake VSM (继承 EventTriggerMixin), 只给方法体真正访问的属性,
  其他 monkeypatch.

如果 M1.2c 重构后任一基线测试 FAIL:
  说明无插件场景行为漂移, 必须修到 0 diff 才能合入.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ============================================================
# 测试基础设施: 调用录制器 + minimal fake VSM
# ============================================================


class _CallRecorder:
    """按调用顺序录所有副作用 (name, kwargs)."""
    def __init__(self) -> None:
        self.events: List[Tuple[str, Dict[str, Any]]] = []

    def record(self, name: str, **kwargs) -> None:
        self.events.append((name, kwargs))

    def names(self) -> List[str]:
        return [e[0] for e in self.events]

    def first_with(self, name: str) -> Dict[str, Any]:
        for n, kw in self.events:
            if n == name:
                return kw
        raise AssertionError(f"未录到 {name}, 实际: {self.names()}")

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.events if n == name)


def _make_fake_vsm(
    recorder: _CallRecorder,
    *,
    pipeline_config: Dict[str, Any] = None,
    cycle_start_time: float = None,
    last_event_time: float = 0.0,
    last_ng_time: float = 0,
    pending_ack: bool = False,
    end_cycle_side_effect=None,
):
    """造一个能跑 _trigger_event 的 fake VSM, 副作用方法全 mock 到 recorder."""
    from backend.api.source_event_trigger_mixin import EventTriggerMixin

    class _FakeVSM(EventTriggerMixin):
        pass

    vsm = _FakeVSM()

    # ---- 基础属性 ----
    vsm.project_config = {
        'pipeline_config': pipeline_config or {},
        'events_config': [
            {'id': 1, 'name': 'OK合格', 'actions': [], 'show_notification': False},
            {'id': 2, 'name': 'NG不合格', 'actions': [{'counter_name': 'NG总数', 'delta': 1}]},
        ],
        'logic_mode': 'sequential',
        'steps_config': [],
    }
    vsm._pending_ack = pending_ack
    vsm._last_event_time = last_event_time
    vsm._last_ng_time = last_ng_time
    vsm.cycle_start_time = cycle_start_time
    vsm.cycle_start_frame_pos = 0
    vsm.cycle_times = []
    vsm.ng_cycle_times = []
    vsm.current_cycle_id = 100
    vsm.channel_id = 0
    vsm._mes_hook = None
    vsm.counters = {'NG步骤': 0, 'NG总数': 0, 'OK总数': 0}
    vsm.current_cycle_steps = []
    vsm.ng_step_cycle_counts = {}
    vsm.events_log = []
    vsm._event_seq = 0
    vsm._router = None
    vsm._pending_ack_started_at = None
    vsm._pending_ack_event_id = None
    vsm._pending_ack_event_name = None
    vsm._pending_ack_timeout_sec = None

    # ---- 方法 mock ----
    def fake_end_cycle(is_good, event_id, event_name, reason):
        recorder.record('end_cycle', is_good=is_good, event_id=event_id,
                        event_name=event_name, reason=reason)
        vsm.current_cycle_id = None  # 真实 end_cycle 会清这个, 模拟
        if end_cycle_side_effect:
            end_cycle_side_effect()
    vsm.end_cycle = fake_end_cycle

    vsm._discard_empty_cycle = lambda: recorder.record('_discard_empty_cycle')
    vsm._persist_counters = lambda: recorder.record('_persist_counters')
    vsm._compute_duration_sec = lambda *a, **kw: 1.5
    vsm._video_frame_pos = lambda: 0

    return vsm


def _patch_alarm_and_hook(monkeypatch, recorder: _CallRecorder, alarm_raises=False):
    """把 alarm_router.trigger_alarm 和 fire_plugin_hook 都 patch 到 recorder.

    无 active 插件场景下 fire_plugin_hook 自然返回 {}, 等价于 patch 后的 fake 行为.
    """
    from backend.api import alarm as alarm_mod
    from backend.plugin_system import hook_dispatch as hd

    def fake_trigger_alarm(event_type, channel_id=0):
        recorder.record('alarm.trigger_alarm', event_type=event_type, channel_id=channel_id)
        if alarm_raises:
            raise RuntimeError("模拟串口失败")

    monkeypatch.setattr(alarm_mod.alarm_router, 'trigger_alarm', fake_trigger_alarm)

    def fake_fire(hook_type, phase, when, ctx):
        recorder.record('fire_plugin_hook',
                        hook_type=hook_type, phase=phase, when=when, channel_id=ctx.get('channel_id'))
        return {}

    monkeypatch.setattr(hd, 'fire_plugin_hook', fake_fire)


# ============================================================
# A. alarm 调用次数 + 入参 — 无插件场景
# ============================================================


def test_baseline_no_plugin_ok_triggers_alarm_once(monkeypatch):
    """OK 事件: alarm 调 1 次, event_type='event1', channel_id 对齐."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    _patch_alarm_and_hook(monkeypatch, rec)

    ret = vsm._trigger_event(event_id=1, reason="完整 OK")

    assert ret is True
    assert rec.count('alarm.trigger_alarm') == 1
    alarm_kw = rec.first_with('alarm.trigger_alarm')
    assert alarm_kw['event_type'] == 'event1'
    assert alarm_kw['channel_id'] == 0


def test_baseline_no_plugin_ng_triggers_alarm_once(monkeypatch):
    """NG 事件: alarm 调 1 次, event_type='event2'."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    _patch_alarm_and_hook(monkeypatch, rec)

    ret = vsm._trigger_event(event_id=2, reason="缺少: [step1]")

    assert ret is True
    assert rec.count('alarm.trigger_alarm') == 1
    alarm_kw = rec.first_with('alarm.trigger_alarm')
    assert alarm_kw['event_type'] == 'event2'
    assert alarm_kw['channel_id'] == 0


def test_baseline_no_plugin_alarm_channel_id_propagated(monkeypatch):
    """多工位场景: alarm 必须用本通道 channel_id, 不能漂."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    vsm.channel_id = 2
    _patch_alarm_and_hook(monkeypatch, rec)

    vsm._trigger_event(event_id=2, reason="x")

    alarm_kw = rec.first_with('alarm.trigger_alarm')
    assert alarm_kw['channel_id'] == 2


def test_baseline_no_plugin_event_fire_hook_called_once(monkeypatch):
    """fire_plugin_hook 必须被调 1 次 (即使无 active 插件)."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    _patch_alarm_and_hook(monkeypatch, rec)

    vsm._trigger_event(event_id=1, reason="x")

    hook_calls = [e for e in rec.events if e[0] == 'fire_plugin_hook']
    assert len(hook_calls) == 1
    assert hook_calls[0][1]['hook_type'] == 'event_fire'


# ============================================================
# B. 关键相对顺序契约 (与 hook 位置无关)
# ============================================================


def _assert_order(rec: _CallRecorder, *names_in_order: str) -> None:
    """断言 events 序列里给定的 name 按指定顺序出现 (允许中间有其他事件)."""
    indices = []
    for target in names_in_order:
        for idx, (n, _) in enumerate(rec.events):
            if n == target and idx not in indices:
                indices.append(idx)
                break
        else:
            raise AssertionError(
                f"未找到 {target}, 已确认顺序的 idx: {indices}, 实际: {rec.names()}"
            )
    # 严格递增
    for i in range(len(indices) - 1):
        assert indices[i] < indices[i + 1], (
            f"顺序违反: {names_in_order[i]} (idx={indices[i]}) 必须在 "
            f"{names_in_order[i + 1]} (idx={indices[i + 1]}) 之前. 实际: {rec.names()}"
        )


def test_baseline_no_plugin_order_end_cycle_before_alarm(monkeypatch):
    """end_cycle 必须在 alarm 之前 (MES Hub on_cycle_end 已在 end_cycle 内执行).

    重构 M1.2c 不动 end_cycle 位置.
    """
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    _patch_alarm_and_hook(monkeypatch, rec)

    vsm._trigger_event(event_id=2, reason="x")

    _assert_order(rec, 'end_cycle', 'alarm.trigger_alarm')


def test_baseline_no_plugin_order_alarm_before_anchor_implicit(monkeypatch):
    """alarm 触发必须在 _last_event_time 锚点之前 (settle_dedup 依赖).

    _last_event_time 不是 mock 调用, 是 self 属性赋值, 没法直接录;
    用 alarm < hook 隐含 (hook 在 _last_event_time 之后, 是 M1.2c 重构前的事实);
    重构后 hook 上移, 但 alarm 仍在 _last_event_time 之前.

    本测试在重构前: 录 alarm < hook (= alarm < anchor < hook).
    重构后: 录 hook < alarm < (anchor 在 alarm 之后, hook 之后) — 这条断言会失效.

    所以这里只录"重构后仍然成立"的强契约: alarm < router 调用 (= router 在 alarm 之后).
    router 在 _router 上调, 测试里 _router=None 不会调到 — 跳过.

    替代: 用专门的 _last_event_time 读取测试.
    """
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    _patch_alarm_and_hook(monkeypatch, rec)

    # 记录 alarm 调用前的 _last_event_time
    captured_anchor_before_alarm = []

    from backend.api import alarm as alarm_mod
    orig_fake = alarm_mod.alarm_router.trigger_alarm
    def alarm_wrapper(event_type, channel_id=0):
        captured_anchor_before_alarm.append(vsm._last_event_time)
        orig_fake(event_type, channel_id=channel_id)
    monkeypatch.setattr(alarm_mod.alarm_router, 'trigger_alarm', alarm_wrapper)

    before = vsm._last_event_time
    vsm._trigger_event(event_id=2, reason="x")
    after = vsm._last_event_time

    # alarm 触发时, _last_event_time 还没更新
    assert captured_anchor_before_alarm[0] == before
    # _trigger_event 结束后, anchor 必须已更新到 > before
    assert after > before, f"_last_event_time 没在事件成功后更新 (before={before}, after={after})"


def test_baseline_no_plugin_anchor_updated_after_success(monkeypatch):
    """成功路径: _last_event_time 必须更新; 抑制路径: 不更新."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec, last_event_time=1000.0)
    _patch_alarm_and_hook(monkeypatch, rec)

    vsm._trigger_event(event_id=1, reason="x")
    assert vsm._last_event_time > 1000.0


class _LoggingList(list):
    """list 子类, append 时往 recorder 记一条."""
    def __init__(self, recorder: _CallRecorder):
        super().__init__()
        self._rec = recorder

    def append(self, item):
        self._rec.record('events_log.append')
        super().append(item)


def test_baseline_no_plugin_events_log_before_alarm(monkeypatch):
    """events_log.append 必须在 alarm 之前 (v3.9.x 阻塞态契约)."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    vsm.events_log = _LoggingList(rec)  # list 子类替换, append 走 recorder
    _patch_alarm_and_hook(monkeypatch, rec)

    vsm._trigger_event(event_id=2, reason="缺少: [step1]")

    _assert_order(rec, 'events_log.append', 'alarm.trigger_alarm')


# ============================================================
# C. 抑制路径 — alarm 不调
# ============================================================


def test_baseline_pending_ack_blocks_alarm(monkeypatch):
    """_pending_ack=True: alarm 必须 0 次, 返回 False."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec, pending_ack=True)
    _patch_alarm_and_hook(monkeypatch, rec)

    ret = vsm._trigger_event(event_id=2, reason="x")

    assert ret is False
    assert rec.count('alarm.trigger_alarm') == 0
    assert rec.count('end_cycle') == 0
    assert rec.count('fire_plugin_hook') == 0


def test_baseline_settle_dedup_blocks_alarm(monkeypatch):
    """settle_dedup 冷却窗内: alarm 0 次, anchor 不更新, _discard_empty_cycle 调 1 次."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(
        rec,
        pipeline_config={'settle_dedup': True, 'settle_dedup_window_seconds': 5.0},
        last_event_time=time.time() - 1.0,  # 1s ago, in 5s window
    )
    _patch_alarm_and_hook(monkeypatch, rec)
    anchor_before = vsm._last_event_time

    ret = vsm._trigger_event(event_id=2, reason="x")

    assert ret is False
    assert rec.count('alarm.trigger_alarm') == 0
    assert rec.count('_discard_empty_cycle') == 1
    assert vsm._last_event_time == anchor_before  # 锚点不更新


def test_baseline_ng_protect_blocks_alarm(monkeypatch):
    """ng_cycle_protect_seconds 冷却窗内 NG: alarm 0 次, _discard_empty_cycle 调 1 次."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(
        rec,
        pipeline_config={'ng_cycle_protect_seconds': 5.0},
        last_ng_time=time.time() - 1.0,
    )
    _patch_alarm_and_hook(monkeypatch, rec)

    ret = vsm._trigger_event(event_id=2, reason="x")

    assert ret is False
    assert rec.count('alarm.trigger_alarm') == 0
    assert rec.count('_discard_empty_cycle') == 1


def test_baseline_event_not_found_no_alarm(monkeypatch):
    """events_config 找不到 event_id: alarm 0 次, end_cycle 0 次."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    _patch_alarm_and_hook(monkeypatch, rec)

    ret = vsm._trigger_event(event_id=999, reason="x")

    assert ret is False
    assert rec.count('alarm.trigger_alarm') == 0
    assert rec.count('end_cycle') == 0


def test_baseline_no_project_config_no_alarm(monkeypatch):
    """无 project_config: 立即 return False, 任何副作用都不发生."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    vsm.project_config = None
    _patch_alarm_and_hook(monkeypatch, rec)

    ret = vsm._trigger_event(event_id=2, reason="x")

    assert ret is False
    assert rec.events == []  # 一个调用都没发生


# ============================================================
# D. 异常隔离 — alarm 串口失败不上抛
# ============================================================


def test_baseline_alarm_exception_swallowed(monkeypatch):
    """alarm 抛错被 swallow, _trigger_event 仍 return True, 后续 router/anchor/hook 仍执行."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    _patch_alarm_and_hook(monkeypatch, rec, alarm_raises=True)

    ret = vsm._trigger_event(event_id=2, reason="x")

    # alarm 抛错被 swallow, 函数仍 return True
    assert ret is True
    # alarm 仍被尝试调 1 次 (记录在 patch 内)
    assert rec.count('alarm.trigger_alarm') == 1
    # anchor 仍被更新 (alarm 异常不影响后续)
    assert vsm._last_event_time > 0
    # hook 仍 fire (M1.2c 重构后变成在 alarm 之前 fire, 但仍执行)
    assert rec.count('fire_plugin_hook') == 1


# ============================================================
# E. 共享灯柱场景占位 (M1.2c 必须不影响)
# ============================================================


def test_baseline_alarm_event_type_format_locked(monkeypatch):
    """alarm event_type 格式必须是 'event{id}' (重构不能改成 'event_{id}' 之类)."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    _patch_alarm_and_hook(monkeypatch, rec)

    vsm._trigger_event(event_id=1, reason="x")
    vsm._pending_ack = False
    vsm._last_event_time = 0  # reset
    vsm.current_cycle_id = 200
    vsm._trigger_event(event_id=2, reason="x")

    alarm_events = [e for e in rec.events if e[0] == 'alarm.trigger_alarm']
    assert len(alarm_events) == 2
    assert alarm_events[0][1]['event_type'] == 'event1'
    assert alarm_events[1][1]['event_type'] == 'event2'


# ============================================================
# F. 返回值契约
# ============================================================


def test_baseline_return_true_on_success(monkeypatch):
    """成功触发 → return True."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    _patch_alarm_and_hook(monkeypatch, rec)
    assert vsm._trigger_event(event_id=1, reason="x") is True


def test_baseline_return_false_on_suppress_dedup(monkeypatch):
    """settle_dedup 抑制 → return False."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(
        rec,
        pipeline_config={'settle_dedup': True, 'settle_dedup_window_seconds': 5.0},
        last_event_time=time.time(),
    )
    _patch_alarm_and_hook(monkeypatch, rec)
    assert vsm._trigger_event(event_id=2, reason="x") is False


def test_baseline_return_false_on_pending_ack(monkeypatch):
    """_pending_ack 抑制 → return False."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec, pending_ack=True)
    _patch_alarm_and_hook(monkeypatch, rec)
    assert vsm._trigger_event(event_id=2, reason="x") is False


def test_baseline_return_false_on_event_not_found(monkeypatch):
    """event_id 未找到 → return False."""
    rec = _CallRecorder()
    vsm = _make_fake_vsm(rec)
    _patch_alarm_and_hook(monkeypatch, rec)
    assert vsm._trigger_event(event_id=999, reason="x") is False
