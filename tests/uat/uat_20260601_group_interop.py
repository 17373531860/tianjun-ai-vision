"""D 工位组互通端到端: synchronized_any_ng「A站NG→B站联动NG」前端效果.

先红后绿对照:
  Phase 1 (无组):     ch1 跑单个正序 OK 周期 → 应判 OK (good=1)
  Phase 2 (建组any_ng): ch0 持续 NG + ch1 同样正序周期 → ch1 被联动改 NG (good=0)
"""
import time
import requests
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8011/api/v1"
FE = "http://127.0.0.1:6011"
SHOTS = "/tmp/uat_jinlong/shots"
logs = []


def step(l, ok, d=""):
    logs.append((l, bool(ok), d))
    print(f"[{'OK' if ok else '!!'}] {l}  {d}", flush=True)


def seg(frm, to, label=None):
    dets = [{"label": label, "confidence": 0.95, "bbox": [0.3, 0.3, 0.2, 0.2]}] if label else []
    return {"from": frm, "to": to, "detections": dets}


def loop_ng(name, cycles=30):
    """连续 a→b→c 循环 → 周期边界顺序错位 → sequential 判 NG."""
    tl = []
    f = 0
    for _ in range(cycles):
        for lab in ["step_a", "step_b", "step_c"]:
            tl.append(seg(f, f + 12, lab)); f += 13
            tl.append(seg(f, f + 2)); f += 3
    return {"name": name, "fps": 10, "timeline": tl}


def single_ok(name):
    """单个正序 a→b→c→留白→a重现(结算上一周期为OK)→长留白."""
    tl = [seg(0, 4)]
    f = 5
    for lab in ["step_a", "step_b", "step_c"]:
        tl.append(seg(f, f + 19, lab)); f += 20
        tl.append(seg(f, f + 4)); f += 5
    tl.append(seg(f, f + 24, "step_a")); f += 25   # a 重现 → 结算前一正序周期
    tl.append(seg(f, f + 120))                     # 长留白保持
    return {"name": name, "fps": 10, "timeline": tl}


def db_last_cycle(ch):
    import sqlite3
    c = sqlite3.connect("/tmp/uat_jinlong_data/sql_app.db")
    try:
        row = c.execute("""select dc.id,dc.is_good,dc.result_reason from detection_cycles dc
            join detection_sessions ds on dc.session_id=ds.id
            where ds.channel_id=? and dc.end_time is not null and dc.step_sequence like '%step_a%step_b%step_c%'
            order by dc.id desc limit 1""", (ch,)).fetchone()
        return row
    finally:
        c.close()


def stop_all():
    for ch in (0, 1):
        requests.post(f"{B}/source/detection/stop?channel={ch}")
        requests.post(f"{B}/test/synthetic/stop?channel={ch}")


def del_all_groups():
    gs = requests.get(f"{B}/channel-groups").json()
    items = gs if isinstance(gs, list) else gs.get("items", [])
    for g in items:
        requests.put(f"{B}/channel-groups/{g['id']}", json={"enabled": False})
        requests.delete(f"{B}/channel-groups/{g['id']}")


# 准备: 关三档, 2工位, 清组, 清残留
requests.put(f"{B}/plugins/internal-demo/durations/step-durations",
             json={"enabled": False, "default": {"min_sec": 0, "warn_sec": 0, "max_sec": 0},
                   "steps": {}, "alarm_event": {"warn": "event2", "ng": "event2"}}, timeout=10)
requests.post(f"{B}/workstations/mode", json={"channel_count": 2, "channels": []}, timeout=15)
del_all_groups()
stop_all()
time.sleep(1.5)

# ---------- Phase 1: 无组, ch1 正序周期 → OK ----------
print("\n===== Phase 1: 无组对照 (ch1 正序应 OK) =====", flush=True)
requests.post(f"{B}/test/synthetic/start",
              json={"scenario_json": single_ok("p1_ch1"), "with_project": True, "channel": 1}, timeout=15)
requests.post(f"{B}/source/detection/start?channel=1", json={"conf": 0.25, "iou": 0.45}, timeout=15)
time.sleep(14)
stop_all()
time.sleep(1)
row = db_last_cycle(1)
step("Phase1 无组: ch1 正序周期判 OK", row is not None and row[1] == 1, f"cycle={row}")

# ---------- Phase 2: 建组 synchronized_any_ng, ch0 NG → ch1 联动 NG ----------
print("\n===== Phase 2: 建组 synchronized_any_ng (ch0 NG → ch1 联动 NG) =====", flush=True)
requests.post(f"{B}/channel-groups", headers={"Content-Type": "application/json"},
              json={"name": "__uat_interop", "member_channel_ids": [0, 1],
                    "settle_strategy": "synchronized_any_ng", "timeout_ms": 8000}, timeout=15)
step("建工位组 synchronized_any_ng [0,1]", True)
time.sleep(1)

# ch0 先持续跑 NG 循环 (不断给 ch1 设 pending NG)
requests.post(f"{B}/test/synthetic/start",
              json={"scenario_json": loop_ng("p2_ch0"), "with_project": True, "channel": 0}, timeout=15)
requests.post(f"{B}/source/detection/start?channel=0", json={"conf": 0.25, "iou": 0.45}, timeout=15)
time.sleep(6)  # 让 ch0 先产生 NG 周期 → pending(ch1)=NG
row0 = db_last_cycle(0)
step("Phase2 ch0 持续判 NG", row0 is not None and row0[1] == 0, f"ch0 cycle={row0}")

# ch1 跑同样正序周期 (本来该 OK), 结算时消费 pending → 被联动 NG
requests.post(f"{B}/test/synthetic/start",
              json={"scenario_json": single_ok("p2_ch1"), "with_project": True, "channel": 1}, timeout=15)
requests.post(f"{B}/source/detection/start?channel=1", json={"conf": 0.25, "iou": 0.45}, timeout=15)

# 浏览器看双工位都 NG 的效果
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu",
                                               "--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(viewport={"width": 1600, "height": 1000}, record_video_dir="/tmp/uat_jinlong/video")
    pg = ctx.new_page()
    pg.set_default_timeout(60000)
    pg.goto(f"{FE}/", wait_until="commit", timeout=90000)
    reloaded = False
    for i in range(12):
        time.sleep(4)
        blen = pg.evaluate("document.body ? document.body.innerText.length : 0")
        if blen and blen > 50:
            break
        if not reloaded and i >= 2:
            try:
                pg.reload(wait_until="commit", timeout=90000)
            except Exception:
                pass
            reloaded = True
    time.sleep(6)
    pg.screenshot(path=f"{SHOTS}/E_group_interop.png", full_page=True)
    ctx.close()
    b.close()

stop_all()
time.sleep(1)
row1 = db_last_cycle(1)
# ch1 正序周期本应 OK, 被工位组联动改 NG
linked = row1 is not None and row1[1] == 0
step("Phase2 ch1 正序周期被联动改 NG (对照Phase1的OK)", linked, f"ch1 cycle={row1}")

del_all_groups()
failed = [x for x in logs if not x[1]]
print(f"\nfailed: {len(failed)}", flush=True)
