# -*- coding: utf-8 -*-
"""离线确定性复盘: 把真实模型的逐帧检测轨迹灌进真实状态机(synthetic 通道).

用途: 真模型 UAT 里轮次不切/步骤被时长门吃掉这类问题, 在 GPU 实跑里不可复现、
不可断点。本脚本把 _motor_trace_95_255.json (best(3).pt 对客户视频 95~255s
每帧推理结果, 由 _gen_motor_trace.py 生成) 转成 synthetic 剧本, 用 TestClient
走 拆分引擎→步骤统计→严格顺序→结算 全链路, 输出与 UAT 相同的遥测轨迹。

覆盖客户全流程 13 步: 前罩螺丝1-4 → 前罩力矩 → 前罩标记 →
后罩螺丝1-4 → 后罩力矩 → 后罩标记 → 工件横放。
力矩/标记用"整幅区域+多轮次前缀"拆成前/后罩两个虚拟步骤。

跑法: ~/anaconda3/envs/tianjun/bin/python tests/uat/_replay_motor_trace.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
os.environ.setdefault("BACKEND_SKIP_INIT", "1")
os.environ.setdefault("ENABLE_DEV_MOCKS", "1")
os.environ.setdefault("RUNTIME_MODE", "test")   # 挂载 /test/synthetic/*
os.environ.setdefault("TIANJUN_DATA_DIR", "/tmp/replay_motor_data")
os.makedirs("/tmp/replay_motor_data", exist_ok=True)

TRACE = os.path.join(os.path.dirname(__file__), "_motor_trace_95_255.json")
CH = 0

from _motor_flow_config import FLOW_STEPS, build_flow_config  # noqa: E402


def build_scenario(speedup: float = 4.0):
    """真实轨迹 → synthetic 剧本。speedup: 加速回放(同倍缩短时长阈值)。"""
    data = json.load(open(TRACE))
    src_fps = data["fps"]                     # 21
    play_fps = 60                             # synthetic 播放帧率
    frames = data["frames"]
    tl = []
    for fr in frames:
        t_play = fr["t"] / speedup
        f0 = int(t_play * play_fps)
        dets = [{"label": d["label"], "confidence": d["confidence"],
                 "bbox": d["bbox"]} for d in fr["detections"]]
        if tl and tl[-1]["_key"] == json.dumps(dets, sort_keys=True):
            tl[-1]["to"] = f0 + 0
            continue
        tl.append({"from": f0, "to": f0, "detections": dets,
                   "_key": json.dumps(dets, sort_keys=True)})
    for i, e in enumerate(tl):
        e.pop("_key")
        if i + 1 < len(tl):
            e["to"] = tl[i + 1]["from"] - 1
    tl[-1]["to"] = tl[-1]["from"] + 300
    return {"name": "replay_motor", "fps": play_fps, "timeline": tl}, speedup


def project_config(speedup: float):
    cfg = build_flow_config(speedup)
    return {"project_id": -1, "name": "__replay_motor__",
            "task_type": "detection", "logic_mode": "sequential", **cfg}


def main():
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    scenario, speedup = build_scenario()
    total_frames = scenario["timeline"][-1]["to"]
    wall_needed = total_frames / scenario["fps"]
    print(f"[replay] 剧本段数={len(scenario['timeline'])} 总帧={total_frames} "
          f"回放时长≈{wall_needed:.0f}s (加速 {speedup}x)")

    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": scenario, "channel": CH, "with_project": False})
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}",
                    json=project_config(speedup))
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]
    # 计数器跨进程持久在数据目录 — 清零, 否则上一轮回放的违序残值污染判读
    client.post(f"/api/v1/source/detection/reset-stats?channel={CH}")

    t0 = time.time()
    last = None
    try:
        while time.time() - t0 < wall_needed + 5:
            b = client.get(f"/api/v1/source/detection/results?channel={CH}").json()
            rec = {
                "replay_t": round((time.time() - t0) * speedup + 95, 1),
                "rounds": (b.get("label_split_rounds") or {}).get("打螺丝", {}).get("round"),
                "sc": {k: v for k, v in (b.get("step_counts") or {}).items() if v},
                "ctr": {k: v for k, v in (b.get("counters") or {}).items() if v},
                "cycle": b.get("current_cycle_steps"),
            }
            key = json.dumps({k: rec[k] for k in ("rounds", "sc", "ctr", "cycle")},
                             ensure_ascii=False, sort_keys=True)
            if key != last:
                print("[TL]", json.dumps(rec, ensure_ascii=False), flush=True)
                last = key
            time.sleep(0.1)
    finally:
        client.post(f"/api/v1/source/detection/stop?channel={CH}")
        client.post(f"/api/v1/test/synthetic/stop?channel={CH}")

    print("\n[replay] ==== 期望: 13 步全流程各计 1 次(工件2 前罩螺丝再各+1) → "
          "违序0 → 合格1 ====")
    print("[replay] 流程顺序:", " → ".join(FLOW_STEPS))


if __name__ == "__main__":
    main()
