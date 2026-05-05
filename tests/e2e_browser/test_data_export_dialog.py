"""Phase 3.4 — Data 页 自定义导出对话框 5 个浏览器 E2E 场景。

涵盖:
  1. Data 页加载，看到 3 个 tab
  2. 切到"数据导出"tab → 看到"自定义导出"按钮
  3. 点"自定义导出"按钮 → 弹出对话框
  4. 自定义导出对话框 → 切换格式（fmt）选择
  5. 自定义导出对话框 → 关闭/取消
"""
from __future__ import annotations

import time

import pytest


def _goto_data(page, base_url):
    page.goto(f"{base_url}/#/data", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=记录设置", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)


def _switch_to_export_tab(page):
    """点'数据导出'tab"""
    tab = page.locator(".el-tabs__item:has-text('数据导出')").first
    if tab.count() == 0:
        tab = page.get_by_text("数据导出", exact=False).first
    tab.click(timeout=5000)
    time.sleep(0.5)


def test_Data_页_有_3_个_tab(page, base_url):
    _goto_data(page, base_url)
    for label in ["记录设置", "数据导出", "存储与清理"]:
        loc = page.locator(f".el-tabs__item:has-text('{label}')")
        assert loc.count() >= 1, f"tab '{label}' 未渲染"


def test_切到导出_tab_看到自定义导出按钮(page, base_url):
    _goto_data(page, base_url)
    _switch_to_export_tab(page)

    btn = page.locator("button:has-text('自定义导出')").first
    btn.wait_for(state="visible", timeout=8000)
    assert btn.is_visible(), "应有'自定义导出'按钮"

    btn2 = page.locator("button:has-text('实时规则')").first
    btn2.wait_for(state="visible", timeout=5000)
    assert btn2.is_visible(), "应有'实时规则'按钮"


def test_点自定义导出_弹出对话框(page, base_url):
    _goto_data(page, base_url)
    _switch_to_export_tab(page)

    btn = page.locator("button:has-text('自定义导出')").first
    btn.click(timeout=5000)
    time.sleep(0.5)

    # 对话框出现 — 标题里应有"自定义导出"或"模板"字样
    dialog = page.locator(".el-dialog__wrapper:visible, .el-overlay-dialog:visible, [role='dialog']:visible").first
    if dialog.count() == 0:
        dialog = page.locator(".el-dialog:visible").first
    dialog.wait_for(state="visible", timeout=5000)
    assert dialog.is_visible(), "对话框未弹出"

    # 关闭对话框
    close = dialog.locator("button:has-text('取消'), .el-dialog__headerbtn").first
    if close.count() > 0:
        close.click(timeout=3000)


def test_自定义导出对话框_格式选择器(page, base_url):
    _goto_data(page, base_url)
    _switch_to_export_tab(page)

    page.locator("button:has-text('自定义导出')").first.click(timeout=5000)
    time.sleep(0.7)

    # 对话框里应有格式选择（el-radio 或 el-select 含 txt/csv/docx 等）
    dialog = page.locator(".el-dialog:visible").first
    dialog.wait_for(state="visible", timeout=5000)

    body_text = dialog.inner_text(timeout=3000)
    has_fmt = any(fmt in body_text for fmt in ["txt", "csv", "docx"])
    assert has_fmt, f"对话框应包含格式选项 (txt/csv/docx); body 摘要={body_text[:200]}"


def test_点实时规则_弹出_实时规则对话框(page, base_url):
    _goto_data(page, base_url)
    _switch_to_export_tab(page)

    btn = page.locator("button:has-text('实时规则')").first
    btn.click(timeout=5000)
    time.sleep(0.7)

    dialog = page.locator(".el-dialog:visible").first
    dialog.wait_for(state="visible", timeout=5000)

    body_text = dialog.inner_text(timeout=3000)
    # 实时规则对话框应含"规则"字样
    assert "规则" in body_text, f"实时规则对话框 body 应含'规则' 字样; got={body_text[:200]}"
