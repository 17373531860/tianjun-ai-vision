"""Source 页 E2E：用 SourcePage POM 验证页面基本元素。"""
from __future__ import annotations

from .pages import SourcePage


def test_source_page_loaded(page, base_url):
    sp = SourcePage(page, base_url).goto()
    assert sp.has_source_form(), "Source 页应有'选择输入源类型'表单"


def test_source_page_status_keywords(page, base_url):
    sp = SourcePage(page, base_url).goto()
    keywords = sp.status_keywords()
    assert keywords, f"Source 页应包含连接/类型相关字样, 实际无匹配, body={sp.body_text()[:200]}"


def test_source_page_save_button_present(page, base_url):
    sp = SourcePage(page, base_url).goto()
    assert sp.wait_for_text("保存并启动检测", timeout_ms=4000), \
        "Source 页应有'保存并启动检测'按钮"


def test_source_page_select_video_radio_changes_form(page, base_url):
    sp = SourcePage(page, base_url).goto()
    sp.select_source_type("video").sleep(0.5)
    body = sp.body_text()
    assert "视频" in body or "video" in body.lower()
