# -*- coding: utf-8 -*-
"""工件-周期绑定的健壮性 (捷昌二期现场故障链)。

现场日志实录: cycle_start hook 里 link_to_cycle 抛
`UNIQUE constraint failed: workpiece_inspections.workpiece_id, cycle_id`,
异常在 `_inspecting_workpiece[ch] = wp_id` 之前冒出去 → pending 已 pop、
inspecting 未登记, 工件两头都不在 (违反不变量 14):
  - 监控页一直显示"未绑码 请扫描工件条码"
  - 开了"扫码后才计数"时 has_workpiece_in_flight 为假 → 一件都不入账

两层防线:
  1. link_to_cycle 同 (workpiece, cycle) 幂等, 不再抛 UNIQUE
  2. 万一仍落库失败, cycle_start 也要先保住在检身份再往外抛
"""
import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest


def _project_id(db):
    from backend.models.models import Project
    proj = db.query(Project).first()
    if proj is None:
        proj = Project(name="wpbind-fixture")
        db.add(proj)
        db.flush()
    return proj.id


def _mk_workpiece(db, serial="WPBIND-001"):
    from backend.models.mes_models import Workpiece
    wp = Workpiece(serial_no=serial, project_id=_project_id(db),
                   created_at=datetime.now())
    db.add(wp)
    db.flush()
    return wp


def _mk_cycle(db):
    from backend.models.models import DetectionCycle
    cycle = DetectionCycle(cycle_uuid=uuid.uuid4().hex, start_time=datetime.now())
    db.add(cycle)
    db.flush()
    return cycle


# ==================== 防线 1: link_to_cycle 幂等 ====================

def test_link_to_cycle_is_idempotent(db_session):
    """同一 workpiece+cycle 二次绑定返回同一行, 不抛 UNIQUE。"""
    from backend.services.workpiece import WorkpieceService
    svc = WorkpieceService()
    wp = _mk_workpiece(db_session)
    cycle = _mk_cycle(db_session)

    first = svc.link_to_cycle(db_session, wp.id, cycle.id, channel_id=0)
    second = svc.link_to_cycle(db_session, wp.id, cycle.id, channel_id=0)

    assert second.id == first.id
    db_session.rollback()


def test_link_to_cycle_still_separates_different_cycles(db_session):
    """幂等只针对同一周期, 同一工件跨周期复检仍各记一行。"""
    from backend.services.workpiece import WorkpieceService
    svc = WorkpieceService()
    wp = _mk_workpiece(db_session, serial="WPBIND-002")
    c1, c2 = _mk_cycle(db_session), _mk_cycle(db_session)

    a = svc.link_to_cycle(db_session, wp.id, c1.id, channel_id=0)
    b = svc.link_to_cycle(db_session, wp.id, c2.id, channel_id=0)

    assert a.id != b.id
    db_session.rollback()


# ==================== 防线 2: 落库失败不丢在检身份 ====================

def test_cycle_start_keeps_inspecting_when_link_fails():
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr._pending_workpiece[0] = 262
    mgr._workpiece_svc = MagicMock()
    mgr._workpiece_svc.link_to_cycle.side_effect = RuntimeError("UNIQUE constraint failed")

    with pytest.raises(RuntimeError):
        mgr._handle_cycle_start(MagicMock(), 0, 487, session_id=379, project_id=1)

    assert mgr._inspecting_workpiece.get(0) == 262, "在检身份必须保住"
    assert mgr.has_workpiece_in_flight(0) is True, "扫码后才计数不能因落库失败关门"


def test_cycle_start_normal_path_links_and_registers():
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr._pending_workpiece[0] = 262
    mgr._workpiece_svc = MagicMock()

    mgr._handle_cycle_start(MagicMock(), 0, 487, session_id=379, project_id=1)

    assert mgr._inspecting_workpiece.get(0) == 262
    assert mgr._pending_workpiece.get(0) is None
    mgr._workpiece_svc.link_to_cycle.assert_called_once()
