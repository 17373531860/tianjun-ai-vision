"""事件中心不再触发单次 NG 短信的回归。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from backend.api.source_event_trigger_mixin import EventTriggerMixin


class _SmsSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_alarm(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _make_host() -> SimpleNamespace:
    return SimpleNamespace(
        project_config={
            "pipeline_config": {"settle_dedup": False},
            "events_config": [
                {"id": 1, "name": "合格", "actions": []},
                {"id": 2, "name": "装配 NG", "actions": []},
            ],
            "logic_mode": "detection",
            "steps_config": [],
        },
        _last_ng_time=0,
        counters={},
        cycle_start_time=None,
        cycle_times=[],
        ng_cycle_times=[],
        ng_step_cycle_counts={},
        current_cycle_steps=[],
        current_cycle_id="cycle-1",
        current_cycle_uuid=None,
        _mes_hook=None,
        channel_id=3,
        _event_seq=0,
        events_log=[],
        _discard_empty_cycle=lambda: None,
        end_cycle=lambda **_kwargs: None,
        _persist_counters=lambda: None,
        _dispatch_event_alarm=lambda *_args, **_kwargs: None,
    )


def _install_sms_spy(monkeypatch: pytest.MonkeyPatch) -> _SmsSpy:
    from backend.api import sms as sms_api

    spy = _SmsSpy()
    monkeypatch.setattr(sms_api, "get_sms_service", lambda: spy)
    return spy


def test_confirmed_ng_event_does_not_queue_immediate_sms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _make_host()
    spy = _install_sms_spy(monkeypatch)

    fired = EventTriggerMixin._trigger_event(host, 2, "缺少：压板")

    assert fired is True
    assert spy.calls == []


def test_ok_event_does_not_queue_sms(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _make_host()
    spy = _install_sms_spy(monkeypatch)

    assert EventTriggerMixin._trigger_event(host, 1, "全部完成") is True
    assert spy.calls == []


@pytest.mark.parametrize("guard", ["pending_ack", "dedup", "ng_protect", "missing"])
def test_suppressed_or_missing_event_never_queues_sms(
    monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    host = _make_host()
    spy = _install_sms_spy(monkeypatch)
    event_id = 2
    if guard == "pending_ack":
        host._pending_ack = True
    elif guard == "dedup":
        host.project_config["pipeline_config"].update(
            {"settle_dedup": True, "settle_dedup_window_seconds": 30}
        )
        host._last_event_time = time.time()
    elif guard == "ng_protect":
        host.project_config["pipeline_config"]["ng_cycle_protect_seconds"] = 30
        host._last_ng_time = time.time()
    else:
        event_id = 999

    assert EventTriggerMixin._trigger_event(host, event_id, "应被守门") is False
    assert spy.calls == []


def test_ng_event_does_not_touch_sms_facade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _make_host()
    from backend.api import sms as sms_api

    def forbidden_sms_lookup():
        raise AssertionError("单次 NG 不应再读取短信门面")

    monkeypatch.setattr(sms_api, "get_sms_service", forbidden_sms_lookup)

    assert EventTriggerMixin._trigger_event(host, 2, "短信层假异常") is True
