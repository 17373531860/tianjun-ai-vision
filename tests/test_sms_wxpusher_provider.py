"""WxPusher Provider：成功、业务失败、网络重试与脱敏回归。"""

from __future__ import annotations

from types import SimpleNamespace

import requests

from backend.services.sms_providers.wxpusher_provider import (
    DEFAULT_WXPUSHER_API_URL,
    WxpusherProvider,
    mask_wxpusher_secret,
    wxpusher_target_labels,
)


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object | None = None,
        *,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def _config(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "wxpusher_app_token": "AT_secret_token_demo",
        "wxpusher_uids": ("UID_worker_alpha",),
        "wxpusher_topic_ids": (101,),
        "wxpusher_content_type": 1,
        "wxpusher_summary_template": "【天军AI视觉】{time_range} OK={ok_count} NG={ng_count}",
        "wxpusher_api_url": DEFAULT_WXPUSHER_API_URL,
        "wxpusher_timeout_seconds": 8.0,
        "wxpusher_verify_ssl": True,
        "retries": 2,
        "retry_backoff_seconds": (0.0, 0.0, 0.0),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _send(provider: WxpusherProvider, *, message: str = "汇总正文"):
    return provider.send(
        (),
        message,
        message_id="sms-wx-1",
        event_name="12小时汇总",
        raw_message=message,
        context={
            "device_name": "2号工位",
            "time_range": "2026-08-04 00:00~2026-08-04 12:00",
            "ok_count": 10,
            "ng_count": 3,
            "event_time": "2026-08-04 12:00:00",
        },
    )


def test_wxpusher_target_labels_and_secret_mask() -> None:
    assert wxpusher_target_labels(("UID_a",), (9,)) == ("UID_a", "topic:9")
    assert mask_wxpusher_secret("AT_abcdefghij") == "AT_a***ghij"


def test_wxpusher_success_posts_expected_json() -> None:
    calls: list[dict[str, object]] = []

    def request(**kwargs: object) -> _FakeResponse:
        calls.append(kwargs)
        return _FakeResponse(
            200,
            {"code": 1000, "msg": "处理成功", "data": [{"messageId": 77}]},
        )

    result = _send(WxpusherProvider(_config(), request_func=request))

    assert result.success
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == DEFAULT_WXPUSHER_API_URL
    assert calls[0]["timeout"] == 8.0
    assert calls[0]["verify"] is True
    assert calls[0]["json"] == {
        "appToken": "AT_secret_token_demo",
        "content": "汇总正文",
        "summary": "【天军AI视觉】2026-08-04 00:00~2026-08-04 12:00 OK=10 NG=3",
        "contentType": 1,
        "uids": ["UID_worker_alpha"],
        "topicIds": [101],
    }
    assert {item.phone for item in result.results} == {
        "UID_worker_alpha",
        "topic:101",
    }
    assert all(item.message_reference == "77" for item in result.results)


def test_wxpusher_business_error_is_not_retryable() -> None:
    calls = 0

    def request(**_kwargs: object) -> _FakeResponse:
        nonlocal calls
        calls += 1
        return _FakeResponse(200, {"code": 1001, "msg": "appToken不正确"})

    result = _send(WxpusherProvider(_config(retries=2), request_func=request))

    assert not result.success
    assert calls == 1
    assert result.results[0].error_code == "WXPUSHER_1001"
    assert result.results[0].retryable is False


def test_wxpusher_connection_error_retries_then_fails() -> None:
    calls = 0

    def request(**_kwargs: object):
        nonlocal calls
        calls += 1
        raise requests.ConnectionError("offline")

    result = _send(WxpusherProvider(_config(retries=2), request_func=request))

    assert not result.success
    assert calls == 3
    assert result.results[0].error_code == "HTTP_OFFLINE"
    assert result.results[0].retryable is True
    assert result.retryable_failure is True


def test_wxpusher_missing_token_or_targets_fail_fast() -> None:
    calls = 0

    def request(**_kwargs: object) -> _FakeResponse:
        nonlocal calls
        calls += 1
        return _FakeResponse(200, {"code": 1000})

    no_token = _send(WxpusherProvider(_config(wxpusher_app_token=""), request_func=request))
    assert no_token.results[0].error_code == "WXPUSHER_NO_TOKEN"

    no_target = _send(
        WxpusherProvider(
            _config(wxpusher_uids=(), wxpusher_topic_ids=()),
            request_func=request,
        )
    )
    assert no_target.results[0].error_code == "WXPUSHER_NO_TARGET"
    assert calls == 0
