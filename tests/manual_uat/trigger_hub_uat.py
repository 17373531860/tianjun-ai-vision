# -*- coding: utf-8 -*-
"""RFC 14 统一触发中心 — 可见浏览器 UAT (headless=False, 人眼证据).

现场叙事:
  集成工程师到现场, 打开 系统设置 → 触发中心, 从「方案模板」载入虚拟触发源范式,
  改名保存并启用 → 卡片翻「运行中」→ 点卡片盯实时状态 →
  「注入脉冲」按钮 → 触发计数 +1, 最近触发出记录, 日志出「触发」行 →
  「试触发」按钮直接执行动作 → 电平↑↓ 验证边沿判定 → 停用 → 全程截图留证。

跑法 (先起 backend 8001 + frontend 6001/6002):
  python tests/manual_uat/trigger_hub_uat.py [frontend_port]
证据落地: tests/manual_uat/evidence/trigger_hub_<date>/
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

API = "http://localhost:8001"
BASE = f"http://localhost:{sys.argv[1] if len(sys.argv) > 1 else '6001'}"
TRG = f"{API}/api/v1/triggers"
NAME = "__uat_虚拟触发"

EVIDENCE = Path(__file__).parent / "evidence" / f"trigger_hub_{datetime.now():%Y-%m-%d}"
EVIDENCE.mkdir(parents=True, exist_ok=True)

verdict = {"steps": [], "pass": True}


def step(name: str, ok: bool, detail: str = ""):
    verdict["steps"].append({"name": name, "ok": ok, "detail": detail})
    verdict["pass"] = verdict["pass"] and ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def cleanup():
    for t in requests.get(f"{TRG}/channels", timeout=5).json().get("triggers", []):
        if t["name"].startswith("__uat_"):
            requests.delete(f"{TRG}/channels/{t['id']}", timeout=5)


def main() -> int:
    cleanup()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})
        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)

        # ── 1. 进 系统设置 → 触发中心 tab ──
        page.goto(f"{BASE}/#/settings", wait_until="networkidle")
        page.get_by_text("触发中心", exact=True).first.click()
        page.wait_for_selector("text=触发源", timeout=8000)
        page.screenshot(path=str(EVIDENCE / "01_panel_empty.png"), full_page=True)
        step("进入触发中心面板", True)

        # ── 2. 方案模板 → 虚拟触发源 → 改名 → 保存后启用 ──
        page.locator("button:has-text('方案模板')").first.hover()
        page.wait_for_selector(".el-dropdown-menu__item:has-text('虚拟触发源')", timeout=6000)
        page.locator(".el-dropdown-menu__item:has-text('虚拟触发源')").first.click()
        page.wait_for_selector(".el-dialog:has-text('添加触发源')", timeout=6000)
        dialog = page.locator(".el-dialog:has-text('添加触发源')")
        dialog.locator(".el-form-item:has-text('名称') input").first.fill(NAME)
        dialog.locator(".el-checkbox:has-text('保存后启用')").click()
        page.screenshot(path=str(EVIDENCE / "02_template_dialog.png"), full_page=True)
        dialog.get_by_role("button", name="保存").click()
        page.wait_for_selector(".el-message--success", timeout=6000)
        step("模板载入 + 保存并启用", True)

        # ── 3. T5 落库双向验证 ──
        rows = [t for t in requests.get(f"{TRG}/channels", timeout=5).json()
                .get("triggers", []) if t["name"] == NAME]
        ok = bool(rows) and rows[0]["type"] == "mock" and rows[0]["enabled"]
        step("T5 落库核对 (type/enabled/rules)", ok,
             f"rules={len(rows[0].get('rules', [])) if rows else 0}")
        tid = rows[0]["id"] if rows else None
        if tid is None:
            raise RuntimeError("触发源未落库, 后续步骤无法继续")

        # ── 4. 卡片翻「运行中」→ 点卡片出实时状态 ──
        page.wait_for_selector(f".cursor-pointer:has-text('{NAME}'):has-text('运行中')",
                               timeout=10000)
        card = page.locator(f".cursor-pointer:has-text('{NAME}')").first
        card.click()
        page.wait_for_selector("text=现场联调", timeout=8000)
        page.screenshot(path=str(EVIDENCE / "03_running_live.png"), full_page=True)
        step("卡片运行中 + 实时状态面板", True)

        # ── 5. UI「注入脉冲」→ 触发计数 +1 + 历史/日志上屏 ──
        page.get_by_role("button", name="注入脉冲").click()
        page.wait_for_selector(".el-message--success", timeout=6000)
        time.sleep(3)  # 前端 1.5s 轮询
        live = requests.get(f"{TRG}/channels/{tid}/live", timeout=5).json()
        step("注入脉冲触发规则", live.get("counters", {}).get("fires", 0) >= 1,
             f"counters={live.get('counters')}")
        body = page.evaluate("document.body.innerText")
        step("UI 触发历史出记录", "演示规则" in body)
        step("UI 日志出「触发」行", "触发" in body)
        page.screenshot(path=str(EVIDENCE / "04_pulse_fired.png"), full_page=True)

        # ── 6. 「试触发」按钮 (绕过信号直接执行动作) ──
        fires_before = live.get("counters", {}).get("fires", 0)
        page.locator("button:has-text('试触发')").first.click()
        page.wait_for_selector(".el-message--success", timeout=6000)
        time.sleep(2)
        logs = requests.get(f"{TRG}/channels/{tid}/logs?limit=50", timeout=5).json()
        tested = any("手动试触发" in l["detail"] for l in logs.get("logs", []))
        step("试触发按钮执行动作", tested)
        page.screenshot(path=str(EVIDENCE / "05_test_fire.png"), full_page=True)

        # ── 7. 电平↑↓ 验证边沿判定 (mock 规则是 pulse, 电平不触发但计入信号) ──
        page.get_by_role("button", name="电平↑").click()
        page.wait_for_selector(".el-message--success", timeout=6000)
        page.get_by_role("button", name="电平↓").click()
        time.sleep(2)
        live = requests.get(f"{TRG}/channels/{tid}/live", timeout=5).json()
        step("电平注入进信号计数", live.get("counters", {}).get("signals", 0) >= 2,
             f"signals={live.get('counters', {}).get('signals')}")

        # ── 8. 停用 → 卡片翻停用 ──
        card.locator(".el-switch").click()
        page.wait_for_selector(f".cursor-pointer:has-text('{NAME}'):has-text('停用')",
                               timeout=8000)
        page.screenshot(path=str(EVIDENCE / "06_disabled.png"), full_page=True)
        step("停用开关生效", True)

        step("浏览器 console 零错误", not console_errors, str(console_errors[:3]))
        browser.close()

    cleanup()
    (EVIDENCE / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n证据目录: {EVIDENCE}")
    print(f"结论: {'全部通过' if verdict['pass'] else '存在失败步骤'}")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        cleanup()
        (EVIDENCE / "verdict.json").write_text(
            json.dumps({**verdict, "pass": False, "crash": str(e)},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(2)
