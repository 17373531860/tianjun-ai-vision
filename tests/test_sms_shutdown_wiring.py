"""主程序两条关闭路径都必须收口短信 worker。"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_main_shutdown_paths_call_shared_sms_cleanup() -> None:
    source = (Path(__file__).parents[1] / "backend" / "main.py").read_text(
        encoding="utf-8"
    )

    assert "def _shutdown_sms_notifications(" in source
    assert source.count("_shutdown_sms_notifications()") >= 2


def test_shared_main_cleanup_calls_sms_service_with_finite_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend import main
    from backend.api import sms as sms_api

    calls: list[float] = []

    class FakeSmsService:
        def shutdown(self, timeout: float) -> None:
            calls.append(timeout)

    monkeypatch.setattr(sms_api, "get_sms_service", lambda: FakeSmsService())

    main._shutdown_sms_notifications()

    assert calls == [3.0]


def test_electron_cleanup_step_stops_sms_before_clearing_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend import main
    from backend.api import alarm as alarm_api

    order: list[str] = []

    class FakeVideoManager:
        is_running = True

        def _clear_all_caches(self) -> None:
            order.append("clear_runtime")

    class FakeAlarmRouter:
        managers: dict[int, object] = {}

        def disconnect_all(self) -> None:
            order.append("disconnect_alarm")

    monkeypatch.setattr(main, "get_video_manager", lambda: FakeVideoManager())
    monkeypatch.setattr(
        main, "_shutdown_sms_notifications", lambda: order.append("shutdown_sms")
    )
    monkeypatch.setattr(alarm_api, "alarm_router", FakeAlarmRouter())

    result = main.shutdown_step("cleanup")

    assert result["status"] == "success"
    assert order[:2] == ["shutdown_sms", "clear_runtime"]
