"""班次汇总调度：早八～晚八、默认不发夜窗。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from backend.services.sms_service import SmsService, SmsServiceConfig
from backend.services.sms_summary import (
    SmsSummaryCounts,
    next_shift_window_start,
    open_daily_shift_window_start,
    shift_window_end,
)


DAY = datetime(2026, 8, 4, 8, 0, 0)


def _shift_config(**overrides: object) -> SmsServiceConfig:
    values: dict[str, object] = {
        "enabled": True,
        "port": "COM_TEST",
        "recipients": ("13800138000",),
        "summary_schedule_mode": "daily_shift",
        "shift_start_hour": 8,
        "shift_end_hour": 20,
        "send_night_window": False,
        "cooldown_seconds": 0,
    }
    values.update(overrides)
    return SmsServiceConfig(**values)


def _service(tmp_path, *, config=None, reader=None, wall_clock=None) -> SmsService:
    return SmsService(
        config or _shift_config(),
        summary_state_path=tmp_path / "sms_summary_state.json",
        offline_queue_path=tmp_path / "sms_offline_queue.db",
        summary_reader=reader or (lambda _start, _end: {}),
        wall_clock=wall_clock or (lambda: DAY),
    )


def test_shift_helpers_day_only() -> None:
    end = shift_window_end(DAY, start_hour=8, end_hour=20)
    assert end == datetime(2026, 8, 4, 20, 0, 0)
    nxt = next_shift_window_start(
        end, start_hour=8, end_hour=20, send_night_window=False
    )
    assert nxt == datetime(2026, 8, 5, 8, 0, 0)
    assert open_daily_shift_window_start(
        datetime(2026, 8, 4, 10, 30),
        start_hour=8,
        end_hour=20,
        send_night_window=False,
    ) == DAY


def test_daily_shift_sends_at_end_hour_and_skips_night(tmp_path, monkeypatch) -> None:
    calls: list[tuple[datetime, datetime]] = []

    def reader(start: datetime, end: datetime):
        calls.append((start, end))
        return {0: SmsSummaryCounts(ok_count=5, ng_count=1)}

    service = _service(
        tmp_path,
        reader=reader,
        wall_clock=lambda: datetime(2026, 8, 4, 10, 0, 0),
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)
    service.start_summary_scheduler()
    try:
        # 未到 20:00 不发
        assert service.run_due_summaries(datetime(2026, 8, 4, 19, 59, 0)) == 0
        assert service._queue.qsize() == 0

        assert service.run_due_summaries(datetime(2026, 8, 4, 20, 0, 0)) == 1
        job = service._queue.get_nowait()
        assert job.context["ok_count"] == 5
        assert job.context["ng_count"] == 1
        assert calls[-1] == (
            datetime(2026, 8, 4, 8, 0, 0),
            datetime(2026, 8, 4, 20, 0, 0),
        )

        state = json.loads(
            (tmp_path / "sms_summary_state.json").read_text(encoding="utf-8")
        )
        assert state["window_start"] == "2026-08-05T08:00:00"

        # 夜窗不发：次日 08:00 前不应再因 20:00~08:00 入队
        assert service.run_due_summaries(datetime(2026, 8, 5, 7, 59, 0)) == 0
        assert service._queue.qsize() == 0
    finally:
        service.shutdown(timeout=0.5)


def test_daily_shift_with_night_window(tmp_path, monkeypatch) -> None:
    service = _service(
        tmp_path,
        config=_shift_config(send_night_window=True),
        reader=lambda _s, _e: {0: SmsSummaryCounts(ok_count=1)},
        wall_clock=lambda: datetime(2026, 8, 4, 10, 0, 0),
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)
    service.start_summary_scheduler()
    try:
        assert service.run_due_summaries(datetime(2026, 8, 4, 20, 0, 0)) == 1
        assert service._summary_state.window_start == datetime(2026, 8, 4, 20, 0, 0)
        assert service.run_due_summaries(datetime(2026, 8, 5, 8, 0, 0)) == 1
        assert service._summary_state.window_start == datetime(2026, 8, 5, 8, 0, 0)
        assert service._queue.qsize() == 2
    finally:
        service.shutdown(timeout=0.5)


def test_rolling_default_unchanged(tmp_path, monkeypatch) -> None:
    start = datetime(2026, 8, 2, 8, 0, 0)
    end = start + timedelta(hours=12)
    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            port="COM_TEST",
            recipients=("13800138000",),
            summary_schedule_mode="rolling_12h",
        ),
        summary_state_path=tmp_path / "sms_summary_state.json",
        offline_queue_path=tmp_path / "offline.db",
        summary_reader=lambda _s, _e: {0: SmsSummaryCounts(ok_count=2)},
        wall_clock=lambda: start,
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)
    service.start_summary_scheduler()
    try:
        assert service.run_due_summaries(end) == 1
        assert service._summary_state.window_start == end
    finally:
        service.shutdown(timeout=0.5)
