# -*- coding: utf-8 -*-
"""UAT 回归: SY6 跑上银 7-23 正常视频 (4 箱满装 96, 全流程正确) — 别影响合格的.

7-27 全NG跑通之后的合格回归: 同一套配置 (数量门/挂起/封箱单帧45/峰值封顶24/
折算守门) 跑正常视频, 验证合格箱不被误伤。

期望: 全 4 箱合格 (合格 +4, 不良 +0), 全程不应弹人工确认。
若弹了确认, 脚本仍代点避免卡死, 但最终判定按计数器算 FAIL。

证据: /home/qianqian/uat_sy6_ok_regression/ (run.log + 截图 + 浏览器录像)
"""
import json
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8001"
WEB = "http://localhost:6001"
OUT = Path("/home/qianqian/uat_sy6_ok_regression")
CH = 0
PROJECT_ID = 37  # SY6
MODEL = ("/home/qianqian/桌面/word/tianjun-main/backend/uploads/models/"
         "sy6_packing_v29.pt")
VIDEO = ("/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/"
         "msg/file/2026-07/2026-07-23 15-43-27.mkv")
VIDEO_DUR = 270  # 秒

_log_fh = None
_t0 = None


def log(msg):
    pos = f"{time.time() - _t0:6.1f}s" if _t0 else "  --  "
    line = f"[{time.strftime('%H:%M:%S')}][视频~{pos}] {msg}"
    print(line, flush=True)
    if _log_fh:
        _log_fh.write(line + "\n")
        _log_fh.flush()


def api(method, path, **kw):
    r = getattr(requests, method)(f"{API}{path}", timeout=15, **kw)
    r.raise_for_status()
    return r.json()


def results():
    r = requests.get(f"{API}/api/v1/source/detection/results?channel={CH}", timeout=10)
    return r.json() if r.status_code == 200 else {}


def apply_sy6():
    p = api("get", f"/api/v1/projects/{PROJECT_ID}")
    assert p["name"] == "SY6", f"项目 {PROJECT_ID} 不是 SY6: {p['name']}"
    pc = p.get("pipeline_config") or {}
    assert pc.get("custom_mix_container_per_tray_guard") is False, "逐盘校验应已关"
    ngh = pc.get("ng_handling") or {}
    assert ngh.get("gate_enabled") and ngh.get("gate_escalate_steps") == ["封箱"], \
        f"数量门/升级放行配置不符: {ngh}"
    api("post", f"/api/v1/source/detection/set-project?channel={CH}", json={
        "project_id": p["id"], "name": p["name"], "task_type": p["task_type"],
        "logic_mode": p["logic_mode"],
        "steps_config": p.get("steps_config") or [],
        "pipeline_config": pc,
        "events_config": p.get("events_config") or [],
        "counters_config": p.get("counters_config") or [],
        "data_config": p.get("data_config") or {},
    })
    log(f"SY6 已推运行时: 逐盘校验=关 ng_handling={json.dumps(ngh, ensure_ascii=False)}")


def main():
    global _log_fh, _t0
    OUT.mkdir(parents=True, exist_ok=True)
    _log_fh = open(OUT / "run.log", "w", encoding="utf-8")
    assert Path(VIDEO).exists(), f"视频不存在: {VIDEO}"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1600, "height": 900},
                                  record_video_dir=str(OUT / "browser_video"))
        page = ctx.new_page()
        page.goto(f"{WEB}/#/monitor", wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)

        for ep in ("/api/v1/source/detection/stop", "/api/v1/source/video/stop"):
            try:
                requests.post(f"{API}{ep}?channel={CH}", timeout=10)
            except Exception:
                pass
        time.sleep(1.0)

        apply_sy6()
        api("post", f"/api/v1/source/video/start?channel={CH}",
            json={"file_path": VIDEO, "speed": 1.0})
        api("post", f"/api/v1/source/detection/start?channel={CH}",
            json={"session_name": "uat_sy6_ok_reg", "models": [
                {"name": "main", "model_path": MODEL, "priority": 100},
                {"name": "tray_aux",
                 "model_path": ("/home/qianqian/桌面/word/tianjun-main/backend/"
                                "uploads/models/77cd8782355d461ba496aa94fa12ce8a_sy_v10_last.pt"),
                 "conf": 0.25, "iou": 0.45, "class_filter": ["托盘"],
                 "priority": 50, "use_half": True,
                 "schedule": {"type": "every_frame", "n": 1, "events": []}},
            ]})
        _t0 = time.time()
        base = results().get("counters") or {}
        log(f"检测启动, 基线={json.dumps(base, ensure_ascii=False)}")
        page.screenshot(path=str(OUT / "00_start.png"), full_page=True)

        acks = []
        last_booked = -1
        last_steps = None
        shot_n = 0
        deadline = _t0 + VIDEO_DUR + 150   # 视频跑完后留循环止损窗口
        ng_target = 4
        while time.time() < deadline:
            st = results()
            c = st.get("counters") or {}
            cont = (st.get("custom_mix_state") or {}).get("container") or {}
            done_detail = cont.get("done_detail") or []
            booked = sum((cont.get("item_total_done") or {}).values())
            steps = st.get("current_cycle_steps")
            if steps != last_steps:
                log(f"周期步骤: {steps}")
                last_steps = steps
            if booked != last_booked:
                log(f"箱账变化: 已进箱={booked}/{cont.get('item_target')} "
                    f"逐盘={json.dumps(done_detail, ensure_ascii=False)}")
                last_booked = booked
            pa = st.get("pending_ack") or {}
            if pa.get("active"):
                shot_n += 1
                shot = f"{shot_n:02d}_ack_{pa.get('event_name','')}.png"
                try:
                    page.screenshot(path=str(OUT / shot), full_page=True)
                except Exception:
                    pass
                log(f"人工确认弹出: event={pa.get('event_name')} reason={pa.get('reason')!r} → 截图 {shot}, 3s 后代点确认")
                time.sleep(3)
                r = requests.post(
                    f"{API}/api/v1/source/detection/ack-event?channel={CH}",
                    timeout=10)
                acks.append({"t": round(time.time() - _t0, 1),
                             "event": pa.get("event_name"),
                             "reason": pa.get("reason"),
                             "resp": r.json() if r.status_code == 200 else r.text[:200]})
                log(f"已确认: {json.dumps(acks[-1]['resp'], ensure_ascii=False)[:200]}")
            ng = c.get("不良总数", 0) - base.get("不良总数", 0)
            ok = c.get("合格总数", 0) - base.get("合格总数", 0)
            if ok >= 4:
                log(f"已达 4 合格, 提前收尾: counters={json.dumps(c, ensure_ascii=False)}")
                break
            time.sleep(0.5)

        final = results()
        c = final.get("counters") or {}
        ng = c.get("不良总数", 0) - base.get("不良总数", 0)
        ok = c.get("合格总数", 0) - base.get("合格总数", 0)
        page.screenshot(path=str(OUT / "99_final.png"), full_page=True)
        log(f"最终 counters={json.dumps(c, ensure_ascii=False)}")
        log(f"确认记录 {len(acks)} 次: {json.dumps(acks, ensure_ascii=False, indent=1)}")

        requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/api/v1/source/video/stop?channel={CH}", timeout=10)
        ctx.close()
        browser.close()

    passed = (ok == 4 and ng == 0)
    log(f"判定: 期望 全4箱合格/0NG, 实得 OK={ok} NG={ng} → {'PASS' if passed else 'FAIL'}")
    _log_fh.close()
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
