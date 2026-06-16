"""可见浏览器 UAT — 包装箱结算「滑块口径」(上银 MES 闭环, v3.22).

验证新增的 sliders 计数口径在 Monitor 卡的真实渲染 (headless=False + 视频留痕):
  扫工单 → 协调器按 sliders 口径开工单/开箱 → Monitor 卡显示「滑块 N / 目标」而非托盘;
  禁用配置刷新后卡干净消失 (未开此功能的客户零差异).

不依赖 synthetic 检测 mock: 用 on_mes_fail=offline 让扫码即开第 1 箱 (滑块 0/每箱数),
重点验证前端 isSliders 分支渲染正确 (滑块标签 / 进度条 / 卡显示与隐藏).

跑法 (后端 8001 + 前端 6001 已起):
  python tests/uat/uat_20260617_packaging_sliders.py
"""
import json
import os
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8001"
FRONTEND = "http://127.0.0.1:6001"
SHOTS = "/tmp/uat_pkg_sliders_shots"
VIDEO = "/tmp/uat_pkg_sliders_video"
RUNLOG = "/tmp/uat_pkg_sliders_run.log"

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": str(detail)}
    steps_log.append(rec)
    print(f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}")


def _scan(code, channel_id=0):
    r = requests.post(f"{API}/api/v1/packaging-flows/scan",
                      json={"code": code, "channel_id": channel_id}, timeout=10)
    return r.json() if r.status_code == 200 else {"_http": r.status_code, "_body": r.text}


def _purge():
    r = requests.get(f"{API}/api/v1/packaging-flows", timeout=10)
    items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
    for c in items:
        if str(c.get("name", "")).startswith("__uat_"):
            if c.get("enabled"):
                requests.put(f"{API}/api/v1/packaging-flows/{c['id']}",
                             json={"enabled": False}, timeout=10)
            requests.delete(f"{API}/api/v1/packaging-flows/{c['id']}", timeout=10)


def phase_a():
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(VIDEO, exist_ok=True)
    _purge()
    name = f"__uat_sliders_{uuid.uuid4().hex[:6]}"

    # A1 建启用 sliders 配置 (每箱 96 滑块, offline → 扫码即开第 1 箱)
    r = requests.post(f"{API}/api/v1/packaging-flows", json={
        "name": name, "enabled": True, "channel_id": 0,
        "count_unit": "sliders", "items_per_box_source": "config",
        "items_per_box_fixed": 96, "on_mes_fail": "offline",
    }, timeout=10)
    ok_create = r.status_code == 201
    cid = r.json()["id"] if ok_create else None
    step("A1 建启用 sliders 包装配置", ok_create, f"id={cid} http={r.status_code}")
    if not ok_create:
        return None

    # A2 扫工单 → sliders 口径开工单 + 立即开第 1 箱 (滑块 0/96)
    s2 = _scan("ORD-SLID-001")
    st2 = s2.get("state") or {}
    step("A2 扫工单 → sliders 开工单 + 开第 1 箱",
         s2.get("handled") and st2.get("count_unit") == "sliders"
         and st2.get("current_box_index") == 1,
         f"count_unit={st2.get('count_unit')} box_idx={st2.get('current_box_index')} "
         f"slid={st2.get('current_box_sliders')}/{st2.get('items_per_box')}")
    return cid


def phase_b(cid):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 1000},
            ignore_https_errors=True,
        )
        page = ctx.new_page()

        # B1 Monitor 应显示包装卡, 且按 sliders 口径显示「滑块」标签 (非托盘)
        page.goto(f"{FRONTEND}/#/monitor")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3.5)
        page.screenshot(path=f"{SHOTS}/B1_sliders_card.png", full_page=False)
        body1 = page.evaluate("document.body.innerText")
        has_card = page.locator(".packaging-card").count() > 0
        shows_slider_unit = "滑块" in body1 and "包装箱结算" in body1
        step("B1 启用时 Monitor 卡按滑块口径渲染",
             has_card and shows_slider_unit,
             f"card={page.locator('.packaging-card').count()} 滑块={'滑块' in body1}")

        # B2 禁用 → 刷新 → 卡干净消失 (零差异)
        requests.put(f"{API}/api/v1/packaging-flows/{cid}",
                     json={"enabled": False}, timeout=10)
        page.reload()
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3.5)
        page.screenshot(path=f"{SHOTS}/B2_no_card.png", full_page=False)
        no_card = page.locator(".packaging-card").count() == 0
        step("B2 禁用后刷新卡消失 (零差异)",
             no_card, f"card={page.locator('.packaging-card').count()}")

        ctx.close()
        browser.close()


def report():
    _purge()
    failed = [s for s in steps_log if not s["ok"]]
    summary = {"total": len(steps_log), "failed": len(failed), "steps": steps_log}
    with open(RUNLOG, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write(f"\n\nfailed: {len(failed)}\n")
    print("\n" + "=" * 50)
    print(f"UAT sliders 口径: {len(steps_log) - len(failed)}/{len(steps_log)} OK, failed: {len(failed)}")
    print(f"视频: {VIDEO}/  截图: {SHOTS}/  日志: {RUNLOG}")
    print("=" * 50)


if __name__ == "__main__":
    cid = phase_a()
    if cid:
        phase_b(cid)
    report()
