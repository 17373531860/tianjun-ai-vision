"""Settings 插件管理页 E2E。"""
from __future__ import annotations

from .pages import PluginPage


def test_plugin_tab_visible(page, base_url):
    pp = PluginPage(page, base_url).goto()
    assert pp.has_plugin_tab(), "Settings 页应显示插件管理 Tab"


def test_plugin_upload_control_visible(page, base_url):
    pp = PluginPage(page, base_url).goto()
    assert pp.has_upload_control(), "插件管理页应显示 .tjvplugin 上传控件"


def test_plugin_status_copy_visible(page, base_url):
    pp = PluginPage(page, base_url).goto()
    assert pp.has_status_words() and pp.has_restart_hint(), \
        "插件管理页应显示 License、插件状态、最近错误和重启提示"
