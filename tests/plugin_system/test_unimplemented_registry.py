"""坑 2 护栏: 占位 registry 改 fail-fast

客户视角叙事:
  ACME 客户码插件作者写了一段
    registry.export_templates.register(...)
  以为这个模板会出现在自定义导出里. 装上去后客户**导出 docx 一看, 模板全没**,
  审计日志里却写着 "placeholder registered (count=1)" -- 完美的
  '善意的谎言' 灾难现场.

  改进前 (v3.7.0 ~ v3.12.0):
    _PlaceholderRegistry.register() 收集 + 写日志, 不抛错.
    插件作者无任何信号说"这接口现在不接".

  改进后 (本测试守护):
    _UnimplementedRegistry.register() 抛 PluginNotImplementedError, 异常消息
    带 customer_code + 占位 milestone (F7/F9) + 临时绕路建议.

  里程碑变更:
    - F8 export_fields 已于 v3.46 落地为真实现 (ExportFieldsRegistry),
      相关测试改为验证真行为, 详见 test_export_fields_registry_F8.py
    - F7 export_templates / F9 realtime_triggers 仍 fail-fast

  收益:
    - 插件作者 import 阶段 / register_plugin 阶段就 RED, 不到客户现场才发现
    - PluginManager 加载流程把这种异常转成 audit log + state=failed, 主程序不挂
    - 等 F7/F9 真接入时, 把这条注释删掉 + 实现真 register, 测试自然 FAIL 提醒
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.plugin_system.registry import (
    PluginNotImplementedError,
    PluginRegistry,
    _UnimplementedRegistry,
)


def _mk_registry() -> PluginRegistry:
    app = FastAPI()
    return PluginRegistry(app=app, engine=None, customer_code="acme")


# ----------------------- 三个 registry 都 fail-fast -----------------------


def test_export_templates_register_raises_with_clear_message():
    reg = _mk_registry()
    with pytest.raises(PluginNotImplementedError) as exc_info:
        reg.export_templates.register({"id": "tpl1", "format": "docx"})
    msg = str(exc_info.value)
    assert "acme" in msg
    assert "export_templates" in msg
    assert "F7" in msg


def test_export_fields_is_now_implemented():
    """F8 已落地: export_fields 不再 fail-fast, 而是真接入中央仓库.

    这里只守护"不再抛 PluginNotImplementedError"; 完整行为测试在
    test_export_fields_registry_F8.py.
    """
    from backend.services import export_field_registry as efr

    reg = _mk_registry()
    try:
        reg.export_fields.register(
            fields=[{"path": "plugin.acme.demo_field", "label": "演示字段"}],
        )
        assert "plugin.acme.demo_field" in reg.export_fields.registered()
    finally:
        efr.unregister_plugin_fields("acme")


def test_realtime_triggers_register_raises():
    reg = _mk_registry()
    with pytest.raises(PluginNotImplementedError):
        reg.realtime_triggers.register("session_end", handler=lambda x: None)


# ----------------------- PluginNotImplementedError 是 NotImplementedError 子类 -----------------------


def test_exception_is_subclass_of_not_implemented_error():
    """允许外层用 ``except NotImplementedError`` 兜底捕获 (Python 习惯), 同时
    PluginManager 可以用 ``except PluginNotImplementedError`` 精准捕获.
    """
    assert issubclass(PluginNotImplementedError, NotImplementedError)


# ----------------------- snapshot 反映状态 -----------------------


def test_snapshot_reports_unimplemented_status():
    """诊断快照应明确标记 F7/F9 registry 是未接入状态, 而非 0 计数误导;
    F8 export_fields 已实现, 快照给出已注册字段列表."""
    reg = _mk_registry()
    snap = reg.snapshot()
    assert snap["export_templates_status"] == "not_implemented_F7"
    assert snap["export_fields"] == []  # 未注册任何字段
    assert snap["realtime_triggers_status"] == "not_implemented_F9"


# ----------------------- routes / hooks / tables 不受影响 -----------------------


def test_routes_hooks_tables_still_work():
    """改 fail-fast 不应影响三个真实 registry 的正常工作."""
    reg = _mk_registry()
    # hooks 仍然可以注册
    reg.hooks.register(
        hook_type="cycle_end",
        phase="post_cycle",
        when="post",
        priority=100,
        handler=lambda ctx: {"got_cycle": ctx.get("cycle_id")},
    )
    # snapshot 反映了 hook
    snap = reg.snapshot()
    assert len(snap["hooks"]) == 1
    assert snap["hooks"][0]["hook_type"] == "cycle_end"
