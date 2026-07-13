# -*- coding: utf-8 -*-
"""隔离实验: 不开浏览器, 纯 API 驱动真实模型跑工件1前罩段(视频100→130s).

用途: UAT 里步骤计数丢失, 而离线1x复放全对 —— 二分变量:
浏览器/监控页在不在场, 对活链路是否有影响。
"""
import json
import time

import requests

API = "http://127.0.0.1:8001/api/v1"
CH = 0
VIDEO = ("/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/"
         "msg/video/2026-07/1593386d5368d7afda8e0b4f47b15587.mp4")

# 复用 UAT 的项目构造
import importlib.util
spec = importlib.util.spec_from_file_location(
    "uat_mod", __file__.replace("_probe_live_nobrowser.py",
                                "uat_20260708_motor_realmodel_e2e.py"))
# 不能直接 import UAT(顶层就开跑) — 拷贝其配置常量
ANCHOR_REF = {"x": 0.355, "y": 0.362, "w": 0.277, "h": 0.638}
REGIONS = [
    {"name": "螺丝1", "polygon": [[0.470, 0.34], [0.545, 0.34], [0.545, 0.52], [0.470, 0.52]]},
    {"name": "螺丝2", "polygon": [[0.460, 0.52], [0.580, 0.52], [0.580, 0.76], [0.460, 0.76]]},
    {"name": "螺丝3", "polygon": [[0.545, 0.38], [0.660, 0.38], [0.660, 0.52], [0.545, 0.52]]},
    {"name": "螺丝4", "polygon": [[0.380, 0.34], [0.470, 0.34], [0.470, 0.52], [0.380, 0.52]]},
]
VSTEPS = [f"{p}螺丝{i}" for p in ("前罩", "后罩") for i in range(1, 5)]


def project_config():
    steps = [{"id": 100, "label": "打螺丝", "displayLabel": "打螺丝",
              "enabled": False, "threshold": 50}]
    steps += [
        {"id": 101 + i, "label": name, "displayLabel": name,
         "enabled": True, "threshold": 50, "min_frames": 1,
         "min_duration": 0.6, "disappear_delay": 1.5, "accept_once": True,
         "strict_order": True, "split_origin": "ls_motor"}
        for i, name in enumerate(VSTEPS)
    ]
    return {
        "project_id": -1, "name": "__probe_live__", "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": steps,
        "pipeline_config": {
            "sequence_order": [{"step_id": 101 + i} for i in range(8)],
            "settlement_mode": "first_step",
            "settle_dedup": False,
            "strict_order_violation_event_id": 3,
            "label_splits": [{
                "id": "ls_motor", "enabled": True, "source_label": "打螺丝",
                "mode": "anchor", "anchor_label": "工件",
                "anchor_ref": ANCHOR_REF, "anchor_hold_seconds": 5,
                "unmatched": "drop", "regions": REGIONS,
                "rounds": {"enabled": True, "trigger_label": "盖罩", "count": 2,
                           "prefixes": ["前罩", "后罩"],
                           "trigger_gap_seconds": 3.0,
                           "trigger_min_seconds": 0.5},
            }],
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [{"counter_name": "合格总数", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [{"counter_name": "不良总数", "delta": 1}]},
            {"id": 3, "name": "违序警告", "actions": [{"counter_name": "违序次数", "delta": 1}]},
        ],
        "counters_config": [{"name": "合格总数", "value": 0},
                            {"name": "不良总数", "value": 0},
                            {"name": "违序次数", "value": 0}],
    }


def main():
    d = requests.get(f"{API}/models", timeout=20).json()
    models = d.get("items") if isinstance(d, dict) else d
    mp = next(m["file_path"] for m in models
              if "motor_screw" in (m.get("file_path") or ""))

    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=15)
    requests.post(f"{API}/source/video/stop?channel={CH}", timeout=15)
    time.sleep(1)

    requests.post(f"{API}/source/video/start?channel={CH}",
                  json={"file_path": VIDEO, "speed": 0.1}, timeout=15).raise_for_status()
    requests.post(f"{API}/source/video/progress?channel={CH}",
                  json={"progress": 98.0 / 650.0}, timeout=10)
    requests.post(f"{API}/source/detection/set-project?channel={CH}",
                  json=project_config(), timeout=15).raise_for_status()
    requests.post(f"{API}/source/detection/start?channel={CH}",
                  json={"conf": 0.4, "iou": 0.45, "model_path": mp},
                  timeout=120).raise_for_status()
    for _ in range(90):
        st = requests.get(f"{API}/source/status?channel={CH}", timeout=5).json()
        if st.get("model_loaded") and st.get("is_detecting"):
            break
        time.sleep(1)
    print("[probe] 模型已加载, 清计数并放行 100s→135s @1x")
    requests.post(f"{API}/source/detection/reset-stats?channel={CH}", timeout=10)
    requests.put(f"{API}/debug/flags",
                 json={"flags": {"backend.settlement": True}}, timeout=10)
    requests.post(f"{API}/debug/logs/clear", timeout=10)
    requests.post(f"{API}/source/video/progress?channel={CH}",
                  json={"progress": 100.0 / 650.0}, timeout=10)
    requests.post(f"{API}/source/video/speed?channel={CH}", json={"speed": 1.0},
                  timeout=10)

    last = None
    t0 = time.time()
    while time.time() - t0 < 40:
        b = requests.get(f"{API}/source/detection/results?channel={CH}", timeout=10).json()
        vp = requests.get(f"{API}/source/video/info?channel={CH}", timeout=10).json()
        rec = {
            "video_t": round(vp.get("current_time", -1), 1),
            "round": ((b.get("label_split_rounds") or {}).get("打螺丝") or {}).get("round"),
            "sc": {k: v for k, v in (b.get("step_counts") or {}).items() if v},
            "ctr": {k: v for k, v in (b.get("counters") or {}).items() if v},
            "cyc": b.get("current_cycle_steps"),
            "fps_inf": requests.get(f"{API}/source/status?channel={CH}",
                                    timeout=5).json().get("fps_inference"),
        }
        key = json.dumps({k: rec[k] for k in ("round", "sc", "ctr", "cyc")},
                         ensure_ascii=False, sort_keys=True)
        if key != last:
            print("[TL]", json.dumps(rec, ensure_ascii=False), flush=True)
            last = key
        time.sleep(0.4)

    logs = requests.get(f"{API}/debug/logs?limit=2000", timeout=10).json()
    print("[probe] ==== 结算调试日志 ====")
    for e in logs.get("logs") or []:
        print(e.get("ts"), e.get("detail"))
    requests.put(f"{API}/debug/flags",
                 json={"flags": {"backend.settlement": False}}, timeout=10)
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=15)
    requests.post(f"{API}/source/video/stop?channel={CH}", timeout=15)


if __name__ == "__main__":
    main()
