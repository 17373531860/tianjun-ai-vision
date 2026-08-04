"""异步告警门面、冷却和错误隔离测试。"""

from __future__ import annotations

import threading
import unittest

from sms_4g.sms_service import SmsBatchResult, SmsService, SmsServiceConfig
from sms_4g.utils import SmsValidationError, parse_recipients

from fake_serial import FakeSerial, registered_responses


class FakeSerialFactory:
    def __init__(self) -> None:
        self.instances: list[FakeSerial] = []

    def __call__(self, **kwargs: object) -> FakeSerial:
        fake = FakeSerial(registered_responses(), **kwargs)
        self.instances.append(fake)
        return fake


class RetrySerialFactory:
    def __init__(self) -> None:
        self.instances: list[FakeSerial] = []

    def __call__(self, **kwargs: object) -> FakeSerial:
        payload_response = (
            b"\r\n+CMS ERROR: 515\r\n"
            if not self.instances
            else b"\r\n+CMGS: 99\r\n\r\nOK\r\n"
        )
        fake = FakeSerial(
            registered_responses(), payload_response=payload_response, **kwargs
        )
        self.instances.append(fake)
        return fake


class SmsServiceTests(unittest.TestCase):
    def test_default_off_rejects_without_touching_serial(self) -> None:
        factory = FakeSerialFactory()
        service = SmsService(SmsServiceConfig(), serial_factory=factory)
        receipt = service.send_alarm(event_id=2, event_name="NG", message="不合格")
        self.assertFalse(receipt.accepted)
        self.assertEqual(receipt.status, "disabled")
        self.assertEqual(factory.instances, [])

    def test_send_alarm_is_queued_and_duplicate_hits_cooldown(self) -> None:
        factory = FakeSerialFactory()
        completed = threading.Event()
        results: list[SmsBatchResult] = []
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

        def callback(result: SmsBatchResult) -> None:
            results.append(result)
            completed.set()

        first = service.send_alarm(
            event_id=2,
            event_name="NG",
            message="工位异常",
            callback=callback,
        )
        second = service.send_alarm(event_id=2, event_name="NG", message="重复异常")
        self.assertTrue(first.accepted)
        self.assertEqual(first.status, "queued")
        self.assertFalse(second.accepted)
        self.assertEqual(second.status, "cooldown")
        self.assertTrue(
            completed.wait(1.0), "异步 worker 未在预期时间完成 fake serial 任务"
        )
        service.shutdown()
        self.assertTrue(results[0].success)

    def test_callback_exception_does_not_kill_worker(self) -> None:
        factory = FakeSerialFactory()
        service = SmsService(
            SmsServiceConfig(
                enabled=True,
                port="COM_TEST",
                recipients=("13800138000",),
                retries=0,
                cooldown_seconds=0,
            ),
            serial_factory=factory,
        )
        first_done = threading.Event()
        second_done = threading.Event()

        def bad_callback(_result: SmsBatchResult) -> None:
            first_done.set()
            raise RuntimeError("callback boom")

        service.send_alarm(event_id=2, event_name="NG", callback=bad_callback)
        service.send_alarm(
            event_id=3,
            event_name="警告",
            callback=lambda _result: second_done.set(),
        )
        self.assertTrue(first_done.wait(1.0))
        self.assertTrue(second_done.wait(1.0), "回调异常后 worker 未继续处理下一任务")
        service.shutdown()

    def test_recipient_parser_deduplicates_and_validates(self) -> None:
        self.assertEqual(
            parse_recipients("13800138000； +86 139-0013-9000,13800138000"),
            ("13800138000", "+8613900139000"),
        )
        with self.assertRaises(SmsValidationError):
            parse_recipients("not-a-phone")

    def test_sync_test_send_retries_after_module_busy(self) -> None:
        factory = RetrySerialFactory()
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
        self.assertTrue(result.success)
        self.assertEqual(result.results[0].attempts, 2)
        self.assertEqual(result.results[0].message_reference, "99")
        self.assertEqual(len(factory.instances), 2)

    def test_invalid_template_is_rejected_before_serial_io(self) -> None:
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
        receipt = service.send_alarm(event_id=2, event_name="NG")
        self.assertFalse(receipt.accepted)
        self.assertEqual(receipt.status, "rejected")
        self.assertIn("只允许简单字段名", receipt.detail)
        self.assertEqual(factory.instances, [])


if __name__ == "__main__":
    unittest.main()
