"""验证 source_routes.get_detection_results 暴露 PT/CT 三档新字段（v3.5.0+）。

新字段：
  - last_step_durations    — 每个 step 最近一次 duration（来自 step_durations_history[-1]）
  - last_cycle_time        — 最近一个合格 cycle 的 CT
  - last_cycle_time_with_ng — 最近一个 cycle 的 CT（含 NG）
  - current_cycle_time     — 当前正在运行的 cycle 已耗时（time.time() - cycle_start_time）
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock


def _make_mock_mgr(*, cycle_times, ng_cycle_times, step_history,
                    cycle_start_time=None, is_detecting=False):
    mgr = MagicMock()
    mgr.channel_id = 0
    mgr.events_log = []
    mgr.cycle_times = cycle_times
    mgr.ng_cycle_times = ng_cycle_times
    mgr.step_durations_history = step_history
    mgr.step_counts = {}
    mgr.step_screenshots = {}
    mgr.step_detection_times = {}
    mgr.step_durations = {"A": 0.5}  # 当前 cycle 中累加的
    # v3.5.x: PT 合并档新增字段（默认空 dict 让路由代码走"无累计"分支）
    mgr.step_cycle_durations = {}
    mgr.step_cycle_durations_history = {}
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
    mgr.is_detecting = is_detecting
    mgr.cycle_start_time = cycle_start_time
    mgr.source_type = "test"
    mgr.fps_actual = 0.0
    mgr.fps_inference = 0.0
    mgr.latency = 0.0
    mgr.get_detections = MagicMock(return_value=[])
    mgr.get_periodic_actions_status = MagicMock(return_value=[])
    mgr.get_recording_failures = MagicMock(return_value=[])
    return mgr


def test_last_step_durations_取最近一项(client, monkeypatch):
    from backend.api import source_routes
    mgr = _make_mock_mgr(
        cycle_times=[10.0, 11.5, 12.0],
        ng_cycle_times=[],
        step_history={
            "A": [1.0, 1.2, 1.4, 1.5],
            "B": [2.0],
        },
    )
    monkeypatch.setattr(source_routes, "_get_mgr", lambda channel=0: mgr)

    resp = client.get("/api/v1/source/detection/results")
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()

    assert body["last_step_durations"] == {"A": 1.5, "B": 2.0}, \
        f"last_step_durations 应取每个 label 历史的最后一项; got {body.get('last_step_durations')}"
    # 平均依然在
    assert "A" in body["avg_step_durations"]


def test_last_cycle_time_取最后(client, monkeypatch):
    from backend.api import source_routes
    mgr = _make_mock_mgr(
        cycle_times=[8.0, 9.0, 10.0],
        ng_cycle_times=[15.0, 20.0],
        step_history={},
    )
    monkeypatch.setattr(source_routes, "_get_mgr", lambda channel=0: mgr)

    body = client.get("/api/v1/source/detection/results").json()

    assert body["last_cycle_time"] == 10.0, "last_cycle_time 应是合格 cycle_times 最后一项"
    # max(cycle_times[-1], ng_cycle_times[-1]) = max(10.0, 20.0) = 20.0
    assert body["last_cycle_time_with_ng"] == 20.0, \
        f"含 NG 应取两者最大; got {body.get('last_cycle_time_with_ng')}"


def test_current_cycle_time_未在跑时为_0(client, monkeypatch):
    """is_detecting=False 时 current_cycle_time 必须是 0（不能反映虚假数据）"""
    from backend.api import source_routes
    mgr = _make_mock_mgr(
        cycle_times=[],
        ng_cycle_times=[],
        step_history={},
        cycle_start_time=time.time() - 5,
        is_detecting=False,  # 关键：未在检测
    )
    monkeypatch.setattr(source_routes, "_get_mgr", lambda channel=0: mgr)

    body = client.get("/api/v1/source/detection/results").json()
    assert body["current_cycle_time"] == 0, \
        f"未检测时 current_cycle_time 应为 0; got {body.get('current_cycle_time')}"


def test_current_cycle_time_在跑时大致正确(client, monkeypatch):
    """is_detecting=True + cycle_start_time 5 秒前 → current_cycle_time 应 ≈ 5"""
    from backend.api import source_routes
    mgr = _make_mock_mgr(
        cycle_times=[],
        ng_cycle_times=[],
        step_history={},
        cycle_start_time=time.time() - 5.0,
        is_detecting=True,
    )
    monkeypatch.setattr(source_routes, "_get_mgr", lambda channel=0: mgr)

    body = client.get("/api/v1/source/detection/results").json()
    val = body["current_cycle_time"]
    assert 4.5 < val < 6.0, \
        f"current_cycle_time 应在 4.5~6.0 (~5 秒) 范围; got {val}"


def test_空_history_新字段优雅缺省(client, monkeypatch):
    from backend.api import source_routes
    mgr = _make_mock_mgr(
        cycle_times=[],
        ng_cycle_times=[],
        step_history={},
    )
    monkeypatch.setattr(source_routes, "_get_mgr", lambda channel=0: mgr)

    body = client.get("/api/v1/source/detection/results").json()
    assert body["last_cycle_time"] == 0
    assert body["last_cycle_time_with_ng"] == 0
    assert body["current_cycle_time"] == 0
    assert body["last_step_durations"] == {}
    # v3.5.x: PT 合并档字段在空状态下应为空 dict, 不能缺失
    assert body["cycle_sum_step_durations"] == {}
    assert body["last_cycle_sum_step_durations"] == {}
    assert body["avg_cycle_sum_step_durations"] == {}


def test_PT合并档_暴露三个字段_v3_5_x(client, monkeypatch):
    """验证 PT 合并档（同步骤同周期 SUM）三个新字段都正确暴露。

    场景：步骤 A 在 3 个已结束周期里 SUM 分别为 [3.0, 4.5, 6.0]，
         当前正在跑的 cycle 中 A 已累计 1.5s。
    """
    from backend.api import source_routes
    mgr = _make_mock_mgr(
        cycle_times=[10.0, 11.0, 12.0],
        ng_cycle_times=[],
        step_history={"A": [1.5, 1.5, 2.25, 2.25, 3.0, 3.0]},  # 段级 history
        cycle_start_time=time.time() - 1.5,
        is_detecting=True,
    )
    # 周期级 SUM（A 在 3 个周期分别 SUM）
    mgr.step_cycle_durations_history = {"A": [3.0, 4.5, 6.0]}
    # 当前周期中 A 已累计
    mgr.step_cycle_durations = {"A": 1.5}
    monkeypatch.setattr(source_routes, "_get_mgr", lambda channel=0: mgr)

    body = client.get("/api/v1/source/detection/results").json()

    assert body["cycle_sum_step_durations"] == {"A": 1.5}, \
        f"当前周期 SUM; got {body.get('cycle_sum_step_durations')}"
    assert body["last_cycle_sum_step_durations"] == {"A": 6.0}, \
        f"最近一周期 SUM 应取 history[-1]; got {body.get('last_cycle_sum_step_durations')}"
    # 平均 (3.0 + 4.5 + 6.0) / 3 = 4.5
    assert body["avg_cycle_sum_step_durations"] == {"A": 4.5}, \
        f"周期 SUM 平均; got {body.get('avg_cycle_sum_step_durations')}"
