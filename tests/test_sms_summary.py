"""短信滚动 12 小时汇总与持久化水位回归。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from backend.db.database import SessionLocal
from backend.models.models import DetectionCycle, DetectionSession
from backend.services.sms_service import SmsService, SmsServiceConfig
from backend.services.sms_summary import (
    SUMMARY_WINDOW_SECONDS,
    SmsSummaryCounts,
    load_completed_cycle_counts,
)


WINDOW_START = datetime(2026, 8, 2, 8, 0, 0)
WINDOW_END = WINDOW_START + timedelta(seconds=SUMMARY_WINDOW_SECONDS)


def _enabled_config() -> SmsServiceConfig:
    return SmsServiceConfig(
        enabled=True,
        port="COM_TEST",
        recipients=("13800138000",),
        cooldown_seconds=60,
    )


def _service(tmp_path, *, config=None, reader=None, wall_clock=None) -> SmsService:
    return SmsService(
        config or _enabled_config(),
        summary_state_path=tmp_path / "sms_summary_state.json",
        offline_queue_path=tmp_path / "sms_offline_queue.db",
        summary_reader=reader or (lambda _start, _end: {}),
        wall_clock=wall_clock or (lambda: WINDOW_START),
    )


def test_constructor_does_not_start_window_until_scheduler_starts(tmp_path) -> None:
    state_path = tmp_path / "sms_summary_state.json"
    service = _service(tmp_path)

    assert not state_path.exists()
    assert service.summary_scheduler_running is False

    service.start_summary_scheduler()
    try:
        assert state_path.exists()
        assert service.summary_scheduler_running is True
    finally:
        service.shutdown(timeout=0.5)


def test_summary_rolls_at_each_12_hour_boundary(tmp_path, monkeypatch) -> None:
    def reader(_start, _end):
        return {0: SmsSummaryCounts(ok_count=4, ng_count=1)}

    service = _service(tmp_path, reader=reader)
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)

    assert service.run_due_summaries(WINDOW_END - timedelta(seconds=1)) == 0
    assert service._queue.qsize() == 0

    assert service.run_due_summaries(WINDOW_END) == 1
    first = service._queue.get_nowait()
    assert first.context["channel_id"] == 0
    assert first.context["ok_count"] == 4
    assert first.context["ng_count"] == 1

    second_end = WINDOW_END + timedelta(seconds=SUMMARY_WINDOW_SECONDS)
    assert service.run_due_summaries(second_end) == 1
    second = service._queue.get_nowait()
    assert second.context["window_id"] != first.context["window_id"]


def test_summary_queues_each_channel_independently(tmp_path, monkeypatch) -> None:
    service = _service(
        tmp_path,
        reader=lambda _start, _end: {
            0: SmsSummaryCounts(ok_count=8, ng_count=2),
            1: SmsSummaryCounts(ok_count=3, ng_count=5),
        },
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)

    assert service.run_due_summaries(WINDOW_END) == 1
    jobs = [service._queue.get_nowait(), service._queue.get_nowait()]

    assert [job.context["channel_id"] for job in jobs] == [0, 1]
    assert [job.context["device_name"] for job in jobs] == ["工位1", "工位2"]
    assert [(job.context["ok_count"], job.context["ng_count"]) for job in jobs] == [
        (8, 2),
        (3, 5),
    ]


def test_all_zero_window_advances_without_queuing(tmp_path, monkeypatch) -> None:
    service = _service(
        tmp_path,
        reader=lambda _start, _end: {0: SmsSummaryCounts(), 1: SmsSummaryCounts()},
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)

    assert service.run_due_summaries(WINDOW_END) == 1
    assert service._queue.qsize() == 0
    state = json.loads(
        (tmp_path / "sms_summary_state.json").read_text(encoding="utf-8")
    )
    assert state["window_start"] == WINDOW_END.isoformat(timespec="seconds")


def test_disabled_service_skips_io_and_advances_window(tmp_path) -> None:
    calls: list[tuple[datetime, datetime]] = []

    def reader(start: datetime, end: datetime) -> dict[int, SmsSummaryCounts]:
        calls.append((start, end))
        return {0: SmsSummaryCounts(ok_count=1)}

    service = _service(
        tmp_path,
        config=SmsServiceConfig(enabled=False),
        reader=reader,
    )

    assert service.run_due_summaries(WINDOW_END) == 1
    assert calls == []
    assert service._queue.qsize() == 0
    assert service._summary_state.window_start == WINDOW_END


def test_restart_does_not_requeue_finalized_window(tmp_path, monkeypatch) -> None:
    def reader(_start, _end):
        return {0: SmsSummaryCounts(ok_count=2, ng_count=1)}

    first = _service(tmp_path, reader=reader)
    monkeypatch.setattr(first, "_ensure_worker_locked", lambda: None)

    assert first.run_due_summaries(WINDOW_END) == 1
    assert first._queue.qsize() == 1
    first.shutdown(timeout=0)

    restarted = _service(
        tmp_path,
        reader=reader,
        wall_clock=lambda: WINDOW_END,
    )
    monkeypatch.setattr(restarted, "_ensure_worker_locked", lambda: None)

    assert restarted.run_due_summaries(WINDOW_END) == 0
    assert restarted._queue.qsize() == 0
    assert restarted._summary_state.window_start == WINDOW_END


def test_test_send_uses_current_incomplete_window_and_bypasses_enabled(
    tmp_path,
    monkeypatch,
) -> None:
    now = WINDOW_START + timedelta(hours=2)
    service = _service(
        tmp_path,
        config=SmsServiceConfig(
            enabled=False,
            port="COM_TEST",
            recipients=("13800138000",),
        ),
        reader=lambda _start, _end: {1: SmsSummaryCounts(ok_count=6, ng_count=2)},
        wall_clock=lambda: now,
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)

    receipt = service.queue_test_sms()

    assert receipt.status == "queued"
    job = service._queue.get_nowait()
    assert "非完整窗口" in job.message
    assert job.context["channel_id"] == 1
    assert job.context["ok_count"] == 6
    assert job.context["ng_count"] == 2


def test_completed_cycle_query_is_channel_scoped_and_excludes_in_progress() -> None:
    db = SessionLocal()
    token = uuid.uuid4().hex
    try:
        session_0 = DetectionSession(
            session_uuid=f"sms-summary-s0-{token}",
            start_time=WINDOW_START,
            channel_id=0,
        )
        session_1 = DetectionSession(
            session_uuid=f"sms-summary-s1-{token}",
            start_time=WINDOW_START,
            channel_id=1,
        )
        db.add_all([session_0, session_1])
        db.flush()
        db.add_all(
            [
                DetectionCycle(
                    cycle_uuid=f"sms-ok-{token}",
                    session_id=session_0.id,
                    start_time=WINDOW_START,
                    end_time=WINDOW_START + timedelta(minutes=1),
                    is_good=True,
                ),
                DetectionCycle(
                    cycle_uuid=f"sms-ng-{token}",
                    session_id=session_1.id,
                    start_time=WINDOW_START,
                    end_time=WINDOW_START + timedelta(minutes=2),
                    is_good=False,
                ),
                DetectionCycle(
                    cycle_uuid=f"sms-running-{token}",
                    session_id=session_0.id,
                    start_time=WINDOW_START,
                    end_time=None,
                    is_good=False,
                ),
                DetectionCycle(
                    cycle_uuid=f"sms-outside-{token}",
                    session_id=session_0.id,
                    start_time=WINDOW_END,
                    end_time=WINDOW_END,
                    is_good=False,
                ),
            ]
        )
        db.commit()

        counts = load_completed_cycle_counts(WINDOW_START, WINDOW_END)

        assert counts[0] == SmsSummaryCounts(ok_count=1, ng_count=0)
        assert counts[1] == SmsSummaryCounts(ok_count=0, ng_count=1)
    finally:
        db.rollback()
        db.query(DetectionCycle).filter(
            DetectionCycle.cycle_uuid.like(f"sms-%-{token}")
        ).delete(synchronize_session=False)
        db.query(DetectionSession).filter(
            DetectionSession.session_uuid.like(f"sms-summary-%-{token}")
        ).delete(synchronize_session=False)
        db.commit()
        db.close()
