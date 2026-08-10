"""LG 工时看板 v1.5.0 UAT: LG 口径全参数可配 (系统设置·LG 工时参数).

现场叙事:
  1. 领导看板出厂口径: 占比环"今日合格 N 轮" + 趋势"近 7 天" + 等待归 NVA
  2. 管理员进 系统设置(展会页) → LG 工时参数卡: 统计范围切"全部轮"、
     等待归类切"归 BVA"、趋势天数改 14 → 保存 → SystemConfig KV 落库
  3. 回检测中心: 占比环标题变"今日累计 N 轮"、趋势卡变"近 14 天趋势",
     汇总接口按新口径出数 (NG 轮进统计 + 等待折入 BVA)
  4. 重进设置页: 表单回显持久化真值 (UI→DB→UI 双向)
  5. 还原出厂, 不污染演示环境

用法 (dev: 后端 8004 + 前端 6004 + lg-worktime v1.5.0 已激活):
    python tests/uat/uat_lg_worktime_settings.py

产出: evidence/lgwt_settings_card.png / lgwt_settings_monitor_all.png
      / lgwt_settings_restored.png
"""
import os
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6004"
BACKEND = "http://localhost:8004/api/v1"
SETTINGS_API = f"{BACKEND}/plugins/lg-worktime/dashboard/settings"
OUT = Path(__file__).resolve().parents[2] / "evidence"
OUT.mkdir(exist_ok=True)
fails = []
front_errors = []

FACTORY = {"default_value_type": "VA", "wait_value_type": "NVA",
           "lean_scope": "good_only", "trend_days": 7}


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        fails.append(msg)


def hook_frontend(page):
    page.on("console", lambda m: front_errors.append(f"console.{m.type}: {m.text}")
            if m.type == "error" and "favicon" not in m.text else None)
    page.on("pageerror", lambda e: front_errors.append(f"pageerror: {e}"))
    page.on("response", lambda r: front_errors.append(f"HTTP {r.status} {r.url[-80:]}")
            if r.status >= 500 else None)


def api_settings():
    return requests.get(SETTINGS_API, timeout=10).json()["settings"]


def goto_monitor(page):
    page.goto(f"{FRONTEND}/monitor", wait_until="domcontentloaded")
    time.sleep(2)
    if not page.locator(".lgwt-shell").count():
        page.evaluate("window.location.hash = '#/monitor'")
    page.wait_for_selector(".lgwt-shell", timeout=60000)
    time.sleep(3)


def goto_settings_iframe(page):
    page.goto(f"{FRONTEND}/#/settings", wait_until="domcontentloaded")
    page.wait_for_selector("iframe[src*='showcase-app']", timeout=20000)
    time.sleep(3)
    fr = page.frame_locator("iframe[src*='showcase-app']")
    fr.locator("#lgwtParamsCard").wait_for(timeout=15000)
    time.sleep(1.0)
    return fr


def card_values(fr):
    return {
        "default_value_type": fr.locator("#lgwtDefVt").input_value(),
        "wait_value_type": fr.locator("#lgwtWaitVt").input_value(),
        "lean_scope": fr.locator("#lgwtScope").input_value(),
        "trend_days": int(fr.locator("#lgwtTrendDays").input_value()),
    }


def main():
    # 前置: API 钉回出厂, UAT 从确定状态出发
    requests.post(SETTINGS_API, json=FACTORY, timeout=10).raise_for_status()

    with sync_playwright() as p:
        # UAT_HEADLESS=1: 多 agent 并行时禁止抢显示器/鼠标, 全程 headless
        browser = p.chromium.launch(headless=os.environ.get("UAT_HEADLESS") == "1")
        page = browser.new_page(viewport={"width": 1680, "height": 945})
        hook_frontend(page)

        # ---- 1. 出厂口径下的看板 ----
        print("[uat] === 出厂口径看板 ===")
        goto_monitor(page)
        donut = page.locator(".lgwt-card-donut .lgwt-card-title").inner_text()
        trendt = page.locator(".lgwt-card-trend .lgwt-card-title").inner_text()
        ok("今日合格" in donut, f"出厂: 占比环标题应含「今日合格」(实际: {donut})")
        ok("近 7 天" in trendt, f"出厂: 趋势卡应为「近 7 天」(实际: {trendt})")

        # ---- 2. 系统设置页: LG 工时参数卡改口径并保存 ----
        print("[uat] === 系统设置·LG 工时参数 ===")
        fr = goto_settings_iframe(page)
        vals = card_values(fr)
        ok(vals == FACTORY, f"设置卡应回显出厂值 (实际: {vals})")
        fr.locator("#lgwtScope").select_option("all")
        fr.locator("#lgwtWaitVt").select_option("BVA")
        fr.locator("#lgwtTrendDays").fill("14")
        page.screenshot(path=str(OUT / "lgwt_settings_card.png"))
        fr.locator("#lgwtParamsSaveBtn").click()
        time.sleep(2)
        got = api_settings()
        ok(got["lean_scope"] == "all" and got["wait_value_type"] == "BVA"
           and got["trend_days"] == 14,
           f"保存后 KV 应为 all/BVA/14 (实际: {got})")

        # 汇总接口按新口径出数: NG 轮进统计 + 等待折入 BVA (nva_total 不再含等待)
        L = requests.get(f"{BACKEND}/plugins/lg-worktime/dashboard/summary",
                         timeout=10).json()["lean"]
        ok(L["scope"] == "all" and L["wait_value_type"] == "BVA",
           f"summary 应报 scope=all + wait→BVA (实际: {L['scope']}/{L['wait_value_type']})")
        ok(L["wait"] == 0 or L["bva"] >= L["wait"],
           f"等待 {L['wait']}s 应折入 BVA (BVA={L['bva']}s)")

        # ---- 3. 回看板: 标题跟随新口径 ----
        print("[uat] === 看板跟随新口径 ===")
        goto_monitor(page)
        donut = page.locator(".lgwt-card-donut .lgwt-card-title").inner_text()
        trendt = page.locator(".lgwt-card-trend .lgwt-card-title").inner_text()
        ok("今日累计" in donut, f"切全部轮后占比环应显「今日累计」(实际: {donut})")
        ok("近 14 天" in trendt, f"趋势卡应变「近 14 天」(实际: {trendt})")
        page.screenshot(path=str(OUT / "lgwt_settings_monitor_all.png"))

        # ---- 4. 重进设置页: 回显持久化真值 (UI→DB→UI) ----
        print("[uat] === 设置回显持久化 ===")
        fr = goto_settings_iframe(page)
        vals = card_values(fr)
        ok(vals["lean_scope"] == "all" and vals["wait_value_type"] == "BVA"
           and vals["trend_days"] == 14,
           f"重进设置页应回显 all/BVA/14 (实际: {vals})")

        # ---- 5. UI 还原出厂 ----
        print("[uat] === 还原出厂 ===")
        fr.locator("#lgwtScope").select_option("good_only")
        fr.locator("#lgwtWaitVt").select_option("NVA")
        fr.locator("#lgwtTrendDays").fill("7")
        fr.locator("#lgwtParamsSaveBtn").click()
        time.sleep(2)
        ok(api_settings() == FACTORY, "UI 还原后 KV 应回出厂默认")
        goto_monitor(page)
        donut = page.locator(".lgwt-card-donut .lgwt-card-title").inner_text()
        trendt = page.locator(".lgwt-card-trend .lgwt-card-title").inner_text()
        ok("今日合格" in donut and "近 7 天" in trendt, "看板应回出厂口径")
        page.screenshot(path=str(OUT / "lgwt_settings_restored.png"))

        # ---- 6. 前端异常汇总 ----
        real_errors = [e for e in front_errors if "ResizeObserver" not in e]
        ok(not real_errors, f"前端应无异常 (收集到 {len(real_errors)} 条)")
        for e in real_errors[:10]:
            print("   前端异常:", e)

        browser.close()

    if fails:
        print(f"[uat] FAIL ({len(fails)}): {fails}")
        sys.exit(1)
    print("[uat] LG 参数可配 UAT 全部通过")


if __name__ == "__main__":
    main()
