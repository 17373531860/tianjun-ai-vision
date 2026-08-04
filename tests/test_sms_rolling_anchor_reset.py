"""滚动 12h：后端冷启动重锚；配置热替换 / 班次模式不重锚。"""

from __future__ import annotations

import json
from datetime import datetime

from backend.services.sms_service import SmsService, SmsServiceConfig
from backend.services.sms_summary import SmsSummaryCounts, SmsSummaryState, SmsSummaryStateStore


BOOT = datetime(2026, 8, 4, 13, 30, 0)
OLD = datetime(2026, 8, 3, 22, 19, 28)


def _rolling(**overrides: object) -> SmsServiceConfig:
    values: dict[str, object] = {
        "enabled": True,
        "port": "COM_TEST",
        "recipients": ("13800138000",),
        "summary_schedule_mode": "rolling_12h",
    }
    values.update(overrides)
    return SmsServiceConfig(**values)


def test_cold_start_resets_rolling_anchor(tmp_path) -> None:
    state_path = tmp_path / "sms_summary_state.json"
    SmsSummaryStateStore(state_path).save(
        SmsSummaryState(
            window_start=OLD,
            last_finalized_window_id="old__window",
        )
    )
    service = SmsService(
        _rolling(),
        summary_state_path=state_path,
        offline_queue_path=tmp_path / "offline.db",
        wall_clock=lambda: BOOT,
        summary_reader=lambda _s, _e: {},
    )
    service.start_summary_scheduler(reset_rolling_anchor=True)
    try:
        assert service._summary_state.window_start == BOOT
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload["window_start"] == "2026-08-04T13:30:00"
        assert payload["last_finalized_window_id"] == ""
    finally:
        service.shutdown(timeout=0.5)


def test_hot_replace_start_keeps_rolling_anchor(tmp_path) -> None:
    state_path = tmp_path / "sms_summary_state.json"
    SmsSummaryStateStore(state_path).save(SmsSummaryState(window_start=OLD))
    service = SmsService(
        _rolling(),
        summary_state_path=state_path,
        offline_queue_path=tmp_path / "offline.db",
        wall_clock=lambda: BOOT,
        summary_reader=lambda _s, _e: {},
    )
    service.start_summary_scheduler()  # 默认 False：模拟配置热替换续接
    try:
        assert service._summary_state.window_start == OLD
    finally:
        service.shutdown(timeout=0.5)


def test_cold_start_reset_ignored_for_daily_shift(tmp_path) -> None:
    state_path = tmp_path / "sms_summary_state.json"
    day = datetime(2026, 8, 4, 8, 0, 0)
    SmsSummaryStateStore(state_path).save(SmsSummaryState(window_start=day))
    service = SmsService(
        _rolling(
            summary_schedule_mode="daily_shift",
            shift_start_hour=8,
            shift_end_hour=20,
            send_night_window=False,
        ),
        summary_state_path=state_path,
        offline_queue_path=tmp_path / "offline.db",
        wall_clock=lambda: BOOT,
        summary_reader=lambda _s, _e: {0: SmsSummaryCounts(ok_count=1)},
    )
    service.start_summary_scheduler(reset_rolling_anchor=True)
    try:
        assert service._summary_state.window_start == day
        # 未到 20:00 不应因冷启动重锚而误发
        assert service.run_due_summaries(BOOT) == 0
    finally:
        service.shutdown(timeout=0.5)
