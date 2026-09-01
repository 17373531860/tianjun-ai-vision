"""可见浏览器 UAT: 检测模式「超时结算」独立卡 (patch v3.48.1i).

现场叙事:
  工程师打开缸体判型项目(检测模式)的逻辑设置 → 直接看到「超时结算」卡 →
  填空闲超时 30 秒 / 周期超时 300 秒 → 保存 → 落库生效, 工人停手 30s 自动结算判定。

验证矩阵:
  1. 检测模式逻辑设置渲染「超时结算」卡(此前只在顺序模式的结算方式卡里)
  2. UI 填 30 / 300 → 保存 → GET /projects/{id} pipeline_config 双向核对
  3. 顺序模式同样渲染(卡片拆出后老模式不丢入口)

用法: conda activate tianjun && python tests/manual_uat/detection_timeout_card_uat.py
前置: 后端 127.0.0.1:8010 (RUNTIME_MODE=test) + 前端 127.0.0.1:6010
"""
import json
import pathlib
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8010"
WEB = "http://127.0.0.1:6010"
EVID = pathlib.Path(__file__).parent / "evidence" / "detection_timeout_card_2026-08-21"
EVID.mkdir(parents=True, exist_ok=True)

verdict = {"scenario": "detection_timeout_card", "checks": []}


def check(name, ok, detail=""):
    verdict["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def mk_project(mode):
    name = f"__uat_timeout_{mode[:4]}_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": mode,
        "steps_config": [{"id": 1, "label": "区A", "name": "区域A", "enabled": True}],
    }, timeout=5)
    r.raise_for_status()
    return r.json()["id"], name


def open_logic_tab(page, name):
    page.goto(f"{WEB}/#/project", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.8)
    page.locator(f"div.p-4:has-text('{name}')").first.click()
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(0.8)


def main():
    pid_det, name_det = mk_project("detection")
    pid_seq, name_seq = mk_project("sequential")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})

        # 场景1: 检测模式可见 + 填值保存
        open_logic_tab(page, name_det)
        card = page.locator(".el-card:has-text('超时结算')").first
        check("检测模式渲染超时结算卡", card.count() > 0)
        card.scroll_into_view_if_needed()
        page.screenshot(path=str(EVID / "01_detection_card_visible.png"))
        inputs = card.locator(".el-input-number input")
        inputs.nth(0).fill("30")
        inputs.nth(1).fill("300")
        page.screenshot(path=str(EVID / "02_filled_30_300.png"))
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)

        # 场景2: 落库双向核对
        pc = requests.get(f"{API}/api/v1/projects/{pid_det}", timeout=5).json().get("pipeline_config") or {}
        check("idle_timeout_seconds落库=30", pc.get("idle_timeout_seconds") == 30, str(pc.get("idle_timeout_seconds")))
        check("cycle_max_duration落库=300", pc.get("cycle_max_duration") == 300, str(pc.get("cycle_max_duration")))

        # 场景3: 回显 (重新打开读输入框值)
        open_logic_tab(page, name_det)
        card = page.locator(".el-card:has-text('超时结算')").first
        card.scroll_into_view_if_needed()
        v0 = card.locator(".el-input-number input").nth(0).input_value()
        v1 = card.locator(".el-input-number input").nth(1).input_value()
        # el-input-number :precision=2 显示 "30.00", 按数值比对
        check("重开回显30/300", float(v0) == 30 and float(v1) == 300, f"{v0}/{v1}")
        page.screenshot(path=str(EVID / "03_reopen_echo.png"))

        # 场景4: 顺序模式不丢入口
        open_logic_tab(page, name_seq)
        card = page.locator(".el-card:has-text('超时结算')").first
        check("顺序模式仍渲染超时结算卡", card.count() > 0)
        card.scroll_into_view_if_needed()
        page.screenshot(path=str(EVID / "04_sequential_still_has_card.png"))

        browser.close()

    verdict["all_pass"] = all(c["ok"] for c in verdict["checks"])
    (EVID / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2))
    print("ALL_PASS" if verdict["all_pass"] else "HAS_FAIL")


if __name__ == "__main__":
    main()
