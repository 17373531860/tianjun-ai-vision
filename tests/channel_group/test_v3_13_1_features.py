"""RFC 10 v3.13.1 新功能测试.

覆盖:
1. 报警链路联动 — Coordinator 在 NG 广播时直驱 alarm_router.trigger_alarm
2. synchronized_all_ok 策略
3. timeout 机制 (fallback_independent / force_ng)
4. channel_group_settle_done hook
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def coord():
    """每个测试独立 coordinator (不污染单例)."""
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


def _make_group_dict(gid, name, members, strategy, timeout_ms=200, timeout_action="fallback_independent"):
    return {
        "id": gid,
        "name": name,
        "member_channel_ids": list(members),
        "settle_strategy": strategy,
        "timeout_ms": timeout_ms,
        "timeout_action": timeout_action,
    }


def _install_group(coord, gid, name, members, strategy, timeout_ms=200, timeout_action="fallback_independent"):
    """手动注入组 (跳过 reload_groups, 避免 DB 依赖)."""
    coord._groups[gid] = _make_group_dict(gid, name, members, strategy, timeout_ms, timeout_action)
    for cid in members:
        coord._channel_to_group[cid] = gid


# ============================================================
# 报警链路联动 (rfc10_alarm)
# ============================================================


def test_synchronized_any_ng_triggers_alarm_for_other_members(coord):
    """ch0 NG 时, alarm_router.trigger_alarm 应被调到 ch1."""
    _install_group(coord, 1, "G-A", [0, 1], "synchronized_any_ng")

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch("backend.api.alarm.alarm_router") as mock_router:
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=False, db=db)

        # alarm_router.trigger_alarm 应该被调 1 次, channel_id=1
        mock_router.trigger_alarm.assert_called_once_with("event2", channel_id=1)


def test_ok_settle_does_not_trigger_alarm(coord):
    """OK 结算不应调 alarm_router."""
    _install_group(coord, 1, "G-A", [0, 1], "synchronized_any_ng")

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch("backend.api.alarm.alarm_router") as mock_router:
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)

        mock_router.trigger_alarm.assert_not_called()


def test_alarm_router_import_failure_does_not_crash(coord):
    """alarm_router 不可用 (导入失败 / 异常) 不应影响主流程 NG 广播."""
    _install_group(coord, 1, "G-A", [0, 1], "synchronized_any_ng")

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    # 模拟 alarm_router.trigger_alarm 抛错
    with patch("backend.api.alarm.alarm_router") as mock_router:
        mock_router.trigger_alarm.side_effect = RuntimeError("simulated alarm fail")

        # 应该不抛, pending override 仍然要设
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=False, db=db)

        # ch1 应仍然拿到 NG override
        assert coord.get_pending_override(1) == "NG"


# ============================================================
# synchronized_all_ok 策略 (rfc10_all_ok)
# ============================================================


def test_all_ok_all_members_ok_fires_done_hook_with_OK(coord):
    """两个成员都 OK 结算 → fire done hook, group_result=OK."""
    _install_group(coord, 1, "G-AOK", [0, 1], "synchronized_all_ok", timeout_ms=2000)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    captured = []

    def fake_fire(hook_name, *args, **kwargs):
        if hook_name == "channel_group_settle_done":
            ctx = args[2] if len(args) >= 3 else kwargs.get("ctx", {})
            captured.append(ctx)
        return None

    with patch("backend.plugin_system.hook_dispatch.fire_plugin_hook", side_effect=fake_fire):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        # ch0 来了, 还没聚齐, done hook 不应 fire
        assert len(captured) == 0

        coord.on_cycle_settled(channel_id=1, cycle_id=101, is_good=True, db=db)
        # ch1 来了, 聚齐 → done hook fire
        assert len(captured) == 1
        ctx = captured[0]
        assert ctx["reason"] == "complete"
        assert ctx["group_result"] == "OK"
        assert set(ctx["members_arrived"]) == {0, 1}


def test_all_ok_one_ng_fires_done_with_NG_and_broadcasts(coord):
    """一个 NG, 等齐后 done hook 标 NG (NG 即时广播也发生)."""
    _install_group(coord, 1, "G-AOK", [0, 1], "synchronized_all_ok", timeout_ms=2000)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    captured = []
    alarms = []

    def fake_fire(hook_name, *args, **kwargs):
        if hook_name == "channel_group_settle_done":
            ctx = args[2] if len(args) >= 3 else kwargs.get("ctx", {})
            captured.append(ctx)
        return None

    with patch("backend.plugin_system.hook_dispatch.fire_plugin_hook", side_effect=fake_fire), \
         patch("backend.api.alarm.alarm_router") as mock_router:
        mock_router.trigger_alarm.side_effect = lambda et, channel_id: alarms.append((et, channel_id))

        # ch0 NG
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=False, db=db)
        # NG 立即广播到 ch1 (alarm + pending_override)
        assert (("event2", 1)) in alarms

        # ch1 后到也 NG
        coord.on_cycle_settled(channel_id=1, cycle_id=101, is_good=False, db=db)
        # 聚齐, done hook 标 NG
        assert len(captured) == 1
        ctx = captured[0]
        assert ctx["reason"] == "complete"
        assert ctx["group_result"] == "NG"


def test_all_ok_timeout_fallback_independent(coord):
    """timeout 后 fallback_independent: done hook 标 PARTIAL, 没到的不被强制改 NG."""
    _install_group(coord, 1, "G-AOK", [0, 1], "synchronized_all_ok",
                    timeout_ms=100, timeout_action="fallback_independent")

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    captured = []

    def fake_fire(hook_name, *args, **kwargs):
        if hook_name == "channel_group_settle_done":
            ctx = args[2] if len(args) >= 3 else kwargs.get("ctx", {})
            captured.append(ctx)
        return None

    with patch("backend.plugin_system.hook_dispatch.fire_plugin_hook", side_effect=fake_fire):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        # 只有 ch0 来, ch1 不来 → 等 timeout
        time.sleep(0.25)

        assert len(captured) == 1, f"timeout 后应 fire done hook 一次, 实际 {len(captured)}"
        ctx = captured[0]
        assert ctx["reason"] == "timeout"
        assert ctx["group_result"] == "PARTIAL"
        assert ctx["members_arrived"] == [0]
        assert set(ctx["members_expected"]) == {0, 1}

        # ch1 不应被强制 NG (fallback_independent 不动)
        assert coord.get_pending_override(1) is None


def test_all_ok_timeout_force_ng(coord):
    """timeout 后 force_ng: done hook 标 NG_BY_TIMEOUT, 没到的被设 NG override."""
    _install_group(coord, 1, "G-FNG", [0, 1], "synchronized_all_ok",
                    timeout_ms=100, timeout_action="force_ng")

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    captured = []

    def fake_fire(hook_name, *args, **kwargs):
        if hook_name == "channel_group_settle_done":
            ctx = args[2] if len(args) >= 3 else kwargs.get("ctx", {})
            captured.append(ctx)
        return None

    with patch("backend.plugin_system.hook_dispatch.fire_plugin_hook", side_effect=fake_fire):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        time.sleep(0.25)

        assert len(captured) == 1
        ctx = captured[0]
        assert ctx["reason"] == "timeout"
        assert ctx["group_result"] == "NG_BY_TIMEOUT"
        assert ctx["timeout_action"] == "force_ng"

        # ch1 应被强制 NG
        assert coord.get_pending_override(1) == "NG"


def test_all_ok_timer_cancelled_on_complete(coord):
    """聚齐前 timer 启动; 聚齐后 timer 应被取消 (不再触发 timeout 路径)."""
    _install_group(coord, 1, "G-OK", [0, 1], "synchronized_all_ok", timeout_ms=200)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    captured = []

    def fake_fire(hook_name, *args, **kwargs):
        if hook_name == "channel_group_settle_done":
            ctx = args[2] if len(args) >= 3 else kwargs.get("ctx", {})
            captured.append(ctx)
        return None

    with patch("backend.plugin_system.hook_dispatch.fire_plugin_hook", side_effect=fake_fire):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        coord.on_cycle_settled(channel_id=1, cycle_id=101, is_good=True, db=db)
        # 聚齐立即 fire 1 次
        assert len(captured) == 1
        assert captured[0]["reason"] == "complete"

        # 等超过 timeout, 不应再 fire
        time.sleep(0.3)
        assert len(captured) == 1, "聚齐后 timer 应被 cancel, 不应再 fire timeout 路径"


# ============================================================
# on_channel_removed 与 aggregation 联动
# ============================================================


def test_on_channel_removed_clears_pending_aggregations(coord):
    """通道移除时, 应从 pending aggregation 内移除."""
    _install_group(coord, 1, "G-X", [0, 1], "synchronized_all_ok", timeout_ms=10000)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch("backend.plugin_system.hook_dispatch.fire_plugin_hook"):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        assert 0 in coord._pending_aggregations[1]["members"]

        coord.on_channel_removed(0)
        assert 0 not in coord._pending_aggregations.get(1, {}).get("members", {})


def test_cleanup_timers_for_testing(coord):
    """cleanup_timers 应取消所有 timer + 清 pending aggregations."""
    _install_group(coord, 1, "G-Y", [0, 1], "synchronized_all_ok", timeout_ms=2000)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch("backend.plugin_system.hook_dispatch.fire_plugin_hook"):
        coord.on_cycle_settled(channel_id=0, cycle_id=100, is_good=True, db=db)
        assert 1 in coord._pending_aggregations
        assert coord._pending_aggregations[1]["timer"] is not None

        coord.cleanup_timers_for_testing()
        assert coord._pending_aggregations == {}
