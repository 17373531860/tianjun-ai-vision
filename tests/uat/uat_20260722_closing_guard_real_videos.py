# -*- coding: utf-8 -*-
"""UAT: 收尾防呆 (v3.44) 用上银 SY3 两段真实现场视频跑真 pipeline — 真模型真推理。

用户叙事 (2026-07-21 两段录像, 理想行为):
  视频一 (13-51-37): 0:36 装完全部托盘 → 忘放油嘴包, 0:45 直接封箱
      期望: 缺步挂起报警 (防呆提示+1); 0:58 补放油嘴包 → 周期自动判合格 (合格+1, 不良+0)
  视频二 (13-53-33): 0:33 第三盘进箱后漏第四盘, 0:36 就放油嘴包
      期望: 数量门当场拒收报警 (防呆提示+1); 0:45 补第四盘再放油嘴包封箱 → 合格+1, 不良+0

跑法: 直连主开发栈 backend 8001 (SY3 项目 34 已配 ng_handling 统一块 + 事件4"包装防呆提示"),
文件视频源逐帧真推理; 同时开可见浏览器 (headless=False) 盯 Monitor 页, 关键节点截图+录屏。

证据落盘: /tmp/uat_closing_guard_real/ (run.log + 截图 + 浏览器录像)
"""
import json
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8001"
WEB = "http://localhost:6001"
OUT = Path("/home/qianqian/uat_closing_guard_real")  # 家目录: 防 /tmp 重启清空丢证据
CH = 0
# SY3 默认模型 id=31 (SY-重训v10, pytorch_fp32) — stop 后模型被释放, start 必须显式带路径
MODEL = ("/home/qianqian/桌面/word/tianjun-main/backend/uploads/models/"
         "255cf23e767b4455a06b1db8fedab64e_best10.pt")

VIDEOS = [
    {
        "tag": "video1_忘放油嘴包直接封箱",
        "path": "/home/qianqian/2026-07-21 13-51-37.mkv",
        # 现场节奏: ~45s 封箱触发挂起报警, ~60s 补油嘴包 → OK
        "alarm_by_sec": 75, "ok_by_sec": 150,
    },
    {
        "tag": "video2_少装就放油嘴包",
        "path": "/home/qianqian/2026-07-21 13-53-33.mkv",
        # 现场节奏: ~36s 提前油嘴包触发数量门报警, ~45s 起补盘 → 结尾 OK
        "alarm_by_sec": 70, "ok_by_sec": 160,
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


def read_status():
    r = requests.get(f"{API}/api/v1/source/status?channel={CH}", timeout=10)
    return r.json() if r.status_code == 200 else {}


def wait_counter_delta(name, baseline, delta, timeout, page=None, shot=None):
    """等某计数器较基线至少 +delta; 顺手截图。返回 (成功?, 当前counters)。"""
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


def apply_sy3_project():
    """把 DB 里 SY3(34) 的最新配置推到通道 0 运行时 (含 NG 处置统一块)。"""
    p = api("get", "/api/v1/projects/34")
    # v3.44 统一模型: 数量门 + 缺步挂起收敛进 ng_handling 块
    cg = (p.get("pipeline_config") or {}).get("ng_handling")
    assert cg and cg.get("gate_enabled") and cg.get("missing_step") == "hold", \
        f"SY3 NG 处置配置缺失/未开防呆: {cg}"
    api("post", f"/api/v1/source/detection/set-project?channel={CH}", json={
        "project_id": p["id"], "name": p["name"], "task_type": p["task_type"],
        "logic_mode": p["logic_mode"],
        "steps_config": p.get("steps_config") or [],
        "pipeline_config": p.get("pipeline_config") or {},
        "events_config": p.get("events_config") or [],
        "counters_config": p.get("counters_config") or [],
        "data_config": p.get("data_config") or {},
    })
    log(f"SY3 配置已推运行时: ng_handling={cg}")


def run_one(page, video):
    tag, vpath = video["tag"], video["path"]
    log(f"===== {tag} =====")
    assert Path(vpath).exists(), f"视频不存在: {vpath}"

    # 停旧检测/视频 (容错)
    for ep in ("/api/v1/source/detection/stop", "/api/v1/source/video/stop"):
        try:
            requests.post(f"{API}{ep}?channel={CH}", timeout=10)
        except Exception:
            pass
    time.sleep(1.0)

    apply_sy3_project()
    api("post", f"/api/v1/source/video/start?channel={CH}",
        json={"file_path": vpath, "speed": 1.0})
    log(f"视频已播放: {vpath}")
    api("post", f"/api/v1/source/detection/start?channel={CH}",
        json={"model_path": MODEL, "session_name": f"uat_cg_{tag[:6]}"})
    baseline = read_counters()
    log(f"检测已启动, 计数基线={json.dumps(baseline, ensure_ascii=False)}")
    st = read_status()
    log(f"源状态: source_type={st.get('source_type')} model={bool(st.get('model_loaded', True))}")
    page.screenshot(path=str(OUT / f"{tag}_00_start.png"), full_page=True)

    ok1, c = wait_counter_delta("防呆提示", baseline, 1, video["alarm_by_sec"] + 20,
                                page=page, shot=f"{tag}_01_防呆提示.png")
    alarm_ts = time.time()
    log(f"防呆提示: {'✓触发' if ok1 else '✗超时未触发'} counters={json.dumps(c, ensure_ascii=False)}")

    ok2, c = wait_counter_delta("合格总数", baseline, 1, video["ok_by_sec"] + 40,
                                page=page, shot=f"{tag}_02_合格.png")
    ok_ts = time.time()
    ng_delta = c.get("不良总数", 0) - baseline.get("不良总数", 0)
    log(f"合格判定: {'✓+1' if ok2 else '✗超时'} 不良Δ={ng_delta} "
        f"counters={json.dumps(c, ensure_ascii=False)}")

    # 报警必须明显早于合格结算 — 防"结算瞬间余像误报凑数"式假通过
    # (视频叙事: 报警点与补做/收尾之间隔着好几秒的人工动作)
    alarm_leads = ok1 and ok2 and (ok_ts - alarm_ts) >= 3.0
    log(f"报警先于合格 {ok_ts - alarm_ts:.1f}s: {'✓' if alarm_leads else '✗ (报警与结算几乎同时=可疑)'}")

    requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=10)
    requests.post(f"{API}/api/v1/source/video/stop?channel={CH}", timeout=10)
    passed = ok1 and ok2 and ng_delta == 0 and alarm_leads
    log(f"结果: {'PASS' if passed else 'FAIL'} (防呆报警={ok1}, 合格+1={ok2}, "
        f"不良Δ={ng_delta}, 报警提前={alarm_leads})")
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
