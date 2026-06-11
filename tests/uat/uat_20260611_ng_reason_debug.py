# -*- coding: utf-8 -*-
"""UAT 2026-06-11: 全局调试系统「一直 NG 要能知道为什么」验收.

客户现场叙事:
  操作员在顺序模式下作业, 周期结束一直弹 NG;
  他打开开发者模式 → 系统设置 → 调试设置 → 打开「结算状态机」开关;
  再做一个周期, 日志面板必须出现人话原因:「顺序模式结算 NG :: 缺少=['step_b']」;
  他据此知道漏做了哪一步, 而不是盲猜.

跑法 (人工触发, 不进 CI):
  1) RUNTIME_MODE=test TIANJUN_DATA_DIR=/tmp/uat_ng_data \
       uvicorn backend.main:app --port 8011
  2) cd frontend && 写 .env.uat.local 指 8011 && npm run dev -- --mode uat --port 6011
  3) python tests/uat/uat_20260611_ng_reason_debug.py

产物: /tmp/uat_video/*.webm + /tmp/uat_shots/*.png + /tmp/uat_run.log
"""
import json
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8011"
FRONTEND = "http://127.0.0.1:6011"
SHOTS = "/tmp/uat_shots"
VIDEO = "/tmp/uat_video"
RUNLOG = "/tmp/uat_run.log"

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": str(detail)[:300]}
    steps_log.append(rec)
    print(f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}")


def run_ng_scenario():
    """启动缺 step_b 的 NG 剧本并等结算 (synthetic 全链路)"""
    r = requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario": "ng_missing_step.json", "channel": 0,
        "with_project": True,
        "project_steps": ["step_a", "step_b", "step_c"],
        "logic_mode": "sequential"}, timeout=10)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=30)
    assert r.status_code == 200, r.text[:200]


def stop_scenario():
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=15)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)


def wait_ng_log(timeout_sec=20):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        logs = requests.get(f"{API}/api/v1/debug/logs?limit=2000", timeout=5).json()["logs"]
        for e in logs:
            if e["category"] == "backend.settlement" and "NG" in e["action"] and "step_b" in e.get("detail", ""):
                return e
        time.sleep(0.5)
    return None


# ════════ Phase A: API 契约 ════════
def phase_a():
    r = requests.put(f"{API}/api/v1/debug/flags",
                     json={"flags": {"backend.settlement": True, "backend.per_item": True}}, timeout=5)
    step("A1 开结算/逐件调试开关", r.status_code == 200 and r.json()["flags"]["backend.settlement"])
    requests.post(f"{API}/api/v1/debug/logs/clear", timeout=5)

    run_ng_scenario()
    entry = wait_ng_log()
    stop_scenario()
    step("A2 NG 原因日志出现(全链路)", entry is not None,
         f"{entry['action']} :: {entry['detail'][:120]}" if entry else "20s 内未出现")

    # 零差异对照: 全关 + 清空 → 再跑 → 不允许有任何结算日志
    requests.put(f"{API}/api/v1/debug/flags",
                 json={"flags": {"backend.settlement": False, "backend.per_item": False}}, timeout=5)
    requests.post(f"{API}/api/v1/debug/logs/clear", timeout=5)
    run_ng_scenario()
    time.sleep(6)
    stop_scenario()
    logs = requests.get(f"{API}/api/v1/debug/logs?limit=2000", timeout=5).json()["logs"]
    leftover = [e for e in logs if e["category"].startswith("backend.")]
    step("A3 开关全关零差异", leftover == [], f"残留 {len(leftover)} 条" if leftover else "0 条")


def deactivate_takeover_plugins():
    """showcase 等 Tier2 插件会用 settings.layout.body 接管设置页, 原生 Tab 不渲染.
    UAT 前 best-effort 停用 active 插件 (上次 UAT 已踩过此坑)."""
    try:
        r = requests.get(f"{API}/api/v1/plugins", timeout=5)
        items = (r.json() or {}).get("items") if r.status_code == 200 else []
        for pl in items or []:
            code = pl.get("customer_code") or pl.get("code") or pl.get("id")
            if pl.get("status") == "active" and code:
                requests.post(f"{API}/api/v1/plugins/{code}/deactivate", timeout=10)
                print(f"  (已停用接管型插件: {code})")
    except Exception as e:
        print(f"  (插件停用跳过: {e})")


# ════════ Phase B: 可见浏览器人眼复核 ════════
def phase_b():
    deactivate_takeover_plugins()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 1000},
        )
        page = ctx.new_page()
        page.add_init_script("localStorage.setItem('developer_mode', 'true')")

        try:
            # B1 设置页出现「调试设置」Tab (开发者模式门控)
            # 注: 不等 networkidle — 前端有常驻轮询永远不会 idle
            page.goto(f"{FRONTEND}/#/settings", wait_until="domcontentloaded")
            time.sleep(3)
            tab = page.get_by_role("tab", name="调试设置")
            step("B1 调试设置 Tab 可见(开发者模式)", tab.is_visible())
            tab.click()
            time.sleep(1.5)
            page.screenshot(path=f"{SHOTS}/B1_debug_panel.png", full_page=True)

            # B2 打开「结算状态机」开关 (后端区默认折叠, 先展开)
            page.get_by_text("后端调试开关", exact=False).first.click()
            time.sleep(0.8)
            row = page.locator("div.flex.items-center.justify-between",
                               has=page.get_by_text("结算状态机")).first
            row.scroll_into_view_if_needed()
            row.locator(".el-switch").click()
            time.sleep(1.0)
            flags = requests.get(f"{API}/api/v1/debug/flags", timeout=5).json()["flags"]
            step("B2 UI 开关落到后端", flags.get("backend.settlement") is True, str({k: v for k, v in flags.items() if v}))
            page.screenshot(path=f"{SHOTS}/B2_switch_on.png", full_page=True)

            # B3 后台跑 NG 剧本, 日志查看器人眼看到 NG 原因
            requests.post(f"{API}/api/v1/debug/logs/clear", timeout=5)
            run_ng_scenario()
            entry = wait_ng_log()
            stop_scenario()
            time.sleep(2.5)  # 给前端日志轮询两个周期
            page.screenshot(path=f"{SHOTS}/B3_ng_log_visible.png", full_page=True)
            body_text = page.evaluate("document.body.innerText")
            ui_sees_reason = ("step_b" in body_text and "NG" in body_text)
            step("B3 日志面板显示 NG 原因(人眼可核)", entry is not None and ui_sees_reason,
                 f"后端={bool(entry)} 前端可见={ui_sees_reason}")

            # B4 关键词过滤 'NG'
            kw = page.locator("input[placeholder*='关键词'], input[placeholder*='过滤'], input[placeholder*='搜索']").first
            if kw.count():
                kw.fill("缺少")
                time.sleep(1.0)
                page.screenshot(path=f"{SHOTS}/B4_filter.png", full_page=True)
                body2 = page.evaluate("document.body.innerText")
                step("B4 关键词过滤后仍能看到原因", "step_b" in body2)
            else:
                step("B4 关键词过滤后仍能看到原因", False, "未找到过滤输入框")
        finally:
            ctx.close()
            browser.close()


# ════════ 清理 + 总结 ════════
def cleanup_and_report():
    requests.put(f"{API}/api/v1/debug/flags",
                 json={"flags": {k: False for k in
                                 requests.get(f"{API}/api/v1/debug/flags", timeout=5).json()["flags"]}},
                 timeout=5)
    requests.post(f"{API}/api/v1/debug/logs/clear", timeout=5)
    failed = [s for s in steps_log if not s["ok"]]
    with open(RUNLOG, "w", encoding="utf-8") as f:
        json.dump({"steps": steps_log, "failed": len(failed)}, f, ensure_ascii=False, indent=2)
    print(f"\n==== UAT 总结: {len(steps_log) - len(failed)}/{len(steps_log)} OK, failed: {len(failed)} ====")
    return len(failed)


if __name__ == "__main__":
    import os
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(VIDEO, exist_ok=True)
    phase_a()
    phase_b()
    raise SystemExit(cleanup_and_report())
