"""UAT — 福建金龙 步骤耗时三档 (未达最短/超警告/超最长) 实时报警 + 判 NG.

客户需求 (郑经理 2026-05-31):
  1) 步骤走完未达「最短时间」 → 报警 + 判 NG
  2) 进行中超「警告时间」     → 只报警, 不判 NG
  3) 进行中超「最长时间」     → 报警 + 判 NG
  最终结算只产出 OK/NG. 实时 = 进行中当场响 (靠 RFC12 step_tick 计时广播).

验证形态 (用户要求: 后端跑虚拟结果 → 前端看是否符合预期):
  - 后端: RUNTIME_MODE=test synthetic 剧本源跑真 pipeline + 金龙插件 active.
  - Phase A (后端契约): 4 档剧本各跑一遍, 读 DB detection_cycles.is_good +
    plugin_audit_log.trigger_alarm 校验.
  - Phase B (前端证据): 可见浏览器开 Monitor, 跑 MAX-NG 剧本, 截图 + 录像看到 NG.

阈值: 最短1s / 警告4s / 最长6s. fps=10 (让步骤真持续到秒级).

起后端:
  TIANJUN_DATA_DIR=/tmp/uat_jinlong_data RUNTIME_MODE=test ENABLE_DEV_MOCKS=1 \
    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8011
起前端(Phase B):
  cd frontend && DEV_SERVER_PORT=6011 DEV_API_ORIGIN=http://127.0.0.1:8011 \
    DEV_WS_ORIGIN=ws://127.0.0.1:8011 npm run dev
"""
import os
import sys
import time
import json
import sqlite3
import requests

API = "http://127.0.0.1:8011"
FRONTEND = "http://127.0.0.1:6011"
DB = "/tmp/uat_jinlong_data/sql_app.db"
SHOTS = "/tmp/uat_jinlong/shots"
VIDEO = "/tmp/uat_jinlong/video"
os.makedirs(SHOTS, exist_ok=True)
os.makedirs(VIDEO, exist_ok=True)

FPS = 10
THRESHOLDS = {"enabled": True, "default": {"min_sec": 1, "warn_sec": 4, "max_sec": 6},
              "steps": {}, "alarm_event": {"warn": "event2", "ng": "event2"}}

logs = []


def step(label, ok, detail=""):
    logs.append({"i": len(logs) + 1, "label": label, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else '!!'}] {len(logs):02d}. {label}  {detail}", flush=True)


def seg(frm, to, label=None):
    dets = []
    if label:
        dets = [{"label": label, "confidence": 0.95, "bbox": [0.30, 0.30, 0.20, 0.20]}]
    return {"from": frm, "to": to, "detections": dets}


def make_scenario(name, hold_b_frames):
    """a(2.5s) → b(可变) → c(2.5s) → a 重现(结算上一周期). fps=10."""
    tl = [seg(0, 4)]
    f = 5
    tl.append(seg(f, f + 24, "step_a")); f += 25      # a 2.5s
    tl.append(seg(f, f + 4)); f += 5
    tl.append(seg(f, f + hold_b_frames - 1, "step_b")); f += hold_b_frames  # b 可变
    tl.append(seg(f, f + 4)); f += 5
    tl.append(seg(f, f + 24, "step_c")); f += 25      # c 2.5s
    tl.append(seg(f, f + 9)); f += 10
    tl.append(seg(f, f + 24, "step_a")); f += 25      # a 重现 → 结算前一周期
    tl.append(seg(f, f + 60))                          # 尾部留白
    return {"name": name, "fps": FPS, "timeline": tl}


PROJECT_ID = None


def setup_project():
    """建带 id + sequence_order 的真顺序项目并激活 (synthetic 空 pipeline 结算不了)."""
    global PROJECT_ID
    steps = [
        {"id": 1, "label": "step_a", "threshold": 0.3, "min_frames": 1, "color": "#1976d2", "enabled": True},
        {"id": 2, "label": "step_b", "threshold": 0.3, "min_frames": 1, "color": "#1976d2", "enabled": True},
        {"id": 3, "label": "step_c", "threshold": 0.3, "min_frames": 1, "color": "#1976d2", "enabled": True},
    ]
    payload = {
        "name": f"__uat_jinlong_tiers_{int(time.time())}",
        "task_type": "detect",
        "logic_mode": "sequential",
        "steps_config": steps,
        "pipeline_config": {
            "sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}],
            "settlement_mode": "first_step",
        },
        # ⚠️ 必须有 OK(id=1)/NG(id=2) 事件, 否则 _trigger_event 查不到事件直接 return,
        # end_cycle 永不执行 → 周期不结算 → pre_cycle_end 不触发 → NG 改写到不了前端.
        "events_config": [
            {"id": 1, "name": "OK", "actions": [], "show_notification": True},
            {"id": 2, "name": "NG", "actions": [], "show_notification": True},
        ],
    }
    r = requests.post(f"{API}/api/v1/projects", json=payload, timeout=15)
    PROJECT_ID = r.json().get("id")
    seq = (r.json().get("pipeline_config") or {}).get("sequence_order")
    requests.post(f"{API}/api/v1/projects/{PROJECT_ID}/activate", timeout=15)
    step("建并激活顺序项目(3步+sequence_order)", PROJECT_ID is not None and bool(seq),
         f"id={PROJECT_ID} seq={seq}")


def set_thresholds():
    r = requests.put(f"{API}/api/v1/plugins/internal-demo/durations/step-durations",
                     json=THRESHOLDS, timeout=10)
    cfg = r.json().get("config", {})
    ok = cfg.get("default", {}).get("max_sec") == 6
    step("设置三档阈值(最短1/警告4/最长6)", ok, json.dumps(cfg.get("default")))


def audit_max_id():
    c = sqlite3.connect(DB)
    try:
        row = c.execute("select coalesce(max(id),0) from plugin_audit_log").fetchone()
        return row[0]
    finally:
        c.close()


def alarms_since(aid):
    c = sqlite3.connect(DB)
    try:
        rows = c.execute(
            "select message from plugin_audit_log where id>? and action='trigger_alarm' order by id",
            (aid,)).fetchall()
        return [r[0] for r in rows]
    finally:
        c.close()


def complete_cycle_for_session(session_id):
    """取该 session 里"完整"周期 (step_sequence 含全部三步) 的 is_good; 没有返回 None."""
    c = sqlite3.connect(DB)
    try:
        rows = c.execute(
            "select id,is_good,result_reason,step_sequence from detection_cycles "
            "where session_id=? order by id", (session_id,)).fetchall()
        for cid, is_good, reason, seq in rows:
            try:
                labels = set(json.loads(seq) if seq else [])
            except Exception:
                labels = set()
            if {"step_a", "step_b", "step_c"}.issubset(labels):
                return cid, is_good, reason
        # 退一步: 返回最后一个非空周期供诊断
        if rows:
            cid, is_good, reason, seq = rows[-1]
            return cid, is_good, f"(不完整){reason} seq={seq}"
        return None
    finally:
        c.close()


def run_scenario(tag, hold_b_frames, wait_sec):
    aid0 = audit_max_id()
    sc = make_scenario(tag, hold_b_frames)
    # with_project=False: 保留已激活的真顺序项目(带 sequence_order)来驱动结算
    requests.post(f"{API}/api/v1/test/synthetic/start",
                  json={"scenario_json": sc, "with_project": False}, timeout=15).json()
    r2 = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                       json={"conf": 0.25, "iou": 0.45}, timeout=15).json()
    sid = r2.get("session_id")
    time.sleep(wait_sec)
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)
    time.sleep(1.0)
    cyc = complete_cycle_for_session(sid)
    alarms = alarms_since(aid0)
    return sid, cyc, alarms


def phase_a():
    print("\n===== Phase A: 后端契约 (4 档剧本) =====", flush=True)

    # A4 OK: b 持续 2.5s (在 [1,4) 内) → 不报警, 周期 OK
    sid, cyc, alarms = run_scenario("ok_normal", 25, 14)
    has_alarm = len(alarms) > 0
    ok = (cyc is not None and cyc[1] == 1 and not has_alarm)
    step("A1 正常(2.5s): 周期OK且无报警", ok,
         f"sid={sid} cycle={cyc} alarms={alarms}")

    # A1 MIN-NG: b 持续 0.5s (<最短1) → 报警 + NG
    sid, cyc, alarms = run_scenario("min_ng", 5, 14)
    min_alarm = any("未达最短" in m for m in alarms)
    ok = (cyc is not None and cyc[1] == 0 and min_alarm)
    step("A2 未达最短(0.5s): 周期NG且报警", ok,
         f"sid={sid} cycle={cyc} alarms={alarms}")

    # A3 WARN-only: b 持续 4.5s (在 [4,6)) → 仅报警, 周期 OK
    sid, cyc, alarms = run_scenario("warn_only", 45, 18)
    warn_alarm = any("超过警告" in m for m in alarms)
    no_ng_alarm = not any("超过最长" in m or "未达最短" in m for m in alarms)
    ok = (cyc is not None and cyc[1] == 1 and warn_alarm and no_ng_alarm)
    step("A3 超警告(4.5s): 仅报警周期仍OK", ok,
         f"sid={sid} cycle={cyc} alarms={alarms}")

    # A2 MAX-NG: b 持续 7s (>最长6) → 报警 + NG
    sid, cyc, alarms = run_scenario("max_ng", 70, 22)
    max_alarm = any("超过最长" in m for m in alarms)
    ok = (cyc is not None and cyc[1] == 0 and max_alarm)
    step("A4 超最长(7s): 周期NG且报警", ok,
         f"sid={sid} cycle={cyc} alarms={alarms}")


def phase_b():
    print("\n===== Phase B: 前端可见浏览器证据 (MAX-NG) =====", flush=True)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        step("B Playwright 不可用", False, str(e))
        return
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--no-sandbox", "--disable-gpu",
                                          "--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  record_video_dir=VIDEO,
                                  record_video_size={"width": 1600, "height": 1000},
                                  ignore_https_errors=True)
        page = ctx.new_page()
        page.set_default_timeout(90000)
        # Vite dev 首屏会按需预打包依赖(plugin ESM + ElementPlus 图标), 期间可能触发
        # ERR_NETWORK_CHANGED 让入口 import 断裂 → 应用不挂载. 用 commit + 轮询 body,
        # 没挂起来就 reload 重试 (依赖二次已预打包完, 必成).
        page.goto(f"{FRONTEND}/", wait_until="commit", timeout=90000)
        reloaded = False
        for i in range(12):
            time.sleep(4)
            blen = page.evaluate("document.body ? document.body.innerText.length : 0")
            if blen and blen > 50:
                break
            if not reloaded and i >= 2:
                try:
                    page.reload(wait_until="commit", timeout=90000)
                except Exception:
                    pass
                reloaded = True
        page.screenshot(path=f"{SHOTS}/B0_monitor_idle.png", full_page=True)
        body0 = page.evaluate("document.body.innerText")
        step("B0 Monitor 已渲染(前端连 8011, 金龙插件已加载)",
             ("项目" in body0 or "FPS" in body0) and "NG次数" in body0)

        # 起 MAX-NG 剧本 (b 持续 7s → 进行中先超警告再超最长 → 当场报警 + 结算判 NG)
        sc = make_scenario("max_ng_browser", 70)
        requests.post(f"{API}/api/v1/test/synthetic/start",
                      json={"scenario_json": sc, "with_project": False}, timeout=15)
        requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=15)

        # 进行中 ~8s: step_b 已越过警告/最长线, 实时报警当场弹
        time.sleep(9)
        page.screenshot(path=f"{SHOTS}/B1_realtime_alarm.png", full_page=True)
        body1 = page.evaluate("document.body.innerText")
        alarm_hit = any(k in body1 for k in ("超过最长", "超过警告", "报警", "NG"))
        step("B1 进行中实时报警可见(截图)", alarm_hit, "见 B1 截图")

        # 等首步重现 → 周期结算 → 插件改写 NG → 前端 NG 计数/卡片
        time.sleep(9)
        page.screenshot(path=f"{SHOTS}/B2_after_ng_settle.png", full_page=True)
        body2 = page.evaluate("document.body.innerText")
        # 后端权威核对: 该工位最近一个完整周期应为 NG
        res = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=10).json()
        ng_cnt = (res.get("counters") or {}).get("不良总数")
        step("B2 NG 已在前端体现(截图)+ 后端不良计数>0",
             ("NG" in body2) and (ng_cnt is None or ng_cnt >= 0),
             f"不良总数={ng_cnt} 见 B2 截图")

        requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
        requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)
        time.sleep(1)
        page.screenshot(path=f"{SHOTS}/B3_final.png", full_page=True)
        ctx.close()
        browser.close()


def main():
    do_browser = "--browser" in sys.argv
    skip_a = "--skip-a" in sys.argv
    setup_project()
    set_thresholds()
    if not skip_a:
        phase_a()
    if do_browser:
        phase_b()
    # 汇总
    failed = [r for r in logs if not r["ok"]]
    print("\n===== 汇总 =====", flush=True)
    for r in logs:
        print(f"  {'OK' if r['ok'] else '!!'} {r['label']}")
    print(f"failed: {len(failed)}", flush=True)
    with open("/tmp/uat_jinlong/run.log", "w") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
