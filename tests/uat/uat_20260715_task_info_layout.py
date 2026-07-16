# -*- coding: utf-8 -*-
"""任务信息条显示定制 · 可见浏览器 UAT (v3.39 川南反馈: 信息显示不全)。

现场叙事 (2026-07-15 客户原话: "信息显示不全" + "建议工单不显示, 然后表头一行,
信息一行" + "现场操作人员完全不会操作此软件, 清除本次扫码和禁用扫码可以不用"):
  1. 默认界面: 工单徽标 + 任务要素单行标签 + 扫码操作按钮 —— 与旧版完全一致。
  2. 入站配置关"工单进度徽标"、开"两行表格布局" → 信息条不再显示工单块,
     任务要素变成 表头一行 + 信息一行 的表格, 字段多也不截断。
  3. 系统设置关"扫码操作按钮" → 监控页不再显示 清除本次扫码 / 禁用扫码。
  4. 三个开关默认值均维持原界面, 其他客户零感知。

跑法(前置: 后端 8013 RUNTIME_MODE=test + 前端 dev 6003 已起):
  cd tests/uat && UAT_DB=/tmp/uat_alarm_clear/sql_app.db python uat_20260715_task_info_layout.py
"""
import os
import sqlite3
import time

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, launch_browser, filter_console_errors

API = os.environ.get("UAT_API", "http://127.0.0.1:8013")
FRONT = os.environ.get("UAT_FRONT", "http://127.0.0.1:6003")
DB = os.environ.get("UAT_DB", "/tmp/uat_alarm_clear/sql_app.db")
TASK_NO = f"CNDL-{time.strftime('%H%M%S')}"
PROJECT_NAME = "CNDL-P1"

run = UatRun("task_info_layout")


def body_text(page):
    return page.evaluate("document.body.innerText")


def set_inbound(task_info_over: dict):
    cfg = requests.get(f"{API}/api/v1/mes/inbound/config", timeout=10).json()
    cfg["enabled"] = True
    cfg["create_work_order_on_task"] = True
    cfg["switch_project_on_task"] = False
    cfg["start_detection_on_task"] = False
    ti = cfg.get("task_info_display") or {}
    ti.update({"show_task_no": True, "show_product_code": True,
               "show_step_code": True, "show_operator": True,
               "show_order_chip": True, "two_line_layout": False,
               **task_info_over})
    cfg["task_info_display"] = ti
    r = requests.put(f"{API}/api/v1/mes/inbound/config", json=cfg, timeout=10)
    assert r.status_code == 200, r.text


def setup():
    r = requests.get(f"{API}/api/v1/projects", timeout=10)
    run.step("后端就绪", r.status_code == 200, f"HTTP {r.status_code}")
    run.step("前端就绪", requests.get(FRONT, timeout=10).status_code == 200)

    items = (r.json() or {}).get("items") or r.json()
    proj = next((p for p in items if p["name"] == PROJECT_NAME), None)
    if proj is None:
        pr = requests.post(f"{API}/api/v1/projects",
                           json={"name": PROJECT_NAME, "task_type": "detection",
                                 "logic_mode": "sequential"}, timeout=10)
        run.step("建真项目", pr.status_code in (200, 201), f"HTTP {pr.status_code}")
        proj = pr.json()
    ar = requests.post(f"{API}/api/v1/projects/{proj['id']}/activate", timeout=30)
    run.step("激活项目", ar.status_code == 200, f"HTTP {ar.status_code}")

    # 先停检测再清工单, 避免上一轮/诊断脚本遗留的工单还挂在工位上
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=15)
    time.sleep(1)
    con = sqlite3.connect(DB, timeout=10)
    try:
        con.execute("DELETE FROM work_orders WHERE order_no LIKE 'CNDL-%' "
                    "OR order_no LIKE 'CNPRB-%'")
        con.commit()
    finally:
        con.close()
    set_inbound({})
    run.step("入站配置就绪(四要素全勾, 徽标/布局默认)", True)
    return proj["id"]


def bind_order_and_detect(project_id: int):
    """post 开工建单 → 起 synthetic 检测 (会话开始时原生绑定该单) → 四要素有数据源。"""
    r = requests.post(f"{API}/api/v1/mes/inbound/task", json={
        "TaskNo": TASK_NO, "ProductCode": "P-88", "StepCode": "5.1",
        "Operator": "川南操作员"}, timeout=10)
    ok = r.status_code == 200 and (r.json().get("code") == 0)
    run.step("post 开工建单", ok, f"HTTP {r.status_code} body={r.text[:100]}")

    timeline = []
    for i in range(600):
        base = i * 60
        timeline.append({"from": base, "to": base + 24, "detections": [
            {"label": "A", "confidence": 0.95, "bbox": [0.1, 0.1, 0.3, 0.3]}]})
        timeline.append({"from": base + 25, "to": base + 59, "detections": []})
    r = requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario_json": {"name": "cn-layout", "fps": 30, "timeline": timeline},
        "channel": 0, "with_project": True, "project_id": project_id}, timeout=15)
    run.step("起 synthetic 源", r.status_code == 200, f"HTTP {r.status_code}")
    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=30)
    run.step("开检测(绑定工单)", r.status_code == 200, f"HTTP {r.status_code}")
    time.sleep(3)
    mes = requests.get(f"{API}/api/v1/source/detection/results?channel=0",
                       timeout=5).json().get("mes") or {}
    run.step("工位已挂本次工单", (mes.get("order") or {}).get("order_no") == TASK_NO,
             f"order={(mes.get('order') or {}).get('order_no')}")


def main():
    project_id = setup()
    bind_order_and_detect(project_id)

    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(p, record_video_dir=run.video_dir)

        # ---------- A. 默认态 = 原界面 ----------
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4)
        txt = body_text(page)
        run.step("默认显示工单徽标", "工单:" in txt)
        run.step("默认任务要素单行标签在屏", "任务号:" in txt and TASK_NO in txt)
        run.step("默认无两行表格", page.locator(".task-info-table").count() == 0)
        run.step("默认显示扫码操作按钮", "禁用扫码" in txt)
        run.shot(page, "01_默认原界面")

        # ---------- B. 关工单徽标 + 开两行表格 (UI→后端→UI 闭环) ----------
        page.goto(f"{FRONT}/#/mes", wait_until="domcontentloaded")
        time.sleep(3)
        page.get_by_text("工单接收", exact=False).first.click()
        time.sleep(2)
        for label in ("工单进度徽标", "两行表格布局"):
            page.locator(".el-form-item", has=page.locator(
                f".el-form-item__label:text-is('{label}')")).first \
                .locator(".el-switch").first.click()
            time.sleep(0.3)
        page.get_by_role("button", name="保存配置").click()
        time.sleep(1.5)
        ti = (requests.get(f"{API}/api/v1/mes/inbound/config", timeout=10).json()
              .get("task_info_display") or {})
        run.step("两个开关经 UI 保存后落库",
                 ti.get("show_order_chip") is False and ti.get("two_line_layout") is True,
                 str({k: ti.get(k) for k in ("show_order_chip", "two_line_layout")}))
        run.shot(page, "02_配置页开关")

        # ---------- C. 监控页: 徽标消失 + 表格布局 ----------
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4)
        txt = body_text(page)
        run.step("工单徽标已隐藏", "工单:" not in txt)
        tbl = page.locator(".task-info-table")
        run.step("两行表格已渲染", tbl.count() > 0)
        heads = tbl.locator("th").all_inner_texts() if tbl.count() else []
        run.step("表头一行 = 四要素", heads == ["任务号", "产品代号", "工序工步", "操作员"],
                 str(heads))
        cells = tbl.locator("td").all_inner_texts() if tbl.count() else []
        run.step("信息一行 = 本次开工值",
                 cells == [TASK_NO, "P-88", "5.1", "川南操作员"], str(cells))
        run.shot(page, "03_两行表格布局")

        # ---------- D. 系统设置隐藏扫码操作按钮 ----------
        page.goto(f"{FRONT}/#/settings", wait_until="domcontentloaded")
        time.sleep(3)
        row = page.locator("div.flex.items-center.justify-between",
                           has=page.get_by_text("扫码操作按钮", exact=True)).first
        row.locator(".el-switch").click()
        time.sleep(1)
        run.shot(page, "04_系统设置关扫码按钮")
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4)
        txt = body_text(page)
        run.step("扫码操作按钮已隐藏", "禁用扫码" not in txt and "清除本次扫码" not in txt)
        run.step("信息条其余内容不受影响", TASK_NO in txt)
        run.shot(page, "05_扫码按钮隐藏后")

        errs = filter_console_errors(console_errs)
        run.step("浏览器 console 无异常报错", len(errs) == 0,
                 "; ".join(str(e) for e in errs[:3]))
        ctx.close()
        browser.close()

    # 收尾复位
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=15)
    set_inbound({"show_task_no": False, "show_product_code": False,
                 "show_step_code": False, "show_operator": False})
    raise SystemExit(run.finish())


if __name__ == "__main__":
    main()
