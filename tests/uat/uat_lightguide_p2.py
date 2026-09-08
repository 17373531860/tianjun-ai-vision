"""投影光引导 P2 可见浏览器 UAT: 自愈标定 + 悬停确认。

剧本:
  A. 种标定进引导 → 四角自愈锚点可见 (截图 A)
  B. 防误触真路径: synthetic 黑帧下真 verify 找到 0 锚点 → 25s 内不得自动重标
     (锚点被遮挡/无画面时绝不能乱动标定)
  C. 漂移自愈闭环 (mock 传感): 拦截 verify 返回 drift 11.3px → 前端应自动进入
     重标 (图案出现, 截图 C1); 拦截 solve 返回成功 → 自动回到引导 (截图 C2)。
     verify/solve 的真实求解已由 tests/test_lightguide_p2.py 合成端到端覆盖,
     此处 mock 只为验证前端自愈决策链路。
  D. 悬停确认闭环: synthetic 缺步 NG + require_ack → pending_ack 阻塞 →
     投影确认按钮出现 (截图 D1); 拦截 interaction/sample 先低后高模拟手进入 →
     进度环充满 → 前端调真 ack-event → 后端 pending_ack 解除 (API 双向验证,
     截图 D2)

运行: ~/miniconda3/envs/tianjun/bin/python tests/uat/uat_lightguide_p2.py
"""
import faulthandler
import json
import os
import sqlite3
import sys
import time

import requests
from playwright.sync_api import sync_playwright

# 看门狗: 任何环节挂死 240s 后转储所有线程堆栈并强退, 不许无限挂
faulthandler.dump_traceback_later(240, exit=True)

API = "http://127.0.0.1:8005/api/v1"
FE = "http://127.0.0.1:6005"
CH = 0
SHOTS = os.path.join(os.path.dirname(__file__), "shots_lightguide_p2")
DB = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "sql_app.db")

VIEW_W, VIEW_H = 1280, 800
CAM_W, CAM_H = 1280, 720

PASS, FAIL = [], []


def step(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}  {detail}", flush=True)


def seed_calibration():
    calib = {
        "homography": [[1.0, 0.0, 0.0], [0.0, VIEW_H / CAM_H, 0.0], [0.0, 0.0, 1.0]],
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
        cur = conn.execute("SELECT id FROM system_configs WHERE key=?", (key,))
        if cur.fetchone():
            conn.execute("UPDATE system_configs SET value=? WHERE key=?", (val, key))
        else:
            conn.execute(
                "INSERT INTO system_configs (key, value, description) VALUES (?,?,?)",
                (key, val, "UAT 种入"))
        conn.commit()
    finally:
        conn.close()
    return calib


def ng_scenario():
    """缺 step_b → 首步重现结算 NG (require_ack 阻塞)。

    前 10s 空闲缓冲: detection/start 到推理线程真跑起来有秒级延迟,
    时间线开头就放 step_a 会被错过 → 周期根本立不起来 (UAT 实测翻车过)。
    """
    def seg(a, b, label, x, y):
        return {"from": a, "to": b, "detections": [
            {"label": label, "confidence": 0.95, "bbox": [x, y, 0.13, 0.18]}]}
    return {"name": "lightguide-p2-ng", "fps": 10, "timeline": [
        seg(100, 160, "step_a", 0.15, 0.55),
        seg(180, 240, "step_c", 0.70, 0.35),     # 跳过 step_b
        seg(280, 900, "step_a", 0.15, 0.55),     # 首步重现 → NG (~28s)
    ]}


def ng_project_config():
    steps = [
        {"id": "lg-1", "label": "step_a", "display_name": "取壳体",
         "threshold": 0.3, "min_frames": 1, "enabled": True},
        {"id": "lg-2", "label": "step_b", "display_name": "装密封圈",
         "threshold": 0.3, "min_frames": 1, "enabled": True},
        {"id": "lg-3", "label": "step_c", "display_name": "锁紧端盖",
         "threshold": 0.3, "min_frames": 1, "enabled": True},
    ]
    return {
        "project_id": -1, "name": "__lightguide_uat_p2__", "task_type": "detect",
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


def start_ng_pipeline():
    r = requests.post(f"{API}/test/synthetic/start", json={
        "scenario_json": ng_scenario(), "channel": CH, "with_project": False}, timeout=15)
    assert r.status_code == 200, r.text[:300]
    r = requests.post(f"{API}/source/detection/set-project?channel={CH}",
                      json=ng_project_config(), timeout=15)
    assert r.status_code == 200, r.text[:300]
    requests.post(f"{API}/source/detection/reset-stats?channel={CH}", timeout=10)
    r = requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"conf": 0.25, "iou": 0.45}, timeout=30)
    assert r.status_code == 200, r.text[:300]


def stop_pipeline():
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
    requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)


def pending_ack():
    d = requests.get(f"{API}/source/detection/results?channel={CH}", timeout=5).json()
    return d.get("pending_ack") or {}


# 页面在 6005、API 在 8005 跨域: route.fulfill 伪造的响应必须自带 CORS 头,
# 否则浏览器直接拦掉, 前端 axios 只看到 Network Error (踩过: mock 全被静默丢弃)
_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "*",
}


def fulfill_json(route, payload):
    if route.request.method == "OPTIONS":     # 预检
        route.fulfill(status=204, headers=_CORS)
    else:
        route.fulfill(json=payload, headers=_CORS)


def main():
    os.makedirs(SHOTS, exist_ok=True)
    calib = seed_calibration()
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)

    print("[phase] 启动浏览器…", flush=True)
    with sync_playwright() as p:
        # macOS 血泪: headless=False 窗口被遮挡时 Chromium 后台节流 → rAF 画布
        # 出不了稳定帧 → page.screenshot 无限等待。三个开关关掉节流治本。
        browser = p.chromium.launch(headless=False, args=[
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
        ])
        page = browser.new_page(viewport={"width": VIEW_W, "height": VIEW_H})
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(20000)

        def shot(name):
            # 尽力而为: 截图失败不拖垮 UAT (断言全走 DOM/API)
            try:
                page.bring_to_front()
                page.screenshot(path=f"{SHOTS}/{name}", timeout=10000)
            except Exception as e:
                print(f"  ⚠️ 截图失败 {name}: {str(e)[:80]}", flush=True)

        # --- A. 进引导, 锚点可见 ---
        print("[phase] A 进引导", flush=True)
        # 起一个空 synthetic 让工位有帧 (黑帧, verify 真路径找不到锚点)
        requests.post(f"{API}/test/synthetic/start", json={
            "scenario_json": {"name": "black", "fps": 10, "timeline": []},
            "channel": CH, "with_project": False}, timeout=15)
        page.goto(f"{FE}/#/projection?channel={CH}", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        step("A1 已标定自动进引导", page.locator(".status-chip").is_visible())
        shot("A_anchors.png")

        # --- B. 防误触真路径: 黑帧 verify 找到 0 锚点, 一个自检周期内不得自动重标 ---
        print("[phase] B 防误触真路径 (等 23s)", flush=True)
        t0 = time.time()
        recal_triggered = False
        while time.time() - t0 < 23:   # 漂移自检周期 20s, 覆盖一轮真 verify
            if page.locator(".pattern-img").count() > 0:
                recal_triggered = True
                break
            page.wait_for_timeout(500)  # 必须走 playwright 等待, 否则 route mock 回调被饿死
        step("B1 锚点不可见时不误触发重标 (真 verify 一周期)", not recal_triggered)

        # --- C. 漂移自愈闭环 (mock verify/solve) ---
        print("[phase] C 漂移自愈 (mock, 等下个 20s 自检周期)", flush=True)
        solve_calls = {"n": 0}

        def fake_verify(route):
            fulfill_json(route, dict(channel=CH, anchors_expected=4, anchors_found=4,
                                     frames_used=2, drift_px=11.3, drift_max_px=13.2))

        def fake_solve(route):
            if route.request.method == "POST":
                solve_calls["n"] += 1
            fulfill_json(route, {"status": "success", "channel": CH,
                                 "calibration": calib})

        page.route("**/lightguide/calibration/verify", fake_verify)
        page.route("**/lightguide/calibration/solve", fake_solve)

        pattern_seen = False
        t0 = time.time()
        while time.time() - t0 < 30:   # 漂移自检周期 20s
            if page.locator(".pattern-img").count() > 0:
                pattern_seen = True
                shot("C1_auto_recal.png")
                break
            time.sleep(0.4)
        step("C1 漂移超阈值自动进入重标 (图案已投出)", pattern_seen)

        back_to_guide = False
        t0 = time.time()
        while time.time() - t0 < 15:
            if (page.locator(".pattern-img").count() == 0
                    and page.locator(".status-chip").is_visible()):
                back_to_guide = True
                break
            time.sleep(0.4)
        step("C2 重标完成自动回到引导", back_to_guide,
             f"solve 被调 {solve_calls['n']} 次")
        shot("C2_back_guide.png")
        page.unroute("**/lightguide/calibration/verify")
        page.unroute("**/lightguide/calibration/solve")

        # --- D. 悬停确认闭环 ---
        print("[phase] D 悬停确认闭环", flush=True)
        sample_calls = {"n": 0}

        def fake_sample(route):
            if route.request.method != "POST":
                return fulfill_json(route, {})
            sample_calls["n"] += 1
            # 前 2 次低亮度 (采基线), 之后高亮度 (手进入按钮区)
            gray = 40.0 if sample_calls["n"] <= 2 else 90.0
            fulfill_json(route, {"channel": CH, "mean_gray": gray,
                                 "mean_bgr": [gray] * 3,
                                 "frame_size": [CAM_W, CAM_H], "ts": time.time()})

        page.route("**/lightguide/interaction/sample", fake_sample)

        start_ng_pipeline()
        blocked = False
        t0 = time.time()
        while time.time() - t0 < 45:   # NG 预计 ~28s (含 10s 启动缓冲)
            if pending_ack().get("active"):
                blocked = True
                break
            page.wait_for_timeout(500)  # 必须走 playwright 等待, 否则 route mock 回调被饿死
        step("D1 缺步 NG require_ack 阻塞已触发 (API)", blocked)
        page.wait_for_timeout(1500)   # 按钮出现 + 基线采样
        shot("D1_confirm_button.png")

        cleared = False
        t0 = time.time()
        while time.time() - t0 < 30:   # 截图耗时有波动, 窗口放宽 (后端日志实测 ack ~17s 落地)
            if not pending_ack().get("active"):
                cleared = True
                break
            page.wait_for_timeout(500)  # 必须走 playwright 等待, 否则 route mock 回调被饿死
        step("D2 悬停充满 → 真 ack-event → 阻塞解除 (API 双向)", cleared,
             f"sample 被调 {sample_calls['n']} 次")
        page.wait_for_timeout(800)
        shot("D2_ack_cleared.png")

        stop_pipeline()
        browser.close()

    print(f"\n===== UAT 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 =====")
    for n in FAIL:
        print(f"  ❌ {n}")
    print(f"截图目录: {SHOTS}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        stop_pipeline()
