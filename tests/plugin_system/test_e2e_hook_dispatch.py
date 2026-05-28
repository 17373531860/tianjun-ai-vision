"""端到端集成测试: fire_plugin_hook → 真实 PluginRegistry → 真实 handler

不用 MagicMock — 用真实的 PluginRegistry / HooksRegistry / 真实回调函数,
模拟客户机上"插件已加载 → 主程序 fire 业务事件 → 插件代码真被执行"的完整路径.

为什么需要这一层 (在 test_new_hook_points 单元层之上加这层):
  test_new_hook_points 用 MagicMock 验证 fire_plugin_hook 调用 registry.hooks.fire,
  但**没验证真实 handler 真能拿到 ctx 字段**. 如果哪天 hooks.fire 实现被改坏 (例如
  忘了把 ctx 传给 handler), 单元测试可能不发现, 集成测试会立刻 FAIL.

覆盖客户场景:
  ACME 插件作者在 plugins-examples/tier3-fullstack/backend/__init__.py 里写:
    registry.hooks.register("pre_cycle_end", ..., handler=on_pre_cycle_end_post)
    registry.hooks.register("session_end", ..., handler=on_session_end_post)
    registry.hooks.register("box_complete", ..., handler=on_box_complete_post)
  本测试模拟主程序 fire 这三种 hook, 断言 handler 真的被调 + 真的拿到完整 ctx.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.plugin_system import manager as manager_mod
from backend.plugin_system.hook_dispatch import fire_plugin_hook
from backend.plugin_system.registry import PluginRegistry


@pytest.fixture
def active_plugin_registry(monkeypatch):
    """模拟一个 active 插件的 registry, 注入到 plugin_manager."""
    app = FastAPI()
    reg = PluginRegistry(app=app, engine=None, customer_code="acme-e2e")

    monkeypatch.setattr(manager_mod.plugin_manager, "registry", reg)
    return reg


# ----------------------- pre_cycle_end -----------------------


def test_e2e_pre_cycle_end_hook_reaches_real_handler(active_plugin_registry):
    """主程序 fire pre_cycle_end → 真实插件 handler 被调用 + 拿到完整 ctx."""
    captured = []

    def real_handler(ctx):
        captured.append(ctx)
        return {"plugin_acked": ctx["cycle_id"]}

    active_plugin_registry.hooks.register(
        hook_type="pre_cycle_end", phase="pre_cycle", when="pre",
        priority=100, handler=real_handler,
    )

    fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {
        "channel_id": 1,
        "cycle_id": 99,
        "session_id": 7,
        "is_good": True,
        "result": "OK",
        "judgement": "OK",
        "event_id": None,
        "event_name": None,
        "reason": None,
        "project_id": 3,
        "step_sequence": ["A", "B"],
    })

    assert len(captured) == 1
    ctx = captured[0]
    assert ctx["cycle_id"] == 99
    assert ctx["is_good"] is True
    assert ctx["step_sequence"] == ["A", "B"]


# ----------------------- session_end -----------------------


def test_e2e_session_end_hook_reaches_real_handler(active_plugin_registry):
    captured = []

    def real_handler(ctx):
        captured.append(ctx)

    active_plugin_registry.hooks.register(
        hook_type="session_end", phase="post_session", when="post",
        priority=100, handler=real_handler,
    )

    fire_plugin_hook("session_end", "post_session", "post", {
        "channel_id": 1,
        "session_id": 42,
        "session_uuid": "uuid-42",
        "total_cycles": 100,
        "good_cycles": 95,
        "ng_cycles": 5,
        "avg_cycle_time": 12.3,
        "min_cycle_time": 8.0,
        "max_cycle_time": 18.5,
        "counters_snapshot": {"x": 1},
        "project_id": 3,
    })

    assert len(captured) == 1
    ctx = captured[0]
    assert ctx["total_cycles"] == 100
    assert ctx["counters_snapshot"] == {"x": 1}


# ----------------------- box_complete -----------------------


def test_e2e_box_complete_hook_reaches_real_handler(active_plugin_registry):
    captured = []

    def real_handler(ctx):
        captured.append(ctx)

    active_plugin_registry.hooks.register(
        hook_type="box_complete", phase="post_box", when="post",
        priority=100, handler=real_handler,
    )

    fire_plugin_hook("box_complete", "post_box", "post", {
        "box_serial": "B-XYZ-001",
        "overall_result": "NG",
        "result": "NG",
        "total_stations": 4,
        "completed_stations": 4,
        "stations": [{"channel_id": 0, "result": "OK"}, {"channel_id": 1, "result": "NG"}],
        "order_no": "WO-2026-001",
        "workpiece_id": 1234,
        "ng_items": ["划痕"],
        "timestamp": "2026-05-28T10:00:00",
    })

    assert len(captured) == 1
    ctx = captured[0]
    assert ctx["box_serial"] == "B-XYZ-001"
    assert ctx["overall_result"] == "NG"
    assert "划痕" in ctx["ng_items"]


# ----------------------- 多 hook 协同 + 优先级 -----------------------


def test_e2e_multiple_handlers_priority_order(active_plugin_registry):
    """同一个 hook 上多个 handler 按 priority 排序触发, 模拟真实多模块插件."""
    call_order = []

    def hp50(ctx):
        call_order.append("priority_50")

    def hp100(ctx):
        call_order.append("priority_100")

    def hp200(ctx):
        call_order.append("priority_200")

    active_plugin_registry.hooks.register("pre_cycle_end", "pre_cycle", "pre", 200, hp200)
    active_plugin_registry.hooks.register("pre_cycle_end", "pre_cycle", "pre", 50, hp50)
    active_plugin_registry.hooks.register("pre_cycle_end", "pre_cycle", "pre", 100, hp100)

    fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {"cycle_id": 1})

    assert call_order == ["priority_50", "priority_100", "priority_200"]


# ----------------------- handler 抛错不污染主流程 -----------------------


def test_e2e_handler_exception_does_not_break_other_handlers(active_plugin_registry):
    """真实 handler 抛异常 → 其他 handler 仍然执行 → fire_plugin_hook 不抛."""
    call_order = []

    def boom(ctx):
        call_order.append("boom_called")
        raise RuntimeError("插件作者写的代码炸了")

    def survive(ctx):
        call_order.append("survive_called")

    active_plugin_registry.hooks.register("session_end", "post_session", "post", 50, boom)
    active_plugin_registry.hooks.register("session_end", "post_session", "post", 100, survive)

    # 主程序调 fire_plugin_hook 不应被 handler 异常打断
    fire_plugin_hook("session_end", "post_session", "post", {"session_id": 1})

    # 两个 handler 都被调
    assert "boom_called" in call_order
    assert "survive_called" in call_order


# ----------------------- 没注册对应 hook_type 的 handler 时静默 -----------------------


def test_e2e_no_handler_registered_for_hook_type(active_plugin_registry):
    """active 插件存在但没注册 box_complete 时, fire 静默不抛."""
    # 注册了 pre_cycle_end, 没注册 box_complete
    active_plugin_registry.hooks.register(
        "pre_cycle_end", "pre_cycle", "pre", 100, lambda c: None,
    )

    # 不应抛
    fire_plugin_hook("box_complete", "post_box", "post", {"box_serial": "B-1"})


# ----------------------- 三个 hook 一次性触发链路 (端到端) -----------------------


def test_e2e_full_lifecycle_chain(active_plugin_registry):
    """模拟一个完整生命周期: pre_cycle_end → session_end → box_complete 都正确路由."""
    flow = []

    active_plugin_registry.hooks.register(
        "pre_cycle_end", "pre_cycle", "pre", 100,
        lambda c: flow.append(("pre_cycle_end", c["cycle_id"])),
    )
    active_plugin_registry.hooks.register(
        "session_end", "post_session", "post", 100,
        lambda c: flow.append(("session_end", c["session_id"])),
    )
    active_plugin_registry.hooks.register(
        "box_complete", "post_box", "post", 100,
        lambda c: flow.append(("box_complete", c["box_serial"])),
    )

    fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {"cycle_id": 1, "is_good": True})
    fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {"cycle_id": 2, "is_good": False})
    fire_plugin_hook("session_end", "post_session", "post", {"session_id": 99})
    fire_plugin_hook("box_complete", "post_box", "post", {"box_serial": "B-1"})

    assert flow == [
        ("pre_cycle_end", 1),
        ("pre_cycle_end", 2),
        ("session_end", 99),
        ("box_complete", "B-1"),
    ]
