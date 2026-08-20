"""短信滚动 12 小时汇总与持久化水位回归。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta

from backend.db.database import SessionLocal
from backend.models.models import DetectionCycle, DetectionSession
from backend.services.sms_service import (
    SummaryChannelMetrics,
    SummaryPayload,
    SmsService,
    SmsServiceConfig,
)
from backend.services.sms_summary import (
    SUMMARY_WINDOW_SECONDS,
    SmsSummaryCounts,
    load_completed_cycle_counts,
    summary_window_id,
)


WINDOW_START = datetime(2026, 8, 2, 8, 0, 0)
WINDOW_END = WINDOW_START + timedelta(seconds=SUMMARY_WINDOW_SECONDS)


def _enabled_config(
    *,
    summary_send_mode: str = "merged_detail",
    summary_channel_ids: tuple[int, ...] = (),
) -> SmsServiceConfig:
    return SmsServiceConfig(
        enabled=True,
        provider="aliyun",
        recipients=("13800138000",),
        aliyun_access_key_id="AKID_TEST",
        aliyun_access_key_secret="AKSECRET_TEST",
        aliyun_sign_name="天军视觉",
        aliyun_template_code="SMS_MERGED_TEST",
        cooldown_seconds=60,
        summary_count_source="window",
        summary_send_mode=summary_send_mode,
        summary_channel_ids=summary_channel_ids,
    )


def _service(tmp_path, *, config=None, reader=None, wall_clock=None) -> SmsService:
    return SmsService(
        config or _enabled_config(),
        summary_state_path=tmp_path / "sms_summary_state.json",
        offline_queue_path=tmp_path / "sms_offline_queue.db",
        # 隔离: 不注入会读 DATA_DIR/workstation_config.json —— 同进程先跑过的
        # 合成流水线测试会把 channel_count=1 写进去, 短路 payload-key 兜底分支
        workstation_config_path=tmp_path / "workstation_config.json",
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
        config=_enabled_config(summary_send_mode="per_channel"),
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


def test_merged_summary_queues_one_job_with_two_channel_rates_and_totals(
    tmp_path, monkeypatch
) -> None:
    service = _service(
        tmp_path,
        config=_enabled_config(
            summary_send_mode="merged_detail",
            summary_channel_ids=(0, 1),
        ),
        reader=lambda _start, _end: {
            0: SmsSummaryCounts(ok_count=8, ng_count=2),
            1: SmsSummaryCounts(ok_count=3, ng_count=1),
        },
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)

    assert service.run_due_summaries(WINDOW_END) == 1
    assert service._queue.qsize() == 1
    job = service._queue.get_nowait()
    params = job.context["template_params"]
    assert params == {
        "time_range": "2026-08-02 08:00~2026-08-02 20:00",
        "device_name": "工位1+工位2",
        "ch1_ok": "8",
        "ch1_ng": "2",
        "ch1_total": "10",
        "ch1_ok_rate": "80",
        "ch1_ng_rate": "20",
        "ch2_ok": "3",
        "ch2_ng": "1",
        "ch2_total": "4",
        "ch2_ok_rate": "75",
        "ch2_ng_rate": "25",
        "total_ok": "11",
        "total_ng": "3",
        "total": "14",
        "ok_rate": "79",
        "ng_rate": "21",
        # v3.53 录像归档三期: 窗口内归档成败数 (测试库无归档记录 = 0)
        "archive_success": "0",
        "archive_failed": "0",
    }
    assert job.context["total_ok"] == 11
    assert job.context["total_ng"] == 3
    assert [item["channel_id"] for item in job.context["channels"]] == [0, 1]
    assert "工位1 OK8 NG2 合格80% NG率20%" in job.message
    assert "工位2 OK3 NG1 合格75% NG率25%" in job.message

    # 水位已经推进；同一窗口再次触发不得重复入队。
    assert service.run_due_summaries(WINDOW_END) == 0
    assert service._queue.qsize() == 0


def test_merged_summary_worker_sends_one_aliyun_request_with_named_params(
    tmp_path,
) -> None:
    request_calls: list[dict[str, object]] = []
    worker_done = threading.Event()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"Code": "OK", "BizId": "merged-worker-1"}

    def fake_request(**kwargs: object) -> FakeResponse:
        request_calls.append(dict(kwargs))
        return FakeResponse()

    def logger(message: str) -> None:
        if "告警短信" in message and "完成" in message:
            worker_done.set()

    service = SmsService(
        _enabled_config(
            summary_send_mode="merged_detail",
            summary_channel_ids=(0, 1),
        ),
        logger=logger,
        http_request=fake_request,
        summary_state_path=tmp_path / "sms_summary_state.json",
        offline_queue_path=tmp_path / "sms_offline_queue.db",
        workstation_config_path=tmp_path / "workstation_config.json",
        summary_reader=lambda _start, _end: {
            0: SmsSummaryCounts(ok_count=8, ng_count=2),
            1: SmsSummaryCounts(ok_count=3, ng_count=1),
        },
        wall_clock=lambda: WINDOW_START,
    )
    try:
        assert service.run_due_summaries(WINDOW_END) == 1
        assert worker_done.wait(2.0), "Aliyun worker 未在超时内完成"
        deadline = time.monotonic() + 1.0
        while service._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service._queue.unfinished_tasks == 0
        assert len(request_calls) == 1
        params = request_calls[0]["params"]
        assert isinstance(params, dict)
        template_params = json.loads(params["TemplateParam"])
        assert template_params["ch1_ok"] == "8"
        assert template_params["ch1_ok_rate"] == "80"
        assert template_params["ch1_ng_rate"] == "20"
        assert template_params["ch2_ok"] == "3"
        assert template_params["ch2_ok_rate"] == "75"
        assert template_params["ch2_ng_rate"] == "25"
        assert template_params["total"] == "14"
        assert template_params["ok_rate"] == "79"
        assert template_params["ng_rate"] == "21"
    finally:
        service.shutdown(timeout=1.0)


def test_merged_summary_selected_zero_channel_is_included_and_all_zero_skips(
    tmp_path, monkeypatch
) -> None:
    service = _service(
        tmp_path,
        config=_enabled_config(
            summary_send_mode="merged_detail",
            summary_channel_ids=(0, 1),
        ),
        reader=lambda _start, _end: {0: SmsSummaryCounts(), 1: SmsSummaryCounts()},
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)

    assert service.run_due_summaries(WINDOW_END) == 1
    assert service._queue.qsize() == 0


def test_merged_empty_selection_uses_workstation_count_and_ignores_stale_channel(
    tmp_path, monkeypatch
) -> None:
    workstation_path = tmp_path / "workstation_config.json"
    workstation_path.write_text(
        json.dumps({"channel_count": 2}),
        encoding="utf-8",
    )
    service = SmsService(
        _enabled_config(summary_channel_ids=()),
        summary_state_path=tmp_path / "sms_summary_state.json",
        offline_queue_path=tmp_path / "sms_offline_queue.db",
        workstation_config_path=workstation_path,
        summary_reader=lambda _start, _end: {
            0: SmsSummaryCounts(ok_count=4, ng_count=1),
            2: SmsSummaryCounts(ok_count=99, ng_count=1),
        },
        wall_clock=lambda: WINDOW_START,
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)

    assert service.run_due_summaries(WINDOW_END) == 1
    job = service._queue.get_nowait()
    assert [item["channel_id"] for item in job.context["channels"]] == [0, 1]
    assert job.context["template_params"]["ch2_ok"] == "0"
    assert job.context["template_params"]["ch2_ng"] == "0"
    assert job.context["template_params"]["ch2_ok_rate"] == "0"
    assert job.context["template_params"]["ch2_ng_rate"] == "0"
    assert "ch3_ok" not in job.context["template_params"]
    assert job.context["total"] == 5


def test_merged_state_sentinel_prevents_requeue_after_restart(
    tmp_path, monkeypatch
) -> None:
    state_path = tmp_path / "sms_summary_state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "window_start": WINDOW_START.isoformat(timespec="seconds"),
                "last_finalized_window_id": "",
                "queued_window_id": summary_window_id(WINDOW_START, WINDOW_END),
                "queued_channels": [-1],
            }
        ),
        encoding="utf-8",
    )
    service = _service(
        tmp_path,
        reader=lambda _start, _end: {
            0: SmsSummaryCounts(ok_count=8, ng_count=2),
            1: SmsSummaryCounts(ok_count=3, ng_count=1),
        },
        wall_clock=lambda: WINDOW_END,
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)

    assert service.run_due_summaries(WINDOW_END) == 1
    assert service._queue.qsize() == 0
    assert service._summary_state.window_start == WINDOW_END


def test_summary_payload_zero_denominator_rates_are_string_zero() -> None:
    payload = SummaryPayload(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        channels=(
            SummaryChannelMetrics(channel_id=0, ok_count=0, ng_count=0),
            SummaryChannelMetrics(channel_id=1, ok_count=1, ng_count=0),
        ),
    )

    context = payload.to_context()

    assert context["channels"][0]["ok_rate"] == "0"
    assert context["channels"][0]["ng_rate"] == "0"
    assert context["template_params"]["ch1_ok_rate"] == "0"
    assert context["template_params"]["ch1_ng_rate"] == "0"


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
            summary_count_source="window",
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
