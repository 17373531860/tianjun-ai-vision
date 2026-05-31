"""WorkpieceFlow 测试 fixture 集合 (RFC 11)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def fresh_coordinator():
    """每个测试用全新单例, 防互相污染."""
    from backend.services.workpiece_flow_coordinator import (
        get_coordinator,
        reset_coordinator_for_testing,
    )
    reset_coordinator_for_testing()
    coord = get_coordinator()
    yield coord
    reset_coordinator_for_testing()


@pytest.fixture
def in_memory_db():
    """构造一个独立内存 SQLite (StaticPool 跨线程共享), 全部 ORM 表都建好.

    StaticPool: SQLite ':memory:' 默认 connection-bound, 后台 Timer 线程拿不到
    主线程建的表. StaticPool 让所有 session 共享一个 connection.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.db.database import Base
    from backend.models import models as _m  # noqa: F401
    from backend.models import auth_models as _a  # noqa: F401
    from backend.models import mes_models as _mes  # noqa: F401
    from backend.models import export_models as _ex  # noqa: F401
    from backend.models import plugin_models as _p  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


def make_flow_row(
    id_,
    name,
    stations,
    trigger_mode="time_window",
    fifo_max_in_flight=3,
    short_circuit=True,
    workpiece_timeout_ms=60000,
    timeout_action="force_ng",
    enabled=True,
    scan_device_id=None,
    physical_trigger_config=None,
):
    """造一个 WorkpieceFlowConfig ORM 行 (in-memory, 不入 DB)."""
    from backend.models.mes_models import WorkpieceFlowConfig
    return WorkpieceFlowConfig(
        id=id_,
        name=name,
        enabled=enabled,
        station_channel_ids=stations,
        trigger_mode=trigger_mode,
        scan_device_id=scan_device_id,
        scan_bind_strategy="entry",
        fifo_max_in_flight=fifo_max_in_flight,
        cycle_to_cycle_window_ms=15000,
        physical_trigger_config=physical_trigger_config,
        settle_strategy="all_ok_required",
        short_circuit_on_ng=short_circuit,
        workpiece_timeout_ms=workpiece_timeout_ms,
        timeout_action=timeout_action,
    )
