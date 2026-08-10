"""
PLC 规则动作 — 兼容垫片 (RFC 14 起动作注册表上移为全局共享)。

实现已迁至 backend/services/triggers/actions.py (统一触发中心与 PLC 共用
一套动作面, 插件注册一次两边可用)。本模块保留原导入路径与
register_plc_action 别名, RFC 13 既有代码/测试/插件零改动。
"""
from backend.services.triggers.actions import (  # noqa: F401
    ACTION_REGISTRY,
    ctx_get,
    execute_actions,
    register_trigger_action,
    render_template,
    resolve_write_value,
)

# RFC 13 时代的注册入口别名 (插件 hook plc_rule_action 经此挂入)
register_plc_action = register_trigger_action
