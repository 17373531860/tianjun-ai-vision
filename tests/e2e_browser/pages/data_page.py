"""Data 数据页。"""
from __future__ import annotations

from .base_page import BasePage


class DataPage(BasePage):
    PATH = "/data"

    class Sel:
        TABLE = ".el-table"
        EXPORT_BTN = "text=导出"
        FILTER_INPUT = ".el-input input"

    def has_table(self) -> bool:
        return self.el_present(self.Sel.TABLE)

    def can_open_export(self) -> bool:
        return self.wait_for_text("导出", timeout_ms=4000)
