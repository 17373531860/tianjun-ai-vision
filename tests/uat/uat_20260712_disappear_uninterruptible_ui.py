# -*- coding: utf-8 -*-
"""UAT (可见浏览器): v3.34 步骤级「等待不被打断」开关 — 滤网清洁检测项目.

路径 H: headless=False 真开浏览器, 人眼可见地在项目 26 (滤网清洁检测) 的
步骤设置里勾选 4 个步骤的「等待不被打断」开关 → 保存 → 后端回读落库.

产出三件套: 截图 (uat_dui_*.png) + run.log (stdout 重定向) + 落库回读断言.
前置: backend 8001 + frontend 6001 已启动.

跑法: ~/anaconda3/envs/tianjun/bin/python tests/uat/uat_20260712_disappear_uninterruptible_ui.py
"""
import json
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://localhost:6001"
API = "http://localhost:8001/api/v1"
SHOT_DIR = "/tmp/uat_dui"
PROJECT_NAME = "滤网清洁检测"

STEP_LABELS = ["出水口吹扫", "皮锤敲击毛坯外形", "检查滤网", "进水口吹扫"]
# 「消失等待时间」输入框占位符唯一, 锚定其后一列的开关
SWITCH_SEL = "td:has(input[placeholder='默认0秒']) + td .el-switch"


def api_get(path):
    with urllib.request.urlopen(f"{API}{path}", timeout=10) as r:
        return json.loads(r.read())


def main():
    import os
    os.makedirs(SHOT_DIR, exist_ok=True)

    projs = api_get("/projects")["items"]
    proj = next(x for x in projs if PROJECT_NAME in x["name"])
    pid = proj["id"]
    print(f"[UAT] project id={pid} name={proj['name']}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=300)
        page = browser.new_page(viewport={"width": 1680, "height": 950})
        page.goto(f"{BASE}/#/project", wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=15000)

        page.locator("input[placeholder*='搜索项目']").fill(PROJECT_NAME)
        time.sleep(0.8)
        page.locator(f"div.p-4:has-text('{PROJECT_NAME}')").first.click()
        time.sleep(1.0)
        page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
        time.sleep(1.2)

        # 滚到表B看到新列
        hdr = page.locator("th:has-text('等待不被打断')").first
        hdr.scroll_into_view_if_needed()
        time.sleep(0.5)
        page.screenshot(path=f"{SHOT_DIR}/01_新列_勾选前.png", full_page=False)
        print("[UAT] 新列「等待不被打断」已渲染 ✓ 截图 01")

        for lbl in STEP_LABELS:
            row = page.locator(
                f"tbody tr:has-text('{lbl}'):has(input[placeholder='默认0秒'])").first
            sw = row.locator(SWITCH_SEL)
            sw.scroll_into_view_if_needed()
            cls = sw.get_attribute("class") or ""
            assert "is-disabled" not in cls, f"{lbl} 开关不该是禁用态 (已配消失等待时间)"
            if "is-checked" not in cls:
                sw.click()
                time.sleep(0.4)
            print(f"[UAT] 勾选 [{lbl}] 等待不被打断 ✓")

        page.screenshot(path=f"{SHOT_DIR}/02_四步骤勾选后.png", full_page=False)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.5)
        page.screenshot(path=f"{SHOT_DIR}/03_保存后.png", full_page=False)
        browser.close()

    # ── T5 双向验证: 后端回读落库 ──
    detail = api_get(f"/projects/{pid}")
    for lbl in STEP_LABELS:
        s = next(x for x in detail["steps_config"] if x["label"] == lbl)
        assert s.get("disappear_uninterruptible") is True, \
            f"{lbl} 落库应为 True, 实际 {s.get('disappear_uninterruptible')!r}"
        print(f"[UAT] 落库回读 [{lbl}] disappear_uninterruptible=True ✓")
    print("[UAT] PASS — UI 勾选→保存→落库 全链路通过")


if __name__ == "__main__":
    main()
