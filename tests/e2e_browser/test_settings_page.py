"""Settings 页 E2E：验证 PT/CT 设置区与 POM 业务方法。"""
from __future__ import annotations

from .pages import SettingsPage


def test_settings_page_loaded(page, base_url):
    sp = SettingsPage(page, base_url).goto()
    titles = sp.get_visible_section_titles()
    assert titles, f"Settings 页应包含 PT/CT/品牌等字段, 实际无匹配, body={sp.body_text()[:200]}"


def test_settings_pt_aggregate_words(page, base_url):
    sp = SettingsPage(page, base_url).goto()
    assert sp.has_pt_aggregate_options(), \
        "Settings 页应有 PT 计算方式 + 合并 + 最后一次 三组关键词"


def test_settings_three_tabs_present(page, base_url):
    """v3.14.0 起 Settings 含 6 个原生 Tab + 可选插件注入 Tab.
    断言改成"包含"语义, 防加 Tab 后回归挂掉 (v3.10 加账号鉴权, v3.14 加流水线串行).
    """
    sp = SettingsPage(page, base_url).goto()
    tabs = sp.get_visible_tabs()
    expected_native = {"显示设置", "检测框设置", "性能设置", "插件管理", "账号鉴权", "流水线串行"}
    missing = expected_native - set(tabs)
    assert not missing, f"Settings 页缺少原生 tab: {missing}, 实际 {tabs}"


def test_settings_operator_table_present(page, base_url):
    sp = SettingsPage(page, base_url).goto()
    assert sp.has_operator_table(), "Settings 页应有操作员管理表"


def test_settings_save_button_present(page, base_url):
    sp = SettingsPage(page, base_url).goto()
    assert sp.wait_for_text("保存此工位设置", timeout_ms=4000)
