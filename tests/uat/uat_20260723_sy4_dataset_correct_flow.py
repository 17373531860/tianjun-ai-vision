# -*- coding: utf-8 -*-
"""UAT: SY4 (SY-重训v11 新模型 + 数据化调参) 用数据集"正确流程"视频验证周期结算。

视频: 2026-07-16 13-33-56 现场数据集录像的紧凑剪辑版 (212s, 60fps)
  = 原片 0~145s (完整周期1+2) + 625s~结尾 (完整周期3), 中间 ~8 分钟空转剪除。
每个周期均为标准正确流程: 贴标 → 套内袋 → 4盘×24滑块 → 放油嘴包 → 封箱 → 放工单。

期望: 合格总数 +3, 不良总数 +0, 防呆提示 +0 (正确流程绝不该报警)。

证据: /home/qianqian/uat_sy4_dataset/ (run.log + 截图)
"""
import json
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8001"
WEB = "http://localhost:6001"
OUT = Path("/home/qianqian/uat_sy4_dataset")
CH = 0
PROJECT_ID = 35  # SY4
MODEL = ("/home/qianqian/桌面/word/tianjun-main/backend/uploads/models/"
         "77cd8782355d461ba496aa94fa12ce8a_sy_v10_last.pt")
VIDEO = "/home/qianqian/2026-07-16_数据集正确流程_3周期紧凑版.mp4"
EXPECT_OK = 3
VIDEO_LEN_S = 213

_log_fh = None


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_fh:
        _log_fh.write(line + "\n")
        _log_fh.flush()


def api(method, path, **kw):
    r = getattr(requests, method)(f"{API}{path}", timeout=15, **kw)
    r.raise_for_status()
    return r.json()


def read_counters():
    r = requests.get(f"{API}/api/v1/source/detection/results?channel={CH}", timeout=10)
    if r.status_code != 200:
        return {}
    return r.json().get("counters") or {}


def apply_sy4_project():
    p = api("get", f"/api/v1/projects/{PROJECT_ID}")
    assert p["name"] == "SY4", f"项目 {PROJECT_ID} 不是 SY4: {p['name']}"
    mdl = p["pipeline_config"]["models"][0]
    assert mdl["model_id"] == 32, f"SY4 主模型不是 v11(32): {mdl}"
    api("post", f"/api/v1/source/detection/set-project?channel={CH}", json={
        "project_id": p["id"], "name": p["name"], "task_type": p["task_type"],
        "logic_mode": p["logic_mode"],
        "steps_config": p.get("steps_config") or [],
        "pipeline_config": p.get("pipeline_config") or {},
        "events_config": p.get("events_config") or [],
        "counters_config": p.get("counters_config") or [],
        "data_config": p.get("data_config") or {},
    })
    log(f"SY4 配置已推运行时: model={mdl['model_name']}")


def main():
    global _log_fh
    OUT.mkdir(parents=True, exist_ok=True)
    _log_fh = open(OUT / "run.log", "w", encoding="utf-8")
    assert Path(VIDEO).exists(), f"视频不存在: {VIDEO}"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})
        page = ctx.new_page()
        page.goto(f"{WEB}/#/monitor", wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)

        for ep in ("/api/v1/source/detection/stop", "/api/v1/source/video/stop"):
            try:
                requests.post(f"{API}{ep}?channel={CH}", timeout=10)
            except Exception:
                pass
        time.sleep(1.0)

        apply_sy4_project()
        api("post", f"/api/v1/source/video/start?channel={CH}",
            json={"file_path": VIDEO, "speed": 1.0})
        api("post", f"/api/v1/source/detection/start?channel={CH}",
            json={"model_path": MODEL, "session_name": "uat_sy4_dataset"})
        baseline = read_counters()
        log(f"检测已启动, 基线={json.dumps(baseline, ensure_ascii=False)}")
        page.screenshot(path=str(OUT / "00_start.png"), full_page=True)

        # 全程观察: 每 15s 记一次计数器轨迹, 顺手在每次合格+1时截图
        end = time.time() + VIDEO_LEN_S + 60
        last_ok = baseline.get("合格总数", 0)
        final = {}
        while time.time() < end:
            c = read_counters()
            final = c
            okd = c.get("合格总数", 0) - baseline.get("合格总数", 0)
            if c.get("合格总数", 0) > last_ok:
                last_ok = c.get("合格总数", 0)
                try:
                    page.screenshot(path=str(OUT / f"ok_{okd}.png"), full_page=True)
                except Exception:
                    pass
                log(f"  合格 +1 → 累计Δ{okd}  counters={json.dumps(c, ensure_ascii=False)}")
            if okd >= EXPECT_OK:
                break
            time.sleep(2)

        ok_delta = final.get("合格总数", 0) - baseline.get("合格总数", 0)
        ng_delta = final.get("不良总数", 0) - baseline.get("不良总数", 0)
        alarm_delta = final.get("防呆提示", 0) - baseline.get("防呆提示", 0)
        page.screenshot(path=str(OUT / "99_final.png"), full_page=True)

        requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/api/v1/source/video/stop?channel={CH}", timeout=10)
        ctx.close()
        browser.close()

    passed = ok_delta == EXPECT_OK and ng_delta == 0 and alarm_delta == 0
    log(f"最终: 合格Δ={ok_delta}(期望{EXPECT_OK}) 不良Δ={ng_delta}(期望0) "
        f"防呆Δ={alarm_delta}(期望0) → {'PASS' if passed else 'FAIL'}")
    log(f"证据目录: {OUT}")
    _log_fh.close()
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
