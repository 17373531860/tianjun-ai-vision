"""双工位 synthetic 前端实测: 两通道都跑虚拟检测 → 可见浏览器看双工位都在动."""
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


def loop_scenario(name, cycles=40):
    """持续 a→b→c 循环, fps=10, 每步 1.5s, 让前端看到检测框 + 周期在结算."""
    tl = []
    f = 0
    for _ in range(cycles):
        for lab in ["step_a", "step_b", "step_c"]:
            tl.append({"from": f, "to": f + 15,
                       "detections": [{"label": lab, "confidence": 0.92,
                                       "bbox": [0.32, 0.30, 0.22, 0.24]}]})
            f += 16
            tl.append({"from": f, "to": f + 3, "detections": []})
            f += 4
    return {"name": name, "fps": 10, "timeline": tl}


requests.post(f"{B}/workstations/mode", json={"channel_count": 2, "channels": []}, timeout=15)
for ch in (0, 1):
    requests.post(f"{B}/source/detection/stop?channel={ch}")
    requests.post(f"{B}/test/synthetic/stop?channel={ch}")
time.sleep(1.5)
for ch in (0, 1):
    requests.post(f"{B}/test/synthetic/start",
                  json={"scenario_json": loop_scenario(f"dual_ch{ch}"), "with_project": True, "channel": ch}, timeout=15)
    requests.post(f"{B}/source/detection/start?channel={ch}",
                  json={"conf": 0.25, "iou": 0.45}, timeout=15)
step("双通道 synthetic + detection 已启动", True)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu",
                                               "--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(viewport={"width": 1600, "height": 1000},
                        record_video_dir="/tmp/uat_jinlong/video")
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

    time.sleep(5)
    pg.screenshot(path=f"{SHOTS}/D1_dual_running.png", full_page=True)
    r0 = requests.get(f"{B}/source/detection/results?channel=0").json()
    r1 = requests.get(f"{B}/source/detection/results?channel=1").json()
    step("双工位都在检测中", r0.get("is_detecting") and r1.get("is_detecting"),
         f"ch0={r0.get('is_detecting')} ch1={r1.get('is_detecting')}")
    sc0 = r0.get("step_counts") or {}
    sc1 = r1.get("step_counts") or {}
    step("双工位步骤统计都在动", sum(sc0.values()) > 0 and sum(sc1.values()) > 0,
         f"ch0={sc0} ch1={sc1}")

    time.sleep(7)
    pg.screenshot(path=f"{SHOTS}/D2_dual_stats.png", full_page=True)
    body = pg.evaluate("document.body.innerText")
    step("前端双工位布局含两组工位数据",
         ("工位 1" in body or "工位1" in body) and ("工位 2" in body or "工位2" in body))
    # 权威统计双工位
    ls = requests.get(f"{B}/plugins/internal-demo/durations/live-stats").json().get("channels", {})
    step("插件权威统计两通道都有数据", "0" in ls and "1" in ls, str(ls))

    ctx.close()
    b.close()

for ch in (0, 1):
    requests.post(f"{B}/source/detection/stop?channel={ch}")
    requests.post(f"{B}/test/synthetic/stop?channel={ch}")
failed = [x for x in logs if not x[1]]
print(f"\nfailed: {len(failed)}", flush=True)
