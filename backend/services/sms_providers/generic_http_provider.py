"""厂商无关的 HTTP/HTTPS JSON 短信 Provider。"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

import requests

from backend.services.sms_providers.base_provider import (
    RecipientResult,
    SmsBatchResult,
    SmsProvider,
)


# 客户短信 API 文档到手后，只需在配置的 field_mapping 覆盖这一组逻辑字段。
# 左边是主程序稳定字段，右边是客户 JSON 的目标点路径；非空配置会完整替换默认映射。
DEFAULT_FIELD_MAPPING: dict[str, str] = {
    "phone_numbers": "phone_numbers",
    "template_id": "template_id",
    "sign_name": "sign_name",
    "params.device_name": "params.device_name",
    "params.alarm_type": "params.alarm_type",
    "params.alarm_message": "params.alarm_message",
    "params.event_time": "params.event_time",
    "params.time_range": "params.time_range",
    "params.ok_count": "params.ok_count",
    "params.ng_count": "params.ng_count",
}


class GenericHttpProvider(SmsProvider):
    """通过可配置字段映射向 HTTP/HTTPS JSON API 发送短信。"""

    name = "generic_http"

    def __init__(
        self,
        config: Any,
        *,
        request_func: Callable[..., object] | None = None,
        logger: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self._request = request_func or requests.request
        self._logger = logger or (lambda _message: None)
        self._stop_event = stop_event or threading.Event()

    def send(
        self,
        recipients: Sequence[str],
        message: str,
        *,
        message_id: str,
        event_name: str,
        raw_message: str,
        context: Mapping[str, object],
    ) -> SmsBatchResult:
        event_time = str(context.get("event_time", "") or "").strip()
        if not event_time:
            event_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logical_values: dict[str, object] = {
            "phone_numbers": list(recipients),
            "template_id": self.config.template_id,
            "sign_name": self.config.sign_name,
            "message": message,
            "message_id": message_id,
            "params.device_name": self._device_name(context),
            "params.alarm_type": event_name or "检测异常",
            "params.alarm_message": raw_message or message,
            "params.event_time": event_time,
            "params.time_range": str(context.get("time_range", "") or ""),
            "params.ok_count": int(context.get("ok_count", 0) or 0),
            "params.ng_count": int(context.get("ng_count", 0) or 0),
        }
        mapping = dict(self.config.field_mapping or {}) or DEFAULT_FIELD_MAPPING
        payload = self._build_payload(logical_values, mapping)
        headers = self._build_headers()
        max_attempts = max(1, int(self.config.retries) + 1)
        last_status: int | None = None
        last_error_code = "HTTP_UNKNOWN"
        last_detail = "云短信接口请求失败"
        retryable = False

        for attempt in range(1, max_attempts + 1):
            if self._stop_event.is_set():
                return self._failure_result(
                    recipients,
                    attempts=max(0, attempt - 1),
                    detail="短信服务正在关闭",
                    status_code=None,
                    error_code="SMS_STOPPED",
                    retryable=False,
                    event_time=event_time,
                )
            try:
                response = self._request(
                    method=str(self.config.request_method).upper(),
                    url=self.config.api_url,
                    json=payload,
                    headers=headers,
                    timeout=float(self.config.timeout_seconds),
                    verify=bool(self.config.verify_ssl),
                )
                last_status = int(getattr(response, "status_code"))
                if 200 <= last_status < 300:
                    reference = self._response_reference(response, message_id)
                    results = tuple(
                        RecipientResult(
                            phone=phone,
                            success=True,
                            attempts=attempt,
                            detail="云短信接口已接受请求；最终送达由短信服务商确认",
                            message_reference=reference,
                            status_code=last_status,
                        )
                        for phone in recipients
                    )
                    return SmsBatchResult(
                        results,
                        provider_metadata={"event_time": event_time},
                    )
                retryable = last_status >= 500 or last_status in {408, 429}
                last_error_code = f"HTTP_{last_status}"
                last_detail = self._safe_http_error(response, last_status)
            except requests.Timeout:
                retryable = True
                last_status = None
                last_error_code = "HTTP_TIMEOUT"
                last_detail = "云短信接口请求超时"
            except requests.ConnectionError:
                retryable = True
                last_status = None
                last_error_code = "HTTP_OFFLINE"
                last_detail = "无法连接云短信接口"
            except requests.RequestException:
                retryable = True
                last_status = None
                last_error_code = "HTTP_REQUEST_ERROR"
                last_detail = "云短信接口网络请求异常"
            except Exception as exc:
                retryable = False
                last_status = None
                last_error_code = "HTTP_CLIENT_ERROR"
                last_detail = f"云短信客户端异常：{type(exc).__name__}"

            self._logger(
                f"云短信第 {attempt}/{max_attempts} 次请求失败：{last_error_code}"
            )
            if not retryable or attempt >= max_attempts:
                return self._failure_result(
                    recipients,
                    attempts=attempt,
                    detail=last_detail,
                    status_code=last_status,
                    error_code=last_error_code,
                    retryable=retryable,
                    event_time=event_time,
                )
            self._stop_event.wait(self._retry_delay(attempt - 1))

        raise AssertionError("generic_http retry loop must return")

    def _retry_delay(self, index: int) -> float:
        backoff = tuple(self.config.retry_backoff_seconds or ())
        if not backoff:
            return 0.0
        return max(0.0, float(backoff[min(index, len(backoff) - 1)]))

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        if self.config.access_key:
            headers["X-Access-Key"] = self.config.access_key
        if self.config.access_secret:
            headers["X-Access-Secret"] = self.config.access_secret
        return headers

    @staticmethod
    def _device_name(context: Mapping[str, object]) -> str:
        explicit = str(context.get("device_name", "") or "").strip()
        if explicit:
            return explicit
        channel = context.get("channel_id")
        return f"工位 {channel}" if channel is not None else "默认工位"

    @classmethod
    def _build_payload(
        cls,
        logical_values: Mapping[str, object],
        mapping: Mapping[str, str],
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        for source_path, target_path in mapping.items():
            if source_path not in logical_values:
                continue
            cls._set_path(payload, target_path, logical_values[source_path])
        return payload

    @staticmethod
    def _set_path(payload: dict[str, object], path: str, value: object) -> None:
        current = payload
        parts = path.split(".")
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        current[parts[-1]] = value

    @staticmethod
    def _response_reference(response: object, fallback: str) -> str:
        try:
            payload = response.json()
        except Exception:
            return fallback
        if isinstance(payload, Mapping):
            for key in ("message_id", "request_id", "biz_id", "bizId", "id"):
                value = payload.get(key)
                if value not in (None, ""):
                    return str(value)
        return fallback

    def _safe_http_error(self, response: object, status: int) -> str:
        message = ""
        try:
            payload = response.json()
            if isinstance(payload, Mapping):
                for key in ("message", "detail", "error", "msg"):
                    value = payload.get(key)
                    if isinstance(value, (str, int, float)):
                        message = str(value)
                        break
        except Exception:
            message = ""
        message = self._redact_secrets(message)[:200]
        return f"云短信接口返回 HTTP {status}" + (f"：{message}" if message else "")

    def _redact_secrets(self, value: str) -> str:
        redacted = value
        for secret in (
            self.config.token,
            self.config.access_key,
            self.config.access_secret,
        ):
            if secret:
                redacted = redacted.replace(str(secret), "***")
        return redacted

    @staticmethod
    def _failure_result(
        recipients: Sequence[str],
        *,
        attempts: int,
        detail: str,
        status_code: int | None,
        error_code: str,
        retryable: bool,
        event_time: str,
    ) -> SmsBatchResult:
        return SmsBatchResult(
            tuple(
                RecipientResult(
                    phone=phone,
                    success=False,
                    attempts=attempts,
                    detail=detail,
                    status_code=status_code,
                    error_code=error_code,
                    retryable=retryable,
                )
                for phone in recipients
            ),
            provider_metadata={"event_time": event_time},
        )
