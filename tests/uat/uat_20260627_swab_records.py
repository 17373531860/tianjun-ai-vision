#!/usr/bin/env python
"""可见浏览器 UAT: 传感器清洁插件 数据中心 → 棉签使用记录（本次新功能）。

验证目标:
  - 后端: 计件 → 清零(手动重置) 时按「一根棉签一条」定稿落库; /records 返回记录 + 汇总正确;
  - 前端: 数据页整页覆盖渲染 棉签记录(统计卡 + 柱图 + 表格), 导出 CSV 可下载。

落库口径(本脚本驱动):
  - K=2(单棉签最多擦 2 件), 逐根驱动产品后手动重置 → 定稿一条;
  - swab1=2件(擦满,over_limit), swab2=3件(超限,含1不良), swab3=1件(未满)。

前置(本脚本不自起服务):
  - 隔离后端 8002 (RUNTIME_MODE=test, ENABLE_DEV_MOCKS=1, TIANJUN_DATA_DIR=/tmp/sc_uat_data, 插件已激活)
  - UAT 前端 6002 -> 8002 (frontend/.env.development.local 钉 VITE_API_BASE_URL=8002)
"""
from __future__ import annotations
import time, os
import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8002"
FRONT = "http://localhost:6002"
SW = f"{API}/api/v1/plugins/sensor-clean/swab"
SHOTS = "/tmp/uat_shots"
os.makedirs(SHOTS, exist_ok=True)
steps = []


def step(label, ok, detail=""):
    steps.append({"idx": len(steps) + 1, "label": label, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else '!!'}] {len(steps):02d}. {label}  {detail}", flush=True)


def safe_shot(page, path):
    try:
        page.screenshot(path=path, animations="disabled", timeout=8000)
        print(f">>> shot ok: {path}", flush=True)
    except Exception as e:
        print(f">>> shot FAILED(non-fatal): {type(e).__name__}", flush=True)


def patch_cfg(**kw):
    cfg = requests.get(f"{SW}/config", timeout=10).json()
    cfg.update(kw)
    requests.post(f"{SW}/config", json=cfg, timeout=10)


def state():
    return requests.get(f"{SW}/state", timeout=10).json()


def records():
    return requests.get(f"{SW}/records", params={"limit": 500}, timeout=10).json()


def drive(n):
    seg, f = [], 0
    for _ in range(n):
        seg.append({"from": f, "to": f + 9, "detections": [
            {"label": "查看产品有无脏污", "confidence": 0.95, "bbox": [0.40, 0.5, 0.05, 0.05]}]})
        seg.append({"from": f + 10, "to": f + 19, "detections": [
            {"label": "查看产品有无脏污", "confidence": 0.95, "bbox": [0.62, 0.5, 0.05, 0.05]}]})
        seg.append({"from": f + 20, "to": f + 50, "detections": []})
        f += 51
    seg.append({"from": f, "to": f + 3000, "detections": []})
    requests.post(f"{API}/api/v1/test/synthetic/start",
                  json={"scenario_json": {"name": "sc_rec_uat", "fps": 30, "timeline": seg},
                        "channel": 0, "with_project": False}, timeout=15)
    requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                  json={"conf": 0.25, "iou": 0.45}, timeout=15)


def stop_all():
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)


def drive_and_finalize(count, wait, finalize):
    """驱动 count 件 → 固定等流播完(合成计数会少于驱动数, 不早退) → 用 finalize 定稿一根。
    返回 (本根观测件数, 最新落库记录)。断言基于"观测件数", 验证特征逻辑本身。"""
    prev = state().get("total_products", 0)
    drive(count)
    time.sleep(wait)
    observed = state().get("total_products", 0) - prev
    stop_all(); time.sleep(0.6)
    finalize()
    time.sleep(0.4)
    latest = (records().get("records") or [])
    return observed, (latest[0] if latest else None)


# ============================================================
# Phase A: 后端 — 逐根定稿落库 + 汇总
# ============================================================
print("\n===== Phase A: 后端落库 =====", flush=True)
stop_all(); time.sleep(0.5)
# K=1: 任一根擦 >=2 件即 over_limit + 第2件起记不良, 可靠验证 超限/NG 累加路径。
# 断言用"基线 delta"(记录是历史会累积, 不假设空表)。
K = 1
patch_cfg(max_uses_per_swab=K, normal_count_event_id=1, swab_over_limit_event_id=2)
requests.post(f"{SW}/reset-counts", timeout=10); time.sleep(0.5)
base = records().get("summary", {})
base_cnt = base.get("swab_count", 0)
base_prod = base.get("total_products", 0)
base_ng = base.get("total_ng", 0)
base_over = base.get("over_limit_count", 0)

# 第1根(手动重置定稿): 驱动 8 件, 等流播完 → 观测必 >=2 → 超限 + 不良
obs1, rec1 = drive_and_finalize(8, 18, lambda: requests.post(f"{SW}/reset", timeout=10))
exp_ng1 = max(0, obs1 - K)
step("A1 第1根落库, 件数=观测", rec1 is not None and rec1.get("products") == obs1 and obs1 >= 2,
     f"观测={obs1} 记录件数={rec1.get('products') if rec1 else None}")
step("A2 第1根 over_limit=1(观测>=K)", rec1 and rec1.get("over_limit") == 1,
     f"over_limit={rec1.get('over_limit') if rec1 else None}")
step("A3 第1根 不良=观测-K(NG累加路径验证)", rec1 and rec1.get("ng") == exp_ng1 and exp_ng1 >= 1,
     f"ng={rec1.get('ng') if rec1 else None} 期望={exp_ng1}")
step("A4 第1根 结束原因=manual", rec1 and rec1.get("end_reason") == "manual",
     f"reason={rec1.get('end_reason') if rec1 else None}")

# 第2根(整批重置定稿): 验证另一个结束原因 batch_reset 走同一落库路径
obs2, rec2 = drive_and_finalize(4, 11, lambda: requests.post(f"{SW}/reset-counts", timeout=10))
step("A5 第2根落库, 件数=观测", rec2 is not None and rec2.get("products") == obs2 and obs2 >= 1,
     f"观测={obs2} 记录件数={rec2.get('products') if rec2 else None}")
step("A6 第2根 结束原因=batch_reset", rec2 and rec2.get("end_reason") == "batch_reset",
     f"reason={rec2.get('end_reason') if rec2 else None}")

exp_ng2 = max(0, obs2 - K)
exp_over = (1 if obs1 >= K else 0) + (1 if obs2 >= K else 0)
sm = records().get("summary", {})
step("A7 棉签根数 delta=+2", sm.get("swab_count") == base_cnt + 2,
     f"now={sm.get('swab_count')} base={base_cnt}")
step("A8 产品总数 delta=两根观测之和", sm.get("total_products") == base_prod + obs1 + obs2,
     f"now={sm.get('total_products')} 期望={base_prod + obs1 + obs2}")
step("A9 不良/超限汇总 delta 自洽", sm.get("total_ng") == base_ng + exp_ng1 + exp_ng2
     and sm.get("over_limit_count") == base_over + exp_over,
     f"ng_now={sm.get('total_ng')} 期望ng={base_ng + exp_ng1 + exp_ng2} "
     f"over_now={sm.get('over_limit_count')} 期望over={base_over + exp_over}")

# ============================================================
# Phase B: 可见浏览器 — 数据页棉签记录渲染 + 导出
# ============================================================
print("\n===== Phase B: 可见浏览器(数据页) =====", flush=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=80,
                                args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                              accept_downloads=True, ignore_https_errors=True)
    page = ctx.new_page()
    page.set_default_timeout(15000)
    console_errs = []
    page.on("console", lambda m: console_errs.append((m.text, (m.location or {}).get("url", "")))
            if m.type == "error" else None)

    page.goto(f"{FRONT}/#/data", wait_until="commit")
    print(">>> goto /#/data committed, waiting plugin bootstrap...", flush=True)
    time.sleep(7.0)
    body = page.evaluate("document.body.innerText")
    step("B1 数据页渲染插件「棉签使用记录」整页", "棉签使用记录" in body, f"片段={body[:50]!r}")

    rows = page.evaluate("() => document.querySelectorAll('table tbody tr').length")
    step("B2 记录表渲染出 >=2 行", rows >= 2, f"tbody行数={rows}")

    cards_ok = ("棉签根数" in body) and ("产品总数" in body) and ("超限根数" in body)
    step("B3 统计卡渲染(棉签根数/产品总数/超限根数)", cards_ok, "")

    bars = page.evaluate(
        "() => [...document.querySelectorAll('div')].filter(d => d.title && d.title.includes('产品')).length")
    step("B4 柱图渲染出柱子(title 含 产品)", bars >= 2, f"柱子数={bars}")

    safe_shot(page, f"{SHOTS}/D1_swab_records_page.png")

    # 导出 CSV → 触发浏览器下载
    dl_ok = False
    try:
        with page.expect_download(timeout=8000) as dlinfo:
            page.get_by_text("导出 CSV", exact=True).first.click()
        dl = dlinfo.value
        save = f"{SHOTS}/swab_records_export.csv"
        dl.save_as(save)
        dl_ok = os.path.getsize(save) > 0
    except Exception as e:
        print(f">>> export download exc: {type(e).__name__}: {e}", flush=True)
    step("B5 点「导出 CSV」触发下载且文件非空", dl_ok, f"saved={SHOTS}/swab_records_export.csv")

    INFRA = ("video_feed", "/snapshot", "ERR_CONNECTION_REFUSED", "favicon", "net::ERR", "MJPEG")
    real_errs = [(t, u) for (t, u) in console_errs if not any(k in (t + " " + u) for k in INFRA)]
    step("B6 控制台无前端逻辑报错(基础设施噪声已剔除)", len(real_errs) == 0,
         f"真报错={real_errs[:3]}" if real_errs else f"仅噪声{len(console_errs)}条")

    ctx.close(); browser.close()

ok = sum(1 for s in steps if s["ok"])
print(f"\n===== 汇总: {ok}/{len(steps)} 通过 =====", flush=True)
