"""v3.51 工位组统一播报 (unified_ok_report) 测试.

覆盖:
1. should_unify_ok_report 判定条件 (组归属 / 策略 / 开关)
2. 聚齐全 OK → 统一播报到全部成员 (fire_external_event_response)
3. 超时 fallback → 只补播已 OK 成员
4. 组内出 NG → 不统一播报
5. reload_groups 从 plugin_data 读 unified_ok_report
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def coord():
    from backend.services.channel_group_coordinator import (
        ChannelGroupCoordinator,
        reset_coordinator_for_testing,
    )
    reset_coordinator_for_testing()
    c = ChannelGroupCoordinator()
    yield c
    try:
        c.cleanup_timers_for_testing()
    except Exception:
        pass
    reset_coordinator_for_testing()


def _install_group(coord, gid, name, members, strategy,
                   timeout_ms=200, timeout_action="fallback_independent",
                   unified_ok_report=False):
    coord._groups[gid] = {
        "id": gid,
        "name": name,
        "member_channel_ids": list(members),
        "settle_strategy": strategy,
        "timeout_ms": timeout_ms,
        "timeout_action": timeout_action,
        "unified_ok_report": unified_ok_report,
    }
    for cid in members:
        coord._channel_to_group[cid] = gid


def _mock_db():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


# ============================================================
# should_unify_ok_report 判定
# ============================================================


def test_should_unify_关_默认不抑制(coord):
    _install_group(coord, 1, "G", [0, 1], "synchronized_all_ok",
                   unified_ok_report=False)
    assert coord.should_unify_ok_report(0) is False


def test_should_unify_开_all_ok组抑制(coord):
    _install_group(coord, 1, "G", [0, 1], "synchronized_all_ok",
                   unified_ok_report=True)
    assert coord.should_unify_ok_report(0) is True
    assert coord.should_unify_ok_report(1) is True


def test_should_unify_any_ng策略不抑制(coord):
    """开关开了但策略不是 all_ok → 不抑制 (统一播报只对聚齐语义有意义)."""
    _install_group(coord, 1, "G", [0, 1], "synchronized_any_ng",
                   unified_ok_report=True)
    assert coord.should_unify_ok_report(0) is False


def test_should_unify_不在组的通道不抑制(coord):
    _install_group(coord, 1, "G", [0, 1], "synchronized_all_ok",
                   unified_ok_report=True)
    assert coord.should_unify_ok_report(5) is False


# ============================================================
# 聚齐 / 超时的统一播报
# ============================================================


def _patch_channel_manager(channel_ids):
    """mock channel_manager.channels: 每个通道一个带 fire_external_event_response 的 mgr."""
    cm = MagicMock()
    mgrs = {cid: MagicMock() for cid in channel_ids}
    cm.channels = mgrs
    return cm, mgrs


def test_全OK聚齐_统一播报到全部成员(coord):
    _install_group(coord, 1, "G", [0, 1], "synchronized_all_ok",
                   timeout_ms=2000, unified_ok_report=True)
    db = _mock_db()
    cm, mgrs = _patch_channel_manager([0, 1])

    with patch("backend.api.channel_manager.channel_manager", cm):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        coord.on_cycle_settled(channel_id=1, cycle_id=101, is_good=True, db=db)

    for cid, mgr in mgrs.items():
        mgr.fire_external_event_response.assert_called_once()
        args, kwargs = mgr.fire_external_event_response.call_args
        assert args[0] == 1  # 合格事件
        assert kwargs.get("remind_only") is True  # 不重复计数


def test_组内出NG_不统一播报(coord):
    _install_group(coord, 1, "G", [0, 1], "synchronized_all_ok",
                   timeout_ms=2000, unified_ok_report=True)
    db = _mock_db()
    cm, mgrs = _patch_channel_manager([0, 1])

    with patch("backend.api.channel_manager.channel_manager", cm), \
         patch("backend.api.alarm.alarm_router"):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        coord.on_cycle_settled(channel_id=1, cycle_id=101, is_good=False, db=db)

    for mgr in mgrs.values():
        mgr.fire_external_event_response.assert_not_called()


def test_超时fallback_只补播已OK成员(coord):
    _install_group(coord, 1, "G", [0, 1], "synchronized_all_ok",
                   timeout_ms=100, timeout_action="fallback_independent",
                   unified_ok_report=True)
    db = _mock_db()
    cm, mgrs = _patch_channel_manager([0, 1])

    with patch("backend.api.channel_manager.channel_manager", cm):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        # ch1 一直不来 → 等 timer 触发
        time.sleep(0.4)

    mgrs[0].fire_external_event_response.assert_called_once()
    mgrs[1].fire_external_event_response.assert_not_called()


def test_开关关_聚齐也不统一播报(coord):
    """unified_ok_report=False (默认) → 聚齐路径零播报, 零差异."""
    _install_group(coord, 1, "G", [0, 1], "synchronized_all_ok",
                   timeout_ms=2000, unified_ok_report=False)
    db = _mock_db()
    cm, mgrs = _patch_channel_manager([0, 1])

    with patch("backend.api.channel_manager.channel_manager", cm):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        coord.on_cycle_settled(channel_id=1, cycle_id=101, is_good=True, db=db)

    for mgr in mgrs.values():
        mgr.fire_external_event_response.assert_not_called()


def test_统一播报单通道异常不影响其它通道(coord):
    _install_group(coord, 1, "G", [0, 1], "synchronized_all_ok",
                   timeout_ms=2000, unified_ok_report=True)
    db = _mock_db()
    cm, mgrs = _patch_channel_manager([0, 1])
    mgrs[0].fire_external_event_response.side_effect = RuntimeError("boom")

    with patch("backend.api.channel_manager.channel_manager", cm):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        coord.on_cycle_settled(channel_id=1, cycle_id=101, is_good=True, db=db)

    mgrs[1].fire_external_event_response.assert_called_once()


# ============================================================
# reload_groups 读 plugin_data
# ============================================================


def test_reload_groups_读plugin_data开关(coord):
    row = MagicMock()
    row.id = 7
    row.name = "G-PD"
    row.member_channel_ids = [0, 1]
    row.settle_strategy = "synchronized_all_ok"
    row.timeout_ms = 5000
    row.timeout_action = "fallback_independent"
    row.plugin_data = {"unified_ok_report": True}

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [row]
    coord.reload_groups(db)

    assert coord._groups[7]["unified_ok_report"] is True
    assert coord.should_unify_ok_report(0) is True


def test_reload_groups_plugin_data为None默认关(coord):
    row = MagicMock()
    row.id = 8
    row.name = "G-N"
    row.member_channel_ids = [0, 1]
    row.settle_strategy = "synchronized_all_ok"
    row.timeout_ms = 5000
    row.timeout_action = "fallback_independent"
    row.plugin_data = None

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [row]
    coord.reload_groups(db)

    assert coord._groups[8]["unified_ok_report"] is False


# ==================== v3.51.1 pending_override TTL (防跨轮污染) ====================

def test_pending_override_expired_discarded(coord):
    """过期的组级 NG 覆盖不得污染后续无关周期."""
    coord._pending_override[3] = "NG"
    coord._pending_override_deadline[3] = time.monotonic() - 1.0
    assert coord.get_pending_override(3) is None
    assert 3 not in coord._pending_override
    assert 3 not in coord._pending_override_deadline


def test_pending_override_valid_within_ttl(coord):
    coord._pending_override[3] = "NG"
    coord._pending_override_deadline[3] = time.monotonic() + 60.0
    assert coord.get_pending_override(3) == "NG"
    # take-once: 取走即清空
    assert coord.get_pending_override(3) is None


def test_pending_override_without_deadline_still_works(coord):
    """无 deadline 的老条目 (兼容) 照常生效."""
    coord._pending_override[3] = "OK"
    assert coord.get_pending_override(3) == "OK"
