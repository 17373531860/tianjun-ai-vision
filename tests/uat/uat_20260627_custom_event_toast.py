#!/usr/bin/env python
"""可见浏览器 UAT: 传感器清洁插件 列级提示框「按事件身份分流」。

本次改动验证目标:
  - 内置 合格(1)/不良(2): 插件仍屏蔽列级提示框, 交给自绘红横幅(回归, 不应弹 toast);
  - 自定义事件(id>=3): 插件放行, 标准提示框正常弹出(新能力)。

关键踩坑(已在脚本内规避):
  - 后端事件日志 event_id 为字符串("3"), 比较需 str() 归一; 插件侧用 Number() 归一。
  - 列级提示框是瞬时元素(自动消失), 用「窗口内轮询 DOM .shadow-2xl 文本」捕捉, 不用 get_by_text 死等。

前置 (本脚本不自起服务):
  - 隔离后端 8002 (RUNTIME_MODE=test, TIANJUN_DATA_DIR=/tmp/sc_uat_data, 插件已激活)
  - UAT 前端 6002 -> 8002 (frontend/vite.uat.config.js, VITE_API_BASE_URL 钉 8002)
  - 项目14 已加自定义事件 id=3「清洁警告」(show_notification=true,
    toast_id=custom_1779286068973 即已有自定义提示「未压墨」) 并激活
  - 插件 max_uses_per_swab=99, 关锁
"""
from __future__ import annotations
import json, time, os
import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8002"
FRONT = "http://localhost:6002"
SW = f"{API}/api/v1/plugins/sensor-clean/swab"
CUSTOM_TOAST_TEXT = "未压墨"   # 自定义事件3 复用的已有自定义提示框文案
SHOTS = "/tmp/uat_shots"
os.makedirs(SHOTS, exist_ok=True)
steps = []


def step(label, ok, detail=""):
    steps.append({"idx": len(steps) + 1, "label": label, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else '!!'}] {len(steps):02d}. {label}  {detail}", flush=True)


def safe_shot(page, path):
    try:
        page.screenshot(path=path, animations="disabled", caret="initial", timeout=8000)
        print(f">>> shot ok: {path}", flush=True)
    except Exception as e:
        print(f">>> shot FAILED(non-fatal): {type(e).__name__}", flush=True)


def set_normal_event(ev_id):
    cfg = requests.get(f"{SW}/config", timeout=10).json()
    cfg["normal_count_event_id"] = ev_id
    requests.post(f"{SW}/config", json=cfg, timeout=10)


def reset_plugin():
    requests.post(f"{SW}/reset-counts", timeout=10)
    requests.post(f"{SW}/reset", timeout=10)


def drive(n=6):
    """虚拟剧本: 锚动作 出现->移动->消失 重复 n 次, 每次记 1 件 -> 触发 normal_count 事件。"""
    seg = []
    f = 0
    for _ in range(n):
        seg.append({"from": f, "to": f + 9, "detections": [
            {"label": "查看产品有无脏污", "confidence": 0.95, "bbox": [0.40, 0.5, 0.05, 0.05]}]})
        seg.append({"from": f + 10, "to": f + 19, "detections": [
            {"label": "查看产品有无脏污", "confidence": 0.95, "bbox": [0.62, 0.5, 0.05, 0.05]}]})
        seg.append({"from": f + 20, "to": f + 50, "detections": []})
        f += 51
    seg.append({"from": f, "to": f + 2000, "detections": []})
    requests.post(f"{API}/api/v1/test/synthetic/start",
                  json={"scenario_json": {"name": "sc_toast_uat", "fps": 30, "timeline": seg},
                        "channel": 0, "with_project": False}, timeout=15)
    requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                  json={"conf": 0.25, "iou": 0.45}, timeout=15)


def stop_all():
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)


def poll_toasts(page, seconds):
    """窗口内轮询 DOM, 汇总期间出现过的所有列级提示框文本(去重)。"""
    seen = set()
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            texts = page.evaluate(
                "() => [...document.querySelectorAll('.shadow-2xl')].map(n => (n.innerText||'').replace(/\\n/g,' ').trim())")
            for t in texts:
                if t:
                    seen.add(t)
        except Exception:
            pass
        time.sleep(0.4)
    return seen


# ============================================================
# Phase A: API 契约 — 自定义事件3 真触发(event_id 字符串归一)
# ============================================================
print("\n===== Phase A: API 契约(自定义事件3) =====", flush=True)
stop_all(); time.sleep(0.5)
set_normal_event(3); reset_plugin(); time.sleep(0.3)
drive(4)
ev3 = []
t0 = time.time()
while time.time() - t0 < 20:
    res = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=10).json()
    for e in (res.get("recent_events") or []):
        if str(e.get("event_id")) == "3" and e.get("show_notification"):
            ev3.append(e.get("seq"))
    if ev3:
        break
    time.sleep(0.5)
st = requests.get(f"{SW}/state", timeout=10).json()
step("A1 计件驱动后总产量上升(自定义事件链路通)", st.get("total_products", 0) >= 1, f"total={st.get('total_products')}")
step("A2 主程序事件日志出现自定义事件3且标记要弹提示框", len(ev3) >= 1, f"event3 seqs={sorted(set(ev3))[:5]}")
stop_all(); time.sleep(0.5)

# ============================================================
# Phase B: 可见浏览器 — 自定义事件弹提示框 + 内置OK/NG仍屏蔽
# ============================================================
print("\n===== Phase B: 可见浏览器 =====", flush=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=80,
                                args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, ignore_https_errors=True)
    page = ctx.new_page()
    page.set_default_timeout(15000)
    console_errs = []
    page.on("console", lambda m: console_errs.append((m.text, (m.location or {}).get("url", ""))) if m.type == "error" else None)

    page.goto(f"{FRONT}/#/monitor", wait_until="commit")
    print(">>> goto committed, waiting plugin bootstrap...", flush=True)
    time.sleep(6.0)
    body = page.evaluate("document.body.innerText")
    step("B1 插件双工位看板已渲染", ("棉签" in body) or ("运行正常" in body), f"片段={body[:40]!r}")

    # --- 新能力: 自定义事件3 → 标准提示框应弹出(文案=未压墨) ---
    set_normal_event(3); reset_plugin(); time.sleep(0.3)
    drive(6)
    time.sleep(2.0)
    toasts_custom = poll_toasts(page, 12)
    custom_shown = any(CUSTOM_TOAST_TEXT in t for t in toasts_custom)
    safe_shot(page, f"{SHOTS}/C1_custom_event_toast.png")
    step("B2 自定义事件 → 列级提示框弹出(新能力)", custom_shown,
         f"期间提示框文本={sorted(toasts_custom)}")
    # 内置OK/NG(合格/不合格)在自定义场景里也应被屏蔽
    ng_leaked = any(("不合格" in t or "不良" in t) for t in toasts_custom)
    step("B2b 同期内置OK/NG提示框被屏蔽(未泄漏)", not ng_leaked,
         "无内置NG提示泄漏" if not ng_leaked else f"异常泄漏={sorted(toasts_custom)}")
    stop_all(); time.sleep(1.0)

    # --- 回归: 插件计件连内置NG(2) → 不应弹列级提示框 ---
    set_normal_event(2); reset_plugin(); time.sleep(0.3)
    drive(6)
    time.sleep(2.0)
    toasts_ng = poll_toasts(page, 10)
    ng_suppressed = not any(("不合格" in t or "不良" in t or CUSTOM_TOAST_TEXT in t) for t in toasts_ng)
    safe_shot(page, f"{SHOTS}/C2_builtin_ng_suppressed.png")
    step("B3 回归: 内置NG计件不弹列级提示框(交给横幅/看板)", ng_suppressed,
         f"期间提示框文本={sorted(toasts_ng)}")
    stop_all()

    INFRA = ("video_feed", "/snapshot", "ERR_CONNECTION_REFUSED", "favicon", "net::ERR")
    real_errs = [(t, u) for (t, u) in console_errs if not any(k in (t + " " + u) for k in INFRA)]
    step("B4 浏览器控制台无前端逻辑报错(视频流噪声已剔除)", len(real_errs) == 0,
         f"真报错={real_errs[:3]}" if real_errs else f"仅基础设施噪声{len(console_errs)}条")

    ctx.close(); browser.close()

stop_all()
set_normal_event(1)  # 还原插件常态(正常计件=合格)
ok = sum(1 for s in steps if s["ok"])
print(f"\n===== 汇总: {ok}/{len(steps)} 通过 =====", flush=True)
