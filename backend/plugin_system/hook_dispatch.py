"""插件 hook 触发统一入口。

设计目标:
- 三处主程序"业务关键事件"统一调 ``fire_plugin_hook(...)``, 避免每处重复
  ``if plugin_manager.registry: try ... except`` 样板代码 (容易漏 / 不一致).
- 函数名独特, 便于 ``rg fire_plugin_hook`` 一次列全所有接入点 (审计友好).
- **底线**: 任何异常 swallow + 写日志, 绝不抛回业务流程 (插件挂不能影响主程序).

约束:
- 仅供 backend/api/* 与 backend/services/* 业务代码调用
- 插件代码 (plugins-examples/*) 不应直接调本模块, 它们走 registry.hooks.register 注册
"""
from __future__ import annotations

import logging
from typing import Any, Dict


log = logging.getLogger("tianjun.plugin")


def fire_plugin_hook(
    hook_type: str,
    phase: str,
    when: str,
    ctx: Dict[str, Any],
) -> None:
    """触发当前 active 插件的 hook, 异常 swallow.

    Args:
        hook_type: hook 类别. 当前主程序业务侧已接入的:
                   - ``"cycle_end"`` (G1.5 起): 周期结束写库后, 主流程联动前
                   - ``"pre_cycle_end"`` (v3.13 起): 周期结束写库前, 副作用尚未发生
                   - ``"session_end"`` (v3.13 起): session 写库 + 统计聚合后, db.close 前
                   - ``"box_complete"`` (v3.13 起): cluster 聚齐推 MES 后
        phase: 业务阶段标识, 通常等同 hook_type 但允许同 hook_type 多 phase
        when: ``"pre"`` 或 ``"post"``
        ctx: 业务上下文字典. **字段名稳定是契约的一部分** —
             改字段名等于改插件 SDK, 必须在 changelog 里明确标注.
    """
    try:
        from backend.plugin_system.manager import plugin_manager
        registry = plugin_manager.registry
        if registry is None:
            return  # 没 active 插件, 静默跳过 (高频路径不打日志)
        registry.hooks.fire(hook_type, phase, when, ctx)
    except Exception as exc:
        # plugin_manager.registry.hooks.fire 自己已经 swallow 了 handler 异常,
        # 这里捕获的是 import / registry 状态等环境层异常 — 也不能抛回主流程.
        log.warning(
            "[Plugin] fire_plugin_hook(%s/%s/%s) 触发层异常 (已隔离, 主流程继续): %s",
            hook_type, phase, when, exc,
        )
