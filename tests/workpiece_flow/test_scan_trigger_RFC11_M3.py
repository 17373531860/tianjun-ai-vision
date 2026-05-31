"""RFC 11 M3 — ScanTrigger (复用 on_scan_received + scan_pair 互斥).

测试矩阵:
  A. Trigger 基础: evaluate_scan 入口 / 非入口 / 模式切换 (5)
  B. Coordinator 集成: on_scan_received 触发新工件 (3)
  C. each_station 策略: 多次扫码同 serial 幂等 (2)
  D. scan_device_id 匹配 (2)
  E. 工件落库 (1)
  F. 完整流水线: 扫码 + 多工位结算 (2)

合计 ~15 测试.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# =============================================================
# A. Trigger 基础
# =============================================================

class TestScanTriggerBasics:
    def test_entry_strategy_entry_channel_triggers(self):
        from backend.services.flow_triggers.scan_trigger import ScanTrigger

        t = ScanTrigger()
        t._flow = {
            "id": 1,
            "station_channel_ids": [0, 1, 2],
            "scan_bind_strategy": "entry",
            "scan_device_id": None,
        }

        # 入口工位扫到 → 返回 barcode
        s = t.evaluate_scan(barcode="ABC123", channel_id=0)
        assert s == "ABC123"

    def test_entry_strategy_non_entry_channel_silent(self):
        from backend.services.flow_triggers.scan_trigger import ScanTrigger

        t = ScanTrigger()
        t._flow = {
            "id": 1,
            "station_channel_ids": [0, 1, 2],
            "scan_bind_strategy": "entry",
            "scan_device_id": None,
        }

        # 非入口工位 → 静默
        assert t.evaluate_scan(barcode="ABC", channel_id=1) is None
        assert t.evaluate_scan(barcode="ABC", channel_id=2) is None

    def test_each_station_strategy_all_channels_trigger(self):
        from backend.services.flow_triggers.scan_trigger import ScanTrigger

        t = ScanTrigger()
        t._flow = {
            "id": 1,
            "station_channel_ids": [0, 1, 2],
            "scan_bind_strategy": "each_station",
            "scan_device_id": None,
        }

        # 任何成员工位都触发
        assert t.evaluate_scan(barcode="ABC", channel_id=0) == "ABC"
        assert t.evaluate_scan(barcode="ABC", channel_id=1) == "ABC"
        assert t.evaluate_scan(barcode="ABC", channel_id=2) == "ABC"

    def test_each_station_strategy_non_member_silent(self):
        from backend.services.flow_triggers.scan_trigger import ScanTrigger

        t = ScanTrigger()
        t._flow = {
            "id": 1,
            "station_channel_ids": [0, 1],
            "scan_bind_strategy": "each_station",
            "scan_device_id": None,
        }
        assert t.evaluate_scan(barcode="ABC", channel_id=99) is None

    def test_no_barcode_or_no_flow_returns_none(self):
        from backend.services.flow_triggers.scan_trigger import ScanTrigger

        t = ScanTrigger()
        # 没 flow
        assert t.evaluate_scan(barcode="ABC", channel_id=0) is None

        t._flow = {"id": 1, "station_channel_ids": [0]}
        # 没 barcode
        assert t.evaluate_scan(barcode="", channel_id=0) is None
        assert t.evaluate_scan(barcode=None, channel_id=0) is None


# =============================================================
# B. Coordinator 集成: on_scan_received
# =============================================================

class TestCoordinatorScanReceived:
    def _setup_scan_flow(self, coord, db, strategy="entry"):
        from tests.workpiece_flow.conftest import make_flow_row
        db.add(make_flow_row(
            1, "scan-line", [0, 1, 2], trigger_mode="scan",
        ))
        db.commit()
        coord.reload_flows(db)
        # 注: scan_bind_strategy 写在 make_flow_row 的默认值 "entry", 这里如果要
        # each_station 需手动改; 简化测试只测 entry
        flow = coord.get_flow(1)
        assert flow["trigger_mode"] == "scan"

    def test_scan_entry_creates_run(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._setup_scan_flow(coord, in_memory_db)

        u = coord.on_scan_received(
            channel_id=0,
            barcode="WP-001",
            scanner_device_id=1,
            project_id=None,  # 测试没 project
            db=in_memory_db,
        )
        assert u is not None
        in_flight = coord.list_in_flight(1)
        assert len(in_flight) == 1
        assert in_flight[0]["serial_no"] == "WP-001"

    def test_scan_non_entry_silent(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._setup_scan_flow(coord, in_memory_db)

        u = coord.on_scan_received(
            channel_id=1,  # 非入口
            barcode="WP-002",
            scanner_device_id=1,
            project_id=None,
            db=in_memory_db,
        )
        assert u is None
        assert coord.list_in_flight(1) == []

    def test_scan_channel_not_in_flow_silent(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._setup_scan_flow(coord, in_memory_db)

        u = coord.on_scan_received(
            channel_id=99,
            barcode="WP-003",
            scanner_device_id=1,
            project_id=None,
            db=in_memory_db,
        )
        assert u is None


# =============================================================
# C. each_station 策略幂等
# =============================================================

class TestEachStationIdempotent:
    def _make_each_station_flow(self, coord, db):
        from tests.workpiece_flow.conftest import make_flow_row
        from backend.models.mes_models import WorkpieceFlowConfig

        row = make_flow_row(1, "each-line", [0, 1], trigger_mode="scan")
        row.scan_bind_strategy = "each_station"
        db.add(row)
        db.commit()
        coord.reload_flows(db)

    def test_duplicate_scan_same_serial_idempotent(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._make_each_station_flow(coord, in_memory_db)

        u1 = coord.on_scan_received(
            channel_id=0, barcode="WP-X", scanner_device_id=1,
            project_id=None, db=in_memory_db,
        )
        # 第二个工位再扫到同一码 → 走 each_station 策略, 但 on_workpiece_enter
        # 内部 1 秒内幂等去重
        u2 = coord.on_scan_received(
            channel_id=1, barcode="WP-X", scanner_device_id=1,
            project_id=None, db=in_memory_db,
        )
        assert u1 is not None
        assert u1 == u2

        # 仍然只有 1 个 in-flight
        assert len(coord.list_in_flight(1)) == 1

    def test_different_serials_different_runs(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._make_each_station_flow(coord, in_memory_db)

        u1 = coord.on_scan_received(
            channel_id=0, barcode="WP-A", scanner_device_id=1,
            project_id=None, db=in_memory_db,
        )
        u2 = coord.on_scan_received(
            channel_id=0, barcode="WP-B", scanner_device_id=1,
            project_id=None, db=in_memory_db,
        )
        assert u1 != u2
        assert len(coord.list_in_flight(1)) == 2


# =============================================================
# D. scan_device_id 匹配
# =============================================================

class TestScanDeviceMatch:
    def _make_with_device(self, coord, db, configured_device):
        from tests.workpiece_flow.conftest import make_flow_row
        row = make_flow_row(
            1, "device-bound", [0, 1], trigger_mode="scan",
            scan_device_id=configured_device,
        )
        db.add(row)
        db.commit()
        coord.reload_flows(db)

    def test_matching_device_triggers(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._make_with_device(coord, in_memory_db, configured_device=5)

        u = coord.on_scan_received(
            channel_id=0, barcode="WP-1", scanner_device_id=5,
            project_id=None, db=in_memory_db,
        )
        assert u is not None

    def test_non_matching_device_silent(self, fresh_coordinator, in_memory_db):
        coord = fresh_coordinator
        self._make_with_device(coord, in_memory_db, configured_device=5)

        u = coord.on_scan_received(
            channel_id=0, barcode="WP-1", scanner_device_id=999,
            project_id=None, db=in_memory_db,
        )
        assert u is None


# =============================================================
# E. 工件落库
# =============================================================

class TestWorkpieceRegistration:
    def test_workpiece_registered_when_project_present(self, fresh_coordinator, in_memory_db):
        from backend.models.models import Project
        from backend.models.mes_models import Workpiece
        from tests.workpiece_flow.conftest import make_flow_row

        # 建一个 project 让 WorkpieceService.register 能跑
        proj = Project(name="p1")
        in_memory_db.add(proj)
        in_memory_db.commit()
        in_memory_db.refresh(proj)

        coord = fresh_coordinator
        in_memory_db.add(make_flow_row(1, "with-proj", [0, 1], trigger_mode="scan"))
        in_memory_db.commit()
        coord.reload_flows(in_memory_db)

        coord.on_scan_received(
            channel_id=0, barcode="WP-REG",
            scanner_device_id=1, project_id=proj.id,
            db=in_memory_db,
        )

        wps = in_memory_db.query(Workpiece).filter_by(serial_no="WP-REG").all()
        assert len(wps) == 1
        assert wps[0].project_id == proj.id


# =============================================================
# F. 完整流水线: 扫码 + 多工位结算
# =============================================================

class TestEndToEndScanFlow:
    def test_full_two_station_scan_flow(self, fresh_coordinator, in_memory_db):
        from backend.models.mes_models import WorkpieceFlowRun
        from tests.workpiece_flow.conftest import make_flow_row

        coord = fresh_coordinator
        in_memory_db.add(make_flow_row(1, "scan-2", [0, 1], trigger_mode="scan"))
        in_memory_db.commit()
        coord.reload_flows(in_memory_db)

        # 1. 扫码触发新工件
        coord.on_scan_received(
            channel_id=0, barcode="WP-100", scanner_device_id=1,
            project_id=None, db=in_memory_db,
        )
        # 2. 工位 0 cycle_start → 应该 FIFO 绑到 WP-100
        coord.on_cycle_started(0, 10, in_memory_db)
        # 3. 工位 0 OK
        coord.on_cycle_settled(0, 10, True, in_memory_db)
        # 4. 工位 1 cycle_start → FIFO 绑到 WP-100
        coord.on_cycle_started(1, 20, in_memory_db)
        # 5. 工位 1 OK → 终态
        coord.on_cycle_settled(1, 20, True, in_memory_db)

        rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
        assert len(rows) == 1
        assert rows[0].serial_no == "WP-100"
        assert rows[0].status == "completed"
        assert rows[0].final_result == "OK"
        assert rows[0].trigger_mode == "scan"

    def test_scan_then_ng_short_circuit(self, fresh_coordinator, in_memory_db):
        from backend.models.mes_models import WorkpieceFlowRun
        from tests.workpiece_flow.conftest import make_flow_row

        coord = fresh_coordinator
        in_memory_db.add(make_flow_row(1, "scan-sc", [0, 1, 2], trigger_mode="scan"))
        in_memory_db.commit()
        coord.reload_flows(in_memory_db)

        coord.on_scan_received(
            channel_id=0, barcode="WP-NG", scanner_device_id=1,
            project_id=None, db=in_memory_db,
        )
        coord.on_cycle_started(0, 10, in_memory_db)
        coord.on_cycle_settled(0, 10, False, in_memory_db)  # 短路

        rows = in_memory_db.query(WorkpieceFlowRun).filter_by(flow_config_id=1).all()
        assert rows[0].status == "short_circuited"
        assert rows[0].final_result == "NG"
        assert rows[0].serial_no == "WP-NG"
