# -*- coding: utf-8 -*-
"""v3.39 川南反馈「信息显示不全」显示定制的 CI E2E 回归。

覆盖三个显示层开关（默认值均 = 原界面, 其他客户零感知）:
  1. task_info_display.show_order_chip 关 → 监控页信息条不显示"工单"徽标
  2. task_info_display.two_line_layout 开 → 任务要素渲染成"表头一行+信息一行"表格
  3. display.monitor.showScanButtons 关 (localStorage) → 清除本次扫码/禁用扫码 隐藏

要求：backend 启动时 RUNTIME_MODE=test（synthetic 端点开放），frontend dev 已起。
"""
from __future__ import annotations

import time
import uuid

import requests


TASK_NO = f"__e2e_TIL{uuid.uuid4().hex[:6]}"


# ==================== 后端编排 ====================

def _put_inbound(api_url: str, ti_over: dict):
    cfg = requests.get(f"{api_url}/api/v1/mes/inbound/config", timeout=10).json()
    cfg["enabled"] = True
    cfg["create_work_order_on_task"] = True
    cfg["switch_project_on_task"] = False
    cfg["start_detection_on_task"] = False
    ti = cfg.get("task_info_display") or {}
    ti.update({"show_task_no": True, "show_product_code": True,
               "show_step_code": True, "show_operator": True,
               "show_order_chip": True, "two_line_layout": False, **ti_over})
    cfg["task_info_display"] = ti
    r = requests.put(f"{api_url}/api/v1/mes/inbound/config", json=cfg, timeout=10)
    assert r.status_code == 200, r.text[:200]


def _purge_test_orders(api_url: str):
    """清掉测试前缀的在制工单, 避免抢占工位绑定 (绑定取在制单, 有旧单就轮不到新单)。"""
    r = requests.get(f"{api_url}/api/v1/mes/orders?status=in_progress&page_size=100",
                     timeout=10)
    for it in (r.json().get("items") or []):
        no = it.get("order_no") or ""
        if no.startswith("__e2e_") or no.startswith("CNDL-") or no.startswith("CNPRB-"):
            # 在制单须先取消才可删
            requests.post(f"{api_url}/api/v1/mes/orders/{it['id']}/status",
                          json={"status": "cancelled"}, timeout=10)
            requests.delete(f"{api_url}/api/v1/mes/orders/{it['id']}", timeout=10)


def _seed_order_and_detect(api_url: str):
    """post 开工建单 → synthetic 检测跑起来 → 工位绑定工单, 四要素有数据源。"""
    r = requests.post(f"{api_url}/api/v1/mes/inbound/task", json={
        "TaskNo": TASK_NO, "ProductCode": "P-E2E", "StepCode": "5.1",
        "Operator": "e2e操作员"}, timeout=10)
    assert r.status_code == 200 and r.json().get("code") == 0, r.text[:200]

    timeline = [{"from": 0, "to": 999999, "detections": [
        {"label": "A", "confidence": 0.9, "bbox": [0.1, 0.1, 0.3, 0.3]}]}]
    r = requests.post(f"{api_url}/api/v1/test/synthetic/start", json={
        "scenario_json": {"name": "e2e-til", "fps": 30, "timeline": timeline},
        "channel": 0, "with_project": False}, timeout=15)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{api_url}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25}, timeout=30)
    assert r.status_code == 200, r.text[:200]

    deadline = time.time() + 10
    while time.time() < deadline:
        mes = requests.get(f"{api_url}/api/v1/source/detection/results?channel=0",
                           timeout=5).json().get("mes") or {}
        if (mes.get("order") or {}).get("order_no") == TASK_NO:
            return
        time.sleep(0.5)
    raise AssertionError("10s 内工位未绑定 e2e 工单")


def _teardown(api_url: str):
    requests.post(f"{api_url}/api/v1/source/detection/stop?channel=0", timeout=15)
    requests.post(f"{api_url}/api/v1/test/synthetic/stop?channel=0", timeout=15)
    _purge_test_orders(api_url)
    _put_inbound(api_url, {"show_task_no": False, "show_product_code": False,
                           "show_step_code": False, "show_operator": False})


# ==================== 用例 ====================

def test_task_info_layout_switches(page, base_url, api_url):
    try:
        _put_inbound(api_url, {})
        _purge_test_orders(api_url)
        _seed_order_and_detect(api_url)

        # ---- 默认态 = 原界面: 徽标在, 单行标签, 无表格, 扫码按钮在 ----
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        txt = page.evaluate("document.body.innerText")
        assert "工单:" in txt, "默认应显示工单徽标"
        assert "任务号:" in txt and TASK_NO in txt, "默认任务要素应为单行标签"
        assert page.locator(".task-info-table").count() == 0, "默认不应出现两行表格"
        assert "禁用扫码" in txt, "默认应显示扫码操作按钮"

        # ---- 徽标关 + 两行开 (API 改配置, 重进页面) ----
        _put_inbound(api_url, {"show_order_chip": False, "two_line_layout": True})
        page.reload(wait_until="domcontentloaded")  # 硬刷新触发 Monitor 重挂载重取配置
        page.wait_for_timeout(4000)
        txt = page.evaluate("document.body.innerText")
        assert "工单:" not in txt, "徽标关后信息条不应再显示工单块"
        tbl = page.locator(".task-info-table")
        assert tbl.count() == 1, "两行布局开后应渲染表格"
        assert tbl.locator("th").all_inner_texts() == ["任务号", "产品代号", "工序工步", "操作员"]
        assert TASK_NO in tbl.locator("td").all_inner_texts()

        # ---- 系统显示设置真点"扫码操作按钮"开关 ----
        def _toggle_scan_buttons():
            page.goto(f"{base_url}/#/settings", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            row = page.locator("div.flex.items-center.justify-between",
                               has=page.get_by_text("扫码操作按钮", exact=True)).first
            row.locator(".el-switch").click()
            page.wait_for_timeout(800)

        _toggle_scan_buttons()
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        txt = page.evaluate("document.body.innerText")
        assert "禁用扫码" not in txt and "清除本次扫码" not in txt, "开关关后扫码按钮应隐藏"
        assert TASK_NO in txt, "隐藏按钮不应影响任务要素显示"
        _toggle_scan_buttons()  # 复位回默认显示
    finally:
        _teardown(api_url)
