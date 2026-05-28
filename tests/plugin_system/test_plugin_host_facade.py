"""坑 4 护栏: PluginHost 稳定查询 facade

客户视角叙事:
  ACME 客户的 Tier 3 全栈插件需要在 cycle_end hook 里反查 cycle / step / workpiece
  详情, 把成品照片 + 检测明细推到客户自建 MES.

  改进前 (v3.7.0~v3.12.0):
    插件只能 host.get_db_session() 拿 raw session, 然后 from backend.models.models
    import DetectionCycle, 直接 query — **完全绕过任何契约层**.
    主程序改 ORM 字段 → 插件下次重启炸 OperationalError, 客户现场报障.

  改进后 (本测试守护):
    host.query_session / query_cycle / query_step / query_workpiece 返回 dict
    snapshot, ORM 字段名漂移由 facade 层吸收. 插件不再需要直接 import 主程序 ORM.
    get_db_session 保留 (向后兼容), 但新插件不应再用.

关键设计:
- 返回 dict 而非 ORM 对象: 避免 detached session / lazy load 炸
- 字段集合静态锁定: facade 字段名是 SDK 契约的一部分, 改名要升 plugin SDK 版本
- 内部 SessionLocal 自管理: 插件不需要操心 session 生命周期
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ----------------------- facade 字段集合契约 -----------------------


SESSION_FACADE_FIELDS = {
    "id", "session_uuid", "name", "project_id",
    "start_time", "end_time", "status",
    "total_cycles", "good_cycles", "ng_cycles",
    "avg_cycle_time", "min_cycle_time", "max_cycle_time",
    "counters_snapshot",
}

CYCLE_FACADE_FIELDS = {
    "id", "cycle_uuid", "cycle_number", "session_id",
    "start_time", "end_time", "duration",
    "is_good", "event_id", "event_name", "result_reason",
    "step_sequence",
}

STEP_FACADE_FIELDS = {
    "id", "record_uuid", "cycle_id", "step_id",
    "step_label", "step_name", "step_order",
    "start_time", "end_time", "duration", "interval_to_next",
    "is_valid", "confidence",
}

WORKPIECE_FACADE_FIELDS = {
    "id", "serial_no", "raw_barcode",
    "order_id", "batch_id", "project_id", "channel_id",
    "status", "final_result", "inspection_count", "operator",
    "registered_at", "first_inspect_at", "last_inspect_at",
    "created_at", "updated_at",
}


# ----------------------- 真实 ORM 端到端 (sqlite in-memory) -----------------------


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """创建独立 sqlite 文件 + 用 SessionLocal 注入到 facade.

    与父 conftest 的 fixture 完全隔离, 测试结束自动清理.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_url = f"sqlite:///{tmp_path}/host_facade.db"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    # 建表 (核心检测 + MES)
    from backend.models.models import Base as CoreBase
    from backend.models.mes_models import Base as MESBase
    CoreBase.metadata.create_all(engine)
    MESBase.metadata.create_all(engine)

    LocalSession = sessionmaker(bind=engine)

    # 把 SessionLocal monkey-patch 到 facade 内部使用的位置.
    # facade 在每个 query_* 里 from backend.db.database import SessionLocal,
    # 只 patch backend.db.database.SessionLocal 即可.
    import backend.db.database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", LocalSession, raising=True)

    return LocalSession


@pytest.fixture
def host():
    from backend.plugin_system.registry import PluginHost
    return PluginHost(customer_code="acme-test", plugin_dir="/tmp", main_version="3.13.0")


# ----------------------- query_session -----------------------


def test_query_session_returns_dict_with_locked_fields(host, isolated_db):
    from backend.models.models import DetectionSession
    db = isolated_db()

    s = DetectionSession(
        session_uuid="test-uuid-1",
        project_id=1,
        start_time=datetime.now(timezone.utc),
        status="active",
        total_cycles=10,
        good_cycles=8,
        ng_cycles=2,
        avg_cycle_time=12.5,
    )
    db.add(s)
    db.commit()
    sid = s.id
    db.close()

    snap = host.query_session(sid)
    assert snap is not None
    assert set(snap.keys()) == SESSION_FACADE_FIELDS
    assert snap["session_uuid"] == "test-uuid-1"
    assert snap["total_cycles"] == 10
    assert snap["good_cycles"] == 8
    assert snap["status"] == "active"
    # 时间字段 ISO 字符串
    assert isinstance(snap["start_time"], str)


def test_query_session_returns_none_for_missing(host, isolated_db):
    assert host.query_session(99999) is None


# ----------------------- query_cycle -----------------------


def test_query_cycle_returns_locked_fields(host, isolated_db):
    from backend.models.models import DetectionSession, DetectionCycle
    db = isolated_db()

    s = DetectionSession(
        session_uuid="cycle-test-session",
        project_id=1,
        start_time=datetime.now(timezone.utc),
        status="active",
    )
    db.add(s)
    db.commit()

    c = DetectionCycle(
        cycle_uuid="cycle-uuid-1",
        cycle_number=3,
        session_id=s.id,
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        duration=15.0,
        is_good=True,
        event_name="test_ok",
        step_sequence=["A", "B", "C"],
    )
    db.add(c)
    db.commit()
    cid = c.id
    db.close()

    snap = host.query_cycle(cid)
    assert snap is not None
    assert set(snap.keys()) == CYCLE_FACADE_FIELDS
    assert snap["cycle_uuid"] == "cycle-uuid-1"
    assert snap["cycle_number"] == 3
    assert snap["is_good"] is True
    assert snap["step_sequence"] == ["A", "B", "C"]
    assert snap["event_name"] == "test_ok"


def test_query_cycle_returns_none_for_missing(host, isolated_db):
    assert host.query_cycle(99999) is None


# ----------------------- query_step -----------------------


def test_query_step_returns_locked_fields(host, isolated_db):
    from backend.models.models import DetectionSession, DetectionCycle, StepRecord
    db = isolated_db()

    s = DetectionSession(
        session_uuid="step-test-session",
        project_id=1,
        start_time=datetime.now(timezone.utc),
        status="active",
    )
    db.add(s); db.commit()
    c = DetectionCycle(
        cycle_uuid="step-test-cycle",
        cycle_number=1,
        session_id=s.id,
        start_time=datetime.now(timezone.utc),
    )
    db.add(c); db.commit()
    step = StepRecord(
        record_uuid="step-uuid-1",
        cycle_id=c.id,
        step_label="撕膜",
        step_order=1,
        start_time=datetime.now(timezone.utc),
        is_valid=True,
        confidence=0.92,
    )
    db.add(step); db.commit()
    sid = step.id
    db.close()

    snap = host.query_step(sid)
    assert snap is not None
    assert set(snap.keys()) == STEP_FACADE_FIELDS
    assert snap["step_label"] == "撕膜"
    assert snap["step_order"] == 1
    assert snap["is_valid"] is True
    assert snap["confidence"] == 0.92


def test_query_step_returns_none_for_missing(host, isolated_db):
    assert host.query_step(99999) is None


# ----------------------- query_workpiece -----------------------


def test_query_workpiece_returns_locked_fields(host, isolated_db):
    from backend.models.mes_models import Workpiece
    db = isolated_db()

    # sqlite 默认不强制 FK, 直接用伪 project_id 即可 (本测试只验 facade 字段映射)
    wp = Workpiece(
        serial_no="SN-12345",
        raw_barcode="raw-bc-12345",
        project_id=1,
        status="ok",
        final_result="ok",
        inspection_count=1,
        operator="op-A",
    )
    db.add(wp); db.commit()
    wid = wp.id
    db.close()

    snap = host.query_workpiece(wid)
    assert snap is not None
    assert set(snap.keys()) == WORKPIECE_FACADE_FIELDS
    assert snap["serial_no"] == "SN-12345"
    assert snap["raw_barcode"] == "raw-bc-12345"
    assert snap["final_result"] == "ok"
    assert snap["status"] == "ok"
    assert snap["operator"] == "op-A"


def test_query_workpiece_returns_none_for_missing(host, isolated_db):
    assert host.query_workpiece(99999) is None


# ----------------------- 向后兼容: get_db_session 保留 -----------------------


def test_get_db_session_still_works(host):
    """老插件仍然可以拿原始 session — 不能在 v3.13 里破坏."""
    sess = host.get_db_session()
    assert sess is not None
    sess.close()


def test_host_facade_has_all_query_methods():
    """契约: 4 个 query_* 方法必须都存在.

    任何重命名 / 删方法都会让客户已部署的 Tier 3 插件炸, 必须升 plugin SDK
    main_version_min 拦截.
    """
    from backend.plugin_system.registry import PluginHost
    for name in ("query_session", "query_cycle", "query_step", "query_workpiece"):
        assert hasattr(PluginHost, name), f"PluginHost.{name} 不见了 — 这是 SDK 契约破坏!"
