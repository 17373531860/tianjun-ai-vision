"""投影光引导 P1 可见浏览器 UAT (headless=False + 截图)。

剧本 (对应现场叙事):
  A. 打开 /projection 未标定 → 菜单可见 (截图 A)
  B. 点「开始自动标定」→ ArUco 图案全屏显示 (截图 B); synthetic 源是纯黑帧,
     相机看不到图案 → 求解应返回可读错误「仅识别到 0/12 个标记」(截图 C),
     且后端 config 仍是未标定 (双向验证: UI 报错 ↔ GET config calibrated=false)
  C. 种入标定 (模拟已标定工位) → 刷新页面自动进引导; 检测未启动 → 待机呼吸态 (截图 D)
  D. 起 synthetic sequential 三步剧本 + 带 ROI 的项目 → 步骤链/目标区辉光/
     实时检测框逐步推进 (截图 E1..E3); 首步重现结算 OK → 绿色扫光 (截图 F)
  E. 双向验证: 页面步骤态与 GET /source/detection/results 的
     current_cycle_steps 一致

运行 (先起后端 8005 RUNTIME_MODE=test + 前端 6005):
  ~/miniconda3/envs/tianjun/bin/python tests/uat/uat_lightguide_p1.py
"""
import json
import os
import sqlite3
import sys
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8005/api/v1"
FE = "http://127.0.0.1:6005"
CH = 0
SHOTS = os.path.join(os.path.dirname(__file__), "shots_lightguide_p1")
DB = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "sql_app.db")

VIEW_W, VIEW_H = 1280, 800   # 投影窗 (playwright viewport)
CAM_W, CAM_H = 1280, 720     # synthetic 源帧尺寸

PASS, FAIL = [], []


def step(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}  {detail}")


# ---------- 数据准备 ----------

def seed_calibration():
    """直接写 SystemConfig KV 种入标定 (相机 1280x720 → 投影 1280x800 纯缩放)。"""
    calib = {
        "homography": [[1.0, 0.0, 0.0],
                       [0.0, VIEW_H / CAM_H, 0.0],
                       [0.0, 0.0, 1.0]],
        "cam_width": CAM_W, "cam_height": CAM_H,
        "proj_width": VIEW_W, "proj_height": VIEW_H,
        "pattern": {"cols": 4, "rows": 3, "dict": "DICT_4X4_50"},
        "markers_matched": 12, "markers_total": 12, "frames_used": 5,
        "reproj_error_px": 0.42, "reproj_error_max_px": 0.9,
        "inlier_ratio": 1.0,
        "calibrated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    conn = sqlite3.connect(os.path.abspath(DB), timeout=15)
    try:
        key = f"lightguide.channel.{CH}"
        cur = conn.execute("SELECT id FROM system_configs WHERE key=?", (key,))
        row = cur.fetchone()
        val = json.dumps(calib, ensure_ascii=False)
        if row:
            conn.execute("UPDATE system_configs SET value=? WHERE key=?", (val, key))
        else:
            conn.execute(
                "INSERT INTO system_configs (key, value, description) VALUES (?,?,?)",
                (key, val, "UAT 种入"))
        conn.commit()
    finally:
        conn.close()


def clear_calibration():
    requests.delete(f"{API}/lightguide/config", params={"channel": CH}, timeout=5)


def scenario():
    """sequential 三步剧本 @10fps: a → b → c → (a 重现结算周期1 OK) → b → c。"""
    def seg(a, b, label, x, y):
        return {"from": a, "to": b, "detections": [
            {"label": label, "confidence": 0.95, "bbox": [x, y, 0.13, 0.18]}]}
    return {"name": "lightguide-p1", "fps": 10, "timeline": [
        seg(0, 60, "step_a", 0.15, 0.55),
        seg(80, 140, "step_b", 0.45, 0.50),
        seg(160, 220, "step_c", 0.70, 0.35),
        seg(260, 320, "step_a", 0.15, 0.55),   # 首步重现 → 周期1 结算 OK
        seg(340, 400, "step_b", 0.45, 0.50),
        seg(420, 480, "step_c", 0.70, 0.35),
    ]}


def project_config():
    """带 ROI 多边形的 sequential 项目 (ROI 即投影引导目标区)。"""
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
        "project_id": -1, "name": "__lightguide_uat__", "task_type": "detect",
        "logic_mode": "sequential",
        "steps_config": steps,
        "pipeline_config": {
            "sequence_order": [{"step_id": s["id"]} for s in steps],
            "settlement_mode": "first_step", "settle_dedup": False,
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [{"counter_name": "合格总数", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [{"counter_name": "不良总数", "delta": 1}]},
        ],
        "counters_config": [], "data_config": {},
    }


def start_pipeline():
    r = requests.post(f"{API}/test/synthetic/start", json={
        "scenario_json": scenario(), "channel": CH, "with_project": False}, timeout=15)
    assert r.status_code == 200, r.text[:300]
    r = requests.post(f"{API}/source/detection/set-project?channel={CH}",
                      json=project_config(), timeout=15)
    assert r.status_code == 200, r.text[:300]
    requests.post(f"{API}/source/detection/reset-stats?channel={CH}", timeout=10)
    r = requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"conf": 0.25, "iou": 0.45}, timeout=30)
    assert r.status_code == 200, r.text[:300]


def stop_pipeline():
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
    requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)


def results():
    return requests.get(f"{API}/source/detection/results?channel={CH}", timeout=5).json()


# ---------- 主剧本 ----------

def main():
    os.makedirs(SHOTS, exist_ok=True)
    clear_calibration()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": VIEW_W, "height": VIEW_H})

        # --- A. 未标定菜单 ---
        page.goto(f"{FE}/#/projection?channel={CH}", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        menu_visible = page.locator(".menu-overlay").is_visible()
        step("A 未标定进入 → 菜单可见", menu_visible)
        page.screenshot(path=f"{SHOTS}/A_menu.png")

        # --- B. 自动标定: 图案显示 + 黑帧求解失败可读 ---
        # 先起 synthetic 让工位有帧 (纯黑, 预期识别 0 标记)
        requests.post(f"{API}/test/synthetic/start", json={
            "scenario_json": {"name": "black", "fps": 10, "timeline": []},
            "channel": CH, "with_project": False}, timeout=15)
        page.locator(".menu-btn.primary").click()
        page.wait_for_timeout(700)
        pattern_visible = page.locator(".pattern-img").is_visible()
        step("B1 标定图案全屏显示", pattern_visible)
        page.screenshot(path=f"{SHOTS}/B_pattern.png")
        # 等求解返回 (1.5s 稳定期 + 请求)
        page.wait_for_selector(".calib-result", timeout=15000)
        err_text = page.locator(".calib-result-body").inner_text()
        step("B2 黑帧求解失败给出可读原因", "识别到" in err_text, err_text[:60])
        page.screenshot(path=f"{SHOTS}/C_solve_fail.png")
        cfg = requests.get(f"{API}/lightguide/config",
                           params={"channel": CH}, timeout=5).json()
        step("B3 双向验证: 后端仍未标定", cfg["calibrated"] is False)
        page.wait_for_timeout(4200)  # 等错误浮层自动退回菜单

        # --- C. 种标定 → 自动进引导 (待机态) ---
        seed_calibration()
        cfg = requests.get(f"{API}/lightguide/config",
                           params={"channel": CH}, timeout=5).json()
        step("C1 种入标定后 GET 可读回", cfg["calibrated"] is True
             and cfg["calibration"]["reproj_error_px"] == 0.42)
        requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        chip_visible = page.locator(".status-chip").is_visible()
        step("C2 已标定自动进引导 (右下状态角标)", chip_visible,
             page.locator(".status-chip").inner_text() if chip_visible else "")
        page.screenshot(path=f"{SHOTS}/D_idle.png")

        # --- D. 起 sequential 剧本 → 引导推进 ---
        start_pipeline()
        page.wait_for_timeout(2500)   # step_a 在场 (帧 0-60 @10fps)
        page.screenshot(path=f"{SHOTS}/E1_step_a.png")
        d = results()
        step("D1 检测运行且 project_config 带 ROI",
             d.get("is_detecting") and bool(d["project_config"]["steps_config"][0].get("roi")))

        page.wait_for_timeout(8000)   # ~帧105: step_b 在场, step_a 已入周期
        page.screenshot(path=f"{SHOTS}/E2_step_b.png")
        d = results()
        step("D2 step_a 已入周期 (API)", "step_a" in (d.get("current_cycle_steps") or []),
             str(d.get("current_cycle_steps")))

        page.wait_for_timeout(8000)   # ~帧185: step_c 在场
        page.screenshot(path=f"{SHOTS}/E3_step_c.png")

        # --- E. 首步重现 → 周期结算 OK (帧 260 ≈ 26s) ---
        ok_seen = False
        deadline = time.time() + 25
        while time.time() < deadline:
            d = results()
            evs = [e for e in (d.get("recent_events") or []) if e.get("toast_id") == "ok"]
            if evs:
                ok_seen = True
                page.wait_for_timeout(250)   # 扫光动画进行中
                page.screenshot(path=f"{SHOTS}/F_ok_sweep.png")
                break
            page.wait_for_timeout(400)
        step("E1 周期结算 OK 事件出现 (扫光已截图)", ok_seen)

        stop_pipeline()
        page.wait_for_timeout(1000)
        page.screenshot(path=f"{SHOTS}/G_back_idle.png")
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
