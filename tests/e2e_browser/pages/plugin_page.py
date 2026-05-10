"""Settings 插件管理 Tab Page Object。"""
from __future__ import annotations

from .base_page import BasePage


class PluginPage(BasePage):
    PATH = "/settings"

    def goto(self):
        super().goto()
        self.click_text("插件管理")
        return self

    def has_plugin_tab(self) -> bool:
        return self.wait_for_text("插件管理", timeout_ms=4000)

    def has_upload_control(self) -> bool:
        body = self.body_text()
        return "上传 .tjvplugin" in body and "安装包" in body

    def has_restart_hint(self) -> bool:
        return "重启应用" in self.body_text()

    def has_status_words(self) -> bool:
        body = self.body_text()
        return "当前 License 客户码" in body and "已安装插件" in body and "最近错误" in body
