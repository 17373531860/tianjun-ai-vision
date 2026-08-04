"""模块诊断、GSM 与 UCS2 发送测试。"""

from __future__ import annotations

import unittest

from sms_4g.at_client import SerialConfig
from sms_4g.modem import SmsModem
from sms_4g.utils import encode_ucs2

from fake_serial import FakeSerial, registered_responses


class SmsModemTests(unittest.TestCase):
    def test_diagnose_registered_module(self) -> None:
        fake = FakeSerial(registered_responses())
        modem = SmsModem.from_config(
            SerialConfig("COM_TEST"),
            serial_factory=lambda **_kwargs: fake,
        )
        try:
            report = modem.diagnose()
        finally:
            modem.client.close()
        self.assertTrue(report.success)
        self.assertIn("网络注册", {item.name for item in report.items})
        self.assertEqual(
            next(item.level for item in report.items if item.name == "SIM 卡"), "ok"
        )

    def test_send_chinese_sms_uses_ucs2_without_logging_plaintext(self) -> None:
        responses = registered_responses()
        encoded_phone = encode_ucs2("+8613800138000")
        encoded_message = encode_ucs2("测试短信")
        responses["AT+CMGS=*"] = f'\r\nAT+CMGS="{encoded_phone}"\r\n> '.encode("ascii")
        fake = FakeSerial(
            responses,
            payload_response=(encoded_message + "\r\n+CMGS: 42\r\n\r\nOK\r\n").encode(
                "ascii"
            ),
        )
        logs: list[str] = []
        modem = SmsModem.from_config(
            SerialConfig("COM_TEST"),
            logger=logs.append,
            serial_factory=lambda **_kwargs: fake,
        )
        modem.client.open()
        try:
            modem.ensure_ready()
            result = modem.send_sms("+8613800138000", "测试短信", encoding="auto")
        finally:
            modem.client.close()

        writes = fake.writes
        self.assertIn(f'AT+CMGS="{encoded_phone}"\r'.encode("ascii"), writes)
        self.assertIn(encoded_message.encode("ascii") + b"\x1a", writes)
        self.assertEqual(result.encoding, "ucs2")
        self.assertEqual(result.message_reference, "42")
        joined_logs = "\n".join(logs)
        self.assertNotIn("13800138000", joined_logs)
        self.assertNotIn("测试短信", joined_logs)
        self.assertNotIn(encoded_phone, joined_logs)
        self.assertNotIn(encoded_message, joined_logs)
        self.assertIn("短信正文回显已隐藏", joined_logs)

    def test_send_ascii_sms_uses_gsm_mode(self) -> None:
        fake = FakeSerial(registered_responses())
        modem = SmsModem.from_config(
            SerialConfig("COM_TEST"),
            serial_factory=lambda **_kwargs: fake,
        )
        modem.client.open()
        try:
            modem.ensure_ready()
            result = modem.send_sms("13800138000", "Tianjun SMS test", encoding="auto")
        finally:
            modem.client.close()
        self.assertIn(b'AT+CSCS="GSM"\r', fake.writes)
        self.assertIn(b"AT+CSMP=17,167,0,0\r", fake.writes)
        self.assertIn(b"Tianjun SMS test\x1a", fake.writes)
        self.assertEqual(result.encoding, "gsm")


if __name__ == "__main__":
    unittest.main()
