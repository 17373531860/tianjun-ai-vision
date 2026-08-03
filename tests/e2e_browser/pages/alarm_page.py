"""Alarm 报警页 Page Object（基于真 DOM 编写，新增）。"""

from __future__ import annotations

from playwright.sync_api import expect

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
        SMS_CARD = "[data-testid='sms-config-card']"
        SMS_SUMMARY_NOTICE = "[data-testid='sms-summary-notice']"
        SMS_LEGACY_IMMEDIATE_NOTE = "[data-testid='sms-legacy-immediate-note']"
        SMS_TEST_SNAPSHOT_HINT = "[data-testid='sms-test-snapshot-hint']"
        SMS_ENABLED = "[data-testid='sms-enabled-switch']"
        SMS_ENABLED_INPUT = "[data-testid='sms-enabled-switch'] input[role='switch']"
        SMS_PROVIDER_AT = "[data-testid='sms-provider-at']"
        SMS_PROVIDER_HTTP = "[data-testid='sms-provider-http']"
        SMS_PROVIDER_AT_INPUT = "[data-testid='sms-provider-at'] input"
        SMS_PROVIDER_HTTP_INPUT = "[data-testid='sms-provider-http'] input"
        SMS_AT_FIELDS = "[data-testid='sms-at-fields']"
        SMS_HTTP_FIELDS = "[data-testid='sms-http-fields']"
        SMS_CLOUD_TEMPLATE_HINT = "[data-testid='sms-cloud-template-hint']"
        SMS_PORT = "[data-testid='sms-port-select']"
        SMS_REFRESH = "[data-testid='sms-refresh-ports']"
        # el-input(type="textarea") 会把未声明属性直接透传到原生 textarea。
        SMS_RECIPIENTS = "textarea[data-testid='sms-recipients']"
        SMS_TEMPLATE = "textarea[data-testid='sms-template']"
        SMS_ADVANCED = "[data-testid='sms-advanced-collapse']"
        SMS_RETRIES = "[data-testid='sms-retries'] input"
        SMS_RETRY_BACKOFF = "input[data-testid='sms-retry-backoff']"
        SMS_HTTP_API_URL = "input[data-testid='sms-http-api-url']"
        SMS_HTTP_TOKEN = "input[data-testid='sms-http-token']"
        SMS_HTTP_ACCESS_KEY = "input[data-testid='sms-http-access-key']"
        SMS_HTTP_ACCESS_SECRET = "input[data-testid='sms-http-access-secret']"
        SMS_HTTP_SIGN_NAME = "input[data-testid='sms-http-sign-name']"
        SMS_HTTP_TEMPLATE_ID = "input[data-testid='sms-http-template-id']"
        SMS_HTTP_FIELD_MAPPING = "textarea[data-testid='sms-http-field-mapping']"
        SMS_HTTP_VERIFY_SSL = "[data-testid='sms-http-verify-ssl'] input[role='switch']"
        SMS_HTTP_VERIFY_SSL_CONTROL = "[data-testid='sms-http-verify-ssl']"
        SMS_TEST_SEND = "[data-testid='sms-test-send']"
        SMS_SAVE = "[data-testid='sms-save']"

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
        return ("触发条件" in body) and (
            ("事件1" in body) or ("事件2" in body) or ("当前项目暂无事件配置" in body)
        )

    def get_event_titles(self) -> list[str]:
        body = self.body_text()
        return [
            k
            for k in ("合格(OK)", "不良(NG)", "合格", "NG", "未压墨", "事件1", "事件2")
            if k in body
        ]

    def click_refresh_devices(self):
        return self.click_text("刷新")

    def click_test_ok(self):
        return self.click_text('测试"合格"')

    def click_test_ng(self):
        return self.click_text('测试"不合格"')

    def wait_sms_panel(self, timeout_ms: int = 8000) -> bool:
        try:
            self.page.locator(self.Sel.SMS_CARD).wait_for(
                state="visible", timeout=timeout_ms
            )
            return True
        except Exception:
            return False

    def has_sms_summary_copy(self) -> bool:
        notice = self.page.locator(self.Sel.SMS_SUMMARY_NOTICE)
        return notice.is_visible() and all(
            text in notice.inner_text()
            for text in (
                "每滚动 12 小时按工位分别汇总",
                "不含进行中周期",
                "均为 0 时不发送",
                "不再按单次 NG 或累计 N 次即时推送",
            )
        )

    def legacy_instant_controls_hidden(self) -> bool:
        return (
            self.page.locator("[data-testid='sms-ng-threshold']").count() == 0
            and self.page.locator("[data-testid='sms-cooldown']").count() == 0
            and self.page.locator(self.Sel.SMS_LEGACY_IMMEDIATE_NOTE).count() == 1
            and "每工位 NG 累计阈值"
            not in self.page.locator(self.Sel.SMS_CARD).inner_text()
        )

    def has_sms_test_snapshot_hint(self) -> bool:
        hint = self.page.locator(self.Sel.SMS_TEST_SNAPSHOT_HINT)
        return hint.is_visible() and "当前未闭合窗口快照" in hint.inner_text()

    def has_sms_cloud_template_guidance(self) -> bool:
        hint = self.page.locator(self.Sel.SMS_CLOUD_TEMPLATE_HINT)
        return hint.is_visible() and all(
            text in hint.inner_text()
            for text in (
                "device_name",
                "time_range",
                "ok_count",
                "ng_count",
                "新的汇总模板 CODE",
                "旧即时 NG 模板不适用",
            )
        )

    def is_sms_enabled(self) -> bool:
        value = self.page.locator(self.Sel.SMS_ENABLED_INPUT).get_attribute(
            "aria-checked"
        )
        return value == "true"

    def set_sms_enabled(self, enabled: bool):
        if self.is_sms_enabled() != enabled:
            self.page.locator(self.Sel.SMS_ENABLED).click()
        return self

    def wait_sms_enabled(self, enabled: bool, timeout_ms: int = 8000) -> bool:
        expected = "true" if enabled else "false"
        try:
            expect(self.page.locator(self.Sel.SMS_ENABLED_INPUT)).to_have_attribute(
                "aria-checked", expected, timeout=timeout_ms
            )
            return True
        except Exception:
            return False

    def select_sms_provider(self, provider: str):
        selector = (
            self.Sel.SMS_PROVIDER_AT
            if provider == "at_modem"
            else self.Sel.SMS_PROVIDER_HTTP
        )
        self.page.locator(selector).click()
        return self

    def sms_provider(self) -> str:
        if self.page.locator(self.Sel.SMS_PROVIDER_HTTP_INPUT).is_checked():
            return "generic_http"
        return "at_modem"

    def sms_at_fields_visible(self) -> bool:
        return self.page.locator(self.Sel.SMS_AT_FIELDS).is_visible()

    def sms_http_fields_visible(self) -> bool:
        return self.page.locator(self.Sel.SMS_HTTP_FIELDS).is_visible()

    def refresh_sms_ports(self):
        self.page.locator(self.Sel.SMS_REFRESH).click()
        return self

    def select_sms_port(self, port: str):
        self.page.locator(self.Sel.SMS_PORT).click()
        option = self.page.locator(
            ".el-select-dropdown__item:visible", has_text=port
        ).first
        option.wait_for(state="visible", timeout=5000)
        option.click()
        return self

    def fill_sms_recipients(self, value: str):
        self.page.locator(self.Sel.SMS_RECIPIENTS).fill(value)
        return self

    def fill_sms_template(self, value: str):
        self.page.locator(self.Sel.SMS_TEMPLATE).fill(value)
        return self

    def fill_sms_http_api_url(self, value: str):
        self.page.locator(self.Sel.SMS_HTTP_API_URL).fill(value)
        return self

    def fill_sms_http_token(self, value: str):
        self.page.locator(self.Sel.SMS_HTTP_TOKEN).fill(value)
        return self

    def fill_sms_http_access_key(self, value: str):
        self.page.locator(self.Sel.SMS_HTTP_ACCESS_KEY).fill(value)
        return self

    def fill_sms_http_access_secret(self, value: str):
        self.page.locator(self.Sel.SMS_HTTP_ACCESS_SECRET).fill(value)
        return self

    def fill_sms_http_sign_name(self, value: str):
        self.page.locator(self.Sel.SMS_HTTP_SIGN_NAME).fill(value)
        return self

    def fill_sms_http_template_id(self, value: str):
        self.page.locator(self.Sel.SMS_HTTP_TEMPLATE_ID).fill(value)
        return self

    def fill_sms_http_field_mapping(self, value: str):
        self.page.locator(self.Sel.SMS_HTTP_FIELD_MAPPING).fill(value)
        return self

    def set_sms_http_verify_ssl(self, enabled: bool):
        field = self.page.locator(self.Sel.SMS_HTTP_VERIFY_SSL)
        current = field.get_attribute("aria-checked") == "true"
        if current != enabled:
            self.page.locator(self.Sel.SMS_HTTP_VERIFY_SSL_CONTROL).click()
        return self

    def open_sms_advanced(self):
        section = self.page.locator(self.Sel.SMS_ADVANCED)
        if not self.page.locator(self.Sel.SMS_RETRIES).is_visible():
            section.get_by_text("高级设置", exact=True).click()
        self.page.locator(self.Sel.SMS_RETRIES).wait_for(state="visible", timeout=3000)
        return self

    def set_sms_retries(self, retries: int):
        field = self.page.locator(self.Sel.SMS_RETRIES)
        field.fill(str(retries))
        field.press("Tab")
        return self

    def set_sms_retry_backoff(self, value: str):
        self.page.locator(self.Sel.SMS_RETRY_BACKOFF).fill(value)
        return self

    def save_sms_config(self):
        self.page.locator(self.Sel.SMS_SAVE).click()
        return self

    def test_sms_send(self):
        self.page.locator(self.Sel.SMS_TEST_SEND).click()
        return self

    def wait_sms_test_ready(self, timeout_ms: int = 5000) -> bool:
        try:
            expect(self.page.locator(self.Sel.SMS_TEST_SEND)).to_be_enabled(
                timeout=timeout_ms
            )
            return True
        except Exception:
            return False

    def screenshot_sms_card(self, path: str):
        self.page.locator(self.Sel.SMS_CARD).screenshot(path=path)
        return self

    def wait_message(self, text: str, timeout_ms: int = 5000) -> bool:
        try:
            self.page.get_by_text(text, exact=True).last.wait_for(
                state="visible", timeout=timeout_ms
            )
            return True
        except Exception:
            return False

    def sms_port_text(self) -> str:
        return self.page.locator(self.Sel.SMS_PORT).inner_text()

    def sms_recipients_value(self) -> str:
        return self.page.locator(self.Sel.SMS_RECIPIENTS).input_value()

    def sms_template_value(self) -> str:
        return self.page.locator(self.Sel.SMS_TEMPLATE).input_value()

    def sms_http_api_url_value(self) -> str:
        return self.page.locator(self.Sel.SMS_HTTP_API_URL).input_value()

    def sms_http_token_value(self) -> str:
        return self.page.locator(self.Sel.SMS_HTTP_TOKEN).input_value()

    def sms_http_sign_name_value(self) -> str:
        return self.page.locator(self.Sel.SMS_HTTP_SIGN_NAME).input_value()

    def sms_http_template_id_value(self) -> str:
        return self.page.locator(self.Sel.SMS_HTTP_TEMPLATE_ID).input_value()

    def sms_http_field_mapping_value(self) -> str:
        return self.page.locator(self.Sel.SMS_HTTP_FIELD_MAPPING).input_value()

    def sms_http_verify_ssl(self) -> bool:
        return (
            self.page.locator(self.Sel.SMS_HTTP_VERIFY_SSL).get_attribute(
                "aria-checked"
            )
            == "true"
        )

    def sms_retries_value(self) -> str:
        return self.page.locator(self.Sel.SMS_RETRIES).input_value()
