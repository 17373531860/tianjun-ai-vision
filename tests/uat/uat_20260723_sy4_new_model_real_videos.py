# -*- coding: utf-8 -*-
"""UAT: SY4 项目 (SY-重训v11 新模型 + 数据化调参) 用上银 7-21 三段真实视频跑真 pipeline。

模型: last.pt (2026-07-22 训, 模型库 id=32 'SY-重训v11', imgsz960)
调参依据: /home/qianqian/model_eval_sy4/ 离线批量推理 —
  放油嘴包/封箱 阈值 60 + 最少帧数 3 (真事件段长>=5帧/峰值0.79+, 误检只 1~2 帧)
  放工单 阈值 50 + 最少帧数 5 (新模型此类闪烁最多)
  放托盘动作段长 35~54 帧 (旧模型仅 5~7 帧) — 双结算风险大幅下降

三段视频与期望:
  正常整箱 (13-55-39): 4盘×24 → 放油嘴包 → 封箱, 期望: 防呆提示Δ=0, 合格+1, 不良+0
  视频一 (13-51-37): 装满忘放油嘴包直接封箱 → 缺步挂起报警, 补油嘴包后合格
  视频二 (13-53-33): 少第四盘就放油嘴包 → 数量门拒收报警, 补盘后合格

证据: /home/qianqian/uat_sy4_new_model/ (run.log + 截图 + 浏览器录像)
"""
import json
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8001"
WEB = "http://localhost:6001"
OUT = Path("/home/qianqian/uat_sy4_new_model")
CH = 0
PROJECT_ID = 35  # SY4
MODEL = ("/home/qianqian/桌面/word/tianjun-main/backend/uploads/models/"
         "77cd8782355d461ba496aa94fa12ce8a_sy_v10_last.pt")

VIDEOS = [
    {
        "tag": "normal_正常整箱",
        "path": "/home/qianqian/2026-07-21 13-55-39.mkv",
        "expect_alarm": False, "ok_by_sec": 130,
    },
    {
        "tag": "video1_忘放油嘴包直接封箱",
        "path": "/home/qianqian/2026-07-21 13-51-37.mkv",
        "expect_alarm": True, "alarm_by_sec": 75, "ok_by_sec": 150,
    },
    {
        "tag": "video2_少装就放油嘴包",
        "path": "/home/qianqian/2026-07-21 13-53-33.mkv",
        "expect_alarm": True, "alarm_by_sec": 70, "ok_by_sec": 160,
    },
]

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


def wait_counter_delta(name, baseline, delta, timeout, page=None, shot=None):
    end = time.time() + timeout
    while time.time() < end:
        c = read_counters()
        if c.get(name, 0) >= baseline.get(name, 0) + delta:
            if page is not None and shot:
                try:
                    page.screenshot(path=str(OUT / shot), full_page=True)
                    log(f"  截图 → {shot}")
                except Exception as e:
                    log(f"  截图失败(不影响判定): {e}")
            return True, c
        time.sleep(0.5)
    return False, read_counters()


def apply_sy4_project():
    p = api("get", f"/api/v1/projects/{PROJECT_ID}")
    assert p["name"] == "SY4", f"项目 {PROJECT_ID} 不是 SY4: {p['name']}"
    ngh = (p.get("pipeline_config") or {}).get("ng_handling")
    assert ngh and ngh.get("gate_enabled") and ngh.get("missing_step") == "hold", \
        f"SY4 NG 处置配置缺失/未开防呆: {ngh}"
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
    log(f"SY4 配置已推运行时: model={mdl['model_name']} ng_handling={ngh}")


def run_one(page, video):
    tag, vpath = video["tag"], video["path"]
    log(f"===== {tag} =====")
    assert Path(vpath).exists(), f"视频不存在: {vpath}"

    for ep in ("/api/v1/source/detection/stop", "/api/v1/source/video/stop"):
        try:
            requests.post(f"{API}{ep}?channel={CH}", timeout=10)
        except Exception:
            pass
    time.sleep(1.0)

    apply_sy4_project()
    api("post", f"/api/v1/source/video/start?channel={CH}",
        json={"file_path": vpath, "speed": 1.0})
    api("post", f"/api/v1/source/detection/start?channel={CH}",
        json={"model_path": MODEL, "session_name": f"uat_sy4_{tag[:6]}"})
    baseline = read_counters()
    log(f"检测已启动, 基线={json.dumps(baseline, ensure_ascii=False)}")
    page.screenshot(path=str(OUT / f"{tag}_00_start.png"), full_page=True)

    alarm_ts = None
    if video["expect_alarm"]:
        ok1, c = wait_counter_delta("防呆提示", baseline, 1, video["alarm_by_sec"] + 20,
                                    page=page, shot=f"{tag}_01_防呆提示.png")
        alarm_ts = time.time()
        log(f"防呆提示: {'✓触发' if ok1 else '✗超时未触发'} counters={json.dumps(c, ensure_ascii=False)}")
    else:
        ok1 = True

    ok2, c = wait_counter_delta("合格总数", baseline, 1, video["ok_by_sec"] + 40,
                                page=page, shot=f"{tag}_02_合格.png")
    ok_ts = time.time()
    ng_delta = c.get("不良总数", 0) - baseline.get("不良总数", 0)
    alarm_delta = c.get("防呆提示", 0) - baseline.get("防呆提示", 0)
    log(f"合格判定: {'✓+1' if ok2 else '✗超时'} 不良Δ={ng_delta} 防呆Δ={alarm_delta} "
        f"counters={json.dumps(c, ensure_ascii=False)}")

    if video["expect_alarm"]:
        alarm_leads = ok1 and ok2 and (ok_ts - alarm_ts) >= 3.0
        log(f"报警先于合格 {ok_ts - alarm_ts:.1f}s: {'✓' if alarm_leads else '✗ 可疑'}")
        no_false_alarm = True
    else:
        alarm_leads = True
        no_false_alarm = alarm_delta == 0
        log(f"正常箱无防呆误报: {'✓' if no_false_alarm else f'✗ 误报{alarm_delta}次'}")

    requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=10)
    requests.post(f"{API}/api/v1/source/video/stop?channel={CH}", timeout=10)
    passed = ok1 and ok2 and ng_delta == 0 and alarm_leads and no_false_alarm
    log(f"结果: {'PASS' if passed else 'FAIL'} (报警符合={ok1 and no_false_alarm}, "
        f"合格+1={ok2}, 不良Δ={ng_delta})")
    return passed


def main():
    global _log_fh
    OUT.mkdir(parents=True, exist_ok=True)
    _log_fh = open(OUT / "run.log", "w", encoding="utf-8")

    from playwright.sync_api import sync_playwright
    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1600, "height": 900},
                                  record_video_dir=str(OUT / "browser_video"))
        page = ctx.new_page()
        page.goto(f"{WEB}/#/monitor", wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)
        for video in VIDEOS:
            try:
                results[video["tag"]] = run_one(page, video)
            except Exception as e:
                log(f"{video['tag']} 异常: {e}")
                results[video["tag"]] = False
        ctx.close()
        browser.close()

    log("=" * 50)
    all_pass = all(results.values())
    for k, v in results.items():
        log(f"  {k}: {'PASS' if v else 'FAIL'}")
    log(f"UAT 总结: {'全部通过' if all_pass else '存在失败'} — 证据目录 {OUT}")
    _log_fh.close()
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
