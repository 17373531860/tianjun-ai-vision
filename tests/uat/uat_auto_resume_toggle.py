"""开机自动恢复检测开关 — 主程序真实环境验证脚本。

针对真实运行的主程序: 前端 6002 + 后端 8002 (可用环境变量覆盖)。
不经过 e2e_browser/conftest 的端口 gate, 直接打真实服务。
零副作用: 只读写「开机自动恢复检测」开关, 结束恢复初始值, 不创建/激活任何项目。

运行: UAT_BASE_URL=http://localhost:6002 UAT_API_URL=http://localhost:8002 \
      ~/anaconda3/envs/tianjun/bin/python tests/uat/uat_auto_resume_toggle.py
产物: tests/uat/uat_ar_01_initial.png / _02_after_click.png / _03_after_reload.png
"""
from __future__ import annotations

import os
import sys

import requests
from playwright.sync_api import sync_playwright

BASE = os.environ.get("UAT_BASE_URL", "http://localhost:6002")
API = os.environ.get("UAT_API_URL", "http://localhost:8002")
API_PATH = "/api/v1/workstations/auto-resume"
SW = '[data-testid="auto-resume-switch"]'
SHOT_DIR = os.path.dirname(__file__)

results = []


def step(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'OK ' if ok else 'FAIL'}] {name}  {detail}")


def get_enabled():
    return requests.get(f"{API}{API_PATH}", timeout=5).json().get("enabled")


def ui_on(locator):
    return "is-checked" in (locator.get_attribute("class") or "")


def main():
    init = get_enabled()
    step("后端端点可达, 读到初始开关值", isinstance(init, bool), f"enabled={init}")
    if not isinstance(init, bool):
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            page.goto(f"{BASE}/#/settings", wait_until="domcontentloaded")
            sw = page.locator(SW)
            sw.wait_for(state="visible", timeout=15000)
            sw.scroll_into_view_if_needed()
            page.wait_for_timeout(1200)
            page.screenshot(path=os.path.join(SHOT_DIR, "uat_ar_01_initial.png"))

            ui0 = ui_on(sw)
            step("设置页开关初始状态与后端一致", ui0 == init, f"UI={ui0} 后端={init}")

            sw.click()
            page.wait_for_timeout(900)
            page.screenshot(path=os.path.join(SHOT_DIR, "uat_ar_02_after_click.png"))

            after = get_enabled()
            step("点击开关后, 后端落盘值已取反", after == (not init), f"点击后后端={after}")

            page.reload(wait_until="domcontentloaded")
            sw2 = page.locator(SW)
            sw2.wait_for(state="visible", timeout=15000)
            sw2.scroll_into_view_if_needed()
            page.wait_for_timeout(1200)
            page.screenshot(path=os.path.join(SHOT_DIR, "uat_ar_03_after_reload.png"))

            ui2 = ui_on(sw2)
            step("刷新页面后, 开关回填为切换后的状态", ui2 == (not init), f"刷新后UI={ui2}")
        finally:
            requests.put(f"{API}{API_PATH}", json={"enabled": init}, timeout=5)
            step("已恢复开关初始值, 零副作用", get_enabled() == init, f"恢复为={init}")
            browser.close()

    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n==== 共 {len(results)} 步, 失败 {failed} ====")
    print(f"failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
