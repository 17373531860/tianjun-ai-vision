"""F8 插件导出字段注册 — 真实现行为测试 (v3.46 落地).

现场叙事:
  lg-worktime 工时看板插件在 register_plugin 里调用
    registry.export_fields.register(fields=[...], provider=...)
  之后:
    1. GET /api/v1/export/fields 的字段树多出「插件字段」分组
    2. 自定义导出模板可以写 {{ plugin.lg_worktime.cycle_va_seconds }}
    3. 导出上下文构造时 provider 被调用, 值挂到 ctx["plugin"]["lg_worktime"]
    4. 插件停用(重启不再加载)后字段消失, 老模板渲染为空而不崩

守护点:
  - path 命名空间强制 plugin.<cc_snake>. 前缀
  - provider 异常隔离, 不拖垮导出主流程
  - 同 customer_code 重复注册 = 整体替换
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.plugin_system.registry import PluginRegistry
from backend.services import export_field_registry as efr


CC = "lg-worktime"
NS = "lg_worktime"


@pytest.fixture()
def registry():
    reg = PluginRegistry(app=FastAPI(), engine=None, customer_code=CC)
    yield reg
    efr.unregister_plugin_fields(CC)


def _demo_fields():
    return [
        {"path": f"plugin.{NS}.cycle_va_seconds", "label": "本周期VA时长(秒)",
         "type": "float", "example": "12.5"},
        {"path": f"plugin.{NS}.cycle_nva_seconds", "label": "本周期NVA时长(秒)",
         "type": "float", "example": "3.2"},
    ]


# ----------------------- 注册与元数据可见 -----------------------


def test_register_fields_appear_in_central_registry(registry):
    registry.export_fields.register(fields=_demo_fields())

    hit = efr.lookup_field(f"plugin.{NS}.cycle_va_seconds")
    assert hit is not None
    assert hit.group == efr.GROUP_PLUGIN
    assert hit.label == "本周期VA时长(秒)"

    # list_fields / list_groups / field_paths / stats 都要能看到
    paths = efr.field_paths()
    assert f"plugin.{NS}.cycle_va_seconds" in paths
    assert f"plugin.{NS}.cycle_nva_seconds" in paths

    groups = efr.list_groups()
    plugin_group = next((g for g in groups if g["group"] == efr.GROUP_PLUGIN), None)
    assert plugin_group is not None
    assert plugin_group["label"] == "插件字段"
    assert plugin_group["count"] == 2

    assert efr.stats()["by_group"].get(efr.GROUP_PLUGIN) == 2


def test_registered_paths_in_snapshot(registry):
    registry.export_fields.register(fields=_demo_fields())
    snap = registry.snapshot()
    assert f"plugin.{NS}.cycle_va_seconds" in snap["export_fields"]


def test_unregister_removes_everything(registry):
    registry.export_fields.register(fields=_demo_fields())
    efr.unregister_plugin_fields(CC)
    assert efr.lookup_field(f"plugin.{NS}.cycle_va_seconds") is None
    assert efr.stats()["by_group"].get(efr.GROUP_PLUGIN) is None


def test_reregister_replaces_wholesale(registry):
    registry.export_fields.register(fields=_demo_fields())
    registry.export_fields.register(fields=[
        {"path": f"plugin.{NS}.only_one", "label": "唯一字段"},
    ])
    assert efr.lookup_field(f"plugin.{NS}.cycle_va_seconds") is None
    assert efr.lookup_field(f"plugin.{NS}.only_one") is not None


# ----------------------- 命名空间与参数校验 -----------------------


def test_wrong_namespace_rejected(registry):
    with pytest.raises(ValueError):
        registry.export_fields.register(fields=[
            {"path": "cycle.hacked_field", "label": "越权字段"},
        ])
    # 越权注册整体不生效
    assert efr.lookup_field("cycle.hacked_field") is None


def test_other_plugin_namespace_rejected(registry):
    with pytest.raises(ValueError):
        registry.export_fields.register(fields=[
            {"path": "plugin.other_customer.field", "label": "别家字段"},
        ])


def test_empty_fields_rejected(registry):
    with pytest.raises(ValueError):
        registry.export_fields.register(fields=[])


def test_non_callable_provider_rejected(registry):
    with pytest.raises(TypeError):
        registry.export_fields.register(fields=_demo_fields(), provider="not-callable")


# ----------------------- provider 与上下文集成 -----------------------


def test_provider_values_collected(registry):
    def provider(db, ctx):
        return {"cycle_va_seconds": 12.5, "cycle_nva_seconds": 3.2}

    registry.export_fields.register(fields=_demo_fields(), provider=provider)
    out = efr.collect_plugin_context(db=None, ctx={"cycle": {"id": 1}})
    assert out == {NS: {"cycle_va_seconds": 12.5, "cycle_nva_seconds": 3.2}}


def test_provider_gets_ctx(registry):
    seen = {}

    def provider(db, ctx):
        seen["cycle_id"] = ctx["cycle"]["id"]
        return {"echo": ctx["cycle"]["id"]}

    registry.export_fields.register(
        fields=[{"path": f"plugin.{NS}.echo", "label": "回显"}],
        provider=provider,
    )
    out = efr.collect_plugin_context(db=None, ctx={"cycle": {"id": 42}})
    assert seen["cycle_id"] == 42
    assert out[NS]["echo"] == 42


def test_provider_exception_isolated(registry):
    def bad_provider(db, ctx):
        raise RuntimeError("插件炸了")

    registry.export_fields.register(fields=_demo_fields(), provider=bad_provider)
    out = efr.collect_plugin_context(db=None, ctx={})
    assert out == {}  # 异常被隔离, 不抛给导出主流程


def test_provider_non_dict_return_discarded(registry):
    registry.export_fields.register(
        fields=_demo_fields(), provider=lambda db, ctx: ["not", "a", "dict"],
    )
    out = efr.collect_plugin_context(db=None, ctx={})
    assert out == {}


# ----------------------- Jinja 渲染端到端 -----------------------


def test_template_renders_plugin_field(registry):
    """模板 {{ plugin.lg_worktime.x }} 在字段存在时渲染值。"""
    from backend.services.export_renderer import render_string

    ctx = {"plugin": {NS: {"cycle_va_seconds": 12.5}}}
    result = render_string("VA={{ plugin.lg_worktime.cycle_va_seconds }}", ctx)
    assert "12.5" in result


def test_template_renders_empty_after_plugin_gone(registry):
    """插件停用后 ctx['plugin'] 为空 — 老模板渲染为空串, 不崩 (兜底守护)。"""
    from backend.services.export_renderer import render_string

    result = render_string(
        "VA={{ plugin.lg_worktime.cycle_va_seconds }}", {"plugin": {}}
    )
    assert result == "VA="
