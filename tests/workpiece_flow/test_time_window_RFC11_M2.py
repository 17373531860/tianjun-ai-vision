"""RFC 11 M2 — TimeWindowTrigger (FIFO + cycle_start watch + max_in_flight 上限).

测试矩阵:
  A. Trigger 基础: evaluate_cycle_start 入口工位 / 非入口工位 (3)
  B. Coordinator 集成: on_cycle_started 入口工位自动 enter (3)
  C. FIFO 顺序: 多工件按入队顺序绑 cycle (2)
  D. 完整流水线: 2 工位 OK + 终态结算 (2)
  E. 短路: 工位 1 NG 立即结算 (2)
  F. FIFO 上限: max_in_flight 超上限拒绝 (2)
  G. 超时: workpiece_timeout 触发 force_ng / drop (2)
  H. 跳工位 / 乱序: 异常场景 (2)

合计 ~18 测试.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest


# =============================================================
# A. Trigger 基础
# =============================================================

class TestTimeWindowTriggerBasics:
    def test_entry_channel_returns_serial(self):
        from backend.services.flow_triggers.time_window_trigger import TimeWindowTrigger

        t = TimeWindowTrigger()
        flow = {"id": 7, "station_channel_ids": [0, 1, 2]}
        t._flow = flow

        s = t.evaluate_cycle_start(channel_id=0)
        assert s is not None
        assert s.startswith("auto-7-")
        assert len(s) == len("auto-7-") + 8

    def test_non_entry_channel_returns_none(self):
        from backend.services.flow_triggers.time_window_trigger import TimeWindowTrigger

        t = TimeWindowTrigger()
        t._flow = {"id": 1, "station_channel_ids": [0, 1, 2]}
        assert t.evaluate_cycle_start(channel_id=1) is None
        assert t.evaluate_cycle_start(channel_id=2) is None
        assert t.evaluate_cycle_start(channel_id=99) is None

    def test_serials_are_unique(self):
        from backend.services.flow_triggers.time_window_trigger import TimeWindowTrigger

        t = TimeWindowTrigger()
        t._flow = {"id": 1, "station_channel_ids": [0]}
        s1 = t.evaluate_cycle_start(0)
        s2 = t.evaluate_cycle_start(0)
        assert s1 != s2


# =============================================================
# B. Coordinator 集成: on_cycle_started 入口工位自动 enter
# =============================================================

class TestCoordinatorEntryAutoEnter:
    def _setup(self, coord, db):
        from tests.workpiece_flow.conftest import make_flow_row
        db.add(make_flow_row(1, "line-A", [0, 1, 2]))
        db.commit()
        n = coord.reload_flows(db)
        assert n == 1

    def test_entry_cycle_start_creates_run(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._setup(coord, in_memory_db)

        # 入口工位 0 cycle_start
        coord.on_cycle_started(channel_id=0, cycle_id=100, db=in_memory_db)

        in_flight = coord.list_in_flight(1)
        assert len(in_flight) == 1
        run = in_flight[0]
        assert run["serial_no"].startswith("auto-1-")
        assert run["station_cycle_ids"] == [100, None, None]
        assert run["trigger_mode"] == "time_window"

    def test_non_entry_cycle_without_run_is_silent(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._setup(coord, in_memory_db)

        # 非入口工位 cycle_start 但还没工件 → 静默忽略
        coord.on_cycle_started(channel_id=1, cycle_id=200, db=in_memory_db)
        assert coord.list_in_flight(1) == []

    def test_channel_not_in_flow_is_silent(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._setup(coord, in_memory_db)

        coord.on_cycle_started(channel_id=99, cycle_id=300, db=in_memory_db)
        assert coord.list_in_flight(1) == []


# =============================================================
# C. FIFO 顺序: 多工件按入队顺序绑 cycle
# =============================================================

class TestFIFOOrder:
    def _setup(self, coord, db, fifo_max=3):
        from tests.workpiece_flow.conftest import make_flow_row
        db.add(make_flow_row(1, "line-A", [0, 1, 2], fifo_max_in_flight=fifo_max))
        db.commit()
        coord.reload_flows(db)

    def test_three_workpieces_fifo_bind(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._setup(coord, in_memory_db)

        # 三个工件依次进入入口
        coord.on_cycle_started(channel_id=0, cycle_id=10, db=in_memory_db)
        coord.on_cycle_started(channel_id=0, cycle_id=11, db=in_memory_db)
        coord.on_cycle_started(channel_id=0, cycle_id=12, db=in_memory_db)

        in_flight = coord.list_in_flight(1)
        assert len(in_flight) == 3
        # FIFO: 入队顺序
        assert in_flight[0]["station_cycle_ids"][0] == 10
        assert in_flight[1]["station_cycle_ids"][0] == 11
        assert in_flight[2]["station_cycle_ids"][0] == 12

        # 工位 1 来一个 cycle → 绑到第一个工件
        coord.on_cycle_started(channel_id=1, cycle_id=20, db=in_memory_db)
        in_flight = coord.list_in_flight(1)
        assert in_flight[0]["station_cycle_ids"][1] == 20
        assert in_flight[1]["station_cycle_ids"][1] is None

    def test_settle_progression(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._setup(coord, in_memory_db)

        coord.on_cycle_started(channel_id=0, cycle_id=10, db=in_memory_db)
        coord.on_cycle_settled(channel_id=0, cycle_id=10, is_good=True, db=in_memory_db)

        in_flight = coord.list_in_flight(1)
        assert len(in_flight) == 1
        assert in_flight[0]["station_results"] == ["OK", None, None]
        assert in_flight[0]["current_station_index"] == 1


# =============================================================
# D. 完整流水线: 2 工位 OK + 终态结算
# =============================================================

class TestCompleteFlow:
    def _setup(self, coord, db, stations=(0, 1), short_circuit=True):
        from tests.workpiece_flow.conftest import make_flow_row
        db.add(make_flow_row(1, "line-A", list(stations), short_circuit=short_circuit))
        db.commit()
        coord.reload_flows(db)

    def test_two_station_all_ok(self, fresh_coordinator, in_memory_db):
        from backend.models.mes_models import WorkpieceFlowRun

        coord = fresh_coordinator
        self._setup(coord, in_memory_db)

        # 工位 0 走完 OK
        coord.on_cycle_started(channel_id=0, cycle_id=10, db=in_memory_db)
        coord.on_cycle_settled(channel_id=0, cycle_id=10, is_good=True, db=in_memory_db)
        # 工位 1 走完 OK
        coord.on_cycle_started(channel_id=1, cycle_id=20, db=in_memory_db)
        coord.on_cycle_settled(channel_id=1, cycle_id=20, is_good=True, db=in_memory_db)

        # in-flight 应该清空
        assert coord.list_in_flight(1) == []

        # DB run 应该 completed + OK
        rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
        assert len(rows) == 1
        assert rows[0].status == "completed"
        assert rows[0].final_result == "OK"
        assert rows[0].station_results == ["OK", "OK"]

    def test_three_station_last_ng_completed_ng(self, fresh_coordinator, in_memory_db):
        from backend.models.mes_models import WorkpieceFlowRun

        coord = fresh_coordinator
        self._setup(coord, in_memory_db, stations=(0, 1, 2), short_circuit=True)

        coord.on_cycle_started(0, 10, in_memory_db)
        coord.on_cycle_settled(0, 10, True, in_memory_db)
        coord.on_cycle_started(1, 20, in_memory_db)
        coord.on_cycle_settled(1, 20, True, in_memory_db)
        coord.on_cycle_started(2, 30, in_memory_db)
        coord.on_cycle_settled(2, 30, False, in_memory_db)  # 末站 NG

        rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
        assert rows[0].status == "completed"
        assert rows[0].final_result == "NG"
        assert rows[0].station_results == ["OK", "OK", "NG"]


# =============================================================
# E. 短路: 工位 1 NG 立即结算
# =============================================================

class TestShortCircuit:
    def _setup(self, coord, db, short_circuit=True):
        from tests.workpiece_flow.conftest import make_flow_row
        db.add(make_flow_row(1, "line-A", [0, 1, 2], short_circuit=short_circuit))
        db.commit()
        coord.reload_flows(db)

    def test_short_circuit_on_first_ng(self, fresh_coordinator, in_memory_db):
        from backend.models.mes_models import WorkpieceFlowRun

        coord = fresh_coordinator
        self._setup(coord, in_memory_db, short_circuit=True)

        coord.on_cycle_started(0, 10, in_memory_db)
        coord.on_cycle_settled(0, 10, False, in_memory_db)  # 工位 0 NG → 短路

        # in-flight 已清出
        assert coord.list_in_flight(1) == []
        rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
        assert rows[0].status == "short_circuited"
        assert rows[0].final_result == "NG"
        assert rows[0].station_results == ["NG", None, None]

    def test_no_short_circuit_continues(self, fresh_coordinator, in_memory_db):
        from backend.models.mes_models import WorkpieceFlowRun

        coord = fresh_coordinator
        self._setup(coord, in_memory_db, short_circuit=False)

        coord.on_cycle_started(0, 10, in_memory_db)
        coord.on_cycle_settled(0, 10, False, in_memory_db)  # 工位 0 NG 但不短路

        # 应该还在 in-flight
        in_flight = coord.list_in_flight(1)
        assert len(in_flight) == 1
        assert in_flight[0]["station_results"] == ["NG", None, None]

        coord.on_cycle_started(1, 20, in_memory_db)
        coord.on_cycle_settled(1, 20, True, in_memory_db)
        coord.on_cycle_started(2, 30, in_memory_db)
        coord.on_cycle_settled(2, 30, True, in_memory_db)

        rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
        assert rows[0].status == "completed"
        assert rows[0].final_result == "NG"  # 有一个 NG 就是 NG


# =============================================================
# F. FIFO 上限: max_in_flight 超上限拒绝
# =============================================================

class TestFIFOMaxInFlight:
    def test_max_in_flight_rejects_excess(self, fresh_coordinator, in_memory_db):
        from tests.workpiece_flow.conftest import make_flow_row

        coord = fresh_coordinator
        in_memory_db.add(make_flow_row(1, "line-A", [0, 1], fifo_max_in_flight=2))
        in_memory_db.commit()
        coord.reload_flows(in_memory_db)

        coord.on_cycle_started(0, 10, in_memory_db)
        coord.on_cycle_started(0, 11, in_memory_db)
        assert len(coord.list_in_flight(1)) == 2

        # 第 3 个应该被拒绝 (max=2)
        coord.on_cycle_started(0, 12, in_memory_db)
        in_flight = coord.list_in_flight(1)
        assert len(in_flight) == 2  # 仍然只有 2 个

        # 第三个 cycle 没被绑 (因为 FIFO 已满, on_workpiece_enter 被拒绝, 后续没匹配 run)
        # 实际上第三个 cycle 会绑给前两个工件之一? 不会, 因为它们工位 0 已被绑
        # → cycle 12 应该游离, 不在任何 in-flight 里
        all_bound_cycles = []
        for r in in_flight:
            all_bound_cycles.extend(r["station_cycle_ids"])
        assert 12 not in all_bound_cycles

    def test_duplicate_serial_within_1sec_is_idempotent(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        from tests.workpiece_flow.conftest import make_flow_row
        in_memory_db.add(make_flow_row(1, "line-A", [0, 1]))
        in_memory_db.commit()
        coord.reload_flows(in_memory_db)

        # 手动 on_workpiece_enter 两次同 serial
        u1 = coord.on_workpiece_enter(
            flow_config_id=1, serial_no="ABC",
            trigger_mode="time_window", db=in_memory_db,
        )
        u2 = coord.on_workpiece_enter(
            flow_config_id=1, serial_no="ABC",
            trigger_mode="time_window", db=in_memory_db,
        )
        assert u1 is not None
        assert u1 == u2  # 幂等

        in_flight = coord.list_in_flight(1)
        assert len(in_flight) == 1  # 只有一个


# =============================================================
# G. 超时
# =============================================================

class TestWorkpieceTimeout:
    """超时 timer 测试.

    技术细节: Timer 回调 _on_workpiece_timeout 调 SessionLocal() 新建 session,
    跟 fixture 的 in_memory_db 不是同一个. 用 monkeypatch 让 SessionLocal 返回
    in_memory_db (并把 db.close() 屏蔽防止过早关闭).
    """

    def _patch_session_local(self, monkeypatch, db):
        """把 SessionLocal 替换成返回 db 的工厂."""
        orig_close = db.close
        db.close = lambda: None  # 防 _on_workpiece_timeout 关掉

        def _factory():
            return db

        monkeypatch.setattr("backend.db.database.SessionLocal", _factory)
        return orig_close

    def test_timeout_force_ng(self, fresh_coordinator, in_memory_db, monkeypatch):
        from backend.models.mes_models import WorkpieceFlowRun
        from tests.workpiece_flow.conftest import make_flow_row

        coord = fresh_coordinator
        in_memory_db.add(make_flow_row(
            1, "line-A", [0, 1],
            workpiece_timeout_ms=100,
            timeout_action="force_ng",
        ))
        in_memory_db.commit()
        coord.reload_flows(in_memory_db)

        orig_close = self._patch_session_local(monkeypatch, in_memory_db)
        try:
            coord.on_cycle_started(0, 10, in_memory_db)
            time.sleep(0.3)

            in_memory_db.expire_all()
            rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
            assert len(rows) == 1
            assert rows[0].status == "timeout"
            assert rows[0].final_result == "NG"
        finally:
            in_memory_db.close = orig_close

    def test_timeout_drop_action(self, fresh_coordinator, in_memory_db, monkeypatch):
        from backend.models.mes_models import WorkpieceFlowRun
        from tests.workpiece_flow.conftest import make_flow_row

        coord = fresh_coordinator
        in_memory_db.add(make_flow_row(
            1, "line-A", [0, 1],
            workpiece_timeout_ms=100,
            timeout_action="drop",
        ))
        in_memory_db.commit()
        coord.reload_flows(in_memory_db)

        orig_close = self._patch_session_local(monkeypatch, in_memory_db)
        try:
            coord.on_cycle_started(0, 10, in_memory_db)
            time.sleep(0.3)

            in_memory_db.expire_all()
            rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
            assert rows[0].status == "timeout"
            assert rows[0].final_result is None
        finally:
            in_memory_db.close = orig_close


# =============================================================
# H. 跳工位 / 乱序
# =============================================================

class TestEdgeCases:
    def test_skipped_station_completed_ng(self, fresh_coordinator, in_memory_db):
        """工件跳过中间工位 → 末站结算时检测到中间有 None → final_result=NG."""
        from backend.models.mes_models import WorkpieceFlowRun
        from tests.workpiece_flow.conftest import make_flow_row

        coord = fresh_coordinator
        in_memory_db.add(make_flow_row(1, "line-A", [0, 1, 2], short_circuit=False))
        in_memory_db.commit()
        coord.reload_flows(in_memory_db)

        # 工位 0 OK, 跳过工位 1, 工位 2 OK
        coord.on_cycle_started(0, 10, in_memory_db)
        coord.on_cycle_settled(0, 10, True, in_memory_db)
        # 工位 1 跳过 (没有 cycle)
        coord.on_cycle_started(2, 30, in_memory_db)
        coord.on_cycle_settled(2, 30, True, in_memory_db)

        rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
        assert rows[0].status == "completed"
        assert rows[0].final_result == "NG"  # 因为工位 1 None
        assert rows[0].station_results == ["OK", None, "OK"]

    def test_reload_with_invalid_config_skipped(self, fresh_coordinator, in_memory_db):
        """station_channel_ids 不足 2 个 → 跳过."""
        from tests.workpiece_flow.conftest import make_flow_row

        coord = fresh_coordinator
        in_memory_db.add(make_flow_row(1, "single", [0]))  # 只 1 工位 → 非法
        in_memory_db.add(make_flow_row(2, "ok", [0, 1]))
        in_memory_db.commit()

        n = coord.reload_flows(in_memory_db)
        assert n == 1  # 只加载有效的那一个

        all_flows = coord.list_flows()
        assert len(all_flows) == 1
        assert all_flows[0]["name"] == "ok"
