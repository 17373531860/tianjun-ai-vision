"""WxPusher 微信推送 Provider（第三通知通道）。"""

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

DEFAULT_WXPUSHER_API_URL = "https://wxpusher.zjiecode.com/api/send/message"
DEFAULT_WXPUSHER_SUMMARY_TEMPLATE = (
    "【天军AI视觉】{time_range} OK={ok_count} NG={ng_count}"
)
WXPUSHER_SUCCESS_CODE = 1000


def mask_wxpusher_secret(value: str, *, keep: int = 4) -> str:
    """对 appToken / UID 做日志脱敏。"""

    text = (value or "").strip()
    if len(text) <= keep * 2:
        return "***"
    return f"{text[:keep]}***{text[-keep:]}"


def wxpusher_target_labels(
    uids: Sequence[str], topic_ids: Sequence[int]
) -> tuple[str, ...]:
    """把 UID / Topic 转成 RecipientResult.phone 用的稳定标签。"""

    labels: list[str] = []
    for uid in uids:
        text = str(uid).strip()
        if text:
            labels.append(text)
    for topic_id in topic_ids:
        labels.append(f"topic:{int(topic_id)}")
    return tuple(labels)


class WxpusherProvider(SmsProvider):
    """调用 WxPusher HTTP JSON API；I/O 仅在 worker 中执行。"""

    name = "wxpusher"

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
        del recipients  # 目标以配置中的 UID/Topic 为准，不复用手机号列表
        uids = tuple(
            str(item).strip()
            for item in (getattr(self.config, "wxpusher_uids", ()) or ())
            if str(item).strip()
        )
        topic_ids = tuple(
            int(item) for item in (getattr(self.config, "wxpusher_topic_ids", ()) or ())
        )
        targets = wxpusher_target_labels(uids, topic_ids)
        if not targets:
            return self._failure_result(
                ("(none)",),
                attempts=0,
                detail="未配置 WxPusher UID 或 TopicId",
                status_code=None,
                error_code="WXPUSHER_NO_TARGET",
                retryable=False,
            )

        app_token = str(getattr(self.config, "wxpusher_app_token", "") or "").strip()
        if not app_token:
            return self._failure_result(
                targets,
                attempts=0,
                detail="未配置 WxPusher appToken",
                status_code=None,
                error_code="WXPUSHER_NO_TOKEN",
                retryable=False,
            )

        content = (message or raw_message or "").strip()
        if not content:
            return self._failure_result(
                targets,
                attempts=0,
                detail="推送内容为空",
                status_code=None,
                error_code="WXPUSHER_EMPTY_CONTENT",
                retryable=False,
            )

        event_time = str(context.get("event_time", "") or "").strip()
        if not event_time:
            event_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        payload = {
            "appToken": app_token,
            "content": content,
            "summary": self._render_summary(context, content, event_name),
            "contentType": int(getattr(self.config, "wxpusher_content_type", 1) or 1),
            "uids": list(uids),
            "topicIds": list(topic_ids),
        }
        api_url = str(
            getattr(self.config, "wxpusher_api_url", "") or DEFAULT_WXPUSHER_API_URL
        ).strip()
        timeout = float(getattr(self.config, "wxpusher_timeout_seconds", 10.0) or 10.0)
        verify_ssl = bool(getattr(self.config, "wxpusher_verify_ssl", True))
        max_attempts = max(1, int(getattr(self.config, "retries", 0) or 0) + 1)

        last_status: int | None = None
        last_error_code = "WXPUSHER_UNKNOWN"
        last_detail = "WxPusher 接口请求失败"
        retryable = False

        for attempt in range(1, max_attempts + 1):
            if self._stop_event.is_set():
                return self._failure_result(
                    targets,
                    attempts=max(0, attempt - 1),
                    detail="短信服务正在关闭",
                    status_code=None,
                    error_code="SMS_STOPPED",
                    retryable=False,
                )
            try:
                response = self._request(
                    method="POST",
                    url=api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                    verify=verify_ssl,
                )
                last_status = int(getattr(response, "status_code"))
                body = self._safe_json(response)
                code = body.get("code") if isinstance(body, Mapping) else None
                if 200 <= last_status < 300 and code == WXPUSHER_SUCCESS_CODE:
                    reference = self._message_reference(body, message_id)
                    return SmsBatchResult(
                        tuple(
                            RecipientResult(
                                phone=target,
                                success=True,
                                attempts=attempt,
                                detail="WxPusher 已接受推送请求",
                                message_reference=reference,
                                status_code=last_status,
                            )
                            for target in targets
                        ),
                        provider_metadata={
                            "event_time": event_time,
                            "wxpusher_code": code,
                        },
                    )

                api_msg = ""
                if isinstance(body, Mapping):
                    for key in ("msg", "message", "detail"):
                        value = body.get(key)
                        if isinstance(value, (str, int, float)):
                            api_msg = str(value)
                            break
                if code is not None:
                    last_error_code = f"WXPUSHER_{code}"
                    last_detail = f"WxPusher 返回 code={code}" + (
                        f"：{api_msg}" if api_msg else ""
                    )
                    # 业务错误通常不可重试（token/参数问题）
                    retryable = False
                else:
                    retryable = last_status >= 500 or last_status in {408, 429}
                    last_error_code = f"HTTP_{last_status}"
                    last_detail = f"WxPusher HTTP {last_status}" + (
                        f"：{api_msg}" if api_msg else ""
                    )
            except requests.Timeout:
                retryable = True
                last_status = None
                last_error_code = "HTTP_TIMEOUT"
                last_detail = "WxPusher 接口请求超时"
            except requests.ConnectionError:
                retryable = True
                last_status = None
                last_error_code = "HTTP_OFFLINE"
                last_detail = "无法连接 WxPusher 接口"
            except requests.RequestException:
                retryable = True
                last_status = None
                last_error_code = "HTTP_REQUEST_ERROR"
                last_detail = "WxPusher 网络请求异常"
            except Exception as exc:
                retryable = False
                last_status = None
                last_error_code = "HTTP_CLIENT_ERROR"
                last_detail = f"WxPusher 客户端异常：{type(exc).__name__}"

            self._logger(
                f"WxPusher 第 {attempt}/{max_attempts} 次失败：{last_error_code} "
                f"token={mask_wxpusher_secret(app_token)}"
            )
            if not retryable or attempt >= max_attempts:
                return self._failure_result(
                    targets,
                    attempts=attempt,
                    detail=self._redact(last_detail),
                    status_code=last_status,
                    error_code=last_error_code,
                    retryable=retryable,
                )
            self._stop_event.wait(self._retry_delay(attempt - 1))

        raise AssertionError("wxpusher retry loop must return")

    def _retry_delay(self, index: int) -> float:
        backoff = tuple(getattr(self.config, "retry_backoff_seconds", ()) or ())
        if not backoff:
            return 0.0
        return max(0.0, float(backoff[min(index, len(backoff) - 1)]))

    def _render_summary(
        self,
        context: Mapping[str, object],
        content: str,
        event_name: str,
    ) -> str:
        template = str(
            getattr(self.config, "wxpusher_summary_template", "")
            or DEFAULT_WXPUSHER_SUMMARY_TEMPLATE
        ).strip()
        values = {
            "time_range": str(context.get("time_range", "") or ""),
            "ok_count": int(context.get("ok_count", 0) or 0),
            "ng_count": int(context.get("ng_count", 0) or 0),
            "device_name": str(context.get("device_name", "") or ""),
            "event_name": event_name or "",
            "event_time": str(context.get("event_time", "") or ""),
        }
        try:
            summary = template.format(**values)
        except Exception:
            summary = content[:100]
        summary = summary.strip() or content[:100]
        return summary[:100]

    def _redact(self, value: str) -> str:
        redacted = value
        token = str(getattr(self.config, "wxpusher_app_token", "") or "")
        if token:
            redacted = redacted.replace(token, "***")
        for uid in getattr(self.config, "wxpusher_uids", ()) or ():
            text = str(uid).strip()
            if text:
                redacted = redacted.replace(text, mask_wxpusher_secret(text))
        return redacted[:200]

    @staticmethod
    def _safe_json(response: object) -> Mapping[str, Any] | list[Any] | None:
        try:
            payload = response.json()
        except Exception:
            return None
        if isinstance(payload, (Mapping, list)):
            return payload
        return None

    @staticmethod
    def _message_reference(body: Mapping[str, Any] | list[Any] | None, fallback: str) -> str:
        if not isinstance(body, Mapping):
            return fallback
        data = body.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, Mapping):
                for key in ("messageId", "message_id", "msgId"):
                    value = first.get(key)
                    if value not in (None, ""):
                        return str(value)
        for key in ("messageId", "message_id", "data"):
            value = body.get(key)
            if value not in (None, "") and not isinstance(value, (list, dict)):
                return str(value)
        return fallback

    @staticmethod
    def _failure_result(
        targets: Sequence[str],
        *,
        attempts: int,
        detail: str,
        status_code: int | None,
        error_code: str,
        retryable: bool,
    ) -> SmsBatchResult:
        return SmsBatchResult(
            tuple(
                RecipientResult(
                    phone=target,
                    success=False,
                    attempts=attempts,
                    detail=detail,
                    status_code=status_code,
                    error_code=error_code,
                    retryable=retryable,
                )
                for target in targets
            )
        )
