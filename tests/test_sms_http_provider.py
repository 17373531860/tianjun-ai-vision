"""generic_http Provider 的请求映射、重试与错误分类回归。"""

from __future__ import annotations

from types import SimpleNamespace

import requests

from backend.services.sms_providers.generic_http_provider import GenericHttpProvider


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object | None = None,
        *,
        json_error: Exception | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        self.text = text

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def _config(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "api_url": "https://sms.example.test/v1/send",
        "request_method": "POST",
        "timeout_seconds": 10.0,
        "token": "secret-token",
        "access_key": "",
        "access_secret": "",
        "sign_name": "智能检测系统",
        "template_id": "alarm_001",
        "verify_ssl": True,
        "field_mapping": {},
        "retries": 2,
        "retry_backoff_seconds": (0.0, 0.0, 0.0),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _send(provider: GenericHttpProvider):
    return provider.send(
        ("13800138000", "+8613900139000"),
        "【天军AI视觉】装配 NG：缺少压板",
        message_id="sms-test-1",
        event_name="装配 NG",
        raw_message="缺少压板",
        context={
            "device_name": "1号工位",
            "event_time": "2026-08-02 12:34:56",
            "time_range": "2026-08-02 00:00~2026-08-02 12:00",
            "ok_count": 18,
            "ng_count": 2,
        },
    )


def test_generic_http_normal_send_uses_json_bearer_and_multiple_numbers() -> None:
    calls: list[dict[str, object]] = []

    def request(**kwargs: object) -> _FakeResponse:
        calls.append(kwargs)
        return _FakeResponse(200, {"message_id": "cloud-42"})

    provider = GenericHttpProvider(_config(), request_func=request)

    result = _send(provider)

    assert result.success
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://sms.example.test/v1/send"
    assert calls[0]["headers"] == {"Authorization": "Bearer secret-token"}
    assert calls[0]["timeout"] == 10.0
    assert calls[0]["verify"] is True
    assert calls[0]["json"] == {
        "phone_numbers": ["13800138000", "+8613900139000"],
        "template_id": "alarm_001",
        "sign_name": "智能检测系统",
        "params": {
            "device_name": "1号工位",
            "alarm_type": "装配 NG",
            "alarm_message": "缺少压板",
            "event_time": "2026-08-02 12:34:56",
            "time_range": "2026-08-02 00:00~2026-08-02 12:00",
            "ok_count": 18,
            "ng_count": 2,
        },
    }
    assert result.provider_metadata["event_time"] == "2026-08-02 12:34:56"
    assert all(item.message_reference == "cloud-42" for item in result.results)


def test_generic_http_field_mapping_is_the_single_vendor_mapping_point() -> None:
    captured: dict[str, object] = {}

    def request(**kwargs: object) -> _FakeResponse:
        captured.update(kwargs)
        return _FakeResponse(202, {"request_id": "mapped-1"})

    provider = GenericHttpProvider(
        _config(
            field_mapping={
                "phone_numbers": "mobiles",
                "template_id": "template.code",
                "params.alarm_message": "variables.reason",
                "message_id": "client_request_id",
            }
        ),
        request_func=request,
    )

    result = _send(provider)

    assert result.success
    assert captured["json"] == {
        "mobiles": ["13800138000", "+8613900139000"],
        "template": {"code": "alarm_001"},
        "variables": {"reason": "缺少压板"},
        "client_request_id": "sms-test-1",
    }


def test_generic_http_401_is_not_retried() -> None:
    calls = 0

    def request(**_kwargs: object) -> _FakeResponse:
        nonlocal calls
        calls += 1
        return _FakeResponse(401, {"message": "bad token"})

    result = _send(GenericHttpProvider(_config(), request_func=request))

    assert not result.success
    assert calls == 1
    assert result.results[0].attempts == 1
    assert result.results[0].status_code == 401
    assert result.results[0].error_code == "HTTP_401"
    assert result.results[0].retryable is False


def test_generic_http_timeout_retries_then_reports_retryable_failure() -> None:
    calls = 0

    def request(**_kwargs: object) -> _FakeResponse:
        nonlocal calls
        calls += 1
        raise requests.Timeout("fake timeout")

    result = _send(GenericHttpProvider(_config(), request_func=request))

    assert not result.success
    assert calls == 3
    assert result.results[0].attempts == 3
    assert result.results[0].error_code == "HTTP_TIMEOUT"
    assert result.results[0].retryable is True


def test_generic_http_success_with_non_json_body_does_not_crash() -> None:
    provider = GenericHttpProvider(
        _config(retries=0),
        request_func=lambda **_kwargs: _FakeResponse(
            204,
            json_error=ValueError("not json"),
            text="accepted",
        ),
    )

    result = _send(provider)

    assert result.success
    assert result.results[0].status_code == 204
    assert result.results[0].message_reference == "sms-test-1"
