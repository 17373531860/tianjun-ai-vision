#!/usr/bin/env python
"""可见浏览器 UAT: sensor-clean v1.3.0 工位1 同帧双类别计数门槛 (detect9 对齐)。

验证目标:
  Phase A (后端功能, synthetic 真管线):
    - 未配伴随标签: 仅锚标签移动即计数 (v1.2.0 零差异)
    - 配了伴随标签: 仅锚标签 → 0 计数; 锚+伴随同帧 → 正常计数
  Phase B (可见浏览器):
    - 监控页插件看板: 门槛拦截时总产量不动, 双类别驱动时总产量上涨 (人眼可见)
    - 项目页插件配置 Tab: 「工位1 计数许可标签」输入 → 保存配置 → 接口读回落库值

前置(本脚本不自起服务):
  - 隔离后端 8002 (RUNTIME_MODE=test, ENABLE_DEV_MOCKS=1, TIANJUN_DATA_DIR=/tmp/sc_gate_data,
    sensor-clean 1.3.0 已安装激活)
  - 隔离前端 6002 (VITE_API_BASE_URL 钉到 8002)
证据: /tmp/uat_gate_shots/*.png + /tmp/uat_gate_video/*.webm + 运行输出重定向 run.log
"""
from __future__ import annotations
import os
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8002"
FRONT = "http://localhost:6002"
SW = f"{API}/api/v1/plugins/sensor-clean/swab"
SHOTS = "/tmp/uat_gate_shots"
VIDEO = "/tmp/uat_gate_video"
ANCHOR = "查看产品有无脏污"
COMPANION = "清洁产品"
os.makedirs(SHOTS, exist_ok=True)
os.makedirs(VIDEO, exist_ok=True)
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


def drive(n, labels):
    """驱动 n 件「移到左 → 移到右 → 离场」轨迹; labels 里的每个标签同帧同出。"""
    def dets(x):
        return [{"label": lb, "confidence": 0.95, "bbox": [x, 0.5, 0.05, 0.05]}
                for lb in labels]
    seg, f = [], 0
    for _ in range(n):
        seg.append({"from": f, "to": f + 9, "detections": dets(0.40)})
        seg.append({"from": f + 10, "to": f + 19, "detections": dets(0.62)})
        seg.append({"from": f + 20, "to": f + 50, "detections": []})
        f += 51
    seg.append({"from": f, "to": f + 3000, "detections": []})
    requests.post(f"{API}/api/v1/test/synthetic/start",
                  json={"scenario_json": {"name": "sc_gate_uat", "fps": 30, "timeline": seg},
                        "channel": 0, "with_project": False}, timeout=15)
    requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                  json={"conf": 0.25, "iou": 0.45}, timeout=15)


def stop_all():
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)


def drive_delta(n, labels, wait):
    prev = state().get("total_products", 0)
    drive(n, labels)
    time.sleep(wait)
    delta = state().get("total_products", 0) - prev
    stop_all()
    time.sleep(0.6)
    return delta


# ============================================================
# Phase A: 后端功能 (synthetic 真管线过插件帧钩子)
# ============================================================
print("\n===== Phase A: 后端门槛行为 =====", flush=True)
stop_all(); time.sleep(0.5)
original_cfg = requests.get(f"{SW}/config", timeout=10).json()
patch_cfg(count_anchor_label=ANCHOR, count_require_label="",
          max_uses_per_swab=99, normal_count_event_id=1)
requests.post(f"{SW}/reset-counts", timeout=10); time.sleep(0.5)

d1 = drive_delta(4, [ANCHOR], 11)
step("A1 未配伴随标签: 仅锚标签可计数 (v1.2.0 零差异)", d1 >= 1, f"计数 delta={d1}")

patch_cfg(count_require_label=COMPANION)
requests.post(f"{SW}/reset-counts", timeout=10); time.sleep(0.5)
d2 = drive_delta(4, [ANCHOR], 11)
step("A2 配伴随标签后: 仅锚标签被门槛拦截 = 0", d2 == 0, f"计数 delta={d2}")

d3 = drive_delta(4, [ANCHOR, COMPANION], 11)
step("A3 配伴随标签后: 锚+伴随同帧正常计数", d3 >= 1, f"计数 delta={d3}")

# ============================================================
# Phase B: 可见浏览器 (监控看板 + 配置 Tab)
# ============================================================
print("\n===== Phase B: 可见浏览器 =====", flush=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=80,
                                args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                              record_video_dir=VIDEO,
                              record_video_size={"width": 1600, "height": 1000})
    page = ctx.new_page()
    page.set_default_timeout(20000)

    def board_total():
        """监控页插件看板「总产量」数字 (统计卡标题带图标前缀: 「📦 总产量」)。"""
        return page.evaluate("""() => {
            const divs = [...document.querySelectorAll('div')];
            for (const d of divs) {
                const t = d.textContent.trim();
                if (t.endsWith('总产量') && t.length <= 12 && d.parentElement) {
                    const m = d.parentElement.textContent.match(/(\\d+)/);
                    if (m) return parseInt(m[1]);
                }
            }
            return -1;
        }""")

    # ---- B1/B2: 监控页看板随门槛联动 ----
    page.goto(f"{FRONT}/#/monitor", wait_until="commit")
    print(">>> goto /#/monitor, waiting plugin bootstrap...", flush=True)
    time.sleep(7.0)
    body = page.evaluate("document.body.innerText")
    step("B1 监控页渲染插件双工位看板", "总产量" in body and "棉签" in body,
         f"片段={body[:60]!r}")
    safe_shot(page, f"{SHOTS}/G1_monitor_board.png")

    requests.post(f"{SW}/reset-counts", timeout=10); time.sleep(1.5)
    t0 = board_total()
    drive(3, [ANCHOR])          # 门槛开着, 仅锚标签 → 应被拦
    time.sleep(9.0)
    t1 = board_total()
    stop_all(); time.sleep(0.6)
    safe_shot(page, f"{SHOTS}/G2_gate_blocked_total_unchanged.png")
    step("B2 门槛拦截: 仅锚标签驱动, 看板总产量不动", t0 == 0 and t1 == 0,
         f"驱动前={t0} 驱动后={t1}")

    drive(3, [ANCHOR, COMPANION])  # 双类别同帧 → 应计数
    time.sleep(9.0)
    t2 = board_total()
    stop_all(); time.sleep(0.6)
    safe_shot(page, f"{SHOTS}/G3_dual_label_counted.png")
    step("B3 双类别同帧: 看板总产量上涨", t2 >= 1, f"总产量={t2}")

    # ---- B4/B5: 项目页配置 Tab UI → 落库 ----
    page.goto(f"{FRONT}/#/project", wait_until="commit")
    page.reload(wait_until="domcontentloaded")
    time.sleep(6.0)
    # 配置 Tab 区只在选中项目后渲染, 先点左侧列表第一张项目卡片
    # (卡片是 div.p-4 且带 DETECTION 徽标, 裸 div.p-4 会命中外层容器点不中)
    page.locator("div.p-4:has-text('DETECTION')").first.click(timeout=10000)
    time.sleep(1.5)
    tab = page.locator(".el-tabs__item:has-text('传感器清洁配置')")
    for _ in range(20):
        if tab.count() > 0:
            break
        time.sleep(0.5)
    tab_ok = tab.count() > 0
    if tab_ok:
        tab.first.click()
        time.sleep(1.5)
    body = page.evaluate("document.body.innerText")
    step("B4 项目页插件 Tab 渲染伴随标签字段", tab_ok and "工位1 计数许可标签" in body, "")
    safe_shot(page, f"{SHOTS}/G4_config_tab_field.png")

    saved_ok = False
    if tab_ok:
        inp = page.locator("div:has(> label:text-is('工位1 计数许可标签')) > input").first
        inp.fill("UAT验证标签")
        time.sleep(0.5)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.5)
        cfg = requests.get(f"{SW}/config", timeout=10).json()
        saved_ok = cfg.get("count_require_label") == "UAT验证标签"
    step("B5 UI 保存伴随标签 → 接口读回落库值", saved_ok, "")
    safe_shot(page, f"{SHOTS}/G5_config_saved.png")

    ctx.close(); browser.close()

# 恢复原配置, 不留 UAT 痕迹
requests.post(f"{SW}/config", json=original_cfg, timeout=10)
requests.post(f"{SW}/reset-counts", timeout=10)

ok = sum(1 for s in steps if s["ok"])
print(f"\n===== 汇总: {ok}/{len(steps)} 通过 =====", flush=True)
