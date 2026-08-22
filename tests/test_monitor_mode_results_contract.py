"""Monitor 按工位切工艺面板所需的 results 载荷契约（v3.55）。

前端 resolveModePanelKind / TrackingChecklistPanel / PerItemPanel 依赖：
  - project_config.logic_mode
  - tracking 子树（tracking 模式清点）
  - per_item_state（逐件覆盖；非该模式可为 None）
后端 v3.32+ 已带这些字段；本文件守住「别再悄悄拿掉」。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from tests.test_detection_results_exposure import _make_mock_mgr


def test_results_always_expose_logic_mode_and_mode_payloads(client, monkeypatch):
    from backend.api import source_routes

    mgr = _make_mock_mgr([])
    mgr.project_config = {
        "id": 88,
        "name": "契约项目",
        "logic_mode": "tracking",
        "steps_config": [],
        "pipeline_config": {},
    }
    mgr.get_per_item_state = MagicMock(return_value=None)
    mgr._tracking_objects = {}
    mgr._tracking_class_counters = {"bolt": 2}
    monkeypatch.setattr(source_routes, "_get_mgr", lambda channel=0: mgr)

    resp = client.get("/api/v1/source/detection/results")
    assert resp.status_code == 200, resp.text[:400]
    body = resp.json()
    pc = body.get("project_config") or {}
    assert pc.get("logic_mode") == "tracking", pc
    assert "tracking" in body, f"缺 tracking 子树; keys={list(body)}"
    assert "per_item_state" in body, f"缺 per_item_state; keys={list(body)}"
    assert body["per_item_state"] is None


def test_results_per_item_state_passthrough(client, monkeypatch):
    from backend.api import source_routes

    snapshot = {
        "enabled": True,
        "cycle_active": True,
        "steps": [{"step_id": 1, "label": "screw", "items": []}],
    }
    mgr = _make_mock_mgr([])
    mgr.project_config = {
        "id": 89, "name": "逐件", "logic_mode": "per_item",
        "steps_config": [], "pipeline_config": {},
    }
    mgr.get_per_item_state = MagicMock(return_value=snapshot)
    monkeypatch.setattr(source_routes, "_get_mgr", lambda channel=0: mgr)

    body = client.get("/api/v1/source/detection/results").json()
    assert body["project_config"]["logic_mode"] == "per_item"
    assert body["per_item_state"] == snapshot
