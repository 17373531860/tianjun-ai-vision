"""虚拟 synthetic 源：无模型时仍能轮询到剧本注入的检测框。"""
from __future__ import annotations

import time


def test_synthetic_detection_results_contain_scenario_label(client):
    scenario = {
        "name": "inline_smoke",
        "fps": 60,
        "timeline": [
            {
                "from": 0,
                "to": 500,
                "detections": [
                    {
                        "label": "SynthBox",
                        "confidence": 0.95,
                        "bbox": [0.2, 0.2, 0.15, 0.15],
                    }
                ],
            }
        ],
    }
    r = client.post("/api/v1/test/synthetic/start", json={"scenario_json": scenario, "channel": 0})
    assert r.status_code == 200, r.text

    r2 = client.post("/api/v1/source/detection/start?channel=0", json={"conf": 0.25, "iou": 0.45})
    assert r2.status_code == 200, r2.text

    found = False
    for _ in range(80):
        time.sleep(0.05)
        dr = client.get("/api/v1/source/detection/results?channel=0")
        assert dr.status_code == 200
        data = dr.json()
        for d in data.get("detections") or []:
            if d.get("label") == "SynthBox":
                found = True
                break
        if found:
            break
    assert found, "剧本标签 SynthBox 未出现在 detection/results"

    client.post("/api/v1/source/detection/stop?channel=0")
    client.post("/api/v1/test/synthetic/stop?channel=0")


def test_synthetic_start_from_scenarios_file(client):
    r = client.post(
        "/api/v1/test/synthetic/start",
        json={"scenario": "smoke_static_label.json", "channel": 0},
    )
    assert r.status_code == 200, r.text
    client.post("/api/v1/test/synthetic/stop?channel=0")
