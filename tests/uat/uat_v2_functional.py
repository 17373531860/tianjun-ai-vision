"""V2 完整功能验证 UAT —— 不只看 UI，还要验业务契约。

Phase A: synthetic 正反对照验功能
  A1. ROI 内 (multi_model_roi_in.json)            → 三步 step_counts ≥ 1
  A2. ROI 外 (multi_model_roi_out.json)            → step_b_count == 0（同 bbox，仅 ROI 缩小）
  A3. 顺序模式 enforce  (reverse_order 自定义)     → 出 NG cycle，不出 OK
  A4. 计数器联动事件 (full project + ok_sequential)→ 合格总数计数器递增

Phase B: UI 真 CRUD（headed Chromium，桌面可见）
  B1. 点 "新建项目" → 输项目名 → 创建
  B2. 列表里能看到刚建的项目
  B3. 点中它，"删除" 按钮可点 → 删除 → 列表里消失

Phase C: 综合可视报告 + cleanup
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, Page

API = "http://127.0.0.1:8011"
BASE = "http://127.0.0.1:6011"

SHOT_DIR = Path("/tmp/uat_v2_shots")
VIDEO_DIR = Path("/tmp/uat_v2_video")

steps_log: list[dict] = []


def step(label: str, ok: bool, detail: str = "") -> None:
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": ok, "detail": detail}
    steps_log.append(rec)
    print(f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}")


def snap(page: Page, name: str) -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"{len(steps_log):02d}_{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception as e:
        print(f"  (snapshot fail: {e})")


def reset_dirs() -> None:
    for d in (SHOT_DIR, VIDEO_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)


# ========================================================================
# Phase A 工具
# ========================================================================
def stop_all_synth(channel: int = 0) -> None:
    try:
        requests.post(f"{API}/api/v1/source/detection/stop?channel={channel}", timeout=5)
    except Exception:
        pass
    try:
        requests.post(f"{API}/api/v1/test/synthetic/stop?channel={channel}", timeout=5)
    except Exception:
        pass
    try:
        requests.post(f"{API}/api/v1/source/detection/reset-stats?channel={channel}", timeout=5)
    except Exception:
        pass
    time.sleep(0.4)


def start_synth(scenario: str | None = None,
                scenario_json: dict | None = None,
                with_project: bool = True,
                logic_mode: str = "sequential") -> None:
    stop_all_synth()
    body: dict = {"channel": 0, "with_project": with_project, "logic_mode": logic_mode}
    if scenario_json is not None:
        body["scenario_json"] = scenario_json
    elif scenario is not None:
        body["scenario"] = scenario
    r = requests.post(f"{API}/api/v1/test/synthetic/start", json=body, timeout=10)
    assert r.status_code == 200, f"synth start: {r.status_code} {r.text[:300]}"
    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=10)
    assert r.status_code == 200, f"detect start: {r.status_code} {r.text[:300]}"


def poll_results(timeout_s: float = 8.0, interval: float = 0.1) -> list[dict]:
    """采样这段时间里的 detection results（用于看 step_counts 演变）。"""
    out = []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=4)
            if r.status_code == 200:
                out.append(r.json())
        except Exception:
            pass
        time.sleep(interval)
    return out


def latest_result() -> dict:
    r = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=4)
    return r.json() if r.status_code == 200 else {}


def reverse_order_scenario() -> dict:
    """反序剧本：step_c → step_b → step_a。Sequential 应判 NG（顺序错误）。"""
    return {
        "name": "reverse_order_uat",
        "fps": 60,
        "step_rois": {},
        "timeline": [
            {"from": 0, "to": 9, "detections": []},
            {"from": 10, "to": 39, "detections": [
                {"label": "step_c", "confidence": 0.95, "bbox": [0.10, 0.10, 0.20, 0.20]}
            ]},
            {"from": 40, "to": 49, "detections": []},
            {"from": 50, "to": 79, "detections": [
                {"label": "step_b", "confidence": 0.95, "bbox": [0.10, 0.10, 0.20, 0.20]}
            ]},
            {"from": 80, "to": 89, "detections": []},
            {"from": 90, "to": 119, "detections": [
                {"label": "step_a", "confidence": 0.95, "bbox": [0.10, 0.10, 0.20, 0.20]}
            ]},
            {"from": 120, "to": 240, "detections": []},
        ],
    }


def full_project_payload(name: str) -> dict:
    return {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {
            "settlement_mode": "last_step",
            "sequence_order": [
                {"step_id": 1},
                {"step_id": 2},
                {"step_id": 3},
            ],
        },
        "steps_config": [
            {"id": 1, "label": "step_a", "enabled": True, "threshold": 50,
             "min_frames": 1, "color": "#22c55e"},
            {"id": 2, "label": "step_b", "enabled": True, "threshold": 50,
             "min_frames": 1, "color": "#3b82f6"},
            {"id": 3, "label": "step_c", "enabled": True, "threshold": 50,
             "min_frames": 1, "color": "#f97316"},
        ],
        "events_config": [
            {"id": 1, "name": "合格(OK)", "color": "#10b981",
             "actions": [{"counter_name": "合格总数", "delta": 1},
                         {"counter_name": "总产量", "delta": 1}],
             "show_notification": False, "toast_id": "ok"},
            {"id": 2, "name": "不良(NG)", "color": "#ef4444",
             "actions": [{"counter_name": "不良总数", "delta": 1},
                         {"counter_name": "总产量", "delta": 1}],
             "show_notification": False, "toast_id": "ng"},
        ],
        "counters_config": [
            {"name": "合格总数", "value": 0},
            {"name": "不良总数", "value": 0},
            {"name": "总产量", "value": 0},
        ],
        "alarm_config": {"enabled": False, "triggers": {}},
        "detection_config": {},
        "data_config": {},
    }


def create_and_activate_project(payload: dict) -> int:
    r = requests.post(f"{API}/api/v1/projects", json=payload, timeout=10)
    assert r.status_code in (200, 201), f"create: {r.status_code} {r.text[:300]}"
    pid = r.json()["id"]
    r = requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=10)
    assert r.status_code == 200, f"activate: {r.status_code} {r.text[:300]}"
    return pid


def push_full_config_to_mgr(pid: int, payload: dict, channel: int = 0) -> None:
    """activate 只重载模型；events_config/counters_config 必须经 set-project 才进 mgr.project_config。"""
    body = {
        "project_id": pid,
        "name": payload["name"],
        "task_type": payload.get("task_type", "detection"),
        "logic_mode": payload.get("logic_mode", "sequential"),
        "steps_config": payload.get("steps_config", []),
        "pipeline_config": payload.get("pipeline_config", {}),
        "events_config": payload.get("events_config", []),
        "counters_config": payload.get("counters_config", []),
        "data_config": payload.get("data_config", {}),
    }
    r = requests.post(f"{API}/api/v1/source/detection/set-project?channel={channel}",
                      json=body, timeout=10)
    assert r.status_code == 200, f"set-project: {r.status_code} {r.text[:300]}"


def cleanup_uat_projects() -> None:
    try:
        r = requests.get(f"{API}/api/v1/projects", timeout=5)
        if r.status_code == 200:
            for p in r.json().get("items", r.json()) if isinstance(r.json(), (list, dict)) else []:
                name = p.get("name") or ""
                if name.startswith("__uat_"):
                    requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=5)
    except Exception:
        pass


# ========================================================================
# Phase A 测试
# ========================================================================
def phase_a():
    print("\n========== Phase A: 功能契约验证 ==========\n")

    # ─── A1: ROI 内 → 三步全 ≥1 ────────────────────────────
    start_synth(scenario="multi_model_roi_in.json", with_project=True)
    samples = poll_results(timeout_s=6.0)
    final = samples[-1] if samples else {}
    sc = final.get("step_counts") or {}
    a_in = sc.get("step_a", 0) >= 1
    b_in = sc.get("step_b", 0) >= 1
    c_in = sc.get("step_c", 0) >= 1
    step("A1 [ROI 内]：三步 step_counts 均 ≥ 1",
         a_in and b_in and c_in,
         f"step_counts={sc}")

    # ─── A2: ROI 外 → step_b 应 == 0 ────────────────────────
    start_synth(scenario="multi_model_roi_out.json", with_project=True)
    samples = poll_results(timeout_s=6.0)
    final = samples[-1] if samples else {}
    sc = final.get("step_counts") or {}
    a_out_ok = sc.get("step_a", 0) >= 1
    b_blocked = sc.get("step_b", 0) == 0
    c_out_ok = sc.get("step_c", 0) >= 1
    step("A2 [ROI 外]：同一 bbox，缩小 step_b 的 ROI → step_b 计数恒为 0",
         a_out_ok and b_blocked and c_out_ok,
         f"step_counts={sc}")

    # ─── A3: 反序剧本 → 顺序模式 enforce ──────────────────
    # 必须用真 project（events_config 含 id=1/2），否则 _trigger_event 找不到事件不写日志
    stop_all_synth()
    payload_a3 = full_project_payload(f"__uat_v2_a3_{uuid.uuid4().hex[:6]}")
    pid_a3 = create_and_activate_project(payload_a3)
    push_full_config_to_mgr(pid_a3, payload_a3)
    start_synth(scenario_json=reverse_order_scenario(), with_project=False,
                logic_mode="sequential")
    deadline = time.time() + 10.0
    last_a3 = {}
    while time.time() < deadline:
        r = latest_result()
        if r:
            last_a3 = r
            ctr = r.get("counters") or {}
            if (ctr.get("不良总数") or 0) >= 1:
                break
        time.sleep(0.15)
    ctr_a3 = last_a3.get("counters") or {}
    rec_a3 = [e.get("event_name") or e.get("event_id") for e in last_a3.get("recent_events") or []]
    ng_cycles_a3 = last_a3.get("ng_step_cycle_counts") or {}
    ng_seen = (ctr_a3.get("不良总数") or 0) >= 1 or bool(ng_cycles_a3)
    ok_not_seen = (ctr_a3.get("合格总数") or 0) == 0
    step("A3 [反序剧本]：sequential 模式判 NG（合格总数=0，不良总数≥1）",
         ng_seen and ok_not_seen,
         f"counters={ctr_a3} ng_step_cycle_counts={ng_cycles_a3} recent={rec_a3}")
    stop_all_synth()
    try:
        requests.delete(f"{API}/api/v1/projects/{pid_a3}", timeout=5)
    except Exception:
        pass

    # ─── A4: 完整 events_config + 计数器 (顺序剧本 → 合格总数++) ────────
    payload_a4 = full_project_payload(f"__uat_v2_a4_{uuid.uuid4().hex[:6]}")
    pid_a4 = create_and_activate_project(payload_a4)
    push_full_config_to_mgr(pid_a4, payload_a4)
    start_synth(scenario="ok_sequential_cycle.json", with_project=False,
                logic_mode="sequential")
    # 启动后立刻看 mgr 上的 project_config 是否仍带 events_config + sequence_order
    try:
        diag_r = requests.get(f"{API}/api/v1/source/detection/results?channel=0",
                              timeout=4).json()
        # 这个端点不直接吐 project_config。换个办法：跑一个 hidden GET（如果有就更好；没有就看 step_counts/counters 名单）
        print(f"  [A4 启动后诊断] counters keys = {list((diag_r.get('counters') or {}).keys())}; "
              f"is_detecting = {diag_r.get('is_detecting')}; source_type = {diag_r.get('source_type')}")
    except Exception as e:
        print(f"  [A4 诊断失败]: {e}")
    deadline = time.time() + 15.0
    last_a4 = {}
    sample_history = []
    while time.time() < deadline:
        r = latest_result()
        if r:
            last_a4 = r
            ctr = r.get("counters") or {}
            sc = r.get("step_counts") or {}
            sample_history.append({
                "t": round(time.time(), 2),
                "step_counts": sc,
                "counters": {k: ctr.get(k) for k in ("合格总数", "不良总数", "总产量")},
                "recent": [e.get("event_name") for e in r.get("recent_events") or []],
            })
            if (ctr.get("合格总数") or 0) >= 1:
                break
        time.sleep(0.2)
    ctr_a4 = last_a4.get("counters") or {}
    ok_inc = (ctr_a4.get("合格总数") or 0) >= 1
    tot_inc = (ctr_a4.get("总产量") or 0) >= 1
    rec_a4 = [e.get("event_name") for e in last_a4.get("recent_events") or []]
    sc_a4 = last_a4.get("step_counts") or {}
    print(f"  [A4 诊断] sample_history (last 3): {sample_history[-3:] if sample_history else []}")
    step("A4 [事件→计数器]：合格 OK 周期触发 → 合格总数 / 总产量 同步 +1",
         ok_inc and tot_inc,
         f"counters={ctr_a4} step_counts={sc_a4} recent_events={rec_a4}")
    stop_all_synth()
    try:
        requests.delete(f"{API}/api/v1/projects/{pid_a4}", timeout=5)
    except Exception:
        pass


# ========================================================================
# Phase B: UI CRUD（可见浏览器）
# ========================================================================
def phase_b(page: Page) -> None:
    print("\n========== Phase B: UI 真 CRUD ==========\n")

    page.goto(f"{BASE}/#/project", wait_until="domcontentloaded", timeout=20000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(800)

    new_name = f"__uat_ui_{uuid.uuid4().hex[:6]}"

    # B1: 点 "新建项目"
    try:
        page.locator("button:has-text('新建项目')").first.click(timeout=5000)
        page.wait_for_timeout(500)
        dialog = page.locator(".el-dialog:visible", has_text="新建项目").first
        dialog.wait_for(state="visible", timeout=5000)
        # 输项目名
        name_input = dialog.locator("input").first
        name_input.fill(new_name)
        page.wait_for_timeout(200)
        snap(page, "B1_new_project_dialog_filled")
        # 点 "创建"
        dialog.locator("button:has-text('创建')").first.click(timeout=5000)
        page.wait_for_timeout(800)
        step("B1 UI 点'新建项目' → 填名 → 点'创建'", True, f"name={new_name}")
    except Exception as e:
        step("B1 新建项目对话框", False, str(e)[:150])
        return

    # B2: 列表里能看到
    try:
        page.wait_for_timeout(800)
        body = page.locator("body").inner_text(timeout=5000)
        ok = new_name in body
        snap(page, "B2_project_list_after_create")
        step("B2 列表里出现新建的项目", ok, f"in_list={ok}")
    except Exception as e:
        step("B2 检查项目列表", False, str(e)[:150])
        return

    # B3: 删除（先选中它，然后右上角"删除"）
    try:
        # 点击新建的项目卡片（用 has 容器，避免点 label 不冒泡）
        card = page.locator("div.cursor-pointer", has=page.get_by_text(new_name, exact=True)).first
        card.wait_for(state="visible", timeout=5000)
        card.click(timeout=5000)
        page.wait_for_timeout(500)
        snap(page, "B3a_project_selected")

        # 点击右上角 "删除"
        page.locator("button:has-text('删除')").first.click(timeout=5000)
        page.wait_for_timeout(500)
        # 弹出确认对话框
        msgbox = page.locator(".el-message-box:visible").first
        msgbox.wait_for(state="visible", timeout=5000)
        snap(page, "B3b_confirm_delete_dialog")
        # 确认按钮（"删除"红色按钮在 ElMessageBox 里）
        msgbox.locator("button:has-text('删除')").first.click(timeout=5000)
        page.wait_for_timeout(1500)

        # 验证消失
        body = page.locator("body").inner_text(timeout=5000)
        gone = new_name not in body
        snap(page, "B3c_after_delete")
        step("B3 删除项目 → 列表中消失", gone, f"removed={gone}")
    except Exception as e:
        step("B3 删除项目", False, str(e)[:150])


# ========================================================================
# 入口
# ========================================================================
def main() -> int:
    reset_dirs()

    # 等服务起来
    for _ in range(40):
        try:
            r = requests.get(f"{API}/api/v1/system/version", timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    for _ in range(30):
        try:
            r = requests.get(f"{BASE}/", timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)

    try:
        # Phase A 走 API
        phase_a()

        # Phase B 走 UI（可见 + 录像）
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False,
                                         args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(
                viewport={"width": 1366, "height": 800},
                record_video_dir=str(VIDEO_DIR),
                record_video_size={"width": 1366, "height": 800},
            )
            page = ctx.new_page()
            phase_b(page)
            time.sleep(1)
            ctx.close()
            browser.close()
    finally:
        cleanup_uat_projects()
        stop_all_synth()

    failed = sum(1 for s in steps_log if not s["ok"])
    print(f"\n=== UAT V2 总结 ===")
    print(f"步骤数 {len(steps_log)} / 通过 {len(steps_log)-failed} / 失败 {failed}")
    print(f"截图: {SHOT_DIR}")
    print(f"视频: {VIDEO_DIR}")
    Path("/tmp/uat_v2_run.log").write_text(
        json.dumps({"steps": steps_log, "failed": failed},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
