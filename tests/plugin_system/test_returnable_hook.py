"""M1.2a 护栏: Returnable hook 契约 (RFC 09 §4.3)

客户视角叙事:
  G1 (v3.12) 阶段 fire_plugin_hook 是 void return, 插件只能 "通知" 不能 "决策".
  本次客户需求 1 (步骤耗时三档) 要求客户插件能在 step_change 决定 "这次步骤要不要
  打黄色警告", 这就需要 hook 返回值能被主程序消费.

  M1.2a 交付 (本测试守护):
    - fire_plugin_hook 升签名 None → Dict[str, Any]
    - 加 _merge_handler_results 聚合 (priority 大覆盖 priority 小)
    - 加 RETURNABLE_HOOK_FIELDS 白名单 (仅白名单字段进入返回值)
    - 完全向后兼容: 旧 8 处调用点语句式调用不消费返回值依然正常

  M1.2b 已交付 (v3.13):
    - 主程序业务侧真消费 pre_cycle_end.override_result 改 is_good (端到端见 M1.2b 测试)
    - record_step 消费 step_change.warn_threshold_violated 缓存到 _plugin_step_warn_cache
  M1.2c 已交付 (v3.13):
    - _trigger_event 尾部重排, event_fire hook 上移到 alarm 之前
    - 加 suppress_alarm 进白名单, _resolve_event_fire_suppress_alarm 纯函数消费

如果有人改 fire_plugin_hook 签名 / 改白名单 / 改聚合顺序, 这条测试会立刻 FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ============================================================
# 白名单契约
# ============================================================


def test_returnable_hook_fields_whitelist_pre_cycle_end():
    """pre_cycle_end 白名单字段集合契约."""
    from backend.plugin_system.hook_dispatch import RETURNABLE_HOOK_FIELDS

    assert "pre_cycle_end" in RETURNABLE_HOOK_FIELDS
    expected = {"override_result", "extra_counters"}
    assert RETURNABLE_HOOK_FIELDS["pre_cycle_end"] == expected, (
        f"pre_cycle_end 白名单变了 — 改白名单 = 升 plugin SDK 主版本, "
        f"必须同步更新 RFC 09 §4.3 + 本测试. 实际: {RETURNABLE_HOOK_FIELDS['pre_cycle_end']}"
    )


def test_returnable_hook_fields_whitelist_step_change():
    """step_change 白名单字段集合契约 — 客户需求 1 依赖."""
    from backend.plugin_system.hook_dispatch import RETURNABLE_HOOK_FIELDS

    assert "step_change" in RETURNABLE_HOOK_FIELDS
    expected = {"warn_threshold_violated", "warn_label"}
    assert RETURNABLE_HOOK_FIELDS["step_change"] == expected


def test_returnable_hook_fields_whitelist_event_fire():
    """event_fire 白名单 (M1.2c 起): ``{"suppress_alarm"}``.

    M1.2c (v3.13) 重排 _trigger_event 尾部, hook fire 上移到 alarm 之前 — 至此
    suppress_alarm 来得及作用. 改字段集合 = 升 plugin SDK 主版本号, 必须同步:
    - RFC 09 §4.3 白名单表
    - manifest schema (capabilities/returnable_fields)
    - tests/plugin_system/test_returnable_hook_consumption_M1_2c.py
    """
    from backend.plugin_system.hook_dispatch import RETURNABLE_HOOK_FIELDS

    assert RETURNABLE_HOOK_FIELDS.get("event_fire") == {"suppress_alarm"}, (
        f"event_fire 白名单变了: {RETURNABLE_HOOK_FIELDS.get('event_fire')}. "
        f"改白名单 = 升 plugin SDK + 同步 RFC 09 + manifest schema."
    )


def test_only_three_hooks_in_returnable_whitelist():
    """首批 returnable 白名单只覆盖 3 个 hook — 其它都是只读 hook."""
    from backend.plugin_system.hook_dispatch import RETURNABLE_HOOK_FIELDS

    assert set(RETURNABLE_HOOK_FIELDS.keys()) == {
        "pre_cycle_end",
        "step_change",
        "event_fire",
    }, "首批 returnable 白名单 hook 集合变了, 同步更新 RFC 09 + 本测试"


# ============================================================
# fire_plugin_hook 签名向后兼容
# ============================================================


def test_fire_plugin_hook_returns_dict_when_no_active_plugin():
    """没 active 插件时返回空 dict (不是 None)."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    with patch.object(mod.plugin_manager, "registry", None):
        ret = fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {})
        assert ret == {}
        assert isinstance(ret, dict)


def test_fire_plugin_hook_returns_dict_when_no_handlers():
    """有 registry 但无 handler 时返回空 dict."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    fake_registry = MagicMock()
    fake_registry.hooks.fire.return_value = []  # 无 handler
    with patch.object(mod.plugin_manager, "registry", fake_registry):
        ret = fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {"cycle_id": 1})
        assert ret == {}


def test_fire_plugin_hook_returns_dict_on_registry_layer_exception():
    """registry 层异常时返回空 dict, 不抛."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    fake_registry = MagicMock()
    fake_registry.hooks.fire.side_effect = RuntimeError("registry boom")
    with patch.object(mod.plugin_manager, "registry", fake_registry):
        ret = fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {})
        assert ret == {}


# ============================================================
# _merge_handler_results 单元测试 (核心聚合逻辑)
# ============================================================


def test_merge_non_whitelisted_hook_returns_empty():
    """非白名单 hook 即使 handler 返回 dict 也丢弃."""
    from backend.plugin_system.hook_dispatch import _merge_handler_results

    # cycle_start 不在白名单 → 任何返回值都被丢
    ret = _merge_handler_results("cycle_start", [{"override_result": "OK"}])
    assert ret == {}


def test_merge_filters_non_whitelisted_fields():
    """白名单 hook 的 handler 返回 dict 里, 不在白名单的字段被忽略."""
    from backend.plugin_system.hook_dispatch import _merge_handler_results

    ret = _merge_handler_results(
        "pre_cycle_end",
        [{
            "override_result": "NG",        # 白名单内 → 保留
            "extra_counters": {"a": 1},      # 白名单内 → 保留
            "random_field": "xxx",           # 白名单外 → 丢
            "suppress_alarm": True,          # 白名单外 → 丢
        }],
    )
    assert ret == {"override_result": "NG", "extra_counters": {"a": 1}}


def test_merge_priority_later_overrides_earlier():
    """后入 handler (priority 大) 的字段覆盖前入 handler (priority 小)."""
    from backend.plugin_system.hook_dispatch import _merge_handler_results

    # HooksRegistry.fire 按 priority 升序遍历, 列表后部 = priority 更大
    results = [
        {"override_result": "OK"},   # priority 小 (先入)
        {"override_result": "NG"},   # priority 大 (后入, 应覆盖)
    ]
    ret = _merge_handler_results("pre_cycle_end", results)
    assert ret == {"override_result": "NG"}, (
        "priority 大覆盖 priority 小 (RFC 09 §4.3 约定)"
    )


def test_merge_handles_non_dict_results():
    """handler 返回 None / 字符串 / 列表等非 dict 被静默忽略, 不影响其它 handler."""
    from backend.plugin_system.hook_dispatch import _merge_handler_results

    results = [
        None,
        "string",
        [1, 2, 3],
        {"override_result": "NG"},  # 唯一的有效 dict
        42,
    ]
    ret = _merge_handler_results("pre_cycle_end", results)
    assert ret == {"override_result": "NG"}


def test_merge_skips_error_marker_dicts():
    """handler 抛异常时 HooksRegistry.fire 给的占位 dict (含 _error) 被跳过."""
    from backend.plugin_system.hook_dispatch import _merge_handler_results

    results = [
        {"override_result": "OK"},
        {"_error": "boom", "_customer_code": "x"},  # handler 异常占位
        {"override_result": "NG"},
    ]
    ret = _merge_handler_results("pre_cycle_end", results)
    # 第二个 dict 跳过, 第三个覆盖第一个
    assert ret == {"override_result": "NG"}


def test_merge_step_change_fields():
    """step_change 白名单字段聚合 — 客户需求 1 链路."""
    from backend.plugin_system.hook_dispatch import _merge_handler_results

    ret = _merge_handler_results(
        "step_change",
        [{
            "warn_threshold_violated": True,
            "warn_label": "yellow",
            "extra_counters": {"x": 1},  # 不在 step_change 白名单 → 丢
        }],
    )
    assert ret == {"warn_threshold_violated": True, "warn_label": "yellow"}


def test_merge_event_fire_whitelist_keeps_suppress_alarm_drops_others():
    """event_fire 白名单 = ``{"suppress_alarm"}`` — 仅 suppress_alarm 保留, 其他丢弃."""
    from backend.plugin_system.hook_dispatch import _merge_handler_results

    ret = _merge_handler_results(
        "event_fire",
        [{"suppress_alarm": True, "override_event_kind": "NG"}],
    )
    # 仅 suppress_alarm 在白名单内, 其他 (override_event_kind) 被丢
    assert ret == {"suppress_alarm": True}


# ============================================================
# fire_plugin_hook 端到端 (走 registry.hooks.fire 真实路径)
# ============================================================


def test_fire_plugin_hook_end_to_end_priority_merge():
    """fire_plugin_hook 调用 → registry.hooks.fire → _merge_handler_results 全链路."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system.registry import HooksRegistry
    from backend.plugin_system import manager as mod

    hooks = HooksRegistry(customer_code="test-cc")
    # priority 100 (默认) 与 priority 200 各注册一个 pre_cycle_end handler
    hooks.register(
        "pre_cycle_end", phase="pre_cycle", when="pre", priority=100,
        handler=lambda ctx: {"override_result": "OK"},
    )
    hooks.register(
        "pre_cycle_end", phase="pre_cycle", when="pre", priority=200,
        handler=lambda ctx: {"override_result": "NG"},
    )

    fake_registry = MagicMock()
    fake_registry.hooks = hooks
    with patch.object(mod.plugin_manager, "registry", fake_registry):
        ret = fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {"cycle_id": 1})
    assert ret == {"override_result": "NG"}, "priority 200 应覆盖 priority 100"


def test_fire_plugin_hook_handler_exception_does_not_break_merge():
    """单个 handler 抛异常时, 其它 handler 的返回值仍正常聚合."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system.registry import HooksRegistry
    from backend.plugin_system import manager as mod

    hooks = HooksRegistry(customer_code="test-cc")

    def boom(ctx):
        raise RuntimeError("handler boom")

    hooks.register(
        "pre_cycle_end", phase="pre_cycle", when="pre", priority=100,
        handler=lambda ctx: {"override_result": "OK"},
    )
    hooks.register(
        "pre_cycle_end", phase="pre_cycle", when="pre", priority=150,
        handler=boom,
    )
    hooks.register(
        "pre_cycle_end", phase="pre_cycle", when="pre", priority=200,
        handler=lambda ctx: {"extra_counters": {"a": 5}},
    )

    fake_registry = MagicMock()
    fake_registry.hooks = hooks
    with patch.object(mod.plugin_manager, "registry", fake_registry):
        ret = fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {})
    # priority 100 + priority 200 都生效, priority 150 抛异常被跳过
    assert ret == {"override_result": "OK", "extra_counters": {"a": 5}}


# ============================================================
# 向后兼容: 现有 8 处调用点不消费返回值依然能跑
# ============================================================


def test_existing_callers_dont_break_when_ignoring_return_value():
    """模拟现有 8 处 fire_plugin_hook(...) 语句式调用 (不接返回值)."""
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    from backend.plugin_system import manager as mod

    with patch.object(mod.plugin_manager, "registry", None):
        # 模拟语句式调用 (现有所有 8 处接入点都是这种形式)
        fire_plugin_hook("cycle_start", "post_cycle_start", "post", {"channel_id": 0})
        fire_plugin_hook("step_change", "post_step", "post", {"step_record_id": 1})
        fire_plugin_hook("event_fire", "post_event", "post", {"event_id": 1})
        fire_plugin_hook("scan_received", "post_scan", "post", {"serial_no": "X"})
        fire_plugin_hook("project_activated", "post_activate", "post", {"project_id": 1})
        fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {"cycle_id": 1})
        fire_plugin_hook("session_end", "post_session", "post", {"session_id": 1})
        fire_plugin_hook("box_complete", "post_box", "post", {"box_serial": "B"})
        # 没异常抛 → 向后兼容通过
