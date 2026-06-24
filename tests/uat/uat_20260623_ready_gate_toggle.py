"""加深启动就绪门槛 设置开关 E2E / 可见浏览器 UAT (v3.23.x)

验证设置页「加深启动就绪门槛」开关:
  1. 默认关 (API GET + UI 开关都为 off)。
  2. 点开关打开 → API 立即持久化为 true (落 workstation_config.json)。
  3. 刷新页面 → 开关读回保持 ON (loadStartupReadyGateConfig 生效)。
  4. 再点关 → API 回 false。

自管一个"鉴权关闭"的后端 (复制生产库到临时目录改 auth.enabled=false, 不污染真库)。
前端 dev (6001) 需脚本外部起好。

跑法:
  HEADLESS=1 python tests/uat/uat_20260623_ready_gate_toggle.py
证据: /tmp/uat_video/*.webm + /tmp/uat_shots/gate_*.png + /tmp/uat_gate_run.log
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time

import requests

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PY = os.environ.get("TIANJUN_PY", "/home/qianqian/anaconda3/envs/tianjun/bin/python")
API = "http://127.0.0.1:8001"
FRONT = "http://localhost:6001"
SHOTS = "/tmp/uat_shots"
VIDEO = "/tmp/uat_video"
LOG = "/tmp/uat_gate_run.log"
BK_DATA_DIR = "/tmp/uat_gate_data"
GATE = "/api/v1/workstations/startup-ready-gate"
HEADLESS = os.environ.get("HEADLESS", "1") not in ("0", "false", "no")

os.makedirs(SHOTS, exist_ok=True)
os.makedirs(VIDEO, exist_ok=True)
_steps = []


def step(label, ok, detail=""):
    _steps.append({"label": label, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else '!!'}] {len(_steps):02d}. {label}  {detail}")


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def wait_backend(up: bool, timeout: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            alive = requests.get(f"{API}/api/v1/source/status", timeout=2).status_code == 200
        except Exception:
            alive = False
        if alive == up:
            return True
        time.sleep(1)
    return False


def start_backend():
    f = open("/tmp/uat_gate_backend.log", "ab")
    return subprocess.Popen(
        [PY, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8001"],
        cwd=REPO, stdout=f, stderr=f, start_new_session=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "TIANJUN_DATA_DIR": BK_DATA_DIR},
    )


def stop_backend(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass
    wait_backend(up=False, timeout=20)


def gate_api() -> bool:
    return requests.get(f"{API}{GATE}", timeout=5).json().get("enabled")


def finish():
    failed = [s for s in _steps if not s["ok"]]
    with open(LOG, "w") as f:
        json.dump({"steps": _steps, "failed": len(failed)}, f, ensure_ascii=False, indent=2)
    print(f"\n==== ready-gate toggle UAT: {len(_steps)-len(failed)} ok / {len(failed)} failed ====")
    sys.exit(1 if failed else 0)


def main():
    if not port_open(6001):
        step("前置: 前端 dev 6001 已起", False, "请先 cd frontend && npm run dev"); finish(); return

    shutil.rmtree(BK_DATA_DIR, ignore_errors=True)
    os.makedirs(BK_DATA_DIR, exist_ok=True)
    prod_db = os.path.join(REPO, "backend", "sql_app.db")
    bk_db = os.path.join(BK_DATA_DIR, "sql_app.db")
    if os.path.exists(prod_db):
        shutil.copy2(prod_db, bk_db)
        c = sqlite3.connect(bk_db)
        c.execute("UPDATE system_configs SET value='false' WHERE key='auth.enabled'")
        c.commit(); c.close()
    step("前置: 临时库就绪(鉴权关)", os.path.exists(bk_db))

    proc = start_backend()
    if not wait_backend(up=True, timeout=120):
        step("后端就绪", False, "120s 超时"); stop_backend(proc); finish(); return
    step("后端就绪", True)
    # 已知起点: 强制关
    requests.put(f"{API}{GATE}", json={"enabled": False}, timeout=5)
    step("起点: API 门槛=false", gate_api() is False)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=0 if HEADLESS else 250)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  record_video_dir=VIDEO,
                                  record_video_size={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.goto(f"{FRONT}/#/settings", wait_until="domcontentloaded", timeout=20000)
        sw = page.locator("[data-testid=startup-ready-gate-switch]")
        sw.wait_for(state="visible", timeout=15000)
        sw.scroll_into_view_if_needed()
        time.sleep(0.5)
        page.screenshot(path=f"{SHOTS}/gate_1_initial.png", full_page=True)

        ui_off = "is-checked" not in (sw.get_attribute("class") or "")
        step("UI 初始开关为关 (与默认一致)", ui_off,
             f"class={sw.get_attribute('class')}")

        # 点开
        sw.click()
        time.sleep(1.0)
        page.screenshot(path=f"{SHOTS}/gate_2_turned_on.png", full_page=True)
        api_on = gate_api()
        step("点开后 API 持久化为 true", api_on is True, f"api={api_on}")
        ui_on = "is-checked" in (sw.get_attribute("class") or "")
        step("点开后 UI 开关为开", ui_on)

        # 刷新回读
        page.reload(wait_until="domcontentloaded", timeout=20000)
        sw2 = page.locator("[data-testid=startup-ready-gate-switch]")
        sw2.wait_for(state="visible", timeout=15000)
        sw2.scroll_into_view_if_needed()
        time.sleep(0.8)
        page.screenshot(path=f"{SHOTS}/gate_3_after_reload.png", full_page=True)
        ui_persist = "is-checked" in (sw2.get_attribute("class") or "")
        step("刷新后 UI 开关读回保持开 (持久化生效)", ui_persist,
             f"class={sw2.get_attribute('class')}")

        # 再点关
        sw2.click()
        time.sleep(1.0)
        api_off = gate_api()
        step("再点后 API 回 false", api_off is False, f"api={api_off}")

        ctx.close()
        browser.close()

    stop_backend(proc)
    finish()


if __name__ == "__main__":
    main()
