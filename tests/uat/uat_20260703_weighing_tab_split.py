# -*- coding: utf-8 -*-
"""可见浏览器 UAT: Project 页称重配置 Tab 外置为 WeighingConfigTab.vue（拆分批次 P-1）。

现场叙事: 操作员建了一个称重投料项目 → 打开项目管理选中它 → 出现「称重配置」Tab
→ 五张卡片齐全 → 添加一道料别 + 一个型号并填标准量 → 点保存 → 配置真实落库
→ 刷新页面回读一致。拆分是"只搬不改", 所有行为必须与拆分前完全一致。

前置: 后端 8001 + 前端 6001 (vite dev) 已由用户启动。
测试项目用 __uat_ 前缀, 收尾删除, 不污染用户项目。
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
PNAME = f"__uat_weighing_{uuid.uuid4().hex[:6]}"

run = UatRun("weighing_tab_split")


def create_weighing_project():
    payload = {
        "name": PNAME,
        "task_type": "detection",
        "logic_mode": "weighing",
        "pipeline_config": {},
        "steps_config": [],
        "events_config": [
            {"id": 1, "name": "合格", "type": "ok", "enabled": True},
            {"id": 2, "name": "NG", "type": "ng", "enabled": True},
        ],
        "counters_config": [],
        "alarm_config": {},
        "detection_config": {},
        "data_config": {},
        "default_model_id": None,
        "model_format": "pytorch_fp32",
    }
    r = requests.post(f"{API}/projects", json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def get_detail(pid):
    r = requests.get(f"{API}/projects/{pid}", timeout=10)
    r.raise_for_status()
    return r.json()


proj = create_weighing_project()
PID = proj["id"]
run.step("A1 API 建称重项目", proj.get("logic_mode") == "weighing", f"id={PID}")

try:
    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)

        # ---- 进项目页选中测试项目 ----
        page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        time.sleep(1.5)
        card = page.locator(f"div.p-4:has-text('{PNAME}')").first
        if card.count() == 0:
            card = page.get_by_text(PNAME, exact=False).first
        card.click()
        time.sleep(1.2)
        run.shot(page, "01_project_selected")

        # ---- 称重配置 Tab 存在且可进 ----
        tab = page.locator(".el-tabs__item:has-text('称重配置')").first
        run.step("B1 称重配置 Tab 渲染(外置组件挂载点)", tab.count() > 0)
        tab.click()
        time.sleep(1.0)
        body = page.evaluate("document.body.innerText")
        cards_ok = all(k in body for k in ["前置要求", "料别（投料顺序）", "型号标准量表", "去皮与稳定判定", "校验与报警"])
        run.shot(page, "02_weighing_tab_cards")
        run.step("B2 五张配置卡片齐全", cards_ok, "缺卡片" if not cards_ok else "")

        # ---- 默认料别注入正常（ensureWeighingDefaults 留在父级的链路没断） ----
        run.step("B3 默认料别注入(钢帽/钢脚水泥)", "钢帽水泥" in body and "钢脚水泥" in body)

        # ---- 交互: 添加料别 ----
        page.locator("input[placeholder='新料别名']").fill("锡膏")
        page.locator("button:has-text('添加料别')").click()
        time.sleep(0.6)
        run.step("B4 添加料别 UI 生效", "锡膏" in page.evaluate("document.body.innerText"))

        # ---- 交互: 添加型号 + 填标准量 ----
        page.locator("input[placeholder='新型号名']").fill("UAT型号X")
        page.locator("button:has-text('添加型号')").click()
        time.sleep(0.8)
        body = page.evaluate("document.body.innerText")
        run.step("B5 添加型号出现标准量表", "UAT型号X" in body)
        # 第一行(钢帽水泥)标准量填 1.234
        first_std = page.locator(".el-input-number input").first
        first_std.fill("1.234")
        first_std.press("Enter")
        time.sleep(0.5)
        run.shot(page, "03_model_spec_filled")

        # ---- 保存 ----
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        run.shot(page, "04_saved")

        # ---- T5: 落库双向验证 ----
        detail = get_detail(PID)
        w = (detail.get("pipeline_config") or {}).get("weighing") or {}
        mats = w.get("materials") or []
        models = w.get("models") or {}
        run.step("C1 落库: 料别含新增'锡膏'", "锡膏" in mats, f"materials={mats}")
        std = ((models.get("UAT型号X") or {}).get("钢帽水泥") or {}).get("standard")
        run.step("C2 落库: 型号标准量=1.234", abs((std or 0) - 1.234) < 1e-6, f"standard={std}")

        # ---- 刷新回读 ----
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        time.sleep(1.5)
        card = page.locator(f"div.p-4:has-text('{PNAME}')").first
        if card.count() == 0:
            card = page.get_by_text(PNAME, exact=False).first
        card.click()
        time.sleep(1.0)
        page.locator(".el-tabs__item:has-text('称重配置')").first.click()
        time.sleep(1.0)
        body = page.evaluate("document.body.innerText")
        run.shot(page, "05_reload_readback")
        run.step("C3 刷新回读: 锡膏/UAT型号X 仍在", "锡膏" in body and "UAT型号X" in body)

        # ---- 控制台无前端逻辑报错 ----
        real = filter_console_errors(cerrs)
        run.step("D1 控制台无前端逻辑报错", not real, f"真报错={real[:3]}" if real else f"噪声{len(cerrs)}条")

        ctx.close()
        browser.close()
finally:
    requests.delete(f"{API}/projects/{PID}", timeout=10)
    print(f">>> 清理: 已删测试项目 {PNAME}")

sys.exit(run.finish())
