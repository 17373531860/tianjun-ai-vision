"""RFC 10 CG.7 — PluginHost 工位组主动 API 真实现.

客户视角:
  M1.3b 留了 3 个工位组 stub (list / query / broadcast), 都依赖 RFC 10
  Coordinator 落地. 本测试覆盖 stub 真实现后的行为:

  - list_channel_groups (查询, 无 capability) — 4 测试
  - query_channel_group (查询, 无 capability) — 3 测试
  - broadcast_to_channel_group (capability required) — 8 测试

15 个测试.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def fresh_coordinator():
    """每个测试用全新单例."""
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


def _seed_one_group(in_memory_db):
    from backend.models.models import ChannelGroup
    g = ChannelGroup(
        id=1,
        name="Group-A",
        member_channel_ids=[0, 1],
        settle_strategy="synchronized_any_ng",
        timeout_ms=5000,
        timeout_action="fallback_independent",
        enabled=True,
    )
    in_memory_db.add(g)
    in_memory_db.commit()
    return g


def _make_host(capabilities=None):
    """构造 PluginHost 实例 (绕过 PluginManager)."""
    from backend.plugin_system.registry import PluginHost
    return PluginHost(
        customer_code="acme",
        plugin_dir="/tmp/test_plugin_dir",
        capabilities=list(capabilities or []),
    )


# ============================================================
# A. list_channel_groups
# ============================================================


def test_list_channel_groups_empty(fresh_coordinator):
    host = _make_host()
    assert host.list_channel_groups() == []


def test_list_channel_groups_returns_active(fresh_coordinator, in_memory_db):
    _seed_one_group(in_memory_db)
    fresh_coordinator.reload_groups(in_memory_db)
    host = _make_host()
    groups = host.list_channel_groups()
    assert len(groups) == 1
    assert groups[0]["name"] == "Group-A"
    assert groups[0]["member_channel_ids"] == [0, 1]
    assert groups[0]["settle_strategy"] == "synchronized_any_ng"


def test_list_channel_groups_no_capability_required(fresh_coordinator):
    """查询接口无 capability 要求 (与 query_session 等一致)."""
    host = _make_host()  # 没传 capabilities
    # 不应抛 PluginRuntimeError
    host.list_channel_groups()


def test_list_channel_groups_returns_copy(fresh_coordinator, in_memory_db):
    _seed_one_group(in_memory_db)
    fresh_coordinator.reload_groups(in_memory_db)
    host = _make_host()
    g1 = host.list_channel_groups()
    g1[0]["name"] = "MUTATED"
    g2 = host.list_channel_groups()
    assert g2[0]["name"] == "Group-A"


# ============================================================
# B. query_channel_group
# ============================================================


def test_query_channel_group_nonexistent_returns_none(fresh_coordinator):
    host = _make_host()
    assert host.query_channel_group(99) is None


def test_query_channel_group_existing(fresh_coordinator, in_memory_db):
    _seed_one_group(in_memory_db)
    fresh_coordinator.reload_groups(in_memory_db)
    host = _make_host()
    g = host.query_channel_group(1)
    assert g is not None
    assert g["name"] == "Group-A"


def test_query_channel_group_no_capability_required(fresh_coordinator):
    host = _make_host()  # 没传 capabilities
    host.query_channel_group(99)  # 不抛


# ============================================================
# C. broadcast_to_channel_group
# ============================================================


def test_broadcast_without_capability_raises(fresh_coordinator, in_memory_db):
    _seed_one_group(in_memory_db)
    fresh_coordinator.reload_groups(in_memory_db)
    host = _make_host()  # 没 runtime.channel_group_broadcast
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError):
        host.broadcast_to_channel_group(1, {"key": "value"})


def test_broadcast_with_capability_succeeds(fresh_coordinator, in_memory_db):
    _seed_one_group(in_memory_db)
    fresh_coordinator.reload_groups(in_memory_db)
    host = _make_host(["runtime.channel_group_broadcast"])

    fire_calls = []
    with patch(
        "backend.plugin_system.hook_dispatch.fire_plugin_hook",
        side_effect=lambda *a, **kw: fire_calls.append(a) or {},
    ):
        result = host.broadcast_to_channel_group(1, {"key": "value"})

    assert result is True
    assert len(fire_calls) == 1
    hook_name, position, hook_type, ctx = fire_calls[0]
    assert hook_name == "plugin_broadcast_received"
    assert ctx["group_id"] == 1
    assert ctx["group_name"] == "Group-A"
    assert ctx["member_channel_ids"] == [0, 1]
    assert ctx["sender_customer_code"] == "acme"
    assert ctx["message"] == {"key": "value"}


def test_broadcast_to_nonexistent_group_returns_false(fresh_coordinator):
    host = _make_host(["runtime.channel_group_broadcast"])
    assert host.broadcast_to_channel_group(99, {"key": "value"}) is False


def test_broadcast_rejects_non_dict_message(fresh_coordinator, in_memory_db):
    _seed_one_group(in_memory_db)
    fresh_coordinator.reload_groups(in_memory_db)
    host = _make_host(["runtime.channel_group_broadcast"])
    assert host.broadcast_to_channel_group(1, "not a dict") is False
    assert host.broadcast_to_channel_group(1, [1, 2, 3]) is False
    assert host.broadcast_to_channel_group(1, None) is False


def test_broadcast_rejects_non_json_serializable_message(fresh_coordinator, in_memory_db):
    _seed_one_group(in_memory_db)
    fresh_coordinator.reload_groups(in_memory_db)
    host = _make_host(["runtime.channel_group_broadcast"])
    # 含不可 JSON 序列化的 value
    assert host.broadcast_to_channel_group(1, {"fn": lambda: None}) is False


def test_broadcast_swallows_hook_exception(fresh_coordinator, in_memory_db):
    """fire_plugin_hook 抛错时整体返回 False, 不向上抛."""
    _seed_one_group(in_memory_db)
    fresh_coordinator.reload_groups(in_memory_db)
    host = _make_host(["runtime.channel_group_broadcast"])

    with patch(
        "backend.plugin_system.hook_dispatch.fire_plugin_hook",
        side_effect=RuntimeError("hook fail"),
    ):
        result = host.broadcast_to_channel_group(1, {"key": "value"})

    assert result is False  # swallow 不向上抛


def test_broadcast_audit_log_success(fresh_coordinator, in_memory_db):
    """成功广播写 audit log."""
    _seed_one_group(in_memory_db)
    fresh_coordinator.reload_groups(in_memory_db)
    host = _make_host(["runtime.channel_group_broadcast"])

    audit_calls = []
    with patch.object(host, "_audit_log",
                      side_effect=lambda **kw: audit_calls.append(kw)):
        with patch(
            "backend.plugin_system.hook_dispatch.fire_plugin_hook",
            return_value={},
        ):
            host.broadcast_to_channel_group(1, {"key": "value"})

    # 至少一条 success audit
    assert any(c.get("status") == "success" and c.get("action") == "broadcast_to_channel_group"
               for c in audit_calls)


def test_broadcast_audit_log_rejected_invalid_message(fresh_coordinator, in_memory_db):
    _seed_one_group(in_memory_db)
    fresh_coordinator.reload_groups(in_memory_db)
    host = _make_host(["runtime.channel_group_broadcast"])

    audit_calls = []
    with patch.object(host, "_audit_log",
                      side_effect=lambda **kw: audit_calls.append(kw)):
        host.broadcast_to_channel_group(1, "not a dict")

    assert any(c.get("status") == "rejected" for c in audit_calls)
