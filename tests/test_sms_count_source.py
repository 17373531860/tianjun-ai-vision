"""汇总数字口径：panel（面板会话）默认 / window（时间窗）。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from backend.services.sms_service import SmsService, SmsServiceConfig
from backend.services.sms_summary import SmsChannelSnapshot, SmsSummaryCounts


def test_panel_source_uses_session_snapshot_not_window_reader(tmp_path, monkeypatch) -> None:
    calls: list[tuple[datetime, datetime]] = []

    def reader(start: datetime, end: datetime):
        calls.append((start, end))
        return {0: SmsSummaryCounts(ok_count=99, ng_count=1)}

    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            port="COM_TEST",
            recipients=("13800138000",),
            summary_schedule_mode="daily_shift",
            summary_count_source="panel",
            summary_send_mode="per_channel",
            shift_start_hour=8,
            shift_end_hour=20,
            cooldown_seconds=0,
        ),
        summary_state_path=tmp_path / "state.json",
        offline_queue_path=tmp_path / "offline.db",
        summary_reader=reader,
        wall_clock=lambda: datetime(2026, 8, 4, 10, 0, 0),
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)
    monkeypatch.setattr(
        "backend.services.sms_service.load_panel_session_snapshots",
        lambda as_of=None: {
            0: SmsChannelSnapshot(
                counts=SmsSummaryCounts(ok_count=0, ng_count=11),
                range_start=datetime(2026, 8, 4, 15, 0, 0),
                range_end=datetime(2026, 8, 4, 20, 0, 0),
            ),
            1: SmsChannelSnapshot(
                counts=SmsSummaryCounts(ok_count=0, ng_count=0),
                range_start=datetime(2026, 8, 4, 15, 0, 0),
                range_end=datetime(2026, 8, 4, 20, 0, 0),
            ),
        },
    )
    service.start_summary_scheduler()
    try:
        assert service.run_due_summaries(datetime(2026, 8, 4, 20, 0, 0)) == 1
        assert calls == []  # 未走时间窗 reader
        job = service._queue.get_nowait()
        assert job.context["ok_count"] == 0
        assert job.context["ng_count"] == 11
        assert "15:00" in job.context["time_range"]
        assert service._queue.qsize() == 0  # 工位2 全0 不发
    finally:
        service.shutdown(timeout=0.5)


def test_window_source_keeps_schedule_window_counts(tmp_path, monkeypatch) -> None:
    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            port="COM_TEST",
            recipients=("13800138000",),
            summary_schedule_mode="daily_shift",
            summary_count_source="window",
            summary_send_mode="per_channel",
            shift_start_hour=8,
            shift_end_hour=20,
            cooldown_seconds=0,
        ),
        summary_state_path=tmp_path / "state.json",
        offline_queue_path=tmp_path / "offline.db",
        summary_reader=lambda _s, _e: {0: SmsSummaryCounts(ok_count=5, ng_count=2)},
        wall_clock=lambda: datetime(2026, 8, 4, 10, 0, 0),
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)
    monkeypatch.setattr(
        "backend.services.sms_service.load_panel_session_snapshots",
        MagicMock(side_effect=AssertionError("panel reader must not run")),
    )
    service.start_summary_scheduler()
    try:
        assert service.run_due_summaries(datetime(2026, 8, 4, 20, 0, 0)) == 1
        job = service._queue.get_nowait()
        assert job.context["ok_count"] == 5
        assert job.context["ng_count"] == 2
        assert "08:00" in job.context["time_range"]
    finally:
        service.shutdown(timeout=0.5)


def test_merged_panel_source_keeps_each_channel_counts_and_earliest_start(
    tmp_path, monkeypatch
) -> None:
    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            provider="aliyun",
            recipients=("13800138000",),
            aliyun_access_key_id="AKID_TEST",
            aliyun_access_key_secret="AKSECRET_TEST",
            aliyun_sign_name="天军视觉",
            aliyun_template_code="SMS_MERGED_TEST",
            summary_schedule_mode="daily_shift",
            summary_count_source="panel",
            summary_send_mode="merged_detail",
            summary_channel_ids=(0, 1),
            shift_start_hour=8,
            shift_end_hour=20,
            cooldown_seconds=0,
        ),
        summary_state_path=tmp_path / "state.json",
        offline_queue_path=tmp_path / "offline.db",
        summary_reader=MagicMock(
            side_effect=AssertionError("window reader must not run")
        ),
        wall_clock=lambda: datetime(2026, 8, 4, 10, 0, 0),
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)
    monkeypatch.setattr(
        "backend.services.sms_service.load_panel_session_snapshots",
        lambda as_of=None: {
            0: SmsChannelSnapshot(
                counts=SmsSummaryCounts(ok_count=8, ng_count=2),
                range_start=datetime(2026, 8, 4, 15, 0, 0),
                range_end=datetime(2026, 8, 4, 20, 0, 0),
            ),
            1: SmsChannelSnapshot(
                counts=SmsSummaryCounts(ok_count=3, ng_count=1),
                range_start=datetime(2026, 8, 4, 14, 30, 0),
                range_end=datetime(2026, 8, 4, 20, 0, 0),
            ),
        },
    )
    service.start_summary_scheduler()
    try:
        assert service.run_due_summaries(datetime(2026, 8, 4, 20, 0, 0)) == 1
        job = service._queue.get_nowait()
        assert job.context["time_range"] == ("2026-08-04 14:30~2026-08-04 20:00")
        assert [
            (item["ok_count"], item["ng_count"]) for item in job.context["channels"]
        ] == [(8, 2), (3, 1)]
        assert service._queue.qsize() == 0
    finally:
        service.shutdown(timeout=0.5)
