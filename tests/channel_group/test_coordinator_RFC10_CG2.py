"""RFC 10 CG.2 + CG.3 — ChannelGroupCoordinator + synchronized_any_ng 策略.

客户视角叙事:
  RFC 10 §1.1 客户需求 4: 双工位 A 站如果判 NG, B 站也要同步判 NG.

  本测试覆盖:
    A. Coordinator 单例 / reload / 状态隔离 (4 测试)
    B. on_cycle_settled 调度: 不在组 / OK 不广播 / NG 广播 (4 测试)
    C. synchronized_any_ng 策略行为 (3 测试)
    D. 组级字段回写 detection_cycles (3 测试)
    E. PluginHost 主动 API 用的查询接口 (4 测试)
    F. on_channel_removed 清理 (2 测试)

20 个测试.
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def fresh_coordinator():
    """每个测试用全新单例, 防互相污染."""
    from backend.services.channel_group_coordinator import (
        get_coordinator,
        reset_coordinator_for_testing,
    )
    reset_coordinator_for_testing()
    coord = get_coordinator()
    yield coord
    reset_coordinator_for_testing()


@pytest.fixture
def in_memory_db():
    """构造一个独立内存 SQLite, 仅用于 cycle 字段回写测试."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.db.database import Base
    from backend.models import models as _m  # noqa: F401
    from backend.models import auth_models as _a  # noqa: F401
    from backend.models import mes_models as _mes  # noqa: F401
    from backend.models import export_models as _ex  # noqa: F401
    from backend.models import plugin_models as _p  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


def _make_group_row(id_, name, members, strategy="synchronized_any_ng",
                    timeout_ms=5000, timeout_action="fallback_independent", enabled=True):
    """造一个 ChannelGroup ORM 行 (in-memory, 不入 DB)."""
    from backend.models.models import ChannelGroup
    row = ChannelGroup(
        id=id_,
        name=name,
        member_channel_ids=members,
        settle_strategy=strategy,
        timeout_ms=timeout_ms,
        timeout_action=timeout_action,
        enabled=enabled,
    )
    return row


def _seed_groups(db, rows):
    """把组配置写入 DB."""
    for r in rows:
        db.add(r)
    db.commit()


def _make_detection_cycle(db, cycle_id=1, is_good=True):
    """造一个 DetectionCycle 行供回写测试."""
    from backend.models.models import DetectionCycle, DetectionSession
    # session 在某些测试场景下已经存在 (前一个 _make_detection_cycle 调用建过) — 用 merge 兜底
    sess = db.query(DetectionSession).filter(DetectionSession.id == 1).first()
    if not sess:
        sess = DetectionSession(
            id=1,
            session_uuid=f"test-session-{cycle_id}",
            project_id=1,
            start_time=datetime.now(timezone.utc),
        )
        db.add(sess)
        db.commit()
    cyc = DetectionCycle(
        id=cycle_id,
        cycle_uuid=f"cycle-{cycle_id}",
        session_id=sess.id,
        start_time=datetime.now(timezone.utc),
        is_good=is_good,
    )
    db.add(cyc)
    db.commit()
    return cyc


# ============================================================
# A. Coordinator 单例 / reload / 状态隔离
# ============================================================


def test_singleton_returns_same_instance(fresh_coordinator):
    from backend.services.channel_group_coordinator import get_coordinator
    assert get_coordinator() is fresh_coordinator


def test_reload_groups_with_empty_db(fresh_coordinator, in_memory_db):
    n = fresh_coordinator.reload_groups(in_memory_db)
    assert n == 0
    assert fresh_coordinator.list_groups() == []


def test_reload_groups_picks_up_enabled_only(fresh_coordinator, in_memory_db):
    _seed_groups(in_memory_db, [
        _make_group_row(1, "Group-A", [0, 1], enabled=True),
        _make_group_row(2, "Group-B", [2, 3], enabled=False),  # disabled, 不应被加载
    ])
    n = fresh_coordinator.reload_groups(in_memory_db)
    assert n == 1
    groups = fresh_coordinator.list_groups()
    assert len(groups) == 1
    assert groups[0]["name"] == "Group-A"


def test_channel_to_group_reverse_index(fresh_coordinator, in_memory_db):
    _seed_groups(in_memory_db, [
        _make_group_row(1, "Group-A", [0, 1]),
        _make_group_row(2, "Group-B", [2, 3]),
    ])
    fresh_coordinator.reload_groups(in_memory_db)
    assert fresh_coordinator.is_channel_in_group(0) is True
    assert fresh_coordinator.is_channel_in_group(1) is True
    assert fresh_coordinator.is_channel_in_group(2) is True
    assert fresh_coordinator.is_channel_in_group(99) is False


# ============================================================
# B. on_cycle_settled 调度
# ============================================================


def test_cycle_settled_for_channel_not_in_any_group_returns_early(fresh_coordinator, in_memory_db):
    """通道不在任何组 → 零差异 (不报错, 不写 cycle, 不发 hook)."""
    fresh_coordinator.reload_groups(in_memory_db)  # 空 groups
    fresh_coordinator.on_cycle_settled(channel_id=0, cycle_id=1, is_good=False, db=in_memory_db)
    # 不抛错就过


def test_cycle_settled_with_ok_does_not_set_override(fresh_coordinator, in_memory_db):
    _seed_groups(in_memory_db, [
        _make_group_row(1, "Group-A", [0, 1]),
    ])
    fresh_coordinator.reload_groups(in_memory_db)
    _make_detection_cycle(in_memory_db, cycle_id=1, is_good=True)
    fresh_coordinator.on_cycle_settled(0, 1, is_good=True, db=in_memory_db)
    # 同组的 ch1 不应有 pending override
    assert fresh_coordinator.get_pending_override(1) is None


def test_cycle_settled_with_ng_sets_override_for_other_members(fresh_coordinator, in_memory_db):
    _seed_groups(in_memory_db, [
        _make_group_row(1, "Group-A", [0, 1, 2]),
    ])
    fresh_coordinator.reload_groups(in_memory_db)
    _make_detection_cycle(in_memory_db, cycle_id=1, is_good=False)
    fresh_coordinator.on_cycle_settled(0, 1, is_good=False, db=in_memory_db)
    # ch1, ch2 应有 NG override; ch0 (触发方) 不设
    assert fresh_coordinator.get_pending_override(1) == "NG"
    assert fresh_coordinator.get_pending_override(2) == "NG"
    # 触发方自己不在 pending (它已经决定了自己的结果)
    # 注: 用 fresh override 字段确认 (get_pending_override 是 take-once)
    # 重设以验证 ch0 不在
    fresh_coordinator.on_cycle_settled(0, 1, is_good=False, db=in_memory_db)
    assert fresh_coordinator.get_pending_override(0) is None


def test_get_pending_override_is_take_once(fresh_coordinator, in_memory_db):
    """take-once 语义: 取走后 pending 为 None."""
    _seed_groups(in_memory_db, [_make_group_row(1, "Group-A", [0, 1])])
    fresh_coordinator.reload_groups(in_memory_db)
    _make_detection_cycle(in_memory_db, cycle_id=1, is_good=False)
    fresh_coordinator.on_cycle_settled(0, 1, is_good=False, db=in_memory_db)

    # 第一次拿到 NG
    assert fresh_coordinator.get_pending_override(1) == "NG"
    # 第二次取空
    assert fresh_coordinator.get_pending_override(1) is None


# ============================================================
# C. synchronized_any_ng 策略行为
# ============================================================


def test_any_ng_strategy_fires_hook_with_correct_ctx(fresh_coordinator, in_memory_db):
    """NG 广播时 fire channel_group_settle_start hook + ctx 字段齐."""
    _seed_groups(in_memory_db, [_make_group_row(1, "Group-A", [0, 1])])
    fresh_coordinator.reload_groups(in_memory_db)
    _make_detection_cycle(in_memory_db, cycle_id=1, is_good=False)

    fire_calls = []

    def _capture_fire(hook_name, position, hook_type, ctx):
        fire_calls.append((hook_name, position, hook_type, dict(ctx)))
        return {}

    with patch("backend.plugin_system.hook_dispatch.fire_plugin_hook", side_effect=_capture_fire):
        fresh_coordinator.on_cycle_settled(0, 1, is_good=False, db=in_memory_db)

    # hook 必须被 fire 一次
    assert len(fire_calls) == 1
    hook_name, position, hook_type, ctx = fire_calls[0]
    assert hook_name == "channel_group_settle_start"
    assert position == "broadcast_any_ng"
    assert hook_type == "post"
    assert ctx["group_id"] == 1
    assert ctx["group_name"] == "Group-A"
    assert ctx["trigger_channel_id"] == 0
    assert ctx["trigger_cycle_id"] == 1
    assert ctx["member_channel_ids"] == [0, 1]
    assert ctx["strategy"] == "synchronized_any_ng"


def test_any_ng_strategy_ok_does_not_fire_hook(fresh_coordinator, in_memory_db):
    """OK 结算不广播, 不 fire hook."""
    _seed_groups(in_memory_db, [_make_group_row(1, "Group-A", [0, 1])])
    fresh_coordinator.reload_groups(in_memory_db)
    _make_detection_cycle(in_memory_db, cycle_id=1, is_good=True)

    fire_calls = []
    with patch(
        "backend.plugin_system.hook_dispatch.fire_plugin_hook",
        side_effect=lambda *a, **kw: fire_calls.append(a) or {},
    ):
        fresh_coordinator.on_cycle_settled(0, 1, is_good=True, db=in_memory_db)

    assert fire_calls == []


def test_hook_exception_does_not_break_main_flow(fresh_coordinator, in_memory_db):
    """hook 抛异常被吞掉, 主流程仍设 pending override."""
    _seed_groups(in_memory_db, [_make_group_row(1, "Group-A", [0, 1])])
    fresh_coordinator.reload_groups(in_memory_db)
    _make_detection_cycle(in_memory_db, cycle_id=1, is_good=False)

    with patch(
        "backend.plugin_system.hook_dispatch.fire_plugin_hook",
        side_effect=RuntimeError("plugin crashed"),
    ):
        # 不应抛
        fresh_coordinator.on_cycle_settled(0, 1, is_good=False, db=in_memory_db)

    # pending override 仍然被设
    assert fresh_coordinator.get_pending_override(1) == "NG"


# ============================================================
# D. 组级字段回写 detection_cycles
# ============================================================


def test_writes_group_settle_result_for_triggering_cycle(fresh_coordinator, in_memory_db):
    _seed_groups(in_memory_db, [_make_group_row(1, "Group-A", [0, 1])])
    fresh_coordinator.reload_groups(in_memory_db)
    cyc = _make_detection_cycle(in_memory_db, cycle_id=1, is_good=False)

    fresh_coordinator.on_cycle_settled(0, 1, is_good=False, db=in_memory_db)

    in_memory_db.refresh(cyc)
    assert cyc.channel_group_id == 1
    assert cyc.group_settle_result == "NG"


def test_writes_ok_for_ok_settlement(fresh_coordinator, in_memory_db):
    _seed_groups(in_memory_db, [_make_group_row(1, "Group-A", [0, 1])])
    fresh_coordinator.reload_groups(in_memory_db)
    cyc = _make_detection_cycle(in_memory_db, cycle_id=1, is_good=True)

    fresh_coordinator.on_cycle_settled(0, 1, is_good=True, db=in_memory_db)

    in_memory_db.refresh(cyc)
    assert cyc.channel_group_id == 1
    assert cyc.group_settle_result == "OK"


def test_cycle_write_failure_does_not_crash(fresh_coordinator, in_memory_db):
    """DB 写库失败时 swallow + 不阻塞 pending override 设置."""
    _seed_groups(in_memory_db, [_make_group_row(1, "Group-A", [0, 1])])
    fresh_coordinator.reload_groups(in_memory_db)
    # 不创建 cycle → 查不到 → 内部 silently return

    fresh_coordinator.on_cycle_settled(0, 999, is_good=False, db=in_memory_db)
    # pending override 仍然设了 (因为这一步在写库之前)
    assert fresh_coordinator.get_pending_override(1) == "NG"


# ============================================================
# E. PluginHost 主动 API 用的查询接口
# ============================================================


def test_list_groups_returns_snapshot(fresh_coordinator, in_memory_db):
    _seed_groups(in_memory_db, [
        _make_group_row(1, "Group-A", [0, 1]),
        _make_group_row(2, "Group-B", [2, 3]),
    ])
    fresh_coordinator.reload_groups(in_memory_db)
    groups = fresh_coordinator.list_groups()
    assert len(groups) == 2
    names = sorted(g["name"] for g in groups)
    assert names == ["Group-A", "Group-B"]


def test_list_groups_returns_copy_not_internal_dict(fresh_coordinator, in_memory_db):
    """返 snapshot 副本 — 调用方改返回值不影响内部状态."""
    _seed_groups(in_memory_db, [_make_group_row(1, "Group-A", [0, 1])])
    fresh_coordinator.reload_groups(in_memory_db)
    groups = fresh_coordinator.list_groups()
    groups[0]["name"] = "MUTATED"
    again = fresh_coordinator.list_groups()
    assert again[0]["name"] == "Group-A"


def test_get_group_nonexistent_returns_none(fresh_coordinator):
    assert fresh_coordinator.get_group(99) is None


def test_get_group_returns_copy_with_all_fields(fresh_coordinator, in_memory_db):
    _seed_groups(in_memory_db, [
        _make_group_row(1, "Group-A", [0, 1], strategy="synchronized_any_ng",
                        timeout_ms=3000, timeout_action="force_ng"),
    ])
    fresh_coordinator.reload_groups(in_memory_db)
    g = fresh_coordinator.get_group(1)
    assert g is not None
    assert g["id"] == 1
    assert g["name"] == "Group-A"
    assert g["member_channel_ids"] == [0, 1]
    assert g["settle_strategy"] == "synchronized_any_ng"
    assert g["timeout_ms"] == 3000
    assert g["timeout_action"] == "force_ng"


# ============================================================
# F. on_channel_removed 清理
# ============================================================


def test_on_channel_removed_clears_reverse_index(fresh_coordinator, in_memory_db):
    _seed_groups(in_memory_db, [_make_group_row(1, "Group-A", [0, 1])])
    fresh_coordinator.reload_groups(in_memory_db)
    assert fresh_coordinator.is_channel_in_group(1) is True

    fresh_coordinator.on_channel_removed(1)
    assert fresh_coordinator.is_channel_in_group(1) is False
    # ch0 不动
    assert fresh_coordinator.is_channel_in_group(0) is True


def test_on_channel_removed_clears_pending_override(fresh_coordinator, in_memory_db):
    _seed_groups(in_memory_db, [_make_group_row(1, "Group-A", [0, 1])])
    fresh_coordinator.reload_groups(in_memory_db)
    _make_detection_cycle(in_memory_db, cycle_id=1, is_good=False)
    fresh_coordinator.on_cycle_settled(0, 1, is_good=False, db=in_memory_db)
    assert fresh_coordinator.get_pending_override(1) == "NG"

    # 再造 pending, 然后 remove
    _make_detection_cycle(in_memory_db, cycle_id=2, is_good=False)
    fresh_coordinator.on_cycle_settled(0, 2, is_good=False, db=in_memory_db)
    fresh_coordinator.on_channel_removed(1)
    assert fresh_coordinator.get_pending_override(1) is None
