"""统一短信门面的 generic_http 入队、断网落盘与恢复补发回归。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import requests

from backend.services.sms_offline_queue import SmsOfflineQueue
from backend.services.sms_service import SmsService, SmsServiceConfig


class _Response:
    status_code = 200

    def json(self) -> dict[str, str]:
        return {"message_id": "cloud-ok"}


def _http_config(**overrides: object) -> SmsServiceConfig:
    values: dict[str, object] = {
        "enabled": True,
        "provider": "generic_http",
        "recipients": ("13800138000",),
        "api_url": "https://sms.example.test/send",
        "token": "token",
        "template_id": "alarm_001",
        "sign_name": "智能检测系统",
        "retries": 0,
        "retry_backoff_seconds": (0.01,),
        "cooldown_seconds": 0,
        "offline_queue_max": 20,
        "offline_ttl_seconds": 60,
    }
    values.update(overrides)
    return SmsServiceConfig(**values)


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def _queue_summary(service: SmsService, *, offset_hours: int = 0, callback=None):
    start = datetime(2026, 8, 2, 8, 0) + timedelta(hours=offset_hours)
    return service.queue_summary_sms(
        channel_id=0,
        window_start=start,
        window_end=start + timedelta(hours=12),
        ok_count=4,
        ng_count=1,
        callback=callback,
    )


def test_generic_http_disabled_has_zero_network_and_creates_no_offline_db(
    tmp_path,
) -> None:
    calls = 0
    path = tmp_path / "sms_offline_queue.db"

    def request(**_kwargs: object) -> _Response:
        nonlocal calls
        calls += 1
        return _Response()

    service = SmsService(
        _http_config(enabled=False),
        http_request=request,
        offline_queue_path=path,
    )

    receipt = _queue_summary(service)

    assert receipt.success is False
    assert receipt.queued is False
    assert receipt.error_code == "SMS_DISABLED"
    assert calls == 0
    assert not path.exists()


def test_generic_http_disconnect_is_persisted_and_replayed_after_restart(
    tmp_path,
) -> None:
    path = tmp_path / "sms_offline_queue.db"
    callback_done = threading.Event()
    callback_results = []

    def offline_request(**_kwargs: object):
        raise requests.ConnectionError("fake offline")

    first = SmsService(
        _http_config(),
        http_request=offline_request,
        offline_queue_path=path,
    )
    receipt = _queue_summary(
        first,
        callback=lambda result: (callback_results.append(result), callback_done.set()),
    )

    assert receipt.success is True
    assert receipt.queued is True
    assert receipt.message_id
    assert receipt.status_code == 202
    assert callback_done.wait(1.0)
    assert callback_results[0].offline_queued is True
    first.shutdown(timeout=0.5)

    persisted = SmsOfflineQueue(path, max_items=20, ttl_seconds=60)
    assert persisted.count() == 1

    second = SmsService(
        _http_config(),
        http_request=lambda **_kwargs: _Response(),
        offline_queue_path=path,
    )
    try:
        assert _wait_until(lambda: persisted.count() == 0), (
            "进程重启后未自动补发离线短信"
        )
    finally:
        second.shutdown(timeout=0.5)


def test_summary_windows_bypass_legacy_instant_cooldown(monkeypatch, tmp_path) -> None:
    service = SmsService(
        _http_config(cooldown_seconds=60),
        http_request=lambda **_kwargs: _Response(),
        offline_queue_path=tmp_path / "offline.db",
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)

    first = _queue_summary(service, offset_hours=0)
    second = _queue_summary(service, offset_hours=12)

    assert first.queued is True
    assert second.queued is True
