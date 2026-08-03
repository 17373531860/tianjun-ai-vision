"""AT 串口客户端离线测试。"""

from __future__ import annotations

import unittest

from sms_4g.at_client import AtClient, AtCommandError, AtTimeoutError, SerialConfig

from fake_serial import FakeSerial


class AtClientTests(unittest.TestCase):
    def test_command_reads_ok_response(self) -> None:
        fake = FakeSerial({"AT": b"\r\nOK\r\n"})
        client = AtClient(
            SerialConfig("COM_TEST"), serial_factory=lambda **_kwargs: fake
        )
        with client:
            response = client.command("AT", timeout=0.1)
        self.assertIn("OK", response.lines)
        self.assertEqual(fake.writes, [b"AT\r"])

    def test_command_turns_cms_error_into_chinese_exception(self) -> None:
        fake = FakeSerial({"AT+CMGF=1": b"\r\n+CMS ERROR: 302\r\n"})
        client = AtClient(
            SerialConfig("COM_TEST"), serial_factory=lambda **_kwargs: fake
        )
        with client:
            with self.assertRaisesRegex(AtCommandError, "SIM 是否开通短信"):
                client.command("AT+CMGF=1", timeout=0.1)

    def test_command_times_out_when_module_is_silent(self) -> None:
        fake = FakeSerial({"AT": b""})
        client = AtClient(
            SerialConfig("COM_TEST"), serial_factory=lambda **_kwargs: fake
        )
        with client:
            with self.assertRaisesRegex(AtTimeoutError, "响应超时"):
                client.command("AT", timeout=0.03)


if __name__ == "__main__":
    unittest.main()
