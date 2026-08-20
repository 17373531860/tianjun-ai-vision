"""v3.51 数据清理联动解封条码 (_unlock_ok_workpieces) 测试.

背景 (2026-08-14 捷昌 B 站): strict_ok_dedup 按工件表 status=ok 永久拒码,
数据页删记录不动工件状态 → "记录都删了, 码还是扫不进"。

覆盖:
1. 全量清理 (cycle_ids=None) 解封所有 ok 工件
2. 范围清理只解封关联工件
3. 空范围零动作 / 异常不阻断清理
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def db():
    """独立内存库: 只建 MES 相关表."""
    from backend.db.database import Base
    from backend.models.mes_models import Workpiece, WorkpieceInspection  # noqa: F401
    from backend.models.models import DetectionCycle  # noqa: F401 (FK 目标表)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _mk_wp(db, serial, status="ok", project_id=1):
    from backend.models.mes_models import Workpiece
    wp = Workpiece(serial_no=serial, status=status, project_id=project_id)
    db.add(wp)
    db.flush()
    return wp


def _link(db, wp_id, cycle_id):
    from backend.models.mes_models import WorkpieceInspection
    db.add(WorkpieceInspection(workpiece_id=wp_id, cycle_id=cycle_id))
    db.flush()


def test_全量清理解封所有ok工件(db):
    from backend.api.sessions_maintenance import _unlock_ok_workpieces
    from backend.models.mes_models import Workpiece
    w1 = _mk_wp(db, "SN-A", "ok")
    w2 = _mk_wp(db, "SN-B", "ok")
    w3 = _mk_wp(db, "SN-C", "registered")
    db.commit()

    n = _unlock_ok_workpieces(db, cycle_ids=None)
    db.commit()

    assert n == 2
    assert {w.status for w in db.query(Workpiece).all()} == {"registered"}
    assert db.query(Workpiece).filter(Workpiece.id == w3.id).one().status == "registered"


def test_范围清理只解封关联工件(db):
    from backend.api.sessions_maintenance import _unlock_ok_workpieces
    from backend.models.mes_models import Workpiece
    w1 = _mk_wp(db, "SN-A", "ok")
    w2 = _mk_wp(db, "SN-B", "ok")
    _link(db, w1.id, cycle_id=100)
    _link(db, w2.id, cycle_id=200)
    db.commit()

    n = _unlock_ok_workpieces(db, cycle_ids=[100])
    db.commit()

    assert n == 1
    assert db.query(Workpiece).filter(Workpiece.id == w1.id).one().status == "registered"
    assert db.query(Workpiece).filter(Workpiece.id == w2.id).one().status == "ok"


def test_空cycle列表零动作(db):
    from backend.api.sessions_maintenance import _unlock_ok_workpieces
    _mk_wp(db, "SN-A", "ok")
    db.commit()
    assert _unlock_ok_workpieces(db, cycle_ids=[]) == 0


def test_无关联cycle零动作(db):
    from backend.api.sessions_maintenance import _unlock_ok_workpieces
    w1 = _mk_wp(db, "SN-A", "ok")
    _link(db, w1.id, cycle_id=100)
    db.commit()
    assert _unlock_ok_workpieces(db, cycle_ids=[999]) == 0


def test_异常不阻断返回0():
    from backend.api.sessions_maintenance import _unlock_ok_workpieces
    from unittest.mock import MagicMock
    broken = MagicMock()
    broken.query.side_effect = RuntimeError("db down")
    assert _unlock_ok_workpieces(broken, cycle_ids=None) == 0
