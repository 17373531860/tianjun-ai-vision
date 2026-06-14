"""测试 EventTriggerMixin._trigger_event 是否正确通知 InferenceRouter
(feat/multi-model-roi-link, c1: on_event 事件总线接通)。

策略
====
- 用 SimpleNamespace 拼一个最小 host (满足 _trigger_event 字段需求)
- 真正挂一个 InferenceRouter + 副模型 (schedule.type='on_event')
- 触发 _trigger_event(event_id=1, reason=...)
- 断言 router._event_queue 收到 'event_1' + 事件名两个 key
- 调度一帧验证副模型被选中

也覆盖:
- 没有 _router 字段时不应抛 (老 VSM 兼容)
- 事件没找到时不应通知 router
- NG 保护抑制时不应通知 router
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.api.source_event_trigger_mixin import EventTriggerMixin
from backend.api.source_inference_router import (
    InferenceRouter,
    ModelInstance,
    Schedule,
)


# ============================================================
# host stub
# ============================================================
def _make_host(events_config=None, with_router=True, settle_dedup=False):
    """构造满足 _trigger_event 全部字段需求的最小宿主."""
    if events_config is None:
        events_config = [
            {"id": 1, "name": "合格", "show_notification": True, "actions": []},
            {"id": 2, "name": "NG",   "show_notification": True, "actions": []},
        ]
    host = SimpleNamespace(
        project_config={
            "pipeline_config": {"settle_dedup": settle_dedup},
            "events_config": events_config,
            "logic_mode": "detection",
            "steps_config": [],
        },
        recording_enabled=True,
        _last_ng_time=0,
        counters={},
        cycle_start_time=None,
        cycle_times=[],
        ng_cycle_times=[],
        ng_step_cycle_counts={},
        current_cycle_steps=[],
        _mes_hook=None,
        channel_id=0,
        _event_seq=0,
        events_log=[],
        current_cycle_id=None,
        # 业务方法 stub
        _discard_empty_cycle=lambda: None,
        end_cycle=lambda **kwargs: None,
        _persist_counters=lambda: None,
        # v3.13 M1.2c: alarm 触发抽到 _dispatch_event_alarm, 本测试只验 router hook
        # 不验报警 (报警有独立 baseline 测试守护), 给 no-op 桩.
        _dispatch_event_alarm=lambda *a, **k: None,
    )
    if with_router:
        router = InferenceRouter()
        router.add_model(ModelInstance(name="main"))
        # 监听 event_1 (数字 id 形式)
        router.add_model(ModelInstance(
            name="qc_id",
            schedule=Schedule(type="on_event", events=["event_1"]),
        ))
        # 监听事件名 (人类可读形式)
        router.add_model(ModelInstance(
            name="qc_name",
            schedule=Schedule(type="on_event", events=["合格"]),
        ))
        # 监听不相关事件 (永远不应被触发)
        router.add_model(ModelInstance(
            name="qc_other",
            schedule=Schedule(type="on_event", events=["scan_done"]),
        ))
        host._router = router
    return host


def _trigger(host, event_id, reason="test"):
    """以 EventTriggerMixin 中的 _trigger_event 当普通函数调用."""
    return EventTriggerMixin._trigger_event(host, event_id, reason)


# ============================================================
# 主路径: 事件触发 → router 收到两个 key
# ============================================================
def test_事件触发后_router_收到_event_id_和_事件名_两个_key():
    host = _make_host()
    ok = _trigger(host, 1, reason="所有步骤完成")
    assert ok is True
    queue = host._router._event_queue
    assert "event_1" in queue
    assert "合格" in queue


def test_事件触发后_监听_event_id_的副模型_下一帧被调度():
    host = _make_host()
    _trigger(host, 1, reason="ok")
    chosen = host._router.schedule_models_for_frame(frame_id=10)
    chosen_names = {m.name for m in chosen}
    assert "main" in chosen_names           # main 是 every_frame
    assert "qc_id" in chosen_names          # 监听 'event_1'
    assert "qc_name" in chosen_names        # 监听 '合格'
    assert "qc_other" not in chosen_names   # 监听 'scan_done', 不应触发


def test_NG事件_event_2_对应触发_event_2_key():
    host = _make_host()
    host._router.clear()
    host._router.add_model(ModelInstance(
        name="ng_logger",
        schedule=Schedule(type="on_event", events=["event_2"]),
    ))
    _trigger(host, 2, reason="缺少: [step_a]")
    chosen = host._router.schedule_models_for_frame(frame_id=1)
    assert {m.name for m in chosen} == {"ng_logger"}


# ============================================================
# 边界: host 没有 _router (老路径 / 单测 stub)
# ============================================================
def test_host_无_router_字段_不抛异常():
    host = _make_host(with_router=False)
    # 关键: 没有 _router 字段, _trigger_event 应该正常返回 True 不抛
    ok = _trigger(host, 1, reason="ok")
    assert ok is True
    assert not hasattr(host, "_router")


def test_host_router_为_None_不抛异常():
    host = _make_host(with_router=False)
    host._router = None
    ok = _trigger(host, 1, reason="ok")
    assert ok is True


# ============================================================
# 边界: 事件没找到 → 不通知 router
# ============================================================
def test_事件未在_events_config_中_不通知_router():
    host = _make_host()
    ok = _trigger(host, 999, reason="bogus")
    assert ok is False  # 事件未找到, _trigger_event 自己就 return False
    # 队列应该还是空的
    assert host._router._event_queue == []


# ============================================================
# 边界: NG 保护抑制时 → 不通知 router
# ============================================================
def test_NG保护抑制时_不通知_router():
    host = _make_host()
    host.project_config["pipeline_config"]["ng_cycle_protect_seconds"] = 30
    import time
    host._last_ng_time = time.time()  # 刚刚触发过 NG
    ok = _trigger(host, 2, reason="rapid NG")
    assert ok is False  # 被抑制
    assert host._router._event_queue == []


# ============================================================
# 多次触发: 队列顺序保留 + 不去重 (允许同事件多次累积, drain 时一次消费)
# ============================================================
def test_连续两次触发_队列累积_两份():
    host = _make_host()
    _trigger(host, 1, reason="cycle1")
    _trigger(host, 1, reason="cycle2")
    # event_1 + 合格 各两份 = 4
    queue = host._router._event_queue
    assert queue.count("event_1") == 2
    assert queue.count("合格") == 2


def test_drain后_队列清空():
    host = _make_host()
    _trigger(host, 1, reason="ok")
    host._router.schedule_models_for_frame(frame_id=1)  # drain 一次
    assert host._router._event_queue == []


# ============================================================
# 字符串 event_id (settle_dedup / id='event_1' 形式) 也能匹配
# ============================================================
def test_字符串_event_1_格式_id_也能匹配():
    host = _make_host()
    ok = _trigger(host, "event_1", reason="ok")
    assert ok is True
    # current_event_id 是 events_config 里的 1 (number),
    # 所以投递的 key 是 'event_1' + '合格'
    queue = host._router._event_queue
    assert "event_1" in queue
    assert "合格" in queue


# ============================================================
# 保护: router.trigger_event 内部抛异常时, _trigger_event 不应连带失败
# ============================================================
def test_router_trigger_event_抛异常_不影响_trigger_event_返回():
    host = _make_host()
    # 用一个会抛的 router 替换
    class BoomRouter:
        def trigger_event(self, _name):
            raise RuntimeError("boom")
    host._router = BoomRouter()
    ok = _trigger(host, 1, reason="ok")
    # 业务事件已记录 (counters/events_log 等), 即使通知 router 失败也应返回 True
    assert ok is True
