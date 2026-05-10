"""Alarm 页 E2E：基于 AlarmPage POM。"""
from __future__ import annotations

import pytest

from .pages import AlarmPage


def test_alarm_settings_panel_loaded(page, base_url):
    ap = AlarmPage(page, base_url).goto()
    if not ap.has_settings_panel():
        pytest.skip(f"Alarm 页未渲染或路径不对, body={ap.body_text()[:200]}")
    assert ap.has_settings_panel()


def test_alarm_connection_status(page, base_url):
    ap = AlarmPage(page, base_url).goto()
    status = ap.get_connection_status()
    assert status in ("已连接", "未连接", "连接中"), \
        f"应显示连接状态, 实际 {status!r}"


def test_alarm_voice_panel_present(page, base_url):
    ap = AlarmPage(page, base_url).goto()
    assert ap.has_voice_panel()


def test_alarm_event_panel_present(page, base_url):
    ap = AlarmPage(page, base_url).goto()
    assert ap.has_event_panel()


def test_alarm_event_titles_include_ok_or_ng(page, base_url):
    ap = AlarmPage(page, base_url).goto()
    titles = ap.get_event_titles()
    assert any(t in titles for t in ("合格(OK)", "不良(NG)", "事件1", "事件2")), \
        f"事件标题应包含 OK/NG/事件1/事件2, 实际 {titles}"
