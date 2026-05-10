"""Settings 设置页。"""
from __future__ import annotations

from .base_page import BasePage


class SettingsPage(BasePage):
    PATH = "/settings"

    class Sel:
        TABS = ".el-tabs"

    def has_tabs(self) -> bool:
        return self.el_present(self.Sel.TABS)

    def get_visible_section_titles(self) -> list[str]:
        body = self.body_text()
        return [k for k in ("PT", "CT", "合并", "最后一次", "品牌", "工厂", "License", "通用", "外观") if k in body]
