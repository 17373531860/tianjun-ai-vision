# -*- coding: utf-8 -*-
"""违序后补做实验: 工件1前罩螺丝片段重排为 1→3(提前,违序)→2→3(补做)→4,
其余流程原样, 验证: 违序当场报 1 次、被拦的螺丝3可在轮到它时补做、
整件最终仍判合格(合格+1)。

回答客户问题: "报警确认后能否接着上次的周期补做" —— 不开"需人工确认"时,
周期不清空, 违序步骤只拦不记, 轮到它时重做即可接上。

跑法: ~/anaconda3/envs/tianjun/bin/python tests/uat/_replay_motor_redo.py
期望: 违序次数=1, 8颗螺丝+力矩标记横放全计数, 工件1 合格=1。
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
os.environ.setdefault("TIANJUN_DATA_DIR", "/tmp/replay_motor_redo")
os.makedirs("/tmp/replay_motor_redo", exist_ok=True)

TRACE = os.path.join(os.path.dirname(__file__), "_motor_trace_95_255.json")
CH = 0

from _motor_flow_config import build_flow_config  # noqa: E402

# 前罩打螺丝阶段切块(边界=相邻螺丝窗口中点); 螺丝3块被用两次(提前一次+补做一次)
BLOCKS = [(111.5, 114.6), (114.6, 118.3), (118.3, 120.7), (120.7, 123.0)]
ORDER = [0, 2, 1, 2, 3]     # 螺丝1 → 螺丝3(提前) → 螺丝2 → 螺丝3(补做) → 螺丝4


def rearrange(frames):
    head = [f for f in frames if f["t"] < BLOCKS[0][0]]
    out = list(head)
    cursor = BLOCKS[0][0]
    for bi in ORDER:
        s, e = BLOCKS[bi]
        for f in frames:
            if s <= f["t"] < e:
                out.append({"t": round(cursor + (f["t"] - s), 3),
                            "detections": f["detections"]})
        cursor += e - s
    shift = cursor - BLOCKS[-1][1]      # 复用块引入的时移(2.4s), 尾段整体后移
    for f in frames:
        if f["t"] >= BLOCKS[-1][1]:
            out.append({"t": round(f["t"] + shift, 3),
                        "detections": f["detections"]})
    return out


def build_scenario(speedup: float = 4.0):
    data = json.load(open(TRACE))
    frames = rearrange(data["frames"])
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
    return {"name": "replay_motor_redo", "fps": play_fps, "timeline": tl}, speedup


def main():
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    scenario, speedup = build_scenario()
    total_frames = scenario["timeline"][-1]["to"]
    wall_needed = total_frames / scenario["fps"]
    print(f"[redo] 螺丝顺序 1→3(提前)→2→3(补做)→4, 回放≈{wall_needed:.0f}s (4x)")

    cfg = build_flow_config(speedup)
    proj = {"project_id": -1, "name": "__replay_redo__",
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
                "sc": {k: v for k, v in (b.get("step_counts") or {}).items() if v},
                "ctr": {k: v for k, v in (b.get("counters") or {}).items() if v},
                "cycle": b.get("current_cycle_steps"),
            }
            final = rec
            key = json.dumps({k: rec[k] for k in ("sc", "ctr", "cycle")},
                             ensure_ascii=False, sort_keys=True)
            if key != last:
                print("[TL]", json.dumps(rec, ensure_ascii=False), flush=True)
                last = key
            time.sleep(0.1)
    finally:
        client.post(f"/api/v1/source/detection/stop?channel={CH}")
        client.post(f"/api/v1/test/synthetic/stop?channel={CH}")

    ctr = final.get("ctr", {})
    print(f"\n[redo] ==== 违序={ctr.get('违序次数', 0)} 合格={ctr.get('合格总数', 0)} "
          f"(期望: 违序=1, 合格=1 即补做后整件仍合格) ====")
    assert ctr.get("违序次数", 0) == 1, "违序应恰好报1次"
    assert ctr.get("合格总数", 0) >= 1, "补做后工件1应判合格"
    print("[redo] PASS")


if __name__ == "__main__":
    main()
