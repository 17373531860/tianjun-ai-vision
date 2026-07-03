# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 项目页事件设置 Tab 外置为 EventsConfigTab.vue（拆分批次 P-3）。

现场叙事: 操作员选中项目 → 打开「事件设置」Tab → 看到两条系统预设事件 →
新增一条事件并改名 → 给它加一个计数器动作 → 勾选需人工确认 → 点保存 →
事件配置真实落库 → 刷新页面回读一致。拆分是"只搬不改"。

前置: 后端 8001 + 前端 6001 已启动。测试项目 __uat_ 前缀, 收尾删除。
"""
from __future__ import annotations

import sys
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"
PNAME = f"__uat_ev_{uuid.uuid4().hex[:6]}"
EV_NAME = "UAT超差报警"

run = UatRun("events_tab_split")
created_pid = None

try:
    # 后端直接建项目（默认 events_config 含 OK/NG 两条系统预设）
    r = requests.post(f"{API}/projects", json={
        "name": PNAME, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    r.raise_for_status()
    created_pid = r.json()["id"]
    print(f">>> 测试项目 {PNAME} id={created_pid}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)

        page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        time.sleep(1.5)
        card = page.locator(f"div.p-4:has-text('{PNAME}')").first
        if card.count() == 0:
            card = page.get_by_text(PNAME, exact=False).first
        card.click()
        time.sleep(1.0)

        # ---- 1. 打开事件设置 Tab（外置组件挂载） ----
        page.locator(".el-tabs__item:has-text('事件设置')").first.click()
        time.sleep(1.0)
        body = page.evaluate("document.body.innerText")
        run.step("A1 事件定义卡片渲染", "事件定义" in body and "新增事件" in body)
        preset_tags = page.locator("span:has-text('系统预设')")
        run.step("A2 两条系统预设事件在列", preset_tags.count() >= 2, f"count={preset_tags.count()}")
        run.shot(page, "01_events_tab")

        # ---- 2. 新增事件 + 改名 ----
        page.locator("button:has-text('+ 新增事件')").click()
        time.sleep(0.6)
        inputs = page.locator("input.bg-transparent.border-b")
        new_input = inputs.nth(inputs.count() - 1)
        new_input.fill(EV_NAME)
        time.sleep(0.3)
        run.step("B1 新事件出现且可改名", new_input.input_value() == EV_NAME)

        # ---- 3. 给新事件加计数器动作 ----
        page.locator("button:has-text('+ 添加动作')").last.click()
        time.sleep(0.6)
        body = page.evaluate("document.body.innerText")
        run.step("B2 计数器动作行出现(默认绑第一个计数器)", "增加" in body)
        run.shot(page, "02_event_added")

        # ---- 4. 勾选需人工确认（v3.9.x 字段） ----
        ack_boxes = page.locator(".el-checkbox:has-text('需人工确认')")
        ack_boxes.last.click()
        time.sleep(0.5)
        body = page.evaluate("document.body.innerText")
        run.step("B3 勾选需人工确认后超时配置展开", "超时自动确认" in body)

        # ---- 5. 保存 → 落库验证 ----
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        detail = requests.get(f"{API}/projects/{created_pid}", timeout=10).json()
        evs = detail.get("events_config") or []
        mine = [e for e in evs if e.get("name") == EV_NAME]
        run.step("C1 落库: 新事件在 events_config", bool(mine), f"事件数={len(evs)}")
        if mine:
            ev = mine[0]
            run.step("C2 落库: 计数器动作 + require_ack",
                     len(ev.get("actions") or []) == 1 and ev.get("require_ack") is True,
                     f"actions={ev.get('actions')} require_ack={ev.get('require_ack')}")

        # ---- 6. 刷新回读 ----
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        time.sleep(1.5)
        card = page.locator(f"div.p-4:has-text('{PNAME}')").first
        if card.count() == 0:
            card = page.get_by_text(PNAME, exact=False).first
        card.click()
        time.sleep(1.0)
        page.locator(".el-tabs__item:has-text('事件设置')").first.click()
        time.sleep(1.0)
        vals = page.locator("input.bg-transparent.border-b").evaluate_all(
            "els => els.map(e => e.value)")
        run.shot(page, "03_reload_readback")
        run.step("D1 刷新后回读: 新事件名在 UI", EV_NAME in vals, f"vals={vals}")

        real = filter_console_errors(cerrs)
        run.step("E1 控制台无前端逻辑报错", not real,
                 f"真报错={real[:3]}" if real else f"噪声{len(cerrs)}条")

        ctx.close()
        browser.close()
finally:
    if created_pid:
        requests.delete(f"{API}/projects/{created_pid}", timeout=10)
        print(f">>> 清理: 已删测试项目 {PNAME}")

sys.exit(run.finish())
