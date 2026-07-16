# -*- coding: utf-8 -*-
"""川南 2026-07-16 问题1+2 · 可见浏览器 UAT (v3.40 空闲看门狗 + 轮询状态同步验收)。

现场叙事 (客户原话: "post 请求后项目切换正常, 推理执行, 但开始按钮不会变灰,
画面左下角 fps 为 0; 切到项目管理再切回检测中心就正常了; 信息条也不显示,
怀疑和问题1关联"):
  1. 操作员开着检测中心页面, 视频源/检测处于停止状态 (空闲)。
  2. 中控 post 开工 (开工即建工单 + 自动开始检测 + 四要素显示 全开)。
  3. 旧行为: 后端确实把检测拉起来了, 但前端不知情 — 开始按钮可点、FPS 0、
     信息条不出, 必须切页再切回。
  4. v3.40 修复: 空闲看门狗 2s 探活自动接管 + 轮询循环同步后端检测态。
  5. 预期 UI: post 后 ≤8s, 不切页: 开始按钮变灰 + FPS>0 + 四要素信息条上屏。

跑法(前置: 后端 RUNTIME_MODE=test + 前端 dev 已起):
  cd tests/uat && UAT_API=http://127.0.0.1:8013 UAT_FRONT=http://127.0.0.1:6003 \
    UAT_DB=/tmp/uat_cn_seq/sql_app.db python uat_20260716_cn_idle_autostart_adopt.py
"""
import os
import sqlite3
import time

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, launch_browser, filter_console_errors

API = os.environ.get("UAT_API", "http://127.0.0.1:8001")
FRONT = os.environ.get("UAT_FRONT", "http://127.0.0.1:6001")
DB = os.environ.get("UAT_DB", "/tmp/uat_cn_seq/sql_app.db")
TASK_NO = f"CNIA-{time.strftime('%H%M%S')}"
PROJECT_NAME = "CNIA-P1"

run = UatRun("cn_idle_autostart_adopt")


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

    con = sqlite3.connect(DB, timeout=10)
    try:
        n = con.execute("DELETE FROM work_orders WHERE order_no LIKE 'CNIA-%'").rowcount
        con.commit()
    finally:
        con.close()
    run.step("历史 CNIA 工单已清", True, f"deleted={n}")

    # 复刻客户配置: 建单 + 自动开始检测 + 四要素全勾
    cfg = requests.get(f"{API}/api/v1/mes/inbound/config", timeout=10).json()
    cfg.update({
        "enabled": True,
        "create_work_order_on_task": True,
        "start_detection_on_task": True,
        "switch_project_on_task": False,
        "supersede_previous_task": True,
        "task_info_display": {"show_task_no": True, "show_product_code": True,
                              "show_step_code": True, "show_operator": True},
    })
    ur = requests.put(f"{API}/api/v1/mes/inbound/config", json=cfg, timeout=10)
    run.step("入站配置就绪(建单+自动开始检测+四要素)", ur.status_code == 200,
             f"HTTP {ur.status_code}")
    return proj["id"]


def prime_idle_source(project_id: int):
    """铺一个 synthetic 源然后停掉 — 复刻'配置过视频源但当前停止'的空闲态。"""
    timeline = []
    for i in range(600):
        base = i * 60
        timeline.append({"from": base, "to": base + 30, "detections": [
            {"label": "A", "confidence": 0.95, "bbox": [0.1, 0.1, 0.3, 0.3]}]})
        timeline.append({"from": base + 31, "to": base + 59, "detections": []})
    r = requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario_json": {"name": "cn-idle", "fps": 30, "timeline": timeline},
        "channel": 0, "with_project": True, "project_id": project_id}, timeout=15)
    run.step("起 synthetic 源", r.status_code == 200, f"HTTP {r.status_code}")
    r = requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=15)
    run.step("停检测置空闲(复刻客户时序: 页面开着但没在检测)",
             r.status_code == 200, f"HTTP {r.status_code}")
    time.sleep(1)


def visible_post_and_verify():
    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4)

        start_btn = page.locator("button", has_text="开始").first
        run.shot(page, "01_开工前_空闲_按钮可点")
        run.step("开工前开始按钮可点(空闲)", start_btn.is_enabled())

        # 中控 post 开工 — 全程不切页
        r = requests.post(f"{API}/api/v1/mes/inbound/task", json={
            "TaskNo": TASK_NO, "ProductCode": PROJECT_NAME,
            "StepCode": "5.1", "Operator": "川南操作员"}, timeout=20)
        ok = r.status_code == 200 and (r.json().get("code") == 0)
        run.step("post 开工返回成功码", ok, f"HTTP {r.status_code} body={r.text[:150]}")

        # 问题1: 开始按钮 ≤8s 自动变灰 (看门狗接管)
        adopted_at, t0 = None, time.time()
        while time.time() - t0 < 8:
            if not start_btn.is_enabled():
                adopted_at = time.time() - t0
                break
            time.sleep(0.3)
        run.step("开工后 ≤8s 开始按钮自动变灰(不切页)", adopted_at is not None,
                 f"耗时={adopted_at and round(adopted_at, 1)}s")

        # 问题1: FPS 从 0 变 >0
        got_fps, t0 = False, time.time()
        while time.time() - t0 < 10:
            fps_txt = page.evaluate(
                "() => document.body.innerText.match(/FPS[:：]?\\s*([\\d.]+)/)?.[1] || '0'")
            if float(fps_txt or 0) > 0:
                got_fps = True
                break
            time.sleep(0.4)
        run.step("FPS 从 0 变 >0(轮询自动恢复)", got_fps)

        # 问题2: 四要素信息条上屏
        appeared, t0 = False, time.time()
        txt = ""
        while time.time() - t0 < 12:
            txt = page.evaluate("document.body.innerText")
            if TASK_NO in txt and "任务号:" in txt:
                appeared = True
                break
            time.sleep(0.5)
        run.shot(page, "02_开工后_接管+四要素")
        run.step("信息条四要素上屏(不切页)", appeared,
                 f"含任务号标签={'任务号:' in txt} 含任务号值={TASK_NO in txt}")
        if appeared:
            run.step("工序工步上屏", "5.1" in txt)
            run.step("操作员上屏", "川南操作员" in txt)

        real = filter_console_errors(console_errs)
        run.step("控制台无前端逻辑报错", not real, f"真报错={real[:3]}")
        ctx.close()
        browser.close()


def cleanup():
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)
    run.step("清理完成(停检测/停剧本)", True)


def main():
    pid = setup()
    prime_idle_source(pid)
    visible_post_and_verify()
    cleanup()
    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
