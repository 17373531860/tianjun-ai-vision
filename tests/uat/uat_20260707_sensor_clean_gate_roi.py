#!/usr/bin/env python
"""可见浏览器 UAT: sensor-clean v1.4.0 双类别计数许可 (detect9(1)) + 按标签 ROI。

客户反馈背景: v1.3.0 同帧门槛与现场 detect9(1) 语义不符 —— 现场两类交替出现
(许可标签出现过即解锁, 不必与锚框同帧), 且要求标签可框定 ROI 区域。

验证目标:
  Phase A (后端功能, synthetic 真管线):
    - A1 只动锚标签(许可标签从未出现) → 0 计数 (客户投诉场景)
    - A2 两类交替出现(先许可后锚移动, 不同帧) → 正常计数 (v1.3.0 同帧门槛做不到)
    - A3 许可被消费: 计一件后锚再动不计, 再见许可才计第二件
    - A4 锚标签 ROI: 区域外移动 0 计数, 区域内正常计数
  Phase B (可见浏览器):
    - B1 监控页插件看板渲染
    - B2 交替语义驱动看板总产量上涨 (人眼可见)
    - B3 项目页配置 Tab: ROI 分组渲染 + 画布点 3 点保存 → 接口读回归一化多边形
    - B4 ROI 拦截: 区域外驱动看板总产量不动

前置(本脚本不自起服务):
  - 隔离后端 8002 (RUNTIME_MODE=test, ENABLE_DEV_MOCKS=1, TIANJUN_DATA_DIR=/tmp/sc_gate_data,
    sensor-clean 1.4.0 已安装激活)
  - 隔离前端 6002 (VITE_API_BASE_URL 钉到 8002)
证据: /tmp/uat_gate14_shots/*.png + /tmp/uat_gate14_video/*.webm + 运行输出重定向 run.log
"""
from __future__ import annotations
import os
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8002"
FRONT = "http://localhost:6002"
SW = f"{API}/api/v1/plugins/sensor-clean/swab"
SHOTS = "/tmp/uat_gate14_shots"
VIDEO = "/tmp/uat_gate14_video"
ANCHOR = "正常产品"
PERMIT = "脏污产品"
LEFT_ROI = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
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


def _det(label, x, y=0.5):
    return {"label": label, "confidence": 0.95, "bbox": [x, y, 0.05, 0.05]}


def run_timeline(seg):
    """启动 synthetic 剧本 + 检测 (channel 0)。"""
    requests.post(f"{API}/api/v1/test/synthetic/start",
                  json={"scenario_json": {"name": "sc_gate14_uat", "fps": 30, "timeline": seg},
                        "channel": 0, "with_project": False}, timeout=15)
    requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                  json={"conf": 0.25, "iou": 0.45}, timeout=15)


def stop_all():
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)


def seg_move(f, x1, x2, labels_at=None, y=0.5):
    """一件产品的移动轨迹段: x1 停 10 帧 → x2 停 10 帧 → 离场 30 帧。返回 (segs, next_f)。"""
    labels_at = labels_at or [ANCHOR]
    return ([
        {"from": f, "to": f + 9, "detections": [_det(lb, x1, y) for lb in labels_at]},
        {"from": f + 10, "to": f + 19, "detections": [_det(lb, x2, y) for lb in labels_at]},
        {"from": f + 20, "to": f + 49, "detections": []},
    ], f + 50)


def drive_delta(seg, wait):
    prev = state().get("total_products", 0)
    seg = list(seg)
    last = seg[-1]["to"] if seg else 0
    seg.append({"from": last + 1, "to": last + 3000, "detections": []})
    run_timeline(seg)
    time.sleep(wait)
    delta = state().get("total_products", 0) - prev
    stop_all()
    time.sleep(0.6)
    return delta


# ============================================================
# Phase A: 后端功能 (synthetic 真管线过插件帧钩子)
# ============================================================
print("\n===== Phase A: 后端许可 + ROI 行为 =====", flush=True)
stop_all(); time.sleep(0.5)
original_cfg = requests.get(f"{SW}/config", timeout=10).json()
patch_cfg(count_anchor_label=ANCHOR, count_require_label=PERMIT,
          label_rois={}, max_uses_per_swab=99, normal_count_event_id=1,
          lock_time=0.6, force_lock_frames=10)
requests.post(f"{SW}/reset-counts", timeout=10); time.sleep(0.5)

# A1 只动锚标签, 许可标签从未出现 → 0 (客户投诉场景)
segs = []
f = 0
for _ in range(3):
    s, f = seg_move(f, 0.40, 0.62)
    segs += s
d1 = drive_delta(segs, 9)
step("A1 许可未出现: 只移动锚标签 = 0 计数 (客户反馈场景)", d1 == 0, f"delta={d1}")

# A2 交替出现: 先许可标签单独出现, 再锚标签单独移动 → 计数
segs = [{"from": 0, "to": 14, "detections": [_det(PERMIT, 0.2, 0.3)]}]
s, f = seg_move(15, 0.40, 0.62)
segs += s
d2 = drive_delta(segs, 6)
step("A2 两类交替出现(不同帧): 许可解锁后锚移动正常计数", d2 == 1, f"delta={d2}")

# A3 许可消费: 许可一次 + 锚移动两件 → 只计第一件
segs = [{"from": 0, "to": 14, "detections": [_det(PERMIT, 0.2, 0.3)]}]
s, f = seg_move(15, 0.40, 0.62)
segs += s
s, f = seg_move(f, 0.35, 0.60, y=0.7)   # 第二件换 y, 避开位置锁干扰
segs += s
d3 = drive_delta(segs, 8)
step("A3 许可被消费: 计一件后锚再动不计第二件", d3 == 1, f"delta={d3}")

# A4 ROI: 锚标签限画面左半区
patch_cfg(count_require_label="", label_rois={"count_anchor": LEFT_ROI})
requests.post(f"{SW}/reset-counts", timeout=10); time.sleep(0.5)
segs, f = seg_move(0, 0.60, 0.85)        # 右半区移动 (ROI 外)
d4a = drive_delta(segs, 5)
segs, f = seg_move(0, 0.10, 0.32)        # 左半区移动 (ROI 内)
d4b = drive_delta(segs, 5)
step("A4 锚标签 ROI: 区域外 0 计数 / 区域内正常计数", d4a == 0 and d4b == 1,
     f"区域外 delta={d4a} 区域内 delta={d4b}")

# ============================================================
# Phase B: 可见浏览器 (监控看板 + 配置 Tab ROI 绘制)
# ============================================================
print("\n===== Phase B: 可见浏览器 =====", flush=True)
patch_cfg(count_require_label=PERMIT, label_rois={})
requests.post(f"{SW}/reset-counts", timeout=10)

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

    # ---- B1/B2: 监控页看板随许可语义联动 ----
    page.goto(f"{FRONT}/#/monitor", wait_until="commit")
    print(">>> goto /#/monitor, waiting plugin bootstrap...", flush=True)
    time.sleep(7.0)
    body = page.evaluate("document.body.innerText")
    step("B1 监控页渲染插件双工位看板", "总产量" in body and "棉签" in body,
         f"片段={body[:60]!r}")
    safe_shot(page, f"{SHOTS}/R1_monitor_board.png")

    t0 = board_total()
    segs = [{"from": 0, "to": 14, "detections": [_det(PERMIT, 0.2, 0.3)]}]
    s, f = seg_move(15, 0.40, 0.62)
    segs += s
    segs.append({"from": f, "to": f + 3000, "detections": []})
    run_timeline(segs)
    time.sleep(8.0)
    t1 = board_total()
    stop_all(); time.sleep(0.6)
    safe_shot(page, f"{SHOTS}/R2_alternating_counted.png")
    step("B2 交替语义: 看板总产量上涨", t0 == 0 and t1 >= 1, f"驱动前={t0} 驱动后={t1}")

    # ---- B3: 项目页配置 Tab — ROI 分组 + 画布绘制落库 ----
    page.goto(f"{FRONT}/#/project", wait_until="commit")
    page.reload(wait_until="domcontentloaded")
    time.sleep(6.0)
    # 配置 Tab 区只在选中项目后渲染, 先点左侧列表第一张项目卡片
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
    step("B3a 配置 Tab 渲染许可标签字段 + ROI 分组",
         tab_ok and "工位1 计数许可标签" in body and "标签 ROI 区域" in body, "")
    safe_shot(page, f"{SHOTS}/R3_config_tab_roi_group.png")

    roi_saved = False
    if tab_ok:
        page.locator("button:has-text('绘制区域')").first.click()
        canvas = page.locator("canvas")
        canvas.wait_for(state="visible", timeout=10000)
        time.sleep(1.2)
        box = canvas.bounding_box()
        for fx, fy in [(0.08, 0.08), (0.46, 0.08), (0.46, 0.9), (0.08, 0.9)]:
            page.mouse.click(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
            time.sleep(0.25)
        safe_shot(page, f"{SHOTS}/R4_roi_canvas_drawn.png")
        page.locator("button:has-text('保存区域')").click()
        time.sleep(0.6)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.5)
        cfg = requests.get(f"{SW}/config", timeout=10).json()
        roi = (cfg.get("label_rois") or {}).get("count_anchor")
        roi_saved = isinstance(roi, list) and len(roi) == 4 and all(
            0.0 <= q[0] <= 1.0 and 0.0 <= q[1] <= 1.0 for q in roi)
    step("B3b 画布点 4 点保存 → 接口读回归一化 ROI", roi_saved, "")
    safe_shot(page, f"{SHOTS}/R5_roi_saved.png")

    # ---- B4: ROI 拦截 (区域外驱动看板不动) ----
    patch_cfg(count_require_label="",
              label_rois={"count_anchor": LEFT_ROI})
    requests.post(f"{SW}/reset-counts", timeout=10)
    page.goto(f"{FRONT}/#/monitor", wait_until="commit")
    time.sleep(6.0)
    t2 = board_total()
    segs, f = seg_move(0, 0.60, 0.85)     # 右半区 = ROI 外
    segs.append({"from": f, "to": f + 3000, "detections": []})
    run_timeline(segs)
    time.sleep(6.0)
    t3 = board_total()
    stop_all(); time.sleep(0.6)
    safe_shot(page, f"{SHOTS}/R6_roi_blocked.png")
    step("B4 ROI 拦截: 区域外驱动看板总产量不动", t2 == 0 and t3 == 0,
         f"驱动前={t2} 驱动后={t3}")

    ctx.close(); browser.close()

# 恢复原配置, 不留 UAT 痕迹
requests.post(f"{SW}/config", json=original_cfg, timeout=10)
requests.post(f"{SW}/reset-counts", timeout=10)

ok = sum(1 for s in steps if s["ok"])
print(f"\n===== 汇总: {ok}/{len(steps)} 通过 =====", flush=True)
