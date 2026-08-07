"""每日短信日报对话框 (v3.46) — 浏览器 E2E 场景。

涵盖:
  1. 数据页 → 数据导出 tab → 短信日报入口按钮 → 对话框打开有"新建规则"
  2. API 创建一条 __e2e_ 前缀规则 → UI 重新打开 → 表格里能看到
  3. 「发送通道」tab 只读展示统一通道 (编辑入口在报警页, 与 NG 通知共用)
  4. 规则编辑器有「正文模板」字段 (内容式通道本端渲染)
"""
from __future__ import annotations

import time

from .conftest import E2E_PREFIX


def _open_sms_report_dialog(page, base_url):
    page.goto(f"{base_url}/#/data", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=记录设置", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator(".el-tabs__item:has-text('数据导出')").first.click(timeout=5000)
    time.sleep(0.5)
    page.locator("button:has-text('短信日报')").first.click(timeout=5000)
    time.sleep(0.7)
    dialog = page.locator(".sms-report-dialog:visible").last
    dialog.wait_for(state="visible", timeout=5000)
    return dialog


def test_对话框打开_有新建规则按钮(page, base_url):
    dialog = _open_sms_report_dialog(page, base_url)
    body = dialog.inner_text(timeout=3000)
    assert "新建规则" in body, f"短信日报对话框应有新建规则按钮; body={body[:200]}"


def test_API_创建规则后_UI_重打开能看到(page, base_url, api_helper):
    rule_name = f"{E2E_PREFIX}sms日报规则"
    r = api_helper.post("/api/v1/sms-report/rules", json={
        "name": rule_name,
        "enabled": True,
        "cron_expression": "0 20 * * *",
        "data_window_type": "today",
        "metrics": ["stats.total_cycles", "counters_daily.合格总数"],
        "phone_numbers": ["13800000000"],
    })
    assert r.status_code == 200, r.text
    rule_id = r.json()["id"]

    try:
        dialog = _open_sms_report_dialog(page, base_url)
        body = dialog.inner_text(timeout=3000)
        assert rule_name in body, f"规则表格应显示 API 创建的规则; body={body[:300]}"
        assert "13800000000" in body, "规则表格应显示手机号"
    finally:
        api_helper.delete(f"/api/v1/sms-report/rules/{rule_id}")


def test_发送通道tab_只读展示统一通道(page, base_url):
    """通道 tab 不再是编辑表单 — 只读展示当前通道 + 指路报警页"""
    dialog = _open_sms_report_dialog(page, base_url)
    dialog.locator(".el-tabs__item:has-text('发送通道')").first.click(timeout=5000)
    time.sleep(0.5)
    body = dialog.inner_text(timeout=3000)
    for kw in ("当前通道", "报警设置", "阿里云短信", "腾讯云短信"):
        assert kw in body, f"发送通道 tab 应有 {kw}; body={body[:300]}"
    assert "保存服务商配置" not in body, "通道 tab 应为只读, 不应再有保存按钮"


def test_规则编辑器_有正文模板字段(page, base_url):
    dialog = _open_sms_report_dialog(page, base_url)
    dialog.locator("button:has-text('新建规则')").first.click(timeout=5000)
    time.sleep(0.7)
    editor = page.locator(".el-dialog:visible", has_text="新建短信日报规则").last
    editor.wait_for(state="visible", timeout=5000)
    body = editor.inner_text(timeout=3000)
    assert "正文模板" in body, f"规则编辑器应有正文模板字段; body={body[:300]}"
    assert "模板 Code 覆盖" in body, "规则编辑器应保留云模板 Code 覆盖"
