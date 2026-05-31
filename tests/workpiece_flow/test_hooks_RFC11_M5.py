"""RFC 11 M5 — 5 个插件 hook 触发测试.

测试矩阵:
  - workpiece_flow_enter (1)
  - workpiece_flow_station_done (1)
  - workpiece_flow_completed + override_final_result (2)
  - workpiece_flow_timeout + override_timeout_action (2)
  - workpiece_flow_short_circuit (1)
  - RETURNABLE_HOOK_FIELDS 注册 (1)

合计 8 测试.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def hook_capture(monkeypatch):
    """Patch fire_plugin_hook 来捕获所有 hook 调用."""
    captured = []

    def fake_fire(hook_name, action, phase, ctx, *args, **kwargs):
        captured.append({
            "hook": hook_name, "action": action, "phase": phase, "ctx": dict(ctx),
        })
        return {}  # 默认空, 单个测试可以注入 override

    monkeypatch.setattr(
        "backend.plugin_system.hook_dispatch.fire_plugin_hook",
        fake_fire,
    )
    return captured


def _setup_flow(coord, db, name="hook-line", stations=(0, 1), trigger_mode="time_window",
                short_circuit=True, workpiece_timeout_ms=60000, timeout_action="force_ng"):
    from tests.workpiece_flow.conftest import make_flow_row
    db.add(make_flow_row(
        1, name, list(stations), trigger_mode=trigger_mode,
        short_circuit=short_circuit,
        workpiece_timeout_ms=workpiece_timeout_ms,
        timeout_action=timeout_action,
    ))
    db.commit()
    coord.reload_flows(db)


class TestHookFire:
    def test_enter_hook_fired(self, fresh_coordinator, in_memory_db, hook_capture):
        coord = fresh_coordinator
        _setup_flow(coord, in_memory_db)

        coord.on_cycle_started(0, 10, in_memory_db)

        hooks = [h for h in hook_capture if h["hook"] == "workpiece_flow_enter"]
        assert len(hooks) == 1
        ctx = hooks[0]["ctx"]
        assert ctx["flow_config_id"] == 1
        assert ctx["station_channel_ids"] == [0, 1]

    def test_station_done_hook_fired(self, fresh_coordinator, in_memory_db, hook_capture):
        coord = fresh_coordinator
        _setup_flow(coord, in_memory_db, stations=(0, 1, 2))

        coord.on_cycle_started(0, 10, in_memory_db)
        coord.on_cycle_settled(0, 10, True, in_memory_db)

        hooks = [h for h in hook_capture if h["hook"] == "workpiece_flow_station_done"]
        assert len(hooks) == 1
        ctx = hooks[0]["ctx"]
        assert ctx["station_index"] == 0
        assert ctx["is_good"] is True

    def test_completed_hook_fired_on_ok(self, fresh_coordinator, in_memory_db, hook_capture):
        coord = fresh_coordinator
        _setup_flow(coord, in_memory_db, stations=(0, 1))

        coord.on_cycle_started(0, 10, in_memory_db)
        coord.on_cycle_settled(0, 10, True, in_memory_db)
        coord.on_cycle_started(1, 20, in_memory_db)
        coord.on_cycle_settled(1, 20, True, in_memory_db)

        hooks = [h for h in hook_capture if h["hook"] == "workpiece_flow_completed"]
        assert len(hooks) == 1
        ctx = hooks[0]["ctx"]
        assert ctx["final_result"] == "OK"
        assert ctx["reason"] == "completed"

    def test_completed_hook_override_final_result(self, fresh_coordinator, in_memory_db, monkeypatch):
        """插件 hook 可以把 final_result 从 OK 改成 NG."""
        from backend.models.mes_models import WorkpieceFlowRun

        def fake_fire(hook_name, action, phase, ctx, *args, **kwargs):
            if hook_name == "workpiece_flow_completed":
                return {"override_final_result": "NG"}
            return {}

        monkeypatch.setattr(
            "backend.plugin_system.hook_dispatch.fire_plugin_hook",
            fake_fire,
        )

        coord = fresh_coordinator
        _setup_flow(coord, in_memory_db, stations=(0, 1))

        coord.on_cycle_started(0, 10, in_memory_db)
        coord.on_cycle_settled(0, 10, True, in_memory_db)
        coord.on_cycle_started(1, 20, in_memory_db)
        coord.on_cycle_settled(1, 20, True, in_memory_db)  # 所有 OK, 但 hook override 成 NG

        rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
        assert rows[0].status == "completed"
        assert rows[0].final_result == "NG"  # 被 hook 改写

    def test_short_circuit_hook_fired(self, fresh_coordinator, in_memory_db, hook_capture):
        coord = fresh_coordinator
        _setup_flow(coord, in_memory_db, stations=(0, 1, 2), short_circuit=True)

        coord.on_cycle_started(0, 10, in_memory_db)
        coord.on_cycle_settled(0, 10, False, in_memory_db)  # NG 短路

        hooks = [h for h in hook_capture if h["hook"] == "workpiece_flow_short_circuit"]
        assert len(hooks) == 1
        # 同时 completed 也会触发 (终态都触发 completed)
        completed = [h for h in hook_capture if h["hook"] == "workpiece_flow_completed"]
        assert len(completed) == 1
        assert completed[0]["ctx"]["reason"] == "short_circuited"

    def test_timeout_hook_fired(self, fresh_coordinator, in_memory_db, monkeypatch):
        from backend.models.mes_models import WorkpieceFlowRun

        captured = []

        def fake_fire(hook_name, action, phase, ctx, *args, **kwargs):
            captured.append({"hook": hook_name, "ctx": dict(ctx)})
            return {}

        monkeypatch.setattr(
            "backend.plugin_system.hook_dispatch.fire_plugin_hook",
            fake_fire,
        )

        coord = fresh_coordinator
        _setup_flow(coord, in_memory_db, workpiece_timeout_ms=80, timeout_action="force_ng")

        # Patch SessionLocal 让 timeout 回调拿到 fixture db
        orig_close = in_memory_db.close
        in_memory_db.close = lambda: None
        monkeypatch.setattr("backend.db.database.SessionLocal", lambda: in_memory_db)

        try:
            coord.on_cycle_started(0, 10, in_memory_db)
            time.sleep(0.25)
        finally:
            in_memory_db.close = orig_close

        timeout_hooks = [h for h in captured if h["hook"] == "workpiece_flow_timeout"]
        assert len(timeout_hooks) == 1
        assert timeout_hooks[0]["ctx"]["default_action"] == "force_ng"

    def test_timeout_hook_override_action(self, fresh_coordinator, in_memory_db, monkeypatch):
        """插件 hook 可以把 timeout_action 从 force_ng 改成 drop."""
        from backend.models.mes_models import WorkpieceFlowRun

        def fake_fire(hook_name, action, phase, ctx, *args, **kwargs):
            if hook_name == "workpiece_flow_timeout":
                return {"override_timeout_action": "drop"}
            return {}

        monkeypatch.setattr(
            "backend.plugin_system.hook_dispatch.fire_plugin_hook",
            fake_fire,
        )

        coord = fresh_coordinator
        _setup_flow(coord, in_memory_db, workpiece_timeout_ms=80, timeout_action="force_ng")

        orig_close = in_memory_db.close
        in_memory_db.close = lambda: None
        monkeypatch.setattr("backend.db.database.SessionLocal", lambda: in_memory_db)

        try:
            coord.on_cycle_started(0, 10, in_memory_db)
            time.sleep(0.25)
        finally:
            in_memory_db.close = orig_close

        in_memory_db.expire_all()
        rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
        assert rows[0].status == "timeout"
        # drop 行为: final_result=None (因为 hook override 改了 effective action)
        assert rows[0].final_result is None


class TestReturnableFieldsRegistered:
    def test_5_hooks_in_whitelist(self):
        """5 个新 hook 必须都在 RETURNABLE_HOOK_FIELDS 里."""
        from backend.plugin_system.hook_dispatch import RETURNABLE_HOOK_FIELDS
        assert "workpiece_flow_enter" in RETURNABLE_HOOK_FIELDS
        assert "workpiece_flow_station_done" in RETURNABLE_HOOK_FIELDS
        assert "workpiece_flow_completed" in RETURNABLE_HOOK_FIELDS
        assert "workpiece_flow_timeout" in RETURNABLE_HOOK_FIELDS
        assert "workpiece_flow_short_circuit" in RETURNABLE_HOOK_FIELDS

        assert RETURNABLE_HOOK_FIELDS["workpiece_flow_completed"] == {"override_final_result"}
        assert RETURNABLE_HOOK_FIELDS["workpiece_flow_timeout"] == {"override_timeout_action"}
        assert RETURNABLE_HOOK_FIELDS["workpiece_flow_enter"] == set()
        assert RETURNABLE_HOOK_FIELDS["workpiece_flow_station_done"] == set()
        assert RETURNABLE_HOOK_FIELDS["workpiece_flow_short_circuit"] == set()
