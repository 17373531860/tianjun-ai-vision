"""端到端 pytest：synthetic + 项目 + 检测 → 业务流真跑一遍。

不需要硬件、不需要模型、不需要前端。
基于 TestClient（与 BDD 测试相同 fixture），证明 synthetic 注入路径
能完整跑通：start synthetic → set project → start detection
→ /detection/results 返回非空 → 停干净。
"""
from __future__ import annotations

import time

import pytest


def _start_synth_with_project(client, scenario: str, channel: int = 0):
    return client.post("/api/v1/test/synthetic/start", json={
        "scenario": scenario,
        "channel": channel,
        "with_project": True,
    })


def _stop_all(client, channel: int = 0):
    client.post(f"/api/v1/source/detection/stop?channel={channel}")
    client.post(f"/api/v1/test/synthetic/stop?channel={channel}")


@pytest.fixture
def fresh_channel(client):
    """每个测试前后都把通道 0 清干净。"""
    _stop_all(client, 0)
    yield 0
    _stop_all(client, 0)


def test_synthetic_full_flow_ok_cycle(client, fresh_channel):
    ch = fresh_channel
    r = _start_synth_with_project(client, "ok_sequential_cycle.json", ch)
    assert r.status_code == 200, f"start synthetic 失败: {r.text[:300]}"

    r = client.post(f"/api/v1/source/detection/start?channel={ch}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, f"detection/start 失败: {r.text[:300]}"

    last = None
    for _ in range(20):
        r = client.get(f"/api/v1/source/detection/results?channel={ch}")
        if r.status_code == 200:
            last = r.json()
            if last.get("detections"):
                break
        time.sleep(0.05)

    assert last is not None, "/detection/results 一直没拿到数据"
    assert "detections" in last, f"返回缺 detections 字段: keys={list(last)[:10]}"


def test_synthetic_full_flow_smoke_inline(client, fresh_channel):
    ch = fresh_channel
    spec = {
        "name": "inline-smoke",
        "fps": 60,
        "timeline": [
            {"from": 0, "to": 200, "detections": [
                {"label": "X", "confidence": 0.9, "bbox": [0.1, 0.1, 0.2, 0.2]}
            ]}
        ],
    }
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": spec,
        "channel": ch,
        "with_project": True,
    })
    assert r.status_code == 200, r.text[:300]

    r = client.post(f"/api/v1/source/detection/start?channel={ch}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]

    found_x = False
    for _ in range(40):
        r = client.get(f"/api/v1/source/detection/results?channel={ch}")
        if r.status_code == 200:
            body = r.json()
            for d in body.get("detections") or []:
                if d.get("label") == "X":
                    found_x = True
                    break
        if found_x:
            break
        time.sleep(0.05)

    assert found_x, "synthetic 注入的标签 X 应能从 /detection/results 读到"
