"""Mock 短信 Provider — 只打日志不外发, 供试发/联调/测试用。

不进 ``ALLOWED_PROVIDERS``（不可持久化为正式通道）; 仅通过
``create_provider(..., provider_name="mock")`` 显式获取,
如日报 test-send 的 ``?use_mock=true`` 路径。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from backend.services.sms_providers.base_provider import (
    RecipientResult,
    SmsBatchResult,
    SmsProvider,
)


class MockProvider(SmsProvider):
    """全部成功返回, 记录最近一次发送内容 (类属性, 供测试跨实例断言)。"""

    name = "mock"

    # 工厂每次调用新建实例, 测试从类属性取最近一次发送
    last_send: dict | None = None

    def __init__(
        self,
        config: Any = None,
        *,
        request_func: Callable[..., object] | None = None,
        logger: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        del config, request_func, stop_event
        self._logger = logger or (lambda _message: None)

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
        type(self).last_send = {
            "recipients": list(recipients),
            "message": message,
            "message_id": message_id,
            "event_name": event_name,
            "template_params": dict(context.get("template_params") or {}),
        }
        self._logger(
            f"[MockSms] to={list(recipients)} message={message!r} "
            f"params={type(self).last_send['template_params']}"
        )
        return SmsBatchResult(
            tuple(
                RecipientResult(
                    phone=str(phone),
                    success=True,
                    attempts=1,
                    detail="mock 通道未真实发送",
                    message_reference=message_id,
                    status_code=200,
                )
                for phone in recipients
            ),
            provider_metadata={"mock": True},
        )
