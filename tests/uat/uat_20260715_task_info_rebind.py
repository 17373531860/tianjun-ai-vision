# -*- coding: utf-8 -*-
"""川南"检测运行中开工, 四要素不上屏" · 可见浏览器 UAT (v3.38 回填修复验收)。

现场叙事 (2026-07-15 客户原话: "勾选了显示要素, post 开工, 项目切了, 其他信息没显示"):
  1. 操作员先开着检测 (画面在跑), 中控这时才 post 开工任务。
  2. 旧行为: 工单建了, 但监控页信息条不出现工单/四要素 (活跃工单只在
     检测会话开始那一刻绑定, 会话早于建单 → 永远挂不上, 要停一次检测)。
  3. v3.38 修复: 建单后回填运行中工位的活跃工单 (空位无条件补挂;
     已挂旧单仅在"最新开工为准"开启时顶替)。
  4. 预期 UI: post 开工后 ≤6s, 监控页信息条出现 工单号 + 任务号/产品代号/
     工序工步/操作员 标签, 无需停止/重启检测。

跑法(前置: 后端 8001 RUNTIME_MODE=test + 前端 dev 6001 已起):
  cd tests/uat && UAT_DB=... python uat_20260715_task_info_rebind.py
"""
import os
import sqlite3
import time

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, launch_browser, filter_console_errors

API = os.environ.get("UAT_API", "http://127.0.0.1:8001")
FRONT = os.environ.get("UAT_FRONT", "http://127.0.0.1:6001")
DB = os.environ.get("UAT_DB", "/tmp/uat_rebind/sql_app.db")
TASK_NO = f"CNRB-{time.strftime('%H%M%S')}"
PROJECT_NAME = "CNRB-P1"

run = UatRun("task_info_rebind")


def body_text(page):
    return page.evaluate("document.body.innerText")


def setup_project_and_config():
    r = requests.get(f"{API}/api/v1/projects", timeout=10)
    run.step("后端就绪", r.status_code == 200, f"HTTP {r.status_code}")
    run.step("前端就绪", requests.get(FRONT, timeout=10).status_code == 200)

    # 建/复用真项目并激活 (工单绑定"当前激活项目"要用)
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

    # 清历史 CNRB 工单 (上轮跑留下的在产单会在检测启动时被原生绑定, 污染"空位"前置)
    con = sqlite3.connect(DB, timeout=10)
    try:
        n = con.execute("DELETE FROM work_orders WHERE order_no LIKE 'CNRB-%'").rowcount
        con.commit()
    finally:
        con.close()
    run.step("历史 CNRB 工单已清", True, f"deleted={n}")

    # 复刻客户配置: 开工即建工单 + 四要素全勾; 不开自动开始检测 (客户就没开)
    cfg = requests.get(f"{API}/api/v1/mes/inbound/config", timeout=10).json()
    cfg.update({
        "enabled": True,
        "create_work_order_on_task": True,
        "start_detection_on_task": False,
        "switch_project_on_task": False,
        "supersede_previous_task": False,
        "task_info_display": {"show_task_no": True, "show_product_code": True,
                              "show_step_code": True, "show_operator": True},
    })
    ur = requests.put(f"{API}/api/v1/mes/inbound/config", json=cfg, timeout=10)
    run.step("入站配置就绪(建单+四要素全勾, 未开自动开始检测)",
             ur.status_code == 200, f"HTTP {ur.status_code}")
    return proj["id"]


def start_detection_first(project_id: int):
    """客户时序: 检测先跑起来, 开工任务后到。"""
    timeline = []
    for i in range(600):
        base = i * 60
        timeline.append({"from": base, "to": base + 24, "detections": [
            {"label": "A", "confidence": 0.95, "bbox": [0.1, 0.1, 0.3, 0.3]}]})
        timeline.append({"from": base + 25, "to": base + 49, "detections": [
            {"label": "B", "confidence": 0.95, "bbox": [0.5, 0.5, 0.7, 0.7]}]})
        timeline.append({"from": base + 50, "to": base + 59, "detections": []})
    r = requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario_json": {"name": "cn-rebind", "fps": 30, "timeline": timeline},
        "channel": 0, "with_project": True, "project_id": project_id}, timeout=15)
    run.step("起 synthetic 源", r.status_code == 200, f"HTTP {r.status_code}")
    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=30)
    run.step("检测已先跑起来(复刻客户时序)", r.status_code == 200, f"HTTP {r.status_code}")
    time.sleep(3)
    mes = requests.get(f"{API}/api/v1/source/detection/results?channel=0",
                       timeout=5).json().get("mes") or {}
    run.step("开工前工位未挂工单(客户症状前置)", not mes.get("order"),
             f"order={mes.get('order')}")


def visible_post_and_verify():
    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4)
        run.shot(page, "01_开工前_信息条无四要素")
        txt = body_text(page)
        run.step("开工前页面无'任务号'标签", "任务号:" not in txt)

        # 中控 post 开工 (检测运行中!)
        r = requests.post(f"{API}/api/v1/mes/inbound/task", json={
            "TaskNo": TASK_NO, "ProductCode": PROJECT_NAME,
            "StepCode": "5.1", "Operator": "川南操作员"}, timeout=10)
        ok = r.status_code == 200 and (r.json().get("code") == 0)
        run.step("post 开工返回成功码", ok, f"HTTP {r.status_code} body={r.text[:120]}")

        # 预期 ≤6s 四要素上屏 (前端 1s 轮询 + hook 工作线程异步回填)
        deadline, appeared = time.time() + 10, False
        while time.time() < deadline:
            txt = body_text(page)
            if TASK_NO in txt and "任务号:" in txt:
                appeared = True
                break
            time.sleep(0.5)
        run.shot(page, "02_开工后_四要素应上屏")
        run.step("开工后 ≤10s 四要素上屏(不停不重启检测)", appeared,
                 f"含任务号标签={'任务号:' in txt} 含任务号值={TASK_NO in txt}")
        if appeared:
            run.step("产品代号上屏", PROJECT_NAME in txt)
            run.step("工序工步上屏", "5.1" in txt)
            run.step("操作员上屏", "川南操作员" in txt)
            run.step("工单号上屏", "工单:" in txt)

        real = filter_console_errors(console_errs)
        run.step("控制台无前端逻辑报错", not real, f"真报错={real[:3]}")
        ctx.close()
        browser.close()


def verify_db_and_cleanup():
    con = sqlite3.connect(DB, timeout=10)
    try:
        row = con.execute(
            "SELECT id,status FROM work_orders WHERE order_no=?", (TASK_NO,)).fetchone()
        run.step("工单已落库且在产", bool(row) and row[1] == "in_progress",
                 f"row={row}")
        if row:
            s = con.execute(
                "SELECT COUNT(*) FROM detection_sessions WHERE order_id=?",
                (row[0],)).fetchone()[0]
            run.step("运行中会话已挂到新单(统计口径跟随)", s >= 1, f"sessions={s}")
    finally:
        con.close()
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)
    run.step("清理完成(停检测/停剧本)", True)


def main():
    pid = setup_project_and_config()
    start_detection_first(pid)
    visible_post_and_verify()
    verify_db_and_cleanup()
    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
