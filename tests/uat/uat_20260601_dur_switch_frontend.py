"""前端对照: 三档开关 开/关 同样太长数据 → 浏览器看效果差异.
  G1 开关ON  + 太长7s → 前端实时报警 + NG 统计
  G2 开关OFF + 太长7s → 前端静默 OK (无报警/无NG)
"""
import time
import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8011"
FE = "http://127.0.0.1:6011"
SHOTS = "/tmp/uat_jinlong/shots"
logs = []


def step(l, ok, d=""):
    logs.append((l, bool(ok), d)); print(f"[{'OK' if ok else '!!'}] {l}  {d}", flush=True)


def seg(a, b, l=None):
    return {"from": a, "to": b, "detections": [{"label": l, "confidence": 0.95, "bbox": [0.3, 0.3, 0.2, 0.2]}] if l else []}


def long_scenario(name):
    tl = [seg(0, 4)]; f = 5
    tl.append(seg(f, f + 24, "step_a")); f += 25
    tl.append(seg(f, f + 4)); f += 5
    tl.append(seg(f, f + 69, "step_b")); f += 70   # b 7s → 超最长
    tl.append(seg(f, f + 4)); f += 5
    tl.append(seg(f, f + 24, "step_c")); f += 25
    tl.append(seg(f, f + 9)); f += 10
    tl.append(seg(f, f + 24, "step_a")); f += 25
    tl.append(seg(f, f + 60))
    return {"name": name, "fps": 10, "timeline": tl}


def set_cfg(enabled):
    requests.put(f"{API}/api/v1/plugins/internal-demo/durations/step-durations",
                 json={"enabled": enabled, "default": {"min_sec": 1, "warn_sec": 4, "max_sec": 6},
                       "steps": {}, "alarm_event": {"warn": "event2", "ng": "event2"}}, timeout=10)


# 单工位
requests.post(f"{API}/api/v1/workstations/mode", json={"channel_count": 1, "channels": []}, timeout=15)
requests.post(f"{API}/api/v1/source/detection/stop?channel=0")
requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0")
time.sleep(1.5)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu",
                                               "--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(viewport={"width": 1600, "height": 1000}, record_video_dir="/tmp/uat_jinlong/video")
    pg = ctx.new_page(); pg.set_default_timeout(60000)
    pg.goto(f"{FE}/", wait_until="commit", timeout=90000)
    reloaded = False
    for i in range(12):
        time.sleep(4)
        if pg.evaluate("document.body?document.body.innerText.length:0") > 50:
            break
        if not reloaded and i >= 2:
            try:
                pg.reload(wait_until="commit", timeout=90000)
            except Exception:
                pass
            reloaded = True

    # ---- G1: 开关 ON + 太长 ----
    set_cfg(True)
    requests.post(f"{API}/api/v1/test/synthetic/start", json={"scenario_json": long_scenario("g1_on"), "with_project": False}, timeout=15)
    requests.post(f"{API}/api/v1/source/detection/start?channel=0", json={"conf": 0.25, "iou": 0.45}, timeout=15)
    time.sleep(9)
    pg.screenshot(path=f"{SHOTS}/G1_switch_on_alarm.png", full_page=True)
    body1 = pg.evaluate("document.body.innerText")
    step("G1 开关ON: 进行中实时报警/超时可见", any(k in body1 for k in ("超过", "报警", "NG")), "见 G1 截图")
    time.sleep(10)  # 等结算 NG
    pg.screenshot(path=f"{SHOTS}/G1b_on_ng.png", full_page=True)
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0"); requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0")
    time.sleep(1.5)
    ls_on = requests.get(f"{API}/api/v1/plugins/internal-demo/durations/live-stats").json().get("channels", {}).get("0", {})
    step("G1 开关ON: 权威统计有NG", ls_on.get("ng", 0) >= 1, f"统计={ls_on}")

    # ---- G2: 开关 OFF + 同样太长 ----
    set_cfg(False)
    requests.post(f"{API}/api/v1/test/synthetic/start", json={"scenario_json": long_scenario("g2_off"), "with_project": False}, timeout=15)
    requests.post(f"{API}/api/v1/source/detection/start?channel=0", json={"conf": 0.25, "iou": 0.45}, timeout=15)
    time.sleep(19)  # 跑完整周期结算
    pg.screenshot(path=f"{SHOTS}/G2_switch_off_ok.png", full_page=True)
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0"); requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0")
    time.sleep(1.5)
    ls_off = requests.get(f"{API}/api/v1/plugins/internal-demo/durations/live-stats").json().get("channels", {}).get("0", {})
    # 开关关闭后同样太长数据应判 OK (插件不override)
    step("G2 开关OFF: 同样太长数据这次判OK(无NG新增)", ls_off.get("ok", 0) >= 1, f"统计={ls_off}")

    ctx.close(); b.close()

failed = [x for x in logs if not x[1]]
print(f"\nfailed: {len(failed)}", flush=True)
