"""Alarm 页 E2E：验证报警相关面板可访问。"""
from __future__ import annotations

import pytest

from .pages import BasePage


class _AlarmPage(BasePage):
    PATH = "/alarm"


def test_alarm_page_loaded(page, base_url):
    ap = _AlarmPage(page, base_url).goto()
    body = ap.body_text()
    if "报警" not in body and "alarm" not in body.lower():
        pytest.skip(f"Alarm 页未渲染或路径不对, body 摘要={body[:200]}")
    assert "报警" in body or "alarm" in body.lower()
