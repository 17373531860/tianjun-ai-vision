#!/usr/bin/env python
"""可见浏览器 UAT (path H): 传感器清洁插件 "达阈值 → 事件 → 计数/提示框(横幅)/物理报警" 闭环。

面向客户疑问: 棉签超限那一刻, 是否 ①触发事件 ②计数 ③提示框(插件红横幅) ④物理报警 同时到位。

前置 (本脚本不自起服务, 需先按下文起好):
  - 隔离后端 8002 (RUNTIME_MODE=test, TIANJUN_DATA_DIR=/tmp/sc_uat_data, 插件已激活, 通道0挂含OK/NG事件的项目)
  - UAT 前端 6002 -> 8002 (frontend/vite.uat.config.js)
  - 插件配置 max_uses_per_swab=2 (上限小, 第3件即超限)

做法:
  Phase A  纯 API 契约: 重置 → 虚拟剧本驱动 3 件 → /state 验 over_limit + ng_count;
           detection/results 验主程序计数器联动; 后端日志取证 报警派发 / 塔灯抑制。
  Phase B  可见浏览器 (headless=False + 录像): monitor 页先看正常(绿)横幅 → 再驱动 → 看红横幅 + 不良数字跳。

三件套证据: 视频 /tmp/uat_video/*.webm + 截图 /tmp/uat_shots/*.png + 日志 /tmp/uat_run.log。
"""
from __future__ import annotations

import json
import time
import os
import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8002"
FRONT = "http://localhost:6002"
SWAB = f"{API}/api/v1/plugins/sensor-clean/swab"
SHOTS = "/tmp/uat_shots"
VIDEO = "/tmp/uat_video"
BACKEND_LOG = "/tmp/sc_uat_backend.log"

os.makedirs(SHOTS, exist_ok=True)
os.makedirs(VIDEO, exist_ok=True)

steps = []


def step(label, ok, detail=""):
    steps.append({"idx": len(steps) + 1, "label": label, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else '!!'}] {len(steps):02d}. {label}  {detail}", flush=True)


def safe_shot(page, path):
    """整页截图: Monitor 常驻 MJPEG 流会卡死 page.screenshot。
    禁用动画 + 短超时 + 失败不致命(视频才是人眼主证据, 截图仅补充)。"""
    try:
        page.screenshot(path=path, animations="disabled", caret="initial", timeout=8000)
        print(f">>> shot ok: {path}", flush=True)
        return True
    except Exception as e:
        print(f">>> shot FAILED(non-fatal): {path} {type(e).__name__}", flush=True)
        return False


def reset_counts():
    requests.post(f"{SWAB}/reset-counts", timeout=10)
    requests.post(f"{SWAB}/reset", timeout=10)


def get_state():
    return requests.get(f"{SWAB}/state", timeout=10).json()


def drive_three_products():
    """虚拟剧本: 锚动作 出现->移动->消失 重复3次 (max=2 → 第3件超限NG)。"""
    seg = []

    def appear(a, b, x):
        seg.append({"from": a, "to": b, "detections": [
            {"label": "查看产品有无脏污", "confidence": 0.95, "bbox": [x, 0.5, 0.05, 0.05]}]})

    def empty(a, b):
        seg.append({"from": a, "to": b, "detections": []})

    f = 0
    for _ in range(3):
        appear(f, f + 9, 0.40)
        appear(f + 10, f + 19, 0.62)
        empty(f + 20, f + 44)
        f += 45
    empty(f, f + 600)   # 超限后持续空: over_limit 维持红横幅
    scn = {"name": "sc_overlimit_uat", "fps": 30, "timeline": seg}
    requests.post(f"{API}/api/v1/test/synthetic/start",
                  json={"scenario_json": scn, "channel": 0, "with_project": False}, timeout=15)
    requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                  json={"conf": 0.25, "iou": 0.45}, timeout=15)


def stop_all():
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)


def wait_over_limit(timeout=15, need_ng=True):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = get_state()
        if st.get("over_limit") and (not need_ng or st.get("ng_count", 0) >= 1):
            return st
        time.sleep(0.5)
    return get_state()


# ============================================================
# Phase A: 纯 API 契约
# ============================================================
print("\n===== Phase A: API 契约 =====")
reset_counts()
time.sleep(0.5)
st0 = get_state()
step("A0 重置后看板正常态(未超限)", st0.get("over_limit") is False and st0.get("ng_count") == 0, f"state={st0}")

drive_three_products()
st = wait_over_limit()
step("A1 棉签擦满上限 → 看板进入超限态(红横幅源)", st.get("over_limit") is True, f"over_limit={st.get('over_limit')}")
step("A2 超限继续擦 → 触发不良(ng_count≥1)", st.get("ng_count", 0) >= 1, f"ng_count={st.get('ng_count')} total={st.get('total_products')}")

res = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=10).json()
counters = res.get("counters", {})
step("A3 主程序计数器联动(合格/不良/总产量在动)",
     counters.get("合格总数", 0) >= 1 and counters.get("不良总数", 0) >= 1,
     f"counters={json.dumps(counters, ensure_ascii=False)}")

# 报警/抑制 日志取证
log_txt = ""
try:
    with open(BACKEND_LOG, "r", errors="ignore") as f:
        log_txt = f.read()
except Exception:
    pass
suppressed = "suppress_alarm 生效" in log_txt
alarm_dispatch = ("alarm device disabled" in log_txt) or ("trigger_alarm" in log_txt)
step("A4 主程序并行周期结算塔灯被插件抑制(防误亮红灯)", suppressed,
     "日志命中 'suppress_alarm 生效'" if suppressed else "未见抑制日志")
step("A5 报警链路已被触达(本机报警器未启用→记 device disabled)", alarm_dispatch,
     "日志见报警派发/设备状态" if alarm_dispatch else "未见报警派发日志")

# ============================================================
# Phase B: 可见浏览器人眼复核 + 录像
# ============================================================
print("\n===== Phase B: 可见浏览器 =====")
stop_all()       # 彻底停掉 Phase A 的源/检测, 保证下面是一次干净的全新驱动
time.sleep(1.0)
reset_counts()   # 先回正常态, 浏览器里看"绿 → 红"全过程
time.sleep(0.8)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=200,
                                args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                              record_video_dir=VIDEO,
                              record_video_size={"width": 1600, "height": 1000},
                              ignore_https_errors=True)
    page = ctx.new_page()
    page.set_default_timeout(15000)   # 单步操作硬上限, 失败快速暴露而非长挂
    console_errs = []   # (text, url)

    def _on_console(m):
        if m.type == "error":
            loc = m.location or {}
            console_errs.append((m.text, loc.get("url", "")))
    page.on("console", _on_console)

    # Monitor 页有常驻 MJPEG 流, 'load' 事件迟迟不触发 → wait_until=commit 只等导航提交, 不等整页 load
    page.goto(f"{FRONT}/#/monitor", wait_until="commit")
    print(">>> goto committed, waiting plugin bootstrap...", flush=True)
    time.sleep(6.0)   # 等插件 bootstrap(等后端就绪+拉清单+加载ESM) + 看板首次渲染

    body = page.evaluate("document.body.innerText")
    plugin_loaded = ("棉签" in body) or ("运行正常" in body) or ("不良" in body)
    step("B1 插件双工位看板已渲染(整页覆盖生效)", plugin_loaded,
         f"body片段={body[:60]!r}")
    safe_shot(page, f"{SHOTS}/B1_monitor_normal.png")
    step("B2 起始为正常态(绿/未超限横幅)", "运行正常" in body or "棉签状态良好" in body,
         "含'运行正常'" if "运行正常" in body else f"body={body[:80]!r}")

    # 浏览器开着, 驱动剧本 → 后端先确认翻红(可靠), 立即停源去掉高频负载(over_limit 粘滞仍红)
    drive_three_products()
    # Phase B 目标是"看板翻红"的可见证据; 不良 NG 的计数/报警在 Phase A 已硬证(ng=1+计数器+报警派发),
    # 此处只需后端确认 over_limit(红横幅数据源)即可, 不再重复硬卡 ng。
    st_b = wait_over_limit(timeout=30, need_ng=False)
    step("B3a 驱动后 后端确认棉签超限(红横幅数据源, NG联动归 Phase A 硬证)",
         st_b.get("over_limit") is True,
         f"over_limit={st_b.get('over_limit')} ng={st_b.get('ng_count')}")
    # 不在这里停源: 看板轮询要在"检测进行中"翻红(贴近现场); 截图卡顿已修, 不怕源在跑
    # 浏览器内直接 fetch 插件 /state 探针: 排除浏览器缓存/取数路径问题
    probe = page.evaluate(
        "() => fetch('/api/v1/plugins/sensor-clean/swab/state',{cache:'no-store'})"
        ".then(r=>r.json()).catch(e=>({err:String(e)}))"
    )
    print(f">>> in-page /state probe = {probe}", flush=True)
    # 用 Playwright 原生 wait_for_selector(浏览器内高效轮询), 避免反复跨进程取整页文本在高负载下卡死
    turned_red = False
    red_text = ""
    try:
        loc = page.get_by_text("棉签已达上限", exact=False).first
        loc.wait_for(state="visible", timeout=15000)
        turned_red = True
        red_text = loc.inner_text(timeout=5000)
    except Exception as e:
        print(f">>> wait red banner failed: {type(e).__name__}", flush=True)
    safe_shot(page, f"{SHOTS}/B3_monitor_overlimit_red.png")
    step("B3 达阈值那一刻 → 红横幅'棉签已达上限...'在浏览器实时出现", turned_red,
         f"看板已翻红: {red_text!r}" if turned_red else "后端已超限但前端未渲染红横幅")

    has_ng_num = ("累计不良" in red_text) and any(c.isdigit() for c in red_text)
    step("B4 红横幅同时带'累计不良N'数字(提示+计数同屏)", has_ng_num, f"横幅文案={red_text!r}")

    time.sleep(2.0)
    # 基础设施噪声(无真实相机→视频流/快照连接被拒)不算前端逻辑报错; 只盯真正的 JS/页面报错。
    INFRA = ("video_feed", "/snapshot", "/ws", "ERR_CONNECTION_REFUSED",
             "favicon", "net::ERR_CONNECTION_REFUSED")
    real_errs = [(t, u) for (t, u) in console_errs
                 if not any(k in (t + " " + u) for k in INFRA)]
    infra_errs = [(t, u) for (t, u) in console_errs if (t, u) not in real_errs]
    step("B5 浏览器控制台无前端逻辑报错(视频流连接拒绝属无相机基础设施噪声, 已剔除)",
         len(real_errs) == 0,
         f"真报错={real_errs[:3]}" if real_errs else f"仅基础设施噪声{len(infra_errs)}条(无相机)")

    ctx.close()   # flush 视频
    browser.close()

# ============================================================
# 收尾
# ============================================================
stop_all()
ok = sum(1 for s in steps if s["ok"])
fail = len(steps) - ok
print(f"\n===== 汇总: {ok}/{len(steps)} 通过, failed: {fail} =====")
with open("/tmp/uat_run.log", "w") as f:
    f.write(json.dumps({"passed": ok, "failed": fail, "steps": steps}, ensure_ascii=False, indent=2))
print("证据: 视频", VIDEO, "| 截图", SHOTS, "| 日志 /tmp/uat_run.log")
