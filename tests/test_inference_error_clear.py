"""v3.37 川南"框卡死"防线回归: 推理循环连续异常 → 主动清空画面检测框。

背景: 客户报"识别某类别后框冻结、后续类别全不识别、周期照样出 NG"。机制是
推理循环每帧异常时发布点走不到, 上一次发布的检测结果被采集线程反复重发布,
前端框永久定格。防线: 连续 30 帧推理异常 → 主动发布空结果 + 调试中心留证。

本测试用 synthetic 源真跑推理线程, 对活体 manager 注入"每帧必炸"的推理函数,
验证: 框先出现 → 注入后被清空 → 连续异常计数达阈值。
"""
from __future__ import annotations

import time

import pytest


ALWAYS_ON_SPEC = {
    "name": "always-on",
    "fps": 60,
    "timeline": [
        {"from": 0, "to": 100000, "detections": [
            {"label": "PART", "confidence": 0.95, "bbox": [0.2, 0.2, 0.3, 0.3]}
        ]}
    ],
}


@pytest.fixture
def ch0(client):
    client.post("/api/v1/source/detection/stop?channel=0")
    client.post("/api/v1/test/synthetic/stop?channel=0")
    yield 0
    client.post("/api/v1/source/detection/stop?channel=0")
    client.post("/api/v1/test/synthetic/stop?channel=0")


def _detections(client, ch):
    r = client.get(f"/api/v1/source/detection/results?channel={ch}")
    assert r.status_code == 200
    return r.json().get("detections") or []


def test_consecutive_inference_errors_clear_published_boxes(client, ch0):
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": ALWAYS_ON_SPEC, "channel": ch0, "with_project": True,
    })
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={ch0}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]

    # 1) 正常阶段: 框稳定在场
    seen = False
    for _ in range(60):
        if any(d.get("label") == "PART" for d in _detections(client, ch0)):
            seen = True
            break
        time.sleep(0.05)
    assert seen, "前置失败: 正常阶段没等到 PART 框"

    # 2) 注入故障: 推理每帧必炸 (模拟 CUDA/后处理异常反复发生)
    from backend.api.source import _get_mgr
    mgr = _get_mgr(ch0)

    def _boom(frame):
        raise RuntimeError("injected inference failure")

    orig = mgr._inference_select_and_run_model
    mgr._inference_select_and_run_model = _boom
    try:
        # 3) 防线应在连续 30 帧异常后清空已发布结果 (60fps 下 <2s, 放宽等 8s)
        cleared = False
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if not _detections(client, ch0):
                cleared = True
                break
            time.sleep(0.1)
        assert cleared, (
            f"连续推理异常后画面框未被清空 (仍有 {_detections(client, ch0)}), "
            f"consec_errors={getattr(mgr, '_infer_consec_errors', None)}")
        assert getattr(mgr, "_infer_consec_errors", 0) >= 30
    finally:
        mgr._inference_select_and_run_model = orig

    # 4) 故障恢复后框应重新出现 (计数归零, 链路自愈)
    recovered = False
    for _ in range(80):
        if any(d.get("label") == "PART" for d in _detections(client, ch0)):
            recovered = True
            break
        time.sleep(0.05)
    assert recovered, "故障解除后框未恢复"
    assert getattr(mgr, "_infer_consec_errors", -1) == 0
