"""验证 source_routes.get_detection_results 暴露 periodic_actions 字段。

策略：用 MagicMock 自动满足所有属性访问，只显式控制 get_periodic_actions_status
的返回值，验证 result['periodic_actions'] 是否被写入。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_mock_mgr(periodic_status):
    """构造一个最小可跑的 VSM mock"""
    mgr = MagicMock()
    mgr.channel_id = 0
    mgr.events_log = []
    mgr.cycle_times = []
    mgr.ng_cycle_times = []
    mgr.step_durations_history = {}
    mgr.step_counts = {}
    mgr.step_screenshots = {}
    mgr.step_detection_times = {}
    mgr.step_durations = {}
    mgr.step_intervals = {}
    mgr.counters = {}
    mgr.ng_step_cycle_counts = {}
    mgr.current_cycle_steps = []
    mgr.step_backup_map = {}
    mgr.backup_steps_seen_in_cycle = set()
    mgr.project_config = {"id": 1, "logic_mode": "sequential"}
    mgr._mes_hook = None
    mgr._container_mode = False
    mgr._box_objects = {}
    mgr._box_settled_results = []
    mgr._tracking_class_counters = {}
    mgr._event_counters = {}
    mgr.is_running = True
    mgr.is_detecting = False
    mgr.source_type = "test"
    mgr.fps_actual = 0.0
    mgr.fps_inference = 0.0
    mgr.latency = 0.0
    mgr.get_detections = MagicMock(return_value=[])
    mgr.get_periodic_actions_status = MagicMock(return_value=periodic_status)
    mgr.get_recording_failures = MagicMock(return_value=[])
    return mgr


def test_get_detection_results_暴露周期性动作字段(client, monkeypatch):
    """配置了 periodic actions 时, /source/detection/results 应包含 periodic_actions 子树"""
    from backend.api import source_routes

    stub_status = [{
        "id": "pa_test_1",
        "name": "测试规则",
        "counter": 5,
        "interval": 10,
        "state": "ok",
        "remaining": 5,
        "count_basis": "all",
        "reset_policy": "always",
        "trigger_labels": ["E"],
    }]

    mgr = _make_mock_mgr(stub_status)
    monkeypatch.setattr(source_routes, "_get_mgr", lambda channel=0: mgr)

    resp = client.get("/api/v1/source/detection/results")
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert "periodic_actions" in body, f"返回缺 periodic_actions; keys={list(body.keys())}"
    assert body["periodic_actions"] == stub_status


def test_periodic_actions_未配置时不暴露字段(client, monkeypatch):
    """没配规则时 periodic_actions 字段不应该被暴露（或为空）"""
    from backend.api import source_routes

    mgr = _make_mock_mgr([])
    monkeypatch.setattr(source_routes, "_get_mgr", lambda channel=0: mgr)

    resp = client.get("/api/v1/source/detection/results")
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert "periodic_actions" not in body or not body.get("periodic_actions"), \
        f"未配规则时不应暴露 periodic_actions 字段或应为空, got {body.get('periodic_actions')}"
