"""Settings 页 E2E：验证 PT/CT 设置区可见。"""
from __future__ import annotations

from .pages import SettingsPage


def test_settings_page_loaded(page, base_url):
    sp = SettingsPage(page, base_url).goto()
    titles = sp.get_visible_section_titles()
    assert titles, f"Settings 页应包含 PT/CT/品牌等字段, 实际无匹配, body={sp.body_text()[:200]}"


def test_settings_pt_aggregate_words(page, base_url):
    """PT 聚合方式（合并 / 最后一次）应在 Settings 页有相应文案。"""
    sp = SettingsPage(page, base_url).goto()
    body = sp.body_text()
    has_pt = "PT" in body
    has_aggr = ("合并" in body) or ("最后一次" in body)
    assert has_pt and has_aggr, \
        f"Settings 页应有 PT 聚合方式相关字段, has_pt={has_pt}, has_aggr={has_aggr}"
