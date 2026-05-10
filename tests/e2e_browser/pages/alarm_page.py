"""Alarm 报警页 Page Object（基于真 DOM 编写，新增）。"""
from __future__ import annotations

from .base_page import BasePage


class AlarmPage(BasePage):
    PATH = "/alarm"

    class Sel:
        BTN_REFRESH = "button:has-text('刷新')"
        BTN_CONNECT = "button:has-text('连接设备')"
        BTN_TEST_OK = "button:has-text('测试\"合格\"')"
        BTN_TEST_NG = "button:has-text('测试\"不合格\"')"
        BTN_TRIGGER = "button:has-text('触发')"
        BTN_STOP_ALARM = "button:has-text('停止报警')"

    def has_settings_panel(self) -> bool:
        return self.wait_for_text("报警设置", timeout_ms=5000)

    def get_connection_status(self) -> str:
        body = self.body_text()
        for keyword in ("已连接", "未连接", "连接中"):
            if keyword in body:
                return keyword
        return ""

    def has_voice_panel(self) -> bool:
        return self.wait_for_text("语音播报", timeout_ms=3000)

    def has_event_panel(self) -> bool:
        body = self.body_text()
        return ("事件1" in body) or ("事件2" in body)

    def get_event_titles(self) -> list[str]:
        body = self.body_text()
        return [k for k in ("合格(OK)", "不良(NG)", "未压墨", "事件1", "事件2") if k in body]

    def click_refresh_devices(self):
        return self.click_text("刷新")

    def click_test_ok(self):
        return self.click_text("测试\"合格\"")

    def click_test_ng(self):
        return self.click_text("测试\"不合格\"")
