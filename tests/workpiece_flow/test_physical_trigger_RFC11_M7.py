"""RFC 11 M7: PhysicalTrigger 单元测试.

测试矩阵:
  - device_id / signal_key 匹配规则
  - 去抖 (dedup_window_ms) 在指定窗口内重复信号被忽略
  - Coordinator on_physical_signal 端到端: 物理信号 → 工件入口 → 工位 cycle 推进 → 完成
  - 多 flow 互不干扰 (只命中 device_id+signal_key 匹配的那个)
"""
import time
import pytest

from backend.services.flow_triggers import PhysicalTrigger
from backend.services.workpiece_flow_coordinator import get_coordinator


# ========== PhysicalTrigger 纯单元测试 ==========

def _make_trigger(cfg):
    t = PhysicalTrigger()
    t.attach(coordinator=None, flow={"id": 1, "physical_trigger_config": cfg})
    return t


def test_basic_signal_match_returns_serial():
    t = _make_trigger({"device_id": 1, "signal_key": "io_in_1"})
    serial = t.evaluate_physical_signal(device_id=1, signal_key="io_in_1")
    assert serial is not None
    assert serial.startswith("PHY-")


def test_device_id_mismatch_returns_none():
    t = _make_trigger({"device_id": 1, "signal_key": "io_in_1"})
    assert t.evaluate_physical_signal(device_id=2, signal_key="io_in_1") is None


def test_signal_key_mismatch_returns_none():
    t = _make_trigger({"device_id": 1, "signal_key": "io_in_1"})
    assert t.evaluate_physical_signal(device_id=1, signal_key="io_in_2") is None


def test_dedup_within_window_suppressed():
    t = _make_trigger({
        "device_id": 1,
        "signal_key": "io_in_1",
        "dedup_window_ms": 500,
    })
    first = t.evaluate_physical_signal(1, "io_in_1")
    second = t.evaluate_physical_signal(1, "io_in_1")
    assert first is not None
    assert second is None


def test_dedup_after_window_allowed():
    t = _make_trigger({
        "device_id": 1,
        "signal_key": "io_in_1",
        "dedup_window_ms": 50,
    })
    first = t.evaluate_physical_signal(1, "io_in_1")
    time.sleep(0.08)
    second = t.evaluate_physical_signal(1, "io_in_1")
    assert first is not None
    assert second is not None
    assert first != second  # 不同时间戳


def test_serial_prefix_custom():
    t = _make_trigger({
        "device_id": 1,
        "signal_key": "trigger",
        "serial_prefix": "LINE-A",
    })
    serial = t.evaluate_physical_signal(1, "trigger")
    assert serial.startswith("LINE-A-")


def test_no_device_id_in_cfg_accepts_any():
    """device_id 未配置时, 任何 device 都接 (灵活配置)."""
    t = _make_trigger({"signal_key": "trigger"})
    serial = t.evaluate_physical_signal(device_id=99, signal_key="trigger")
    assert serial is not None


def test_detached_returns_none():
    t = PhysicalTrigger()
    # 不 attach
    assert t.evaluate_physical_signal(1, "x") is None


# ========== Coordinator 集成测试 ==========

def _setup_physical_flow(coord, db, *, flow_id, stations, cfg):
    """造一行 physical flow 入 in-memory DB, 并 reload."""
    from tests.workpiece_flow.conftest import make_flow_row
    db.add(make_flow_row(
        flow_id, f"phy-line-{flow_id}", stations,
        trigger_mode="physical",
        physical_trigger_config=cfg,
    ))
    db.commit()
    coord.reload_flows(db)


def test_coordinator_on_physical_signal_enters_workpiece(fresh_coordinator, in_memory_db):
    coord = fresh_coordinator
    _setup_physical_flow(coord, in_memory_db, flow_id=1, stations=[0, 1, 2],
                         cfg={"device_id": 5, "signal_key": "io_in_1"})

    uuid = coord.on_physical_signal(device_id=5, signal_key="io_in_1", db=in_memory_db)
    assert uuid is not None

    in_flight = coord.list_in_flight(1)
    assert len(in_flight) == 1
    assert in_flight[0]["serial_no"].startswith("PHY-")


def test_coordinator_physical_signal_mismatch_ignored(fresh_coordinator, in_memory_db):
    coord = fresh_coordinator
    _setup_physical_flow(coord, in_memory_db, flow_id=1, stations=[0, 1],
                         cfg={"device_id": 5, "signal_key": "io_in_1"})

    # 信号不匹配
    uuid = coord.on_physical_signal(device_id=99, signal_key="io_in_1", db=in_memory_db)
    assert uuid is None

    in_flight = coord.list_in_flight(1)
    assert len(in_flight) == 0


def test_coordinator_full_flow_via_physical_trigger(fresh_coordinator, in_memory_db):
    """物理信号入口 → 3 工位都 OK → COMPLETED_OK."""
    coord = fresh_coordinator
    _setup_physical_flow(coord, in_memory_db, flow_id=1, stations=[0, 1, 2],
                         cfg={"device_id": 5, "signal_key": "io_in_1"})

    uuid = coord.on_physical_signal(device_id=5, signal_key="io_in_1", db=in_memory_db)
    assert uuid

    # 三个工位顺序结算
    coord.on_cycle_started(channel_id=0, cycle_id=100, db=in_memory_db)
    coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=in_memory_db)
    coord.on_cycle_started(channel_id=1, cycle_id=101, db=in_memory_db)
    coord.on_cycle_settled(channel_id=1, cycle_id=101, is_good=True, db=in_memory_db)
    coord.on_cycle_started(channel_id=2, cycle_id=102, db=in_memory_db)
    coord.on_cycle_settled(channel_id=2, cycle_id=102, is_good=True, db=in_memory_db)

    # 流转完成 → state in_flight 应为 0
    assert len(coord.list_in_flight(1)) == 0


def test_coordinator_dedup_window_global(fresh_coordinator, in_memory_db):
    """快速重复信号应被去抖, 不会触发两次入口."""
    coord = fresh_coordinator
    _setup_physical_flow(coord, in_memory_db, flow_id=1, stations=[0, 1],
                         cfg={"device_id": 5, "signal_key": "io_in_1", "dedup_window_ms": 1000})

    uuid1 = coord.on_physical_signal(device_id=5, signal_key="io_in_1", db=in_memory_db)
    uuid2 = coord.on_physical_signal(device_id=5, signal_key="io_in_1", db=in_memory_db)
    assert uuid1 is not None
    assert uuid2 is None  # 被去抖

    assert len(coord.list_in_flight(1)) == 1


def test_coordinator_two_physical_flows_isolated(fresh_coordinator, in_memory_db):
    """两个 flow 各自挂不同 device_id, 互不干扰."""
    coord = fresh_coordinator
    from tests.workpiece_flow.conftest import make_flow_row

    in_memory_db.add(make_flow_row(
        1, "line-A", [0, 1], trigger_mode="physical",
        physical_trigger_config={"device_id": 5, "signal_key": "io_in_1"},
    ))
    in_memory_db.add(make_flow_row(
        2, "line-B", [2, 3], trigger_mode="physical",
        physical_trigger_config={"device_id": 6, "signal_key": "io_in_2"},
    ))
    in_memory_db.commit()
    coord.reload_flows(in_memory_db)

    # 触发 A 的信号
    uuid_a = coord.on_physical_signal(device_id=5, signal_key="io_in_1", db=in_memory_db)
    assert uuid_a is not None
    assert len(coord.list_in_flight(1)) == 1
    assert len(coord.list_in_flight(2)) == 0

    # 触发 B 的信号
    uuid_b = coord.on_physical_signal(device_id=6, signal_key="io_in_2", db=in_memory_db)
    assert uuid_b is not None
    assert len(coord.list_in_flight(1)) == 1  # A 不变
    assert len(coord.list_in_flight(2)) == 1
