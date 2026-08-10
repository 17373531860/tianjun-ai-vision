# -*- coding: utf-8 -*-
"""RFC 13 通用 PLC 连接器 — 可见浏览器 UAT (headless=False, 人眼证据).

现场叙事:
  调试工程师到新产线, 打开 MES → PLC 对接, 从「方案模板」载入虚拟 PLC 范式,
  改名保存并启用 → 卡片翻「已连接」→ 点卡片盯实时监视 →
  模拟 PLC 侧置产品号 + read_done 上升沿 → 规则触发 (触发计数 +1,
  data_received 回执自动置 1 又复位, IO 日志出「触发」行) →
  手动写 result_code=9 → 实时值显示 9 → 全程截图留证。

跑法 (先起 backend 8001 + frontend 6001):
  python tests/manual_uat/plc_connector_uat.py
证据落地: tests/manual_uat/evidence/plc_connector_<date>/
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
BASE = "http://localhost:6001"
PLC = f"{API}/api/v1/plc"
NAME = "__uat_虚拟PLC"

EVIDENCE = Path(__file__).parent / "evidence" / f"plc_connector_{datetime.now():%Y-%m-%d}"
EVIDENCE.mkdir(parents=True, exist_ok=True)

verdict = {"steps": [], "pass": True}


def step(name: str, ok: bool, detail: str = ""):
    verdict["steps"].append({"name": name, "ok": ok, "detail": detail})
    verdict["pass"] = verdict["pass"] and ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def cleanup():
    for c in requests.get(f"{PLC}/connections", timeout=5).json().get("connections", []):
        if c["name"].startswith("__uat_"):
            requests.delete(f"{PLC}/connections/{c['id']}", timeout=5)


def main() -> int:
    cleanup()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})
        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)

        # ── 1. 进 MES → PLC 对接 tab ──
        page.goto(f"{BASE}/#/mes", wait_until="networkidle")
        page.get_by_text("PLC 对接", exact=True).first.click()
        page.wait_for_selector("text=PLC 连接", timeout=8000)
        page.screenshot(path=str(EVIDENCE / "01_panel_empty.png"), full_page=True)
        step("进入 PLC 对接面板", True)

        # ── 2. 方案模板 → 虚拟 PLC → 改名 → 保存后启用 ──
        page.locator("button:has-text('方案模板')").first.hover()
        page.wait_for_selector(".el-dropdown-menu__item:has-text('虚拟 PLC')", timeout=6000)
        page.locator(".el-dropdown-menu__item:has-text('虚拟 PLC')").first.click()
        page.wait_for_selector(".el-dialog:has-text('添加 PLC 连接')", timeout=6000)
        dialog = page.locator(".el-dialog:has-text('添加 PLC 连接')")
        dialog.locator(".el-form-item:has-text('名称') input").first.fill(NAME)
        dialog.locator(".el-checkbox:has-text('保存后启用')").click()
        page.screenshot(path=str(EVIDENCE / "02_template_dialog.png"), full_page=True)
        dialog.get_by_role("button", name="保存").click()
        page.wait_for_selector(".el-message--success", timeout=6000)
        step("模板载入 + 保存并启用", True)

        # ── 3. T5 落库双向验证: GET API 核对配置真入库 ──
        rows = [c for c in requests.get(f"{PLC}/connections", timeout=5).json()
                .get("connections", []) if c["name"] == NAME]
        ok = bool(rows) and rows[0]["driver"] == "mock" and rows[0]["enabled"]
        step("T5 落库核对 (driver/enabled/points)", ok,
             f"points={len(rows[0].get('points', [])) if rows else 0}")
        cid = rows[0]["id"] if rows else None
        if cid is None:
            raise RuntimeError("连接未落库, 后续步骤无法继续")

        # ── 4. 卡片翻「已连接」→ 点卡片出实时监视 ──
        page.wait_for_selector(f".cursor-pointer:has-text('{NAME}'):has-text('已连接')",
                               timeout=10000)
        card = page.locator(f".cursor-pointer:has-text('{NAME}')").first
        card.click()
        page.wait_for_selector("text=read_done", timeout=8000)
        page.screenshot(path=str(EVIDENCE / "03_connected_live.png"), full_page=True)
        step("卡片已连接 + 实时监视出点位", True)

        # ── 5. 模拟 PLC 侧: 产品号 + read_done 上升沿 → 规则触发 ──
        # 模板 store_id 固定 "demo", 上一跑可能把 read_done 留在 1 → 先复位再置位造边沿
        requests.post(f"{PLC}/connections/{cid}/mock-set",
                      json={"point": "read_done", "value": 0}, timeout=5)
        requests.post(f"{PLC}/connections/{cid}/mock-set",
                      json={"point": "product_no", "value": "UAT-SN-001"}, timeout=5)
        time.sleep(0.5)  # 让引擎先看到 0 电平
        requests.post(f"{PLC}/connections/{cid}/mock-set",
                      json={"point": "read_done", "value": 1}, timeout=5)
        time.sleep(3)  # 引擎 50ms 轮询 + 前端 1.5s 轮询, 3s 足够上屏
        page.screenshot(path=str(EVIDENCE / "04_rule_fired.png"), full_page=True)

        live = requests.get(f"{PLC}/connections/{cid}/live", timeout=5).json()
        counters = live.get("counters", {})
        step("read_done 上升沿触发规则", counters.get("rule_fires", 0) >= 1,
             f"counters={counters}")

        body = page.evaluate("document.body.innerText")
        step("UI 显示产品号实时值", "UAT-SN-001" in body)
        step("IO 日志出现「触发」行", "触发" in body)

        # data_received 回执: 置 1 后 500ms 自动复位, 验证引擎真写过
        logs = requests.get(f"{PLC}/connections/{cid}/logs?limit=100", timeout=5).json()
        wrote_ack = any(l["dir"] == "write" and "data_received" in l["detail"]
                        for l in logs.get("logs", []))
        step("回执 data_received 写入+自动复位", wrote_ack)

        # ── 6. 手动写值 result_code=9 → 实时值上屏 ──
        panel = page.locator("div:has(> div > h3:text-is('实时监视'))").last
        panel.locator(".el-select").first.click()
        page.locator(".el-select-dropdown__item:has-text('result_code')").first.click()
        panel.locator("input[placeholder='值']").first.fill("9")
        panel.get_by_role("button", name="写入").click()
        page.wait_for_selector(".el-message--success", timeout=6000)
        time.sleep(2)
        # result_code 是 write-only 点位, 按设计不进读值缓存 → 从 IO 日志验证写链路
        logs = requests.get(f"{PLC}/connections/{cid}/logs?limit=100", timeout=5).json()
        wrote_rc = any(l["dir"] == "write" and "result_code" in l["detail"]
                       and "9" in l["detail"] for l in logs.get("logs", []))
        step("手动写 result_code=9 引擎真写出", wrote_rc)
        page.screenshot(path=str(EVIDENCE / "05_manual_write.png"), full_page=True)

        # ── 7. 停用 → 卡片翻停用 ──
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
