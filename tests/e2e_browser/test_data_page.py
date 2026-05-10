"""Data 页 E2E：基于 POM 业务方法。"""
from __future__ import annotations

from .pages import DataPage


def test_data_center_loaded(page, base_url):
    dp = DataPage(page, base_url).goto()
    assert dp.has_data_center(), "Data 页应有'数据中心'标题"


def test_data_three_tabs_present(page, base_url):
    dp = DataPage(page, base_url).goto()
    tabs = dp.get_tabs()
    assert set(tabs) == {"记录设置", "数据导出", "存储与清理"}, \
        f"Data 页应有 3 个 tab, 实际 {tabs}"


def test_data_export_buttons_visible(page, base_url):
    dp = DataPage(page, base_url).goto()
    body = dp.body_text()
    for btn in ("导出当日数据", "导出某周数据", "导出某月数据", "导出日期范围"):
        assert btn in body, f"Data 页应有按钮 {btn}, body={body[:200]}"


def test_data_workpiece_search_input_writable(page, base_url):
    dp = DataPage(page, base_url).goto()
    dp.search_workpiece("__e2e_test_code_001")
    val = page.locator("input[placeholder*='工件码']").first.input_value(timeout=2000)
    assert val == "__e2e_test_code_001"


def test_data_backup_button_present(page, base_url):
    dp = DataPage(page, base_url).goto()
    assert dp.has_backup_button()
