"""冷启动首屏重试 E2E / 可见浏览器 UAT (v3.23.x)

客户现场: 开机后启动动画放完进主界面, 项目页空白; 后端几十秒后才冷启动就绪;
期望后端一就绪项目列表自动补齐, 不用手动点进项目页 / 不用刷新。

本脚本自管后端启停时序, 真复现"冷启动竞态":
  1. 先起后端 → 确保至少有一个唯一名 __e2e_coldstart_* 项目 → 停后端 (DB 留种子)。
  2. 前端 dev (6001) 已起 (脚本外部起好); 后端 DOWN 状态下浏览器打开项目页 → 断言列表里看不到种子项目 (= 客户症状: 空)。
  3. 不碰浏览器, 起后端 8001 → 在重试窗口内轮询页面 (不刷新), 断言种子项目自动出现 (= 修复生效: axios 冷启动重试补齐首屏)。

证据三件套: /tmp/uat_video/*.webm + /tmp/uat_shots/cold_*.png + /tmp/uat_run.log。

跑法:
  # 前端先起 (脚本不管前端): cd frontend && npm run dev
  HEADLESS=1 python tests/uat/uat_20260623_cold_start_retry.py
  # 想肉眼看: HEADLESS=0 (需要 DISPLAY)

red 对照 (证明测试盯住了 bug): 把 frontend/src/api/index.js 重试块临时注释掉,
vite HMR 重载后再跑本脚本 → 第 3 步应 FAIL (后端起来了列表仍空, 需手动刷新)。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid

import requests

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PY = os.environ.get("TIANJUN_PY", "/home/qianqian/anaconda3/envs/tianjun/bin/python")
API = "http://127.0.0.1:8001"
FRONT = "http://localhost:6001"
SHOTS = "/tmp/uat_shots"
VIDEO = "/tmp/uat_video"
LOG = "/tmp/uat_run.log"
# 给后端用独立临时数据目录: 全新库 → 鉴权默认关 (POST /projects 匿名可建) + 不污染真 dev 库
BK_DATA_DIR = "/tmp/uat_coldstart_data"
HEADLESS = os.environ.get("HEADLESS", "1") not in ("0", "false", "no")

os.makedirs(SHOTS, exist_ok=True)
os.makedirs(VIDEO, exist_ok=True)

_steps = []


def step(label, ok, detail=""):
    _steps.append({"idx": len(_steps) + 1, "label": label, "ok": bool(ok), "detail": detail})
    print(f"[{'OK' if ok else '!!'}] {len(_steps):02d}. {label}  {detail}")


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def wait_backend(up: bool, timeout: float) -> bool:
    """轮询后端 /source/status, up=True 等到就绪, up=False 等到彻底下线。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{API}/api/v1/source/status", timeout=2)
            alive = r.status_code == 200
        except Exception:
            alive = False
        if alive == up:
            return True
        time.sleep(1)
    return False


def start_backend() -> subprocess.Popen:
    f = open("/tmp/uat_backend.log", "ab")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8001"],
        cwd=REPO, stdout=f, stderr=f, start_new_session=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "TIANJUN_DATA_DIR": BK_DATA_DIR},
    )
    return proc


def stop_backend(proc: subprocess.Popen):
    import signal
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass
    wait_backend(up=False, timeout=20)


def main():
    if not port_open(6001):
        step("前置: 前端 dev 6001 已起", False, "请先 cd frontend && npm run dev")
        finish()
        return

    # 准备后端数据目录: 复制生产库到临时目录并关鉴权 (不污染真库 + 让 POST/GET 匿名可用)。
    # 后端见到非空 sql_app.db 会跳过 _migrate_old_data 的生产库覆盖, 用我这份关了鉴权的副本。
    import shutil
    import sqlite3
    shutil.rmtree(BK_DATA_DIR, ignore_errors=True)
    os.makedirs(BK_DATA_DIR, exist_ok=True)
    prod_db = os.path.join(REPO, "backend", "sql_app.db")
    bk_db = os.path.join(BK_DATA_DIR, "sql_app.db")
    if os.path.exists(prod_db):
        shutil.copy2(prod_db, bk_db)
        try:
            c = sqlite3.connect(bk_db)
            c.execute("UPDATE system_configs SET value='false' WHERE key='auth.enabled'")
            c.commit(); c.close()
        except Exception as e:
            step("前置: 临时库关鉴权", False, str(e)[:150]); finish(); return
    step("前置: 临时库就绪(鉴权关)", os.path.exists(bk_db))

    seed_name = f"__e2e_coldstart_{uuid.uuid4().hex[:8]}"

    # ---------- 阶段 0: 起后端, 种一个唯一名项目, 再停后端 ----------
    proc = start_backend()
    if not wait_backend(up=True, timeout=120):
        step("阶段0: 后端起来 (种数据用)", False, "120s 内未就绪")
        stop_backend(proc)
        finish()
        return
    step("阶段0: 后端起来 (种数据用)", True)
    try:
        payload = {
            "name": seed_name, "task_type": "detection", "logic_mode": "sequential",
            "pipeline_config": {}, "steps_config": [{"id": 1, "label": "step_a", "name": "步骤A", "enabled": True}],
            "events_config": [], "counters_config": [], "alarm_config": {},
            "detection_config": {}, "data_config": {},
        }
        r = requests.post(f"{API}/api/v1/projects", json=payload, timeout=10)
        r.raise_for_status()
        seed_id = r.json().get("id")
        step("阶段0: 种子项目已建", True, f"{seed_name} id={seed_id}")
    except Exception as e:
        step("阶段0: 种子项目已建", False, str(e)[:200])
        stop_backend(proc)
        finish()
        return
    stop_backend(proc)
    step("阶段0: 后端已停 (进入冷启动前状态)", not port_open(8001))

    # ---------- 阶段 1+2: 浏览器 ----------
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=0 if HEADLESS else 200)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 1000},
            ignore_https_errors=True,
        )
        page = ctx.new_page()
        console_logs = []
        page.on("console", lambda m: console_logs.append(f"{m.type}: {m.text}"[:200]))

        # 阶段 1: 后端 DOWN 下打开项目页 → 列表应看不到种子项目 (空)
        page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded", timeout=20000)
        time.sleep(4)  # 让首屏请求发出并撞上"后端没起"
        page.screenshot(path=f"{SHOTS}/cold_1_backend_down.png", full_page=True)
        down_visible = page.get_by_text(seed_name, exact=False).count()
        step("阶段1: 后端 DOWN 时项目列表为空(看不到种子项目)", down_visible == 0,
             f"种子可见数={down_visible}")
        retry_logged = any("API Retry" in c or "Network" in c for c in console_logs)
        step("阶段1: 控制台出现网络错误/重试迹象", retry_logged,
             f"(诊断, 不阻断) sample={[c for c in console_logs if 'Retry' in c or 'Network' in c][:2]}")

        # 阶段 2: 起后端, 不碰浏览器, 轮询页面种子项目自动出现
        proc2 = start_backend()
        if not wait_backend(up=True, timeout=120):
            step("阶段2: 后端恢复就绪", False, "120s 内未就绪")
            ctx.close(); browser.close(); stop_backend(proc2); finish(); return
        step("阶段2: 后端恢复就绪", True)

        appeared = False
        t0 = time.time()
        while time.time() - t0 < 60:  # 重试窗口内 (180s 预算, 这里 60s 足够)
            if page.get_by_text(seed_name, exact=False).count() > 0:
                appeared = True
                break
            time.sleep(2)
        page.screenshot(path=f"{SHOTS}/cold_2_backend_up_autofilled.png", full_page=True)
        step("阶段2: 后端就绪后列表自动补齐种子项目(未手动刷新)", appeared,
             f"用时≈{time.time()-t0:.0f}s")

        ctx.close()  # flush video
        browser.close()

        # ---------- 清理种子项目 ----------
        try:
            requests.delete(f"{API}/api/v1/projects/{seed_id}", timeout=10)
        except Exception:
            pass
        stop_backend(proc2)

    finish()


def finish():
    failed = [s for s in _steps if not s["ok"] and "诊断" not in s["detail"]]
    with open(LOG, "w") as f:
        json.dump({"steps": _steps, "failed": len(failed)}, f, ensure_ascii=False, indent=2)
    print(f"\n==== cold-start retry UAT: {len(_steps)-len(failed)} ok / {len(failed)} failed ====")
    print(f"视频: {VIDEO}/  截图: {SHOTS}/cold_*.png  日志: {LOG}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
