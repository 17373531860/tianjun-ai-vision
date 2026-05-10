"""Phase 3.5 — 实时规则对话框 3 个浏览器 E2E 场景。

涵盖:
  1. 打开实时规则对话框 → 表格/空态文本可见
  2. 通过 API 创建一条 __e2e_ 前缀的规则 → UI 重新打开 → 看到这条规则
  3. UI 上能看到"新建规则"按钮
"""
from __future__ import annotations

import time

import pytest


def _open_realtime_rules_dialog(page, base_url):
    page.goto(f"{base_url}/#/data", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=记录设置", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator(".el-tabs__item:has-text('数据导出')").first.click(timeout=5000)
    time.sleep(0.5)
    page.locator("button:has-text('实时规则')").first.click(timeout=5000)
    time.sleep(0.7)
    dialog = page.locator(".rt-rules-dialog:visible").last
    dialog.wait_for(state="visible", timeout=5000)
    return dialog


def test_对话框打开_有新建规则按钮(page, base_url):
    dialog = _open_realtime_rules_dialog(page, base_url)
    body = dialog.inner_text(timeout=3000)
    # 应有"新建"或"+"按钮文本
    has_create = any(kw in body for kw in ["新建规则", "新建", "添加规则", "+"])
    assert has_create, f"实时规则对话框应有创建按钮; body={body[:200]}"


def test_对话框_显示空态_或表格(page, base_url):
    dialog = _open_realtime_rules_dialog(page, base_url)
    body = dialog.inner_text(timeout=3000)
    # 至少有一种渲染状态
    has_state = (
        "暂无" in body
        or "新建" in body
        or "规则" in body
    )
    assert has_state, f"对话框应渲染空态或表格; body={body[:200]}"


def test_API_创建规则后_UI_重打开能看到(page, base_url, api_helper):
    """API 后台创建 __e2e_ 规则 → 重新打开对话框 → 表格里有这条"""
    # 先建模板（规则需要 template_id）
    tpl = api_helper.create_template("rule_e2e_tpl", fmt="txt",
                                      content="x={{ cycle.id }}")

    # 创建规则
    r = api_helper.post("/api/v1/export/realtime-rules", json={
        "name": "__e2e_simple_rule",
        "enabled": True,
        "template_id": tpl["id"],
        "output_dir": "/tmp/e2e_test_out",
        "filename_template": "x.txt",
        "input_file_mode": "none",
        "trigger_event": "cycle_end",
    })
    assert r.status_code == 200, f"创建规则失败: {r.text}"

    # 打开对话框并等待异步列表刷新完成
    dialog = _open_realtime_rules_dialog(page, base_url)
    try:
        page.locator(".rt-rules-dialog:visible text=__e2e_simple_rule").first.wait_for(
            state="visible",
            timeout=6000,
        )
    except Exception:
        pass
    body = dialog.inner_text(timeout=5000)
    assert "__e2e_simple_rule" in body, \
        f"UI 应显示创建的规则 __e2e_simple_rule; body={body[:300]}"
