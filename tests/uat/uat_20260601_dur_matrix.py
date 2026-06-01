"""三档「开关组合 × 多段不同数据」完整功能矩阵 UAT.

每行 = 一种配置组合 + 一段虚拟数据 → 验证预期(OK/NG + 报警).
靠控制 step_b 持续帧数(fps=10)精确控制步骤时长触发不同档.

矩阵:
  M1 正常区          en=T 1/4/6   b=2.5s  → OK   无报警
  M2 未达最短        en=T 1/4/6   b=0.5s  → NG   未达最短
  M3 警告区          en=T 1/4/6   b=4.5s  → OK   仅超警告
  M4 超最长          en=T 1/4/6   b=7s    → NG   超最长
  M5 关开关+太短      en=F 1/4/6   b=0.5s  → OK   无报警(全跳过)
  M6 关开关+太长      en=F 1/4/6   b=7s    → OK   无报警(全跳过)
  M7 最短档归零+太短   en=T 0/4/6   b=0.5s  → OK   无未达最短
  M8 最长档归零+太长   en=T 1/4/0   b=7s    → OK   仅超警告(不超最长)
  M9 警告档归零+警告区  en=T 1/0/6   b=4.5s  → OK   无报警(警告不响,未超最长)
  M10 步骤级覆盖       en=T default0/0/0 step_b min3  b=2.5s → NG 未达最短(2.5<3覆盖)
"""
import os
import sys
import time
import json
import sqlite3
import requests

API = "http://127.0.0.1:8011"
DB = "/tmp/uat_jinlong_data/sql_app.db"
FPS = 10
logs = []


def step(label, ok, detail=""):
    logs.append({"label": label, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else '!!'}] {label}  {detail}", flush=True)


def seg(frm, to, label=None):
    dets = [{"label": label, "confidence": 0.95, "bbox": [0.3, 0.3, 0.2, 0.2]}] if label else []
    return {"from": frm, "to": to, "detections": dets}


def make_scenario(name, hold_b_frames):
    tl = [seg(0, 4)]
    f = 5
    tl.append(seg(f, f + 24, "step_a")); f += 25
    tl.append(seg(f, f + 4)); f += 5
    tl.append(seg(f, f + hold_b_frames - 1, "step_b")); f += hold_b_frames
    tl.append(seg(f, f + 4)); f += 5
    tl.append(seg(f, f + 24, "step_c")); f += 25
    tl.append(seg(f, f + 9)); f += 10
    tl.append(seg(f, f + 24, "step_a")); f += 25
    tl.append(seg(f, f + 60))
    return {"name": name, "fps": FPS, "timeline": tl}


PROJECT_ID = None


def setup_project():
    global PROJECT_ID
    steps = [
        {"id": 1, "label": "step_a", "threshold": 0.3, "min_frames": 1, "color": "#1976d2", "enabled": True},
        {"id": 2, "label": "step_b", "threshold": 0.3, "min_frames": 1, "color": "#1976d2", "enabled": True},
        {"id": 3, "label": "step_c", "threshold": 0.3, "min_frames": 1, "color": "#1976d2", "enabled": True},
    ]
    payload = {
        "name": f"__uat_matrix_{int(time.time())}",
        "task_type": "detect", "logic_mode": "sequential",
        "steps_config": steps,
        "pipeline_config": {"sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}],
                            "settlement_mode": "first_step"},
        "events_config": [{"id": 1, "name": "OK", "actions": [], "show_notification": True},
                          {"id": 2, "name": "NG", "actions": [], "show_notification": True}],
    }
    r = requests.post(f"{API}/api/v1/projects", json=payload, timeout=15)
    PROJECT_ID = r.json().get("id")
    requests.post(f"{API}/api/v1/projects/{PROJECT_ID}/activate", timeout=15)
    return PROJECT_ID


def set_cfg(enabled, default_tier, steps=None):
    cfg = {"enabled": enabled, "default": default_tier, "steps": steps or {},
           "alarm_event": {"warn": "event2", "ng": "event2"}}
    requests.put(f"{API}/api/v1/plugins/internal-demo/durations/step-durations", json=cfg, timeout=10)


def audit_max_id():
    c = sqlite3.connect(DB)
    try:
        return c.execute("select coalesce(max(id),0) from plugin_audit_log").fetchone()[0]
    finally:
        c.close()


def alarms_since(aid):
    c = sqlite3.connect(DB)
    try:
        rows = c.execute("select message from plugin_audit_log where id>? and action='trigger_alarm' order by id",
                         (aid,)).fetchall()
        return [r[0] for r in rows]
    finally:
        c.close()


def complete_cycle(sid):
    c = sqlite3.connect(DB)
    try:
        rows = c.execute("select id,is_good,result_reason,step_sequence from detection_cycles "
                         "where session_id=? order by id", (sid,)).fetchall()
        for cid, good, reason, seq in rows:
            try:
                labels = set(json.loads(seq) if seq else [])
            except Exception:
                labels = set()
            if {"step_a", "step_b", "step_c"}.issubset(labels):
                return cid, good, reason
        return None
    finally:
        c.close()


def run_one(tag, b_frames):
    wait = max(14, int(b_frames / FPS) + 11)
    aid0 = audit_max_id()
    requests.post(f"{API}/api/v1/test/synthetic/start",
                  json={"scenario_json": make_scenario(tag, b_frames), "with_project": False}, timeout=15)
    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=15).json()
    sid = r.get("session_id")
    time.sleep(wait)
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)
    time.sleep(1)
    return sid, complete_cycle(sid), alarms_since(aid0)


def check(tag, cfg_fn, b_frames, expect_good, contains=(), absent=()):
    cfg_fn()
    sid, cyc, alarms = run_one(tag, b_frames)
    if cyc is None:
        step(tag, False, f"无完整周期 sid={sid} alarms={alarms}")
        return
    good_ok = (cyc[1] == 1) == (expect_good)
    c_ok = all(any(k in m for m in alarms) for k in contains)
    a_ok = all(not any(k in m for m in alarms) for k in absent)
    ok = good_ok and c_ok and a_ok
    exp = "OK" if expect_good else "NG"
    step(tag, ok, f"得到{'OK' if cyc[1] else 'NG'}(期望{exp}) alarms={alarms}")


def main():
    setup_project()
    print("\n===== 三档 开关组合 × 多段数据 完整矩阵 =====", flush=True)
    T = lambda mn, wn, mx: {"min_sec": mn, "warn_sec": wn, "max_sec": mx}

    check("M1 正常区(2.5s) en=T 1/4/6 →OK无报警",
          lambda: set_cfg(True, T(1, 4, 6)), 25, True, absent=["未达最短", "超过警告", "超过最长"])
    check("M2 未达最短(0.5s) en=T 1/4/6 →NG",
          lambda: set_cfg(True, T(1, 4, 6)), 5, False, contains=["未达最短"])
    check("M3 警告区(4.5s) en=T 1/4/6 →OK仅警告",
          lambda: set_cfg(True, T(1, 4, 6)), 45, True, contains=["超过警告"], absent=["超过最长", "未达最短"])
    check("M4 超最长(7s) en=T 1/4/6 →NG",
          lambda: set_cfg(True, T(1, 4, 6)), 70, False, contains=["超过最长"])
    check("M5 关开关+太短(0.5s) en=F →OK无报警",
          lambda: set_cfg(False, T(1, 4, 6)), 5, True, absent=["未达最短", "超过警告", "超过最长"])
    check("M6 关开关+太长(7s) en=F →OK无报警",
          lambda: set_cfg(False, T(1, 4, 6)), 70, True, absent=["未达最短", "超过警告", "超过最长"])
    check("M7 最短档归零+太短(0.5s) 0/4/6 →OK",
          lambda: set_cfg(True, T(0, 4, 6)), 5, True, absent=["未达最短"])
    check("M8 最长档归零+太长(7s) 1/4/0 →OK仅警告",
          lambda: set_cfg(True, T(1, 4, 0)), 70, True, contains=["超过警告"], absent=["超过最长"])
    check("M9 警告档归零+警告区(4.5s) 1/0/6 →OK无报警",
          lambda: set_cfg(True, T(1, 0, 6)), 45, True, absent=["超过警告", "超过最长", "未达最短"])
    check("M10 步骤级覆盖 step_b min3 +2.5s →NG",
          lambda: set_cfg(True, T(0, 0, 0), steps={"step_b": T(3, 0, 0)}), 25, False, contains=["未达最短"])

    failed = [r for r in logs if not r["ok"]]
    print(f"\nfailed: {len(failed)}/{len(logs)}", flush=True)
    with open("/tmp/uat_jinlong/matrix.log", "w") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
