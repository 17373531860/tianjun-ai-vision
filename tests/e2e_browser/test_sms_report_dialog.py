"""每日短信日报对话框 (v3.46) — 浏览器 E2E 场景。

涵盖:
  1. 数据页 → 数据导出 tab → 短信日报入口按钮 → 对话框打开有"新建规则"
  2. API 创建一条 __e2e_ 前缀规则 → UI 重新打开 → 表格里能看到
  3. 服务商设置 tab 有 AK/签名/模板 表单
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


def test_服务商设置tab_有配置表单(page, base_url):
    dialog = _open_sms_report_dialog(page, base_url)
    dialog.locator(".el-tabs__item:has-text('服务商设置')").first.click(timeout=5000)
    time.sleep(0.5)
    body = dialog.inner_text(timeout=3000)
    for kw in ("阿里云", "腾讯云", "自建 HTTP 中转", "短信签名", "默认模板 Code"):
        assert kw in body, f"服务商设置应有 {kw}; body={body[:300]}"


def test_服务商_自建中转_表单切换(page, base_url):
    """选中「自建 HTTP 中转」→ 出现中转地址/Token/正文模板, 云字段隐藏"""
    dialog = _open_sms_report_dialog(page, base_url)
    dialog.locator(".el-tabs__item:has-text('服务商设置')").first.click(timeout=5000)
    time.sleep(0.5)
    dialog.locator(".el-radio-button:has-text('自建 HTTP 中转')").first.click(timeout=5000)
    time.sleep(0.5)
    body = dialog.inner_text(timeout=3000)
    for kw in ("中转服务地址", "鉴权 Token", "短信正文模板"):
        assert kw in body, f"自建中转应有 {kw}; body={body[:300]}"
    assert "AccessKey ID" not in body, "选自建中转后云凭据字段应隐藏"
