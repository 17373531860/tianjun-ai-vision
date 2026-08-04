"""WxPusher 接入 SmsService：入队、mock HTTP、无手机号也能发送。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import requests

from backend.services.sms_service import SmsService, SmsServiceConfig


class _OkResponse:
    status_code = 200

    def json(self) -> dict[str, object]:
        return {"code": 1000, "msg": "处理成功", "data": [{"messageId": 42}]}


def _wx_config(**overrides: object) -> SmsServiceConfig:
    values: dict[str, object] = {
        "enabled": True,
        "provider": "wxpusher",
        "recipients": (),
        "wxpusher_app_token": "AT_test_token",
        "wxpusher_uids": ("UID_demo",),
        "wxpusher_topic_ids": (),
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


def test_wxpusher_queue_summary_sends_without_phone_numbers(tmp_path) -> None:
    calls: list[dict[str, object]] = []
    done = threading.Event()
    results: list[object] = []

    def request(**kwargs: object) -> _OkResponse:
        calls.append(kwargs)
        return _OkResponse()

    service = SmsService(
        _wx_config(),
        http_request=request,
        offline_queue_path=tmp_path / "sms_offline_queue.db",
    )
    start = datetime(2026, 8, 4, 8, 0)
    receipt = service.queue_summary_sms(
        channel_id=0,
        window_start=start,
        window_end=start + timedelta(hours=12),
        ok_count=3,
        ng_count=2,
        callback=lambda result: (results.append(result), done.set()),
    )

    assert receipt.success is True
    assert receipt.queued is True
    assert done.wait(1.5)
    assert _wait_until(lambda: len(calls) == 1)
    payload = calls[0]["json"]
    assert payload["appToken"] == "AT_test_token"
    assert payload["uids"] == ["UID_demo"]
    assert results[0].success is True
    service.shutdown(timeout=0.5)


def test_wxpusher_test_send_uses_configured_targets(tmp_path) -> None:
    calls: list[dict[str, object]] = []

    def request(**kwargs: object) -> _OkResponse:
        calls.append(kwargs)
        return _OkResponse()

    service = SmsService(
        _wx_config(),
        http_request=request,
        offline_queue_path=tmp_path / "offline.db",
    )
    # 同步测试路径不走汇总模板渲染，直接验证 Provider 接线与 UID 投递
    result = service.send_test_sms(None, "WxPusher 通道测试")

    assert result.success is True
    assert len(calls) == 1
    assert calls[0]["json"]["uids"] == ["UID_demo"]
    assert "WxPusher 通道测试" in calls[0]["json"]["content"]
    service.shutdown(timeout=0.5)


def test_wxpusher_retryable_failure_goes_offline(tmp_path) -> None:
    path = tmp_path / "sms_offline_queue.db"
    done = threading.Event()
    results: list[object] = []

    def offline_request(**_kwargs: object):
        raise requests.ConnectionError("fake offline")

    service = SmsService(
        _wx_config(),
        http_request=offline_request,
        offline_queue_path=path,
    )
    start = datetime(2026, 8, 4, 8, 0)
    receipt = service.queue_summary_sms(
        channel_id=1,
        window_start=start,
        window_end=start + timedelta(hours=12),
        ok_count=1,
        ng_count=1,
        callback=lambda result: (results.append(result), done.set()),
    )

    assert receipt.queued is True
    assert done.wait(1.5)
    assert results[0].offline_queued is True
    assert path.exists()
    service.shutdown(timeout=0.5)
