"""USB 4G 短信服务的 fake-serial 与异步隔离回归。"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from datetime import datetime, timedelta

import pytest

from backend.services.sms_at_client import AtClient, SerialConfig, SerialConnectionError
from backend.services.sms_service import (
    RecipientResult,
    SmsBatchResult,
    SmsService,
    SmsServiceConfig,
)
from backend.services.sms_utils import encode_ucs2


class FakeSerial:
    """按写入的 AT 指令返回预设响应，不访问真实 COM。"""

    def __init__(
        self,
        responses: Mapping[str, bytes],
        *,
        payload_response: bytes = b"\r\n+CMGS: 42\r\n\r\nOK\r\n",
        **_kwargs: object,
    ) -> None:
        self.responses = dict(responses)
        self.payload_response = payload_response
        self.is_open = True
        self.writes: list[bytes] = []
        self._incoming = bytearray()

    @property
    def in_waiting(self) -> int:
        return len(self._incoming)

    def write(self, payload: bytes) -> int:
        if not self.is_open:
            raise OSError("fake serial is closed")
        self.writes.append(payload)
        if payload.endswith(b"\r"):
            command = payload[:-1].decode("ascii")
            response = self.responses.get(command)
            if response is None and command.startswith("AT+CMGS="):
                response = self.responses.get("AT+CMGS=*")
            self._incoming.extend(response or b"\r\nERROR\r\n")
        elif payload.endswith(b"\x1a"):
            self._incoming.extend(self.payload_response)
        return len(payload)

    def read(self, size: int = 1) -> bytes:
        if not self._incoming:
            return b""
        size = max(1, min(size, len(self._incoming)))
        result = bytes(self._incoming[:size])
        del self._incoming[:size]
        return result

    def flush(self) -> None:
        return None

    def reset_input_buffer(self) -> None:
        self._incoming.clear()

    def close(self) -> None:
        self.is_open = False


def registered_responses() -> dict[str, bytes]:
    """构造 SIM READY、已注册且支持 Text Mode 的标准响应。"""

    return {
        "AT": b"\r\nOK\r\n",
        "ATE0": b"\r\nOK\r\n",
        "AT+CPIN?": b"\r\n+CPIN: READY\r\n\r\nOK\r\n",
        "AT+CEREG?": b"\r\n+CEREG: 0,1\r\n\r\nOK\r\n",
        "AT+CMGF=1": b"\r\nOK\r\n",
        'AT+CSCS="UCS2"': b"\r\nOK\r\n",
        'AT+CSCS="GSM"': b"\r\nOK\r\n",
        "AT+CSMP=17,167,0,8": b"\r\nOK\r\n",
        "AT+CSMP=17,167,0,0": b"\r\nOK\r\n",
        "AT+CMGS=*": b"\r\n> ",
    }


class FakeSerialFactory:
    """记录创建次数的 fake serial 工厂。"""

    def __init__(self) -> None:
        self.instances: list[FakeSerial] = []

    def __call__(self, **kwargs: object) -> FakeSerial:
        fake = FakeSerial(registered_responses(), **kwargs)
        self.instances.append(fake)
        return fake


_SUMMARY_START = datetime(2026, 8, 2, 8, 0, 0)


def _queue_summary(
    service: SmsService,
    *,
    channel_id: int = 0,
    window_offset: int = 0,
    ok_count: int = 1,
    ng_count: int = 1,
    callback=None,
):
    start = _SUMMARY_START + timedelta(hours=12 * window_offset)
    return service.queue_summary_sms(
        channel_id=channel_id,
        window_start=start,
        window_end=start + timedelta(hours=12),
        ok_count=ok_count,
        ng_count=ng_count,
        callback=callback,
    )


def test_immediate_alarm_entry_is_disabled_without_touching_serial() -> None:
    factory = FakeSerialFactory()
    service = SmsService(SmsServiceConfig(), serial_factory=factory)

    receipt = service.send_alarm(event_id=2, event_name="NG", message="不合格")

    assert not receipt.accepted
    assert receipt.status == "summary_only"
    assert receipt.error_code == "SMS_SUMMARY_ONLY"
    assert factory.instances == []


def test_enabled_without_com_is_isolated_before_serial() -> None:
    factory = FakeSerialFactory()
    service = SmsService(
        SmsServiceConfig(enabled=True, recipients=("13800138000",)),
        serial_factory=factory,
    )

    receipt = _queue_summary(service)

    assert not receipt.accepted
    assert receipt.status == "not_configured"
    assert factory.instances == []


def test_summary_is_queued_and_sent_in_background() -> None:
    factory = FakeSerialFactory()
    completed = threading.Event()
    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            port="COM_TEST",
            recipients=("13800138000",),
            retries=0,
            cooldown_seconds=60,
        ),
        serial_factory=factory,
    )

    first = _queue_summary(
        service,
        callback=lambda _result: completed.set(),
    )

    assert first.accepted and first.status == "queued"
    assert completed.wait(1.0), "fake-serial worker 未及时完成"
    service.shutdown()


def test_queue_full_rejects_without_blocking(monkeypatch) -> None:
    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            port="COM_TEST",
            recipients=("13800138000",),
            cooldown_seconds=0,
            queue_size=1,
        )
    )
    monkeypatch.setattr(service, "_ensure_worker_locked", lambda: None)

    first = _queue_summary(service, channel_id=0)
    second = _queue_summary(service, channel_id=1)

    assert first.accepted
    assert not second.accepted
    assert second.status == "queue_full"


def test_worker_exception_isolated_and_next_job_runs(monkeypatch) -> None:
    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            port="COM_TEST",
            recipients=("13800138000",),
            cooldown_seconds=0,
        )
    )
    calls = 0
    second_done = threading.Event()

    def fake_send_batch(recipients: tuple[str, ...], _message: str) -> SmsBatchResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("worker boom")
        return SmsBatchResult(
            (RecipientResult(recipients[0], True, 1, "fake ok", "42"),)
        )

    monkeypatch.setattr(service, "_send_batch", fake_send_batch)
    _queue_summary(service, channel_id=0)
    _queue_summary(
        service,
        channel_id=1,
        callback=lambda _result: second_done.set(),
    )

    assert second_done.wait(1.0), "worker 异常后未继续处理下一任务"
    service.shutdown()
    assert calls == 2


def test_fake_serial_send_masks_phone_and_body_in_logs() -> None:
    encoded_phone = encode_ucs2("+8613800138000")
    encoded_message = encode_ucs2("测试短信")
    responses = registered_responses()
    responses["AT+CMGS=*"] = f'\r\nAT+CMGS="{encoded_phone}"\r\n> '.encode()
    fake = FakeSerial(
        responses,
        payload_response=(encoded_message + "\r\n+CMGS: 42\r\n\r\nOK\r\n").encode(),
    )
    logs: list[str] = []
    service = SmsService(
        SmsServiceConfig(enabled=True, port="COM_TEST", retries=0),
        logger=logs.append,
        serial_factory=lambda **_kwargs: fake,
    )

    result = service.send_test_sms("+8613800138000", "测试短信")

    assert result.success
    assert encoded_message.encode() + b"\x1a" in fake.writes
    joined = "\n".join(logs)
    assert "13800138000" not in joined
    assert "测试短信" not in joined
    assert encoded_phone not in joined
    assert encoded_message not in joined
    assert "+861****8000" in joined


def test_fake_serial_retries_after_module_busy() -> None:
    instances: list[FakeSerial] = []

    def factory(**kwargs: object) -> FakeSerial:
        payload_response = (
            b"\r\n+CMS ERROR: 515\r\n"
            if not instances
            else b"\r\n+CMGS: 99\r\n\r\nOK\r\n"
        )
        fake = FakeSerial(
            registered_responses(), payload_response=payload_response, **kwargs
        )
        instances.append(fake)
        return fake

    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            port="COM_TEST",
            retries=1,
            retry_delay_seconds=0,
        ),
        serial_factory=factory,
    )

    result = service.send_test_sms("13800138000", "测试")

    assert result.success
    assert result.results[0].attempts == 2
    assert result.results[0].message_reference == "99"
    assert len(instances) == 2


def test_invalid_template_is_rejected_before_serial() -> None:
    factory = FakeSerialFactory()
    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            port="COM_TEST",
            recipients=("13800138000",),
            template="{event_name.__class__}",
        ),
        serial_factory=factory,
    )

    receipt = _queue_summary(service)

    assert not receipt.accepted
    assert receipt.status == "rejected"
    assert "只允许简单字段名" in receipt.detail
    assert factory.instances == []


@pytest.mark.parametrize(
    ("provider", "provider_options"),
    [
        ("at_modem", {"port": "COM_TEST"}),
        (
            "generic_http",
            {
                "api_url": "https://sms.example.test/send",
                "token": "fake-token",
            },
        ),
    ],
)
def test_shutdown_rejects_new_alarm_without_restarting_worker(
    provider: str, provider_options: dict[str, object]
) -> None:
    factory = FakeSerialFactory()
    http_calls: list[dict[str, object]] = []
    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            provider=provider,
            recipients=("13800138000",),
            retries=0,
            cooldown_seconds=0,
            **provider_options,
        ),
        serial_factory=factory,
        http_request=lambda **kwargs: http_calls.append(kwargs),
    )

    service.shutdown(timeout=0)
    receipt = _queue_summary(service)

    assert not receipt.accepted
    assert receipt.status == "stopped"
    assert factory.instances == []
    assert http_calls == []


@pytest.mark.parametrize(
    ("provider", "provider_options"),
    [
        ("at_modem", {"port": "COM_TEST"}),
        (
            "generic_http",
            {
                "api_url": "https://sms.example.test/send",
                "token": "fake-token",
            },
        ),
    ],
)
def test_shutdown_handles_inflight_and_rejects_new_for_each_provider(
    provider: str, provider_options: dict[str, object]
) -> None:
    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            provider=provider,
            recipients=("13800138000",),
            cooldown_seconds=0,
            **provider_options,
        )
    )
    current_started = threading.Event()
    release_current = threading.Event()
    provider_shutdown = threading.Event()
    calls: list[str] = []

    class BlockingProvider:
        def send(
            self,
            recipients: tuple[str, ...],
            message: str,
            **_kwargs: object,
        ) -> SmsBatchResult:
            calls.append(message)
            current_started.set()
            release_current.wait(1.0)
            return SmsBatchResult(
                (RecipientResult(recipients[0], True, 1, "fake ok", "42"),)
            )

        def shutdown(self) -> None:
            provider_shutdown.set()
            release_current.set()

    service._provider = BlockingProvider()  # type: ignore[assignment]
    _queue_summary(service, channel_id=0)
    assert current_started.wait(1.0)
    _queue_summary(service, channel_id=1)

    service.shutdown(timeout=0.01)
    rejected = _queue_summary(service, channel_id=2)
    worker = service._worker
    if worker is not None:
        worker.join(timeout=1.0)

    assert provider_shutdown.is_set()
    assert rejected.status == "stopped"
    assert len(calls) == 1


def test_shutdown_timeout_aborts_active_modem(monkeypatch) -> None:
    service = SmsService(
        SmsServiceConfig(
            enabled=True,
            port="COM_TEST",
            recipients=("13800138000",),
            retries=0,
            cooldown_seconds=0,
        )
    )
    sending = threading.Event()
    aborted = threading.Event()

    class BlockingClient:
        def open(self) -> None:
            return None

        def close(self) -> None:
            return None

        def abort(self) -> None:
            aborted.set()

    class BlockingModem:
        client = BlockingClient()

        def ensure_ready(self) -> None:
            return None

        def send_sms(self, *_args, **_kwargs):
            sending.set()
            aborted.wait(1.0)
            raise OSError("fake serial interrupted")

    monkeypatch.setattr(service, "_new_modem", lambda: BlockingModem())
    _queue_summary(service)
    assert sending.wait(1.0)

    service.shutdown(timeout=0.01)

    assert aborted.wait(0.2), "有限等待到期后应主动断开短信 COM"


def test_at_client_abort_closes_fake_serial_and_prevents_reopen() -> None:
    fake = FakeSerial(registered_responses())
    client = AtClient(
        SerialConfig(port="COM_TEST"),
        serial_factory=lambda **_kwargs: fake,
    )
    client.open()

    client.abort()

    assert not fake.is_open
    assert not client.is_open
    with pytest.raises(SerialConnectionError, match="正在关闭|已取消"):
        client.open()
