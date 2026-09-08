"""投影光引导 P3 可见浏览器 UAT: 设置卡 (标定状态 + 引导参数)。

前置: 后端 8005 + 前端 6005 已起 (与 P1/P2 UAT 同环境)。
剧本:
  A. 「工位与输入源 → 多屏工位显示」出现投影光引导卡 (标定状态行 + 参数表单)
  B. 改悬停确认时长 1200→2000 + 关闭自动重标 → 保存 → 后端 GET 验证落库
  C. 「恢复默认」→ 保存 → 后端回读回到出厂默认
  D. 「打开投影窗」按钮存在; 投影窗拉取 /lightguide/params (network 断言)
运行: python tests/uat/uat_lightguide_p3.py
"""
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:6005"
API = "http://127.0.0.1:8005/api/v1"
SHOTS = "tests/uat/shots_lightguide_p3"

FAILED = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def main():
    import os
    os.makedirs(SHOTS, exist_ok=True)
    # 还原参数出厂默认, 保证剧本可重复
    requests.put(f"{API}/lightguide/params", json={
        "hover_dwell_ms": 1200, "auto_recalibrate": True}, timeout=5)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=[
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
        ])
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # ---- A. 设置卡渲染 ----
        page.goto(f"{BASE}/#/source", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.get_by_role("tab", name="多屏工位显示").click()
        card = page.get_by_test_id("lightguide-card")
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        check("A1 投影光引导卡可见", card.is_visible())
        check("A2 工位标定状态行", page.get_by_test_id("lightguide-status-0").count() == 1)
        check("A3 参数表单悬停时长", page.get_by_test_id("lightguide-param-hover-dwell").count() == 1)
        page.bring_to_front()
        page.screenshot(path=f"{SHOTS}/A_card.png")

        # ---- B. 改参数保存 → 后端验证 ----
        dwell_input = page.get_by_test_id("lightguide-param-hover-dwell").locator("input")
        dwell_input.fill("2000")
        dwell_input.press("Enter")
        page.get_by_test_id("lightguide-param-auto-recal").click()
        page.get_by_test_id("lightguide-save").click()
        page.wait_for_timeout(1200)
        saved = requests.get(f"{API}/lightguide/params", timeout=5).json()["params"]
        check("B1 悬停时长落库 2000", saved["hover_dwell_ms"] == 2000,
              f"实际 {saved['hover_dwell_ms']}")
        check("B2 自动重标关闭落库", saved["auto_recalibrate"] is False)
        page.bring_to_front()
        page.screenshot(path=f"{SHOTS}/B_saved.png")

        # ---- C. 恢复默认 → 保存 → 回读 ----
        page.get_by_test_id("lightguide-reset").click()
        page.wait_for_timeout(300)
        page.get_by_test_id("lightguide-save").click()
        page.wait_for_timeout(1200)
        saved = requests.get(f"{API}/lightguide/params", timeout=5).json()["params"]
        check("C1 恢复默认落库", saved["hover_dwell_ms"] == 1200
              and saved["auto_recalibrate"] is True)

        # ---- D. 投影窗拉参数 ----
        check("D1 打开投影窗按钮", page.get_by_test_id("lightguide-open-0").count() == 1)
        params_requests = []
        proj_page = browser.new_page(viewport={"width": 1440, "height": 900})
        proj_page.on("request", lambda r: params_requests.append(r.url)
                     if "/lightguide/params" in r.url else None)
        proj_page.goto(f"{BASE}/#/projection?channel=0", wait_until="domcontentloaded")
        proj_page.wait_for_timeout(2500)
        check("D2 投影窗启动拉取引导参数", len(params_requests) >= 1,
              f"{len(params_requests)} 次")
        proj_page.bring_to_front()
        proj_page.screenshot(path=f"{SHOTS}/D_projection.png")

        browser.close()

    print()
    if FAILED:
        print(f"UAT FAILED: {len(FAILED)} 项 — {FAILED}")
        sys.exit(1)
    print("UAT ALL PASS")


if __name__ == "__main__":
    main()
