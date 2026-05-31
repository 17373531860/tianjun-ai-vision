"""坑 3 护栏: 新增 pre_cycle_end / session_end / box_complete 三个 hook 接入点

客户视角叙事:
  ACME 集群有 4 个工位, 客户希望每个 box (4 个工位都齐发后) 推一份明细到他们自己的
  ClickHouse, 字段含 box_serial / overall_result / NG 项 / 时间戳. 改进前只有 cycle_end
  hook, 拿到的是单个 cycle (1 个工位的) 数据, 完全做不了 box 级聚合 — 客户需求落地不了.

  改进前 (v3.7.0~v3.12.0):
    全代码库只有 cycle_end 1 个 hook fire 点 (source_session_lifecycle_mixin.py:565).
    session_end / box_complete / pre_cycle_end 在 registry 里 register API 存在,
    但 source.py / cluster_collector.py 根本没接.

  改进后 (本测试守护):
    - source_session_lifecycle_mixin.end_cycle() 顶部 fire pre_cycle_end
    - source_session_lifecycle_mixin.end_session() db.close() 前 fire session_end
    - cluster_collector.check_and_dispatch() MES Gateway 推送后 fire box_complete
    全部走统一 backend.plugin_system.hook_dispatch.fire_plugin_hook()
    异常 swallow + 写日志.

如果有人删 hook 接入点 / 改 ctx 字段名, 这条测试会立刻 FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ----------------------- ctx 字段集合契约 -----------------------


PRE_CYCLE_END_FIELDS = {
    "channel_id",
    "cycle_id",
    "session_id",
    "is_good",
    "result",
    "judgement",
    "event_id",
    "event_name",
    "reason",
    "project_id",
    "step_sequence",
}

SESSION_END_FIELDS = {
    "channel_id",
    "session_id",
    "session_uuid",
    "total_cycles",
    "good_cycles",
    "ng_cycles",
    "avg_cycle_time",
    "min_cycle_time",
    "max_cycle_time",
    "counters_snapshot",
    "project_id",
}

BOX_COMPLETE_FIELDS = {
    "box_serial",
    "overall_result",
    "result",
    "total_stations",
    "completed_stations",
    "stations",
    "order_no",
    "workpiece_id",
    "ng_items",
    "timestamp",
}


# ----------------------- fire_plugin_hook 单元 -----------------------


def test_fire_plugin_hook_no_active_plugin_silent():
    """没 active 插件时, fire_plugin_hook 静默返回, 不抛."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    with patch.object(mod.plugin_manager, "registry", None):
        # 应不抛 / 不影响调用者
        fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {"channel_id": 1})


def test_fire_plugin_hook_dispatches_to_registry():
    """有 active 插件时, fire_plugin_hook 把调用代理到 registry.hooks.fire."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    fake_registry = MagicMock()
    with patch.object(mod.plugin_manager, "registry", fake_registry):
        fire_plugin_hook("session_end", "post_session", "post", {"session_id": 42})

    fake_registry.hooks.fire.assert_called_once_with(
        "session_end", "post_session", "post", {"session_id": 42}
    )


def test_fire_plugin_hook_swallows_registry_exception():
    """registry.hooks.fire 抛异常时 fire_plugin_hook 不抛回主流程."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    fake_registry = MagicMock()
    fake_registry.hooks.fire.side_effect = RuntimeError("registry boom")
    with patch.object(mod.plugin_manager, "registry", fake_registry):
        # 不应抛 — 主流程要继续
        fire_plugin_hook("box_complete", "post_box", "post", {"box_serial": "B-001"})


# ----------------------- 三个 hook ctx 字段集合稳定性 -----------------------


def test_pre_cycle_end_ctx_field_set_locked():
    """pre_cycle_end hook ctx 字段集合是契约的一部分.

    验证方式: 静态扫 source_session_lifecycle_mixin.py 的 end_cycle 函数,
    匹配 fire_plugin_hook("pre_cycle_end", ...) 调用的 ctx 字典字面量.
    """
    src = (
        Path(__file__).resolve().parents[2] / "backend" / "api"
        / "source_session_lifecycle_mixin.py"
    ).read_text(encoding="utf-8")

    # 找到 fire_plugin_hook("pre_cycle_end", ...) 调用块, 取它的 ctx dict literal
    import re
    m = re.search(
        r'fire_plugin_hook\("pre_cycle_end".*?\{(.+?)\}\)',
        src,
        re.DOTALL,
    )
    assert m is not None, "未找到 fire_plugin_hook('pre_cycle_end', ...) 调用 — hook 接入点被删?"

    body = m.group(1)
    found = set(re.findall(r'"([a-z_][a-z0-9_]*)":', body))
    missing = PRE_CYCLE_END_FIELDS - found
    extra = found - PRE_CYCLE_END_FIELDS
    assert not missing, f"pre_cycle_end ctx 缺字段: {missing}"
    assert not extra, f"pre_cycle_end ctx 多了字段 (要新增请同步更新本测试 + plugin SDK 文档): {extra}"


def test_session_end_ctx_field_set_locked():
    src = (
        Path(__file__).resolve().parents[2] / "backend" / "api"
        / "source_session_lifecycle_mixin.py"
    ).read_text(encoding="utf-8")

    import re
    m = re.search(
        r'fire_plugin_hook\("session_end".*?\{(.+?)\}\)',
        src,
        re.DOTALL,
    )
    assert m is not None, "未找到 fire_plugin_hook('session_end', ...) 调用"

    body = m.group(1)
    found = set(re.findall(r'"([a-z_][a-z0-9_]*)":', body))
    missing = SESSION_END_FIELDS - found
    extra = found - SESSION_END_FIELDS
    assert not missing, f"session_end ctx 缺字段: {missing}"
    assert not extra, f"session_end ctx 多了字段: {extra}"


def test_box_complete_ctx_passes_aggregated_dict():
    """box_complete hook 的 ctx 直接是 cluster_collector 内部 aggregated dict 拷贝.

    验证 cluster_collector.py 里的 fire_plugin_hook 调用确实存在,
    且字段集合与 BOX_COMPLETE_FIELDS 对齐.
    """
    src = (
        Path(__file__).resolve().parents[2] / "backend" / "services"
        / "cluster_collector.py"
    ).read_text(encoding="utf-8")

    assert 'fire_plugin_hook("box_complete"' in src, \
        "未找到 box_complete hook 接入点 — 已被删?"

    # aggregated 字段集合稳定 (在 cluster_collector.py 第 571 附近的 dict literal)
    import re
    m = re.search(r"aggregated = \{(.+?)\}", src, re.DOTALL)
    assert m is not None
    body = m.group(1)
    found = set(re.findall(r'"([a-z_][a-z0-9_]*)":', body))
    missing = BOX_COMPLETE_FIELDS - found
    assert not missing, f"box_complete ctx 缺字段: {missing}"


# ----------------------- 三个 hook 接入点都使用统一 dispatcher -----------------------


def test_all_three_hook_sites_use_dispatcher():
    """所有新增 hook 接入点必须经 fire_plugin_hook (审计 / 一致性约束).

    禁止业务代码直接调 plugin_manager.registry.hooks.fire(...).
    既有 cycle_end (G1.5) 接入点是历史豁免, 不在本约束内.
    """
    repo = Path(__file__).resolve().parents[2]
    files_to_check = [
        repo / "backend" / "api" / "source_session_lifecycle_mixin.py",
        repo / "backend" / "services" / "cluster_collector.py",
    ]

    for fp in files_to_check:
        src = fp.read_text(encoding="utf-8")
        # 历史 cycle_end hook 接入点豁免
        # 其他 hook_type 出现 plugin_manager.registry.hooks.fire 视为违反约束
        import re
        offending = re.findall(
            r"plugin_manager\.registry\.hooks\.fire\(\s*\"((?!cycle_end\")[^\"]+)\"",
            src,
        )
        assert not offending, (
            f"{fp.name} 出现绕过 fire_plugin_hook 直接调 hooks.fire 的 hook_type: "
            f"{offending} — 改用 fire_plugin_hook(...)"
        )
