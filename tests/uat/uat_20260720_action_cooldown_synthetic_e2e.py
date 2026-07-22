# -*- coding: utf-8 -*-
"""UAT: 动作不应期端到端 (synthetic 剧本源 + 真前端 Monitor 反馈) — v3.43.1。

客户场景 (2026-07-20 上银客户机): 放托盘一次动作被断检拆成两个脉冲, 同一盘记两次账。
本脚本用虚拟剧本走完整后端 pipeline, 同时开真浏览器盯 Monitor 混合清点面板:

  阶段 1 (0-5.9s): 托盘+24滑块常驻, 放托盘动作出现→断检→余波再现→消失
      期望: 前端「已进箱滑块 24/96」— 余波脉冲被不应期吸收, 不是 48
  阶段 2 (9s 起): 第二次真动作 (距首次记账 >2s)
      期望: 前端变 48/96 — 正常节奏不被误伤

前置: 隔离栈 backend 8002 (RUNTIME_MODE=test, 新代码) + frontend 6002 (baseURL 钉 8002)。
剧本档案: tests/scenarios/tray_action_split_pulse.json
"""
import sys
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://localhost:8002"
WEB = "http://localhost:6002"
SHOT = "/tmp/uat_cooldown_e2e"
NAME = "__uat_tray_cooldown__"


def tray_frame_dets(with_action):
    dets = [{"label": "托盘", "confidence": 0.95, "bbox": [0.05, 0.20, 0.55, 0.60]}]
    for i in range(24):
        cx = 0.08 + (i % 8) * 0.065
        cy = 0.28 + (i // 8) * 0.17
        dets.append({"label": "滑块", "confidence": 0.95, "track_id": 100 + i,
                     "bbox": [cx, cy, 0.04, 0.06]})
    if with_action:
        dets.append({"label": "放托盘", "confidence": 0.95,
                     "bbox": [0.30, 0.10, 0.25, 0.30]})
    return dets


def build_scenario():
    idle, act = tray_frame_dets(False), tray_frame_dets(True)
    return {"name": "tray_action_split_pulse", "fps": 60, "timeline": [
        {"from": 0, "to": 299, "detections": idle},
        {"from": 300, "to": 311, "detections": act},    # 动作成立
        {"from": 312, "to": 339, "detections": idle},   # 脉冲1 → 记账 24
        {"from": 340, "to": 351, "detections": act},    # 断检余波 (<2s)
        {"from": 352, "to": 539, "detections": idle},   # 脉冲2 → 吸收, 保持 24
        {"from": 540, "to": 551, "detections": act},    # 正常第二次动作 (>2s)
        {"from": 552, "to": 720, "detections": idle},   # 脉冲3 → 记账 48
    ]}


def build_project():
    return {
        "name": NAME, "task_type": "detect", "logic_mode": "custom",
        "pipeline_config": {
            "custom_based_on": "sequential",
            "custom_mixed_with": "tracking",
            "custom_sequence_order": [{"step_id": "s1"}, {"step_id": "s2"}],
            "custom_mix_container_label": "托盘",
            "custom_mix_container_count_mode": "items_total",
            "custom_mix_container_item_target": 96,
            "custom_mix_container_gone_frames": 20,
            "custom_mix_container_confirm_by_frames": False,
            "custom_mix_container_confirm_by_action": True,
            "custom_mix_container_action_label": "放托盘",
        },
        "steps_config": [
            {"id": "s1", "label": "贴标", "enabled": True, "threshold": 0.3,
             "min_frames": 1, "disappear_delay": 0},
            {"id": "s2", "label": "封箱", "enabled": True, "threshold": 0.3,
             "min_frames": 1, "disappear_delay": 0},
            {"id": "s3", "label": "放托盘", "enabled": True, "threshold": 0.3,
             "min_frames": 2, "disappear_delay": 0},
            {"id": "s4", "label": "滑块", "enabled": True, "threshold": 0.3,
             "detect_role": "item", "count_mode": "track", "expected_count": 24},
        ],
        "events_config": [], "counters_config": [], "data_config": {},
    }


def api_item_total():
    r = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=5)
    c = ((r.json().get("custom_mix_state") or {}).get("container")) or {}
    return sum((c.get("item_total_done") or {}).values())


def wait_total(target, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if api_item_total() >= target:
            return True
        time.sleep(0.3)
    return False


def main():
    import os
    os.makedirs(SHOT, exist_ok=True)
    # 清同名残留项目
    for p in requests.get(f"{API}/api/v1/projects?limit=200", timeout=5).json().get("items", []):
        if p["name"] == NAME:
            requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=5)
    r = requests.post(f"{API}/api/v1/projects", json=build_project(), timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=15).raise_for_status()
    ok = True
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, slow_mo=50)
            page = browser.new_page(viewport={"width": 1600, "height": 950})
            page.goto(f"{WEB}/#/monitor", wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)

            requests.post(f"{API}/api/v1/test/synthetic/start", json={
                "scenario_json": build_scenario(), "channel": 0,
                "with_project": False}, timeout=10).raise_for_status()
            requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                          json={"conf": 0.25, "iou": 0.45}, timeout=10).raise_for_status()
            print("[UAT] 剧本已启动, 盯前端面板…")

            # ---- 阶段 1: 首次记账 24 ----
            assert wait_total(24, 25), "等待首次进箱 24 超时"
            time.sleep(3.0)                       # 盖住余波脉冲窗口
            t = api_item_total()
            assert t == 24, f"余波脉冲未被吸收: 已进箱 {t} (应 24)"
            body = page.evaluate("document.body.innerText")
            assert "已进箱" in body and "24" in body, "前端面板未显示已进箱 24"
            page.screenshot(path=f"{SHOT}/01_after_split_pulse_24.png", full_page=True)
            print("[UAT] 阶段1通过: 拆分脉冲被吸收, 前后端都是 24")

            # ---- 阶段 2: 正常第二次动作 → 48 ----
            assert wait_total(48, 25), "等待第二次进箱 48 超时"
            time.sleep(1.0)
            body = page.evaluate("document.body.innerText")
            assert "48" in body, "前端面板未显示 48"
            page.screenshot(path=f"{SHOT}/02_second_action_48.png", full_page=True)
            t = api_item_total()
            assert t == 48, f"最终进箱应 48, 实际 {t}"
            print("[UAT] 阶段2通过: 正常节奏第二次动作照常记账 48")
            browser.close()
    except Exception as e:
        ok = False
        print(f"[UAT] 失败: {e}")
    finally:
        requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=5)
        requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=5)
        requests.delete(f"{API}/api/v1/projects/{pid}", timeout=5)
    print("[UAT] 结果:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
