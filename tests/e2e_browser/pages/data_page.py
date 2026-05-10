"""Data 数据页 Page Object（基于真 DOM 编写）。"""
from __future__ import annotations

import re
from typing import Optional

from .base_page import BasePage


DATA_TABS = ("记录设置", "数据导出", "存储与清理")
EXPORT_BUTTONS = (
    "导出当日数据",
    "导出某周数据",
    "导出某月数据",
    "导出日期范围",
    "自定义导出 / 客户模板",
    "实时规则（扫码自动写入）",
)


class DataPage(BasePage):
    PATH = "/data"

    class Sel:
        WORKPIECE_INPUT = "input[placeholder*='工件码']"

    def has_data_center(self) -> bool:
        return self.wait_for_text("数据中心", timeout_ms=5000)

    def get_tabs(self) -> list[str]:
        body = self.body_text()
        return [t for t in DATA_TABS if t in body]

    def switch_tab(self, name: str):
        if name not in DATA_TABS:
            raise ValueError(f"未知 tab: {name}, 允许: {DATA_TABS}")
        return self.click_text(name)

    def search_workpiece(self, code: str):
        loc = self.page.locator(self.Sel.WORKPIECE_INPUT).first
        loc.wait_for(state="visible", timeout=4000)
        loc.fill(code)
        return self

    def click_export(self, kind: str):
        if kind not in EXPORT_BUTTONS:
            raise ValueError(f"未知导出按钮: {kind}, 允许: {EXPORT_BUTTONS}")
        return self.click_text(kind)

    def get_total_cycle_count(self) -> Optional[int]:
        body = self.body_text()
        m = re.search(r"总周期数\s*\n\s*(\d+)", body)
        return int(m.group(1)) if m else None

    def get_yield_rate_text(self) -> Optional[str]:
        body = self.body_text()
        m = re.search(r"良率\s*\n\s*([0-9.]+\s*%)", body)
        return m.group(1) if m else None

    def has_backup_button(self) -> bool:
        return self.wait_for_text("备份数据库", timeout_ms=3000)
