# -*- coding: utf-8 -*-
"""乱序打螺丝实验: 把真实轨迹里工件1前罩四颗螺丝的片段重排为 1→3→2→4,
灌进真实状态机, 验证严格顺序能当场报违序 + 工件结算为不良。

片段边界来自 _find_screw_segments.py 的真实扫描:
  螺丝1 112.1~113.7 / 螺丝2 115.2~117.8 / 螺丝3 118.9~120.1 / 螺丝4 121.2~122.2

跑法: ~/anaconda3/envs/tianjun/bin/python tests/uat/_replay_motor_shuffled.py
期望: 螺丝3 提前出现的瞬间违序警告+1(被拦不计数), 后续步骤连锁违序,
     工件1 结算为不良; 对照正常回放(违序0/合格1)。
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
os.environ.setdefault("RUNTIME_MODE", "test")
os.environ.setdefault("TIANJUN_DATA_DIR", "/tmp/replay_motor_shuffled")
os.makedirs("/tmp/replay_motor_shuffled", exist_ok=True)

TRACE = os.path.join(os.path.dirname(__file__), "_motor_trace_95_255.json")
CH = 0

from _motor_flow_config import build_flow_config  # noqa: E402

# 前罩打螺丝阶段按时间切 4 块(边界取两颗螺丝窗口的中点), 重排为 1→3→2→4
BLOCKS = [(111.5, 114.6), (114.6, 118.3), (118.3, 120.7), (120.7, 123.0)]
ORDER = [0, 2, 1, 3]        # 块索引: 螺丝1块 → 螺丝3块 → 螺丝2块 → 螺丝4块


def shuffle_trace(frames):
    """把 BLOCKS 覆盖的帧按 ORDER 重排时间轴, 其余帧原位不动。"""
    head = [f for f in frames if f["t"] < BLOCKS[0][0]]
    tail = [f for f in frames if f["t"] >= BLOCKS[-1][1]]
    out = list(head)
    cursor = BLOCKS[0][0]
    for bi in ORDER:
        s, e = BLOCKS[bi]
        blk = [f for f in frames if s <= f["t"] < e]
        for f in blk:
            out.append({"t": round(cursor + (f["t"] - s), 3),
                        "detections": f["detections"]})
        cursor += e - s
    out.extend(tail)
    return out


def build_scenario(speedup: float = 4.0):
    data = json.load(open(TRACE))
    frames = shuffle_trace(data["frames"])
    play_fps = 60
    tl = []
    for fr in frames:
        f0 = int(fr["t"] / speedup * play_fps)
        dets = [{"label": d["label"], "confidence": d["confidence"],
                 "bbox": d["bbox"]} for d in fr["detections"]]
        if tl and tl[-1]["_key"] == json.dumps(dets, sort_keys=True):
            tl[-1]["to"] = f0
            continue
        tl.append({"from": f0, "to": f0, "detections": dets,
                   "_key": json.dumps(dets, sort_keys=True)})
    for i, e in enumerate(tl):
        e.pop("_key")
        if i + 1 < len(tl):
            e["to"] = tl[i + 1]["from"] - 1
    tl[-1]["to"] = tl[-1]["from"] + 300
    return {"name": "replay_motor_shuffled", "fps": play_fps, "timeline": tl}, speedup


def main():
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    scenario, speedup = build_scenario()
    total_frames = scenario["timeline"][-1]["to"]
    wall_needed = total_frames / scenario["fps"]
    print(f"[shuffled] 螺丝片段重排 1→3→2→4, 回放≈{wall_needed:.0f}s (4x)")

    cfg = build_flow_config(speedup)
    proj = {"project_id": -1, "name": "__replay_shuffled__",
            "task_type": "detection", "logic_mode": "sequential", **cfg}

    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": scenario, "channel": CH, "with_project": False})
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}", json=proj)
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]
    client.post(f"/api/v1/source/detection/reset-stats?channel={CH}")

    t0 = time.time()
    last = None
    final = {}
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
            final = rec
            key = json.dumps({k: rec[k] for k in ("rounds", "sc", "ctr", "cycle")},
                             ensure_ascii=False, sort_keys=True)
            if key != last:
                print("[TL]", json.dumps(rec, ensure_ascii=False), flush=True)
                last = key
            time.sleep(0.1)
    finally:
        client.post(f"/api/v1/source/detection/stop?channel={CH}")
        client.post(f"/api/v1/test/synthetic/stop?channel={CH}")

    ctr = final.get("ctr", {})
    viol = ctr.get("违序次数", 0)
    ng = ctr.get("不良总数", 0)
    print(f"\n[shuffled] ==== 违序次数={viol} 不良={ng} (期望: 违序≥1 且 工件1 记不良) ====")
    assert viol >= 1, "乱序打螺丝未触发违序警告!"


if __name__ == "__main__":
    main()
