"""现有 USB/AT 短信实现的薄 Provider 适配层。"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from backend.services.sms_at_client import AtCommandError
from backend.services.sms_providers.base_provider import (
    RecipientResult,
    SmsBatchResult,
    SmsProvider,
)
from backend.services.sms_utils import mask_phone


class AtModemProvider(SmsProvider):
    """复用 ``SmsModem``，不复制或改写任何 AT 指令。"""

    name = "at_modem"

    def __init__(
        self,
        config: Any,
        *,
        modem_factory: Callable[[], object],
        logger: Callable[[str], None],
        stop_event: threading.Event,
    ) -> None:
        self.config = config
        self._modem_factory = modem_factory
        self._logger = logger
        self._stop_event = stop_event
        self._state_lock = threading.Lock()
        self._active_modem: object | None = None

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
        del message_id, event_name, raw_message, context
        return SmsBatchResult(
            tuple(self._send_one_with_retry(phone, message) for phone in recipients)
        )

    def shutdown(self) -> None:
        with self._state_lock:
            modem = self._active_modem
        if modem is None:
            return
        client = getattr(modem, "client", None)
        abort = getattr(client, "abort", None)
        if callable(abort):
            abort()

    def _send_one_with_retry(self, phone: str, message: str) -> RecipientResult:
        max_attempts = max(1, int(getattr(self.config, "retries", 0)) + 1)
        attempts_made = 0
        last_error = "未知错误"
        last_retryable = True
        for attempt in range(1, max_attempts + 1):
            if self._stop_event.is_set():
                return RecipientResult(
                    phone,
                    False,
                    attempts_made,
                    "短信服务正在关闭",
                    error_code="SMS_STOPPED",
                    retryable=False,
                )
            modem = self._modem_factory()
            with self._state_lock:
                self._active_modem = modem
            attempts_made = attempt
            try:
                modem.client.open()
                modem.ensure_ready()
                sent = modem.send_sms(phone, message, encoding=self.config.encoding)
                return RecipientResult(
                    phone=phone,
                    success=True,
                    attempts=attempt,
                    detail="已提交运营商；最终送达取决于 SIM 资费、运营商和手机状态",
                    message_reference=sent.message_reference,
                )
            except Exception as exc:
                last_error = str(exc)
                last_retryable = self._is_retryable(exc)
                self._logger(
                    f"{mask_phone(phone)} 第 {attempt}/{max_attempts} 次发送失败："
                    f"{type(exc).__name__}"
                )
            finally:
                try:
                    modem.client.close()
                finally:
                    with self._state_lock:
                        if self._active_modem is modem:
                            self._active_modem = None
            if self._stop_event.is_set() or not last_retryable:
                break
            if attempt < max_attempts:
                delay = self._retry_delay(attempt - 1)
                self._stop_event.wait(delay)
        return RecipientResult(
            phone,
            False,
            attempts_made,
            last_error,
            error_code="AT_SEND_FAILED",
            retryable=last_retryable,
        )

    def _retry_delay(self, index: int) -> float:
        backoff = tuple(getattr(self.config, "retry_backoff_seconds", ()) or ())
        if backoff:
            return max(0.0, float(backoff[min(index, len(backoff) - 1)]))
        return max(0.0, float(getattr(self.config, "retry_delay_seconds", 0.0)))

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if not isinstance(exc, AtCommandError):
            return True
        match = re.search(r"\+CMS ERROR:\s*(\d+)", exc.response or "")
        if not match:
            return True
        return int(match.group(1)) not in {302, 310, 311, 330}

