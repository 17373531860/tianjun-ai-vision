"""UAT v3.7.4 周期性强制动作 — 按时间触发 (time_interval_seconds).

客户场景:
  操作员配一条规则 "每 N 秒必须做一次清洁治具" (interval=0, time_interval_seconds=N),
  开始检测后正常生产几个工件, 然后**停下来不开工**, 等过 N 秒.
  期望: Monitor 卡片"周期性强制动作"由绿 → 黄 → 红 (超期), 弹超期事件 toast,
  点重置后立即归零回绿.

铁律 3 双向验证:
  - 在本分支 (feature/periodic-time-trigger) 跑此脚本: 必须 PASS
  - 在 main 分支 (cf5166f, 不含本次改动) 跑此脚本 Phase A: 必须 FAIL
    (因为后端不解析 time_interval_seconds, _check_periodic_actions_time_only
    方法不存在, time_state 字段不返回)

Phase A: 后端 API 契约 (无浏览器)
  A1: 创建带 time_interval_seconds=10 的项目 + activate + set-project
  A2: start synthetic + start detection → inference loop 跑起来
  A3: 立即查 status: time_state == 'ok', time_remaining_seconds 接近 10
  A4: 等 12 秒 → time_state == 'overdue', events_log 出现 source=periodic_action 的事件
  A5: 调 reset → counter=0, time_elapsed 归零, time_state 回 'ok'
  A6: 老格式兼容: 配纯 interval (time_interval_seconds 缺失) → 行为完全不变

Phase B: 可见浏览器 (headless=False + 视频)
  B1: 进 Project 页 → 看到"超时秒数"输入框存在
  B2: 进 Monitor 页 → 周期性强制动作卡片显示 "⏱ Xs / Ys" + 进度条变红
  B3: 点"重置"按钮 → UI 立即归零

Phase C: cleanup + 三件套证据
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, Page

API = "http://127.0.0.1:8001"
BASE = "http://127.0.0.1:6001"

SHOT_DIR = Path("/tmp/uat_pa_v374_shots")
VIDEO_DIR = Path("/tmp/uat_pa_v374_video")
LOG_PATH = Path("/tmp/uat_pa_v374_log/uat_run.log")

steps_log: list[dict] = []


def step(label: str, ok: bool, detail: str = "") -> None:
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": ok, "detail": detail}
    steps_log.append(rec)
    mark = "OK" if ok else "!!"
    print(f"[{mark}] {rec['idx']:02d}. {label}  {detail}")


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
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------- Phase A 工具 ----------
def stop_all(channel: int = 0) -> None:
    for url in (
        f"{API}/api/v1/source/detection/stop?channel={channel}",
        f"{API}/api/v1/test/synthetic/stop?channel={channel}",
    ):
        try:
            requests.post(url, timeout=5)
        except Exception:
            pass
    time.sleep(0.4)


def cleanup_uat_projects() -> None:
    try:
        r = requests.get(f"{API}/api/v1/projects", timeout=5)
        if r.status_code != 200:
            return
        items = r.json().get("items", [])
        for p in items:
            name = p.get("name") or ""
            if name.startswith("__uat_pa_"):
                requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=5)
    except Exception as e:
        print(f"  (cleanup fail: {e})")


def make_project_payload(name: str, *, interval: int, time_interval_seconds: int):
    """构造一个真项目, 含 1 条周期性强制动作规则 + 完整的 events_config."""
    return {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {
            "settlement_mode": "last_step",
            "sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}],
            "periodic_actions": [
                {
                    "id": f"pa_uat_{uuid.uuid4().hex[:6]}",
                    "name": "UAT-清洁治具",
                    "enabled": True,
                    "trigger_step_ids": [3],  # step_c 视为完成动作
                    "interval": interval,
                    "time_interval_seconds": time_interval_seconds,
                    "count_basis": "all",
                    "reset_policy": "always",
                    "due_warning_event_id": 70,
                    "overdue_event_id": 71,
                    "overdue_repeat": "every_cycle",
                    "channel_filter": None,
                    "run_on_start": False,
                }
            ],
        },
        "steps_config": [
            {"id": 1, "label": "step_a", "enabled": True, "threshold": 50, "min_frames": 1, "color": "#22c55e"},
            {"id": 2, "label": "step_b", "enabled": True, "threshold": 50, "min_frames": 1, "color": "#3b82f6"},
            {"id": 3, "label": "step_c", "enabled": True, "threshold": 50, "min_frames": 1, "color": "#f97316"},
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
            {"id": 70, "name": "UAT-保养到期", "color": "#f59e0b",
             "actions": [], "show_notification": True, "toast_id": "ng"},
            {"id": 71, "name": "UAT-保养超期", "color": "#ef4444",
             "actions": [], "show_notification": True, "toast_id": "ng"},
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


def create_activate_pushset(payload, channel: int = 0) -> int:
    r = requests.post(f"{API}/api/v1/projects", json=payload, timeout=10)
    assert r.status_code in (200, 201), f"create: {r.status_code} {r.text[:300]}"
    pid = r.json()["id"]
    r = requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=10)
    assert r.status_code == 200, f"activate: {r.status_code} {r.text[:300]}"
    body = {
        "project_id": pid,
        "name": payload["name"],
        "task_type": payload["task_type"],
        "logic_mode": payload["logic_mode"],
        "steps_config": payload["steps_config"],
        "pipeline_config": payload["pipeline_config"],
        "events_config": payload["events_config"],
        "counters_config": payload["counters_config"],
        "data_config": payload.get("data_config", {}),
    }
    r = requests.post(
        f"{API}/api/v1/source/detection/set-project?channel={channel}",
        json=body, timeout=10,
    )
    assert r.status_code == 200, f"set-project: {r.status_code} {r.text[:300]}"
    return pid


def start_synth_detection(channel: int = 0) -> None:
    """起 synthetic + 起 detection — 让 inference_loop 真转, 才会调 _check_periodic_actions_time_only."""
    stop_all(channel)
    # 用最简单的"什么都不检测"剧本, 让 cycle 不产生, 模拟"停工但仍在检测"
    scenario = {
        "name": "uat_idle",
        "fps": 60,
        "step_rois": {},
        "timeline": [{"from": 0, "to": 3600, "detections": []}],
    }
    body = {
        "channel": channel,
        "with_project": False,
        "logic_mode": "sequential",
        "scenario_json": scenario,
    }
    r = requests.post(f"{API}/api/v1/test/synthetic/start", json=body, timeout=10)
    assert r.status_code == 200, f"synth start: {r.status_code} {r.text[:300]}"
    r = requests.post(
        f"{API}/api/v1/source/detection/start?channel={channel}",
        json={"conf": 0.25, "iou": 0.45}, timeout=10,
    )
    assert r.status_code == 200, f"detect start: {r.status_code} {r.text[:300]}"


def get_status(channel: int = 0) -> dict:
    r = requests.get(f"{API}/api/v1/source/detection/results?channel={channel}", timeout=5)
    return r.json() if r.status_code == 200 else {}


def get_pa(channel: int = 0):
    res = get_status(channel)
    pa = res.get("periodic_actions") or []
    return pa[0] if pa else None


# ========================================================================
# Phase A: 后端 API 契约
# ========================================================================
def phase_a():
    print("\n========== Phase A: 后端 API 契约 ==========\n")

    # ─── A1: 项目创建 + 激活 + set-project ──────────────────
    name = f"__uat_pa_{uuid.uuid4().hex[:6]}"
    payload = make_project_payload(name, interval=0, time_interval_seconds=10)
    try:
        pid = create_activate_pushset(payload)
        step("A1 创建+激活+set-project (含 time_interval_seconds=10)", True, f"pid={pid}")
    except AssertionError as e:
        step("A1 创建+激活+set-project (含 time_interval_seconds=10)", False, str(e)[:200])
        return

    # ─── A2: 起 synthetic + detection ──────────────────────
    try:
        start_synth_detection()
        step("A2 启动 synthetic+detection (空剧本, 模拟停工)", True, "")
    except AssertionError as e:
        step("A2 启动 synthetic+detection (空剧本, 模拟停工)", False, str(e)[:200])
        return

    # ─── A3: 立即查 status: time_state=='ok' ───────────────
    time.sleep(1.5)
    pa = get_pa()
    a3_ok = (
        pa is not None
        and pa.get("time_interval_seconds") == 10
        and pa.get("time_state") == "ok"
        and 7 <= pa.get("time_remaining_seconds", -1) <= 10
    )
    step("A3 [刚起] time_state=='ok', remaining≈10s", a3_ok,
         f"pa={ {k: pa.get(k) for k in ('time_interval_seconds','time_state','time_remaining_seconds','time_elapsed_seconds')} if pa else None}")

    # ─── A4: 等到超期 ──────────────────────────────────────
    print("  ... 等待 14 秒让时间维度超期 (10s 阈值 + 5s throttle 容差) ...")
    time.sleep(14)
    pa = get_pa()
    res = get_status()
    # 路由名 recent_events (不是 events_log; 只返过去 30s 内的事件)
    events = res.get("recent_events") or []
    pa_events = [e for e in events if e.get("source") == "periodic_action"]
    a4_state_ok = pa is not None and pa.get("time_state") == "overdue"
    a4_event_ok = len(pa_events) >= 1
    step("A4 [等 14s] time_state=='overdue'",
         a4_state_ok,
         f"time_state={pa.get('time_state') if pa else None}, "
         f"time_elapsed={pa.get('time_elapsed_seconds') if pa else None}s, "
         f"time_remaining={pa.get('time_remaining_seconds') if pa else None}s")
    step("A5 recent_events 含 source=='periodic_action' 事件 (≥1)",
         a4_event_ok,
         f"matched_events={len(pa_events)} / total={len(events)}; "
         f"first_reason='{(pa_events[0].get('reason') or '')[:80] if pa_events else None}'")

    # ─── A6: reset ─────────────────────────────────────────
    rule_id = pa.get("id") if pa else None
    # API 入参用 query string (rule_id 是 Query, 不是 body)
    url = f"{API}/api/v1/source/detection/reset-periodic?channel=0"
    if rule_id:
        url += f"&rule_id={rule_id}"
    r = requests.post(url, timeout=10)
    a6_call_ok = r.status_code == 200
    time.sleep(1.0)
    pa = get_pa()
    a6_state_ok = (
        pa is not None
        and pa.get("counter") == 0
        and pa.get("time_state") == "ok"
        and pa.get("time_elapsed_seconds", 999) <= 2
    )
    step("A6 调 reset-periodic-action 后 time_state 回 'ok' + elapsed=0",
         a6_call_ok and a6_state_ok,
         f"http={r.status_code}, time_state={pa.get('time_state') if pa else None}, "
         f"elapsed={pa.get('time_elapsed_seconds') if pa else None}s")

    # ─── A7: 老项目兼容 (time_interval_seconds 缺失/为 0) ──
    stop_all()
    name2 = f"__uat_pa_{uuid.uuid4().hex[:6]}_legacy"
    payload2 = make_project_payload(name2, interval=20, time_interval_seconds=0)
    try:
        pid2 = create_activate_pushset(payload2)
        start_synth_detection()
        time.sleep(2.0)
        pa = get_pa()
        a7_ok = (
            pa is not None
            and pa.get("time_interval_seconds") == 0
            and pa.get("time_state") == "disabled"
            and pa.get("interval") == 20
            and pa.get("count_state") == "ok"
            and pa.get("state") == "ok"
        )
        step("A7 老项目兼容: interval=20, time_interval=0 → time_state='disabled'", a7_ok,
             f"pa={ {k: pa.get(k) for k in ('interval','count_state','time_interval_seconds','time_state','state')} if pa else None}")
    except AssertionError as e:
        step("A7 老项目兼容: interval=20, time_interval=0 → time_state='disabled'", False, str(e)[:200])

    stop_all()
    return pid


# ========================================================================
# Phase B: 可见浏览器
# ========================================================================
def phase_b():
    print("\n========== Phase B: 可见浏览器 (headless=False) ==========\n")

    # 先在后端起一个有 time_interval_seconds=10 的项目, 等待 phase B 走完后由 cleanup 删除
    name = f"__uat_pa_{uuid.uuid4().hex[:6]}_ui"
    payload = make_project_payload(name, interval=0, time_interval_seconds=10)
    try:
        pid = create_activate_pushset(payload)
        start_synth_detection()
    except AssertionError as e:
        step("B0 准备 UAT 项目失败", False, str(e)[:200])
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False, slow_mo=300,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": 1600, "height": 1000},
            ignore_https_errors=True,
        )
        page = ctx.new_page()

        try:
            # ─── B1: Project 页应有 "超时秒数" 输入框 ───────────
            # 前端用 hash router → /#/project; 持续 polling 让 networkidle 永远不到
            page.goto(f"{BASE}/#/project", wait_until="domcontentloaded")
            time.sleep(3.0)
            snap(page, "B1_project_page")

            # 选中我们的 UAT 项目 — 列表很长, 优先用搜索框过滤
            try:
                page.keyboard.press("Escape")  # 关掉可能弹出的 Navbar 下拉
                time.sleep(0.5)
                search_input = page.get_by_placeholder("搜索项目").first
                search_input.fill(name, timeout=3000)
                time.sleep(1.0)
                # 过滤后左侧只剩 1 个 .cursor-pointer 卡片, 用 has= 限定
                # (Navbar 顶部下拉的 trigger 不是 cursor-pointer, 这样能避开)
                card = page.locator("div.cursor-pointer", has=page.get_by_text(name, exact=True)).first
                card.scroll_into_view_if_needed(timeout=3000)
                card.click(timeout=3000)
                time.sleep(1.5)
            except Exception as e:
                print(f"  (cursor-pointer click 失败, 退而求其次用 nth=1: {e})")
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.3)
                    # nth=1 跳过 Navbar trigger 那个 visible 元素
                    page.locator(f"text={name}").nth(1).click(timeout=3000)
                    time.sleep(1.5)
                except Exception as e2:
                    print(f"  (二次尝试也失败: {e2})")
            snap(page, "B2_project_selected")

            # 右侧编辑面板默认在"基础设置"tab — 周期性强制动作卡片在"逻辑设置"tab
            try:
                page.get_by_role("tab", name="逻辑设置").click(timeout=3000)
                time.sleep(1.0)
                page.locator("text=周期性强制动作").first.scroll_into_view_if_needed(timeout=3000)
                time.sleep(0.5)
            except Exception as e:
                print(f"  (切到逻辑设置 tab 失败: {e})")
            body_txt = page.evaluate("document.body.innerText")
            has_label = "超时秒数" in body_txt
            step("B1 Project 页 (逻辑设置 tab) 有 '超时秒数' 输入框标签",
                 has_label,
                 f"body 文本含 '超时秒数': {has_label}")
            snap(page, "B3_project_periodic_card")

            # ─── B2: Monitor 页 → 周期性强制动作卡片 ───────────
            page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")
            time.sleep(3.0)
            snap(page, "B4_monitor_initial")
            body_txt = page.evaluate("document.body.innerText")
            has_card = "周期性强制动作" in body_txt
            has_time_unit = "/ 10s" in body_txt or "10s" in body_txt
            step("B2 Monitor 页周期性强制动作卡片显示 + 时间维度",
                 has_card and has_time_unit,
                 f"卡片={has_card}, 时间显示={has_time_unit}")

            # ─── B3: 等到超期, 看红色 ──────────────────────────
            print("  ... 等 14 秒看进度条变红 ...")
            time.sleep(14)
            snap(page, "B5_monitor_overdue")
            body_txt = page.evaluate("document.body.innerText")
            has_overdue = "超期" in body_txt
            step("B3 [等 14s] Monitor 显示 '超期' 文案",
                 has_overdue,
                 f"body 含 '超期': {has_overdue}")

            # ─── B4: 点 "全部重置" → 立即回绿 ──────────────────
            try:
                page.get_by_role("button", name="全部重置").click(timeout=3000)
                # ElementPlus MessageBox 二次确认
                page.get_by_role("dialog").get_by_role("button", name="全部重置").click(timeout=3000)
                time.sleep(2.0)
                snap(page, "B6_after_reset")
                body_txt = page.evaluate("document.body.innerText")
                still_overdue = "超期" in body_txt
                step("B4 点重置后 '超期' 字样消失", not still_overdue,
                     f"body 含 '超期': {still_overdue}")
            except Exception as e:
                step("B4 点重置 (可能没找到按钮)", False, str(e)[:200])

        finally:
            ctx.close()  # ★ 必须先 close ctx 才能 flush video
            browser.close()


# ========================================================================
# Phase C: 清理 + 报告
# ========================================================================
def phase_c():
    print("\n========== Phase C: 清理 + 报告 ==========\n")
    stop_all()
    cleanup_uat_projects()

    failed = sum(1 for s in steps_log if not s["ok"])
    summary = {
        "total": len(steps_log),
        "passed": len(steps_log) - failed,
        "failed": failed,
        "steps": steps_log,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n--- UAT 总结 ---")
    print(f"total: {summary['total']}, passed: {summary['passed']}, failed: {failed}")
    print(f"log:    {LOG_PATH}")
    print(f"shots:  {SHOT_DIR}")
    print(f"video:  {VIDEO_DIR}")
    return failed == 0


def main():
    reset_dirs()
    print(f"API={API}  BASE={BASE}")
    print(f"shots={SHOT_DIR}  video={VIDEO_DIR}  log={LOG_PATH}")
    try:
        phase_a()
        phase_b()
    finally:
        ok = phase_c()
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
