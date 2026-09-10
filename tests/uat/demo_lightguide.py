"""投影光引导 · 一键演示模式 (老板演示/客户展示用, 无需任何硬件)。

一条命令跑完整故事线 (真开浏览器全屏, synthetic 剧本驱动真实检测管线):

  第一幕  待机呼吸 —— 投影窗开机即用 (已标定自动进引导)
  第二幕  引导装配 —— 三步 SOP: 目标区辉光 + 流向引导路径 + 步骤链推进
          → 周期合格: 绿色扫光 + 粒子爆发
  第三幕  防错拦截 —— 操作员跳过一步 → NG 红色脉冲 + 投影确认按钮
          → "手悬停"确认 (演示环境模拟手遮挡) → 进度环充满自动放行
  第四幕  自愈标定 —— 投影仪被撞歪 (模拟漂移) → 自动重标 → 秒级恢复引导

前置 (与 UAT 同环境):
  后端:  RUNTIME_MODE=test + 端口 8005 (环境变量 DEMO_API 可改)
  前端:  vite 端口 6005 (环境变量 DEMO_FE 可改)

运行:
  ~/miniconda3/envs/tianjun/bin/python tests/uat/demo_lightguide.py

接真投影仪演示时: 不跑本脚本, 直接开 /projection 全屏按 C 真标定即可,
第三幕的悬停确认用真手遮挡, 第四幕直接用手推一下投影仪。
"""
import json
import os
import sqlite3
import sys
import time

import requests
from playwright.sync_api import sync_playwright

API = os.environ.get("DEMO_API", "http://127.0.0.1:8005/api/v1")
FE = os.environ.get("DEMO_FE", "http://127.0.0.1:6005")
CH = int(os.environ.get("DEMO_CHANNEL", "0"))
DB = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "sql_app.db")

VIEW_W, VIEW_H = 1600, 900
CAM_W, CAM_H = 1280, 720


def banner(text):
    print(f"\n{'=' * 64}\n  {text}\n{'=' * 64}", flush=True)


def note(text):
    print(f"  · {text}", flush=True)


# ---------------- 环境准备 ----------------

def check_services():
    try:
        requests.get(f"{API}/projects", timeout=3).raise_for_status()
    except Exception:
        sys.exit(f"❌ 后端不可达: {API}\n   先起后端: RUNTIME_MODE=test uvicorn backend.main:app --port 8005")
    try:
        requests.get(FE, timeout=3).raise_for_status()
    except Exception:
        sys.exit(f"❌ 前端不可达: {FE}\n   先起前端: cd frontend && npx vite --port 6005")


def seed_calibration():
    """种入演示标定 (相机→投影纯缩放)。真投影仪演示走真标定, 不用本函数。"""
    calib = {
        "homography": [[VIEW_W / CAM_W, 0.0, 0.0],
                       [0.0, VIEW_H / CAM_H, 0.0], [0.0, 0.0, 1.0]],
        "cam_width": CAM_W, "cam_height": CAM_H,
        "proj_width": VIEW_W, "proj_height": VIEW_H,
        "pattern": {"cols": 4, "rows": 3, "dict": "DICT_4X4_50"},
        "markers_matched": 12, "markers_total": 12, "frames_used": 5,
        "reproj_error_px": 0.42, "reproj_error_max_px": 0.9, "inlier_ratio": 1.0,
        "calibrated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    conn = sqlite3.connect(os.path.abspath(DB), timeout=15)
    try:
        key = f"lightguide.channel.{CH}"
        val = json.dumps(calib, ensure_ascii=False)
        if conn.execute("SELECT id FROM system_configs WHERE key=?", (key,)).fetchone():
            conn.execute("UPDATE system_configs SET value=? WHERE key=?", (val, key))
        else:
            conn.execute("INSERT INTO system_configs (key, value, description) VALUES (?,?,?)",
                         (key, val, "演示种入"))
        conn.commit()
    finally:
        conn.close()
    return calib


def demo_scenario():
    """一条时间线讲完两个周期 @10fps:
    周期1 (合格): a → b → c → a 重现结算 OK
    周期2 (防错): a → 跳过 b → c → a 重现结算 NG (require_ack 阻塞等悬停确认)
    """
    def seg(a, b, label, x, y):
        return {"from": a, "to": b, "detections": [
            {"label": label, "confidence": 0.95, "bbox": [x, y, 0.13, 0.18]}]}
    return {"name": "lightguide-demo", "fps": 10, "timeline": [
        # 10s 启动缓冲 (推理线程起步) —— 周期 1: 规范操作
        seg(100, 160, "step_a", 0.15, 0.55),
        seg(180, 240, "step_b", 0.45, 0.50),
        seg(260, 320, "step_c", 0.70, 0.35),
        # 周期 2 开始 (首步重现 → 周期 1 结算 OK ≈ 36s)
        seg(360, 420, "step_a", 0.15, 0.55),
        seg(460, 520, "step_c", 0.70, 0.35),      # 跳过 step_b (演示防错)
        seg(560, 1800, "step_a", 0.15, 0.55),     # 首步重现 → NG ≈ 56s
    ]}


def demo_project_config():
    def roi(x, y):
        return [[x - 0.03, y - 0.04], [x + 0.16, y - 0.04],
                [x + 0.16, y + 0.22], [x - 0.03, y + 0.22]]
    steps = [
        {"id": "lg-1", "label": "step_a", "display_name": "取壳体",
         "threshold": 0.3, "min_frames": 1, "enabled": True, "roi": roi(0.15, 0.55)},
        {"id": "lg-2", "label": "step_b", "display_name": "装密封圈",
         "threshold": 0.3, "min_frames": 1, "enabled": True, "roi": roi(0.45, 0.50)},
        {"id": "lg-3", "label": "step_c", "display_name": "锁紧端盖",
         "threshold": 0.3, "min_frames": 1, "enabled": True, "roi": roi(0.70, 0.35)},
    ]
    return {
        "project_id": -1, "name": "__lightguide_demo__", "task_type": "detect",
        "logic_mode": "sequential", "steps_config": steps,
        "pipeline_config": {
            "sequence_order": [{"step_id": s["id"]} for s in steps],
            "settlement_mode": "first_step", "settle_dedup": False,
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [{"counter_name": "合格总数", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "require_ack": True, "ack_timeout_sec": 0,
             "actions": [{"counter_name": "不良总数", "delta": 1}]},
        ],
        "counters_config": [], "data_config": {},
    }


def start_pipeline():
    # 必须先停旧源并等清理收尾再起新剧本: synthetic stop→start 背靠背有竞态,
    # 旧源异步 teardown 会掐死新源 (实测只出 ~15 帧后静默, 周期立不起来)
    requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)
    time.sleep(1.5)
    requests.post(f"{API}/test/synthetic/start", json={
        "scenario_json": demo_scenario(), "channel": CH,
        "with_project": False}, timeout=15).raise_for_status()
    requests.post(f"{API}/source/detection/set-project?channel={CH}",
                  json=demo_project_config(), timeout=15).raise_for_status()
    requests.post(f"{API}/source/detection/reset-stats?channel={CH}", timeout=10)
    requests.post(f"{API}/source/detection/start?channel={CH}",
                  json={"conf": 0.25, "iou": 0.45}, timeout=30).raise_for_status()


def stop_pipeline():
    try:
        requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)
    except Exception:
        pass


def results():
    return requests.get(f"{API}/source/detection/results?channel={CH}", timeout=5).json()


def pending_ack():
    return results().get("pending_ack") or {}


# 6005 页面调 8005 API 跨域: mock 响应必须带 CORS 头 (否则浏览器静默丢弃)
_CORS = {"Access-Control-Allow-Origin": "*",
         "Access-Control-Allow-Headers": "*",
         "Access-Control-Allow-Methods": "*"}


def fulfill_json(route, payload):
    if route.request.method == "OPTIONS":
        route.fulfill(status=204, headers=_CORS)
    else:
        route.fulfill(json=payload, headers=_CORS)


# ---------------- 演示主线 ----------------

def main():
    check_services()
    calib = seed_calibration()
    stop_pipeline()
    time.sleep(1.5)   # 等上一轮源清理收尾 (stop→start 竞态守门)

    banner("投影光引导 · 演示开始 (窗口即'投影仪打在工作台上的画面')")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=[
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
        ])
        page = browser.new_page(viewport={"width": VIEW_W, "height": VIEW_H})
        page.set_default_timeout(20000)

        # ---- 第一幕: 待机呼吸 ----
        banner("第一幕 · 开机即用: 已标定工位自动进入引导待机")
        requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)
        time.sleep(1.5)   # stop→start 竞态守门
        requests.post(f"{API}/test/synthetic/start", json={
            "scenario_json": {"name": "black", "fps": 10, "timeline": []},
            "channel": CH, "with_project": False}, timeout=15)
        page.goto(f"{FE}/#/projection?channel={CH}", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.bring_to_front()
        note("呼吸弧环 = 待机; 四角小方块 = 自愈锚点 (相机持续盯着它们)")
        page.wait_for_timeout(5000)

        # ---- 第二幕: 引导装配 + OK ----
        banner("第二幕 · 引导装配: 目标区辉光 + 流向路径 + 步骤链")
        start_pipeline()
        note("顶部步骤链: 取壳体 → 装密封圈 → 锁紧端盖")
        note("台面上: 当前步骤安装位青色辉光呼吸, 底边流向箭头指引取料方向")
        note("琥珀色四角括号 = AI 实时检出的零件位置")
        # 等第一周期结算 OK (~36s), 期间演示自然推进
        t0 = time.time()
        while time.time() - t0 < 50:
            evs = [e for e in (results().get("recent_events") or [])
                   if e.get("toast_id") == "ok"]
            if evs:
                note("✅ 周期合格 —— 绿色扫光 + 粒子爆发")
                break
            page.wait_for_timeout(600)
        page.wait_for_timeout(2500)

        # ---- 第三幕: 防错 NG + 悬停确认 ----
        banner("第三幕 · 防错拦截: 跳过'装密封圈' → NG → 投影按钮悬停放行")
        sample_calls = {"n": 0}

        def fake_sample(route):
            if route.request.method != "POST":
                return fulfill_json(route, {})
            sample_calls["n"] += 1
            gray = 40.0 if sample_calls["n"] <= 2 else 90.0   # 2 帧基线后"手进入"
            fulfill_json(route, {"channel": CH, "mean_gray": gray,
                                 "mean_bgr": [gray] * 3,
                                 "frame_size": [CAM_W, CAM_H], "ts": time.time()})

        note("剧本正在跳过 step_b … (真投影仪演示时: 操作员真跳步即可)")
        t0 = time.time()
        while time.time() - t0 < 60:
            if pending_ack().get("active"):
                note("🔴 NG 拦截! 红色脉冲 + 台面弹出琥珀色确认按钮")
                break
            page.wait_for_timeout(600)
        page.wait_for_timeout(1200)
        note("模拟'手悬停在按钮上' (真机演示用真手遮挡投影按钮)…")
        page.route("**/lightguide/interaction/sample", fake_sample)
        t0 = time.time()
        while time.time() - t0 < 30:
            if not pending_ack().get("active"):
                note("✅ 进度环充满 → 自动确认放行, 产线继续")
                break
            page.wait_for_timeout(500)
        page.unroute("**/lightguide/interaction/sample")
        page.wait_for_timeout(2000)

        # ---- 第四幕: 自愈标定 ----
        banner("第四幕 · 自愈标定: '投影仪被撞歪' → 自动发现 → 秒级重标")
        note("(真机演示: 直接用手推一下投影仪; 这里模拟漂移传感)")

        def fake_verify(route):
            fulfill_json(route, dict(channel=CH, anchors_expected=4, anchors_found=4,
                                     frames_used=2, drift_px=11.3, drift_max_px=13.2))

        def fake_solve(route):
            fulfill_json(route, {"status": "success", "channel": CH,
                                 "calibration": calib})

        page.route("**/lightguide/calibration/verify", fake_verify)
        page.route("**/lightguide/calibration/solve", fake_solve)
        t0 = time.time()
        recal = False
        while time.time() - t0 < 30:   # 漂移自检默认 20s 一轮
            if page.locator(".pattern-img").count() > 0:
                recal = True
                note("📐 检测到漂移 → 自动打出标定图案重新标定…")
                break
            page.wait_for_timeout(500)
        if recal:
            t0 = time.time()
            while time.time() - t0 < 15:
                if (page.locator(".pattern-img").count() == 0
                        and page.locator(".status-chip").is_visible()):
                    note("✅ 重标完成, 自动回到引导 —— 全程无人工介入")
                    break
                page.wait_for_timeout(500)
        page.unroute("**/lightguide/calibration/verify")
        page.unroute("**/lightguide/calibration/solve")
        page.wait_for_timeout(2000)

        banner("演示结束")
        stop_pipeline()
        if sys.stdin.isatty():
            input("  按回车关闭演示窗口…")
        else:
            page.wait_for_timeout(3000)
        browser.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        stop_pipeline()
