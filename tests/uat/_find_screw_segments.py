# -*- coding: utf-8 -*-
"""离线扫描真实轨迹, 用真实拆分引擎标出工件1前罩四颗螺丝各自的时间窗。

供 _replay_motor_shuffled.py 打乱片段顺序做"乱序打螺丝能否报违序"实验。
跑法: ~/anaconda3/envs/tianjun/bin/python tests/uat/_find_screw_segments.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")

from backend.api.source_label_split import parse_label_splits, LabelSplitEngine  # noqa: E402
from _motor_flow_config import build_flow_config  # noqa: E402

TRACE = os.path.join(os.path.dirname(__file__), "_motor_trace_95_255.json")

cfg = build_flow_config(1.0)
rules = parse_label_splits(cfg["pipeline_config"])
engine = LabelSplitEngine(rules)

data = json.load(open(TRACE))
segments = {}   # 虚拟螺丝名 -> [(t_start, t_end)]
prev = {}
for fr in data["frames"]:
    t = fr["t"]
    if t > 150:      # 只看工件1前罩阶段
        break
    dets = [{"label": d["label"], "confidence": d["confidence"],
             "x": d["bbox"][0], "y": d["bbox"][1],
             "w": d["bbox"][2], "h": d["bbox"][3]} for d in fr["detections"]]
    out = engine.apply(dets, now=t, cycle_len=1)
    labels = {d["label"] for d in out if d["label"].endswith(tuple("1234"))}
    for lb in labels:
        if lb not in prev or t - prev[lb][1] > 1.0:
            segments.setdefault(lb, []).append([t, t])
        segments[lb][-1][1] = t
        prev[lb] = (segments[lb][-1][0], t)

for lb in sorted(segments):
    for s, e in segments[lb]:
        print(f"{lb}: {s:.2f} ~ {e:.2f}  ({e-s:.2f}s)")
