"""Source 页 E2E：用 SourcePage POM 验证页面基本元素。"""
from __future__ import annotations

from .pages import SourcePage


def test_source_page_loaded(page, base_url):
    sp = SourcePage(page, base_url).goto()
    assert sp.has_source_select(), "Source 页应有视频源类型选择器"


def test_source_page_status_keywords(page, base_url):
    sp = SourcePage(page, base_url).goto()
    keywords = sp.status_keywords()
    assert keywords, f"Source 页应包含连接/类型相关字样, 实际无匹配, body={sp.body_text()[:200]}"
