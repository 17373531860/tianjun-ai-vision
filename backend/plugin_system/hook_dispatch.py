"""插件 hook 触发统一入口。

设计目标:
- 主程序"业务关键事件"统一调 ``fire_plugin_hook(...)``, 避免每处重复
  ``if plugin_manager.registry: try ... except`` 样板代码 (容易漏 / 不一致).
- 函数名独特, 便于 ``rg fire_plugin_hook`` 一次列全所有接入点 (审计友好).
- **底线**: 任何异常 swallow + 写日志, 绝不抛回业务流程 (插件挂不能影响主程序).

约束:
- 仅供 backend/api/* 与 backend/services/* 业务代码调用
- 插件代码 (plugins-examples/*) 不应直接调本模块, 它们走 registry.hooks.register 注册

v3.13 M1.2a 升级 (RFC 09 §4.3):
- ``fire_plugin_hook`` 返回值从 ``None`` 升为 ``Dict[str, Any]`` (向后兼容: 旧调用方
  以语句形式调用不影响行为).
- 返回 dict = 所有 handler 返回值经"白名单过滤 + priority 覆盖"聚合后的结果.
- 主程序业务代码可消费返回值实现 "Returnable hook" 决策反馈 (如 pre_cycle_end 返回
  ``override_result`` 改写结算结果). 业务消费侧由 M1.2b 单独接入.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Set


log = logging.getLogger("tianjun.plugin")


# ============================================================
# Returnable hook 白名单 (RFC 09 §4.3 + M1.2a)
# ============================================================
#
# 只有在白名单内的 hook + 白名单内的字段, handler 返回值才会被收进聚合 dict.
# 不在白名单的 hook → 即使 handler 返回 dict, 也整体被丢弃 (返回空 dict).
# 不在白名单字段的 key → 静默忽略 (audit log 单独记录, 便于发现误用).
#
# 新增白名单字段 = 升 plugin SDK 主版本, 必须同步更新:
#   1. RFC 09 §4.3 白名单表
#   2. tests/plugin_system/test_returnable_hook.py 白名单契约测试
#   3. docs/plugin-system/CHANGELOG.md
RETURNABLE_HOOK_FIELDS: Dict[str, Set[str]] = {
    # 周期结算前决策 — 写库前, 副作用未发生, 最适合改写结算结果.
    "pre_cycle_end": {
        "override_result",   # "OK" | "NG" | None — 主程序据此改写 is_good
        "extra_counters",    # dict, 写入 cycle.counters_snapshot 扩展键
    },
    # 步骤判定决策 — 客户需求 1 (步骤耗时三档颜色) 的核心钩点.
    "step_change": {
        "warn_threshold_violated",  # bool
        "warn_label",                # str — 触发警告的标签 (例 "yellow" / "near_limit")
    },
    # 事件后处理 — 客户需求 2 (步骤级 NG 抑制 / 自定义 alarm 联动).
    # v3.13 M1.2c (2026-05-28): suppress_alarm 加入白名单.
    #   - _trigger_event 已重构: hook fire 上移到 alarm 之前, suppress_alarm 来得及作用.
    #   - 消费纯函数: backend.api.source_event_trigger_mixin._resolve_event_fire_suppress_alarm
    #   - 严格 ``is True`` 而非 truthy: 防 ``"false"`` 字符串 / ``1`` 整数误抑制.
    #   - 安全侧默认: 任何不显式 True 的值都不抑制 (宁可误报警也不漏报警).
    "event_fire": {"suppress_alarm"},
    # v3.14 RFC 11: 串行流水线 5 个 hook.
    # 工件进入流水线 / 某工位完成 — 仅观察, 无 returnable 字段.
    "workpiece_flow_enter": set(),
    "workpiece_flow_station_done": set(),
    # 工件流转完成 — 插件可重写最终结果 (例 跨工位质量分加权).
    "workpiece_flow_completed": {"override_final_result"},
    # 工件超时 — 插件可重写超时动作 (force_ng / drop / alarm_only).
    "workpiece_flow_timeout": {"override_timeout_action"},
    # 短路触发 — 仅观察, 客户可挂报警/导出.
    "workpiece_flow_short_circuit": set(),
}


def _merge_handler_results(
    hook_type: str,
    results: List[Any],
) -> Dict[str, Any]:
    """聚合 handler 返回值, 按 priority 升序覆盖, 仅保留白名单字段.

    HooksRegistry.fire 返回的 ``results`` 列表索引顺序 = priority 升序,
    所以列表后面的 (priority 大) 覆盖前面的 (priority 小), 与 RFC 09 §4.3
    "priority 大的覆盖小的" 约定一致.

    Args:
        hook_type: hook 类型 (用于查白名单)
        results: HooksRegistry.fire 的返回值 — 元素可以是 dict / 任意 / 错误标识 dict

    Returns:
        聚合 dict. 不在 RETURNABLE_HOOK_FIELDS 内的 hook_type 永远返回 ``{}``.
        非 dict / 含 ``_error`` 键的 result 被跳过 (视为该 handler 没决策反馈).
    """
    allowed = RETURNABLE_HOOK_FIELDS.get(hook_type)
    if not allowed:
        # 该 hook 不在白名单 → handler 返回值全部丢弃, 走只读 hook 语义
        return {}

    merged: Dict[str, Any] = {}
    ignored_keys: Set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        # HooksRegistry.fire 给 handler 异常的占位 dict (含 _error / _customer_code)
        if "_error" in result:
            continue
        for key, value in result.items():
            if key in allowed:
                merged[key] = value  # 后入覆盖 (priority 大覆盖小)
            else:
                ignored_keys.add(key)

    if ignored_keys:
        # 误用提示: 插件作者可能写错字段名 / 用了未来字段
        log.warning(
            "[Plugin] hook %s returnable 字段被忽略 (不在白名单): %s. "
            "白名单字段: %s",
            hook_type, sorted(ignored_keys), sorted(allowed),
        )
    return merged


def fire_plugin_hook(
    hook_type: str,
    phase: str,
    when: str,
    ctx: Dict[str, Any],
) -> Dict[str, Any]:
    """触发当前 active 插件的 hook, 异常 swallow.

    Args:
        hook_type: hook 类别. 当前主程序业务侧已接入的:

                   G1 / G1.5 (v3.7.0~v3.12.0):
                   - ``"cycle_end"`` (G1.5 起): 周期结束写库后, 主流程联动前
                   - ``"pre_cycle_end"`` (v3.13 起): 周期结束写库前, 副作用尚未发生
                   - ``"session_end"`` (v3.13 起): session 写库 + 统计聚合后, db.close 前
                   - ``"box_complete"`` (v3.13 起): cluster 聚齐推 MES 后

                   M1.1 (v3.13 新增, RFC 09 §4.2):
                   - ``"cycle_start"``: cycle 落库 + MES on_cycle_start 已通知, 录像启动之前
                   - ``"step_change"``: step_record 已落库 + 本地缓存已更新; 客户需求 1
                     (步骤耗时三档颜色判定) 挂这里
                   - ``"event_fire"``: M1.2c 起 — end_cycle + 计数 + MES + events_log + _pending_ack
                     完成后、alarm + router + _last_event_time 之前 fire; 客户需求 2 步骤级 NG
                     抑制可通过返回 ``{"suppress_alarm": True}`` 跳过本次 alarm 联动;
                     event_kind 三档 OK / NG / CUSTOM
                   - ``"scan_received"``: 扫码主路径完成 (MES 已广播 + 外设已注入); dedup
                     / 解析失败 / external_only / test 期间路径**不** fire
                   - ``"project_activated"``: 项目激活 + 模型重载 + 配置同步 + MES pending
                     清理后

                   RFC 12 (v3.15, 步骤进行中计时广播):
                   - ``"step_tick"``: 步骤进行中, 主程序在推理热路径里对每个正在计时的
                     步骤按 ~1Hz 节流 fire, ctx 携带 ``elapsed_sec`` + 步骤身份 +
                     主程序侧 min/max_duration (只读参考). **只读 observe hook**
                     (不在 RETURNABLE_HOOK_FIELDS), 返回值丢弃. 客户"步骤耗时三档 +
                     实时警告/超时报警"策略挂这里 — 阈值判定 + trigger_alarm + NG 改写
                     (经 pre_cycle_end) 全在插件侧, 主程序不内嵌任何阈值策略.
                     ctx 字段契约见 tests/plugin_system/test_step_tick_hook.py.
                   - ``"detection_frame"``: 推理循环每帧 (非 tracking 路径) 把本帧
                     检测框广播给插件; ctx 携带 channel_id / frame_seq / timestamp /
                     detections. **只读 observe hook** (不在 RETURNABLE_HOOK_FIELDS),
                     返回值丢弃, 不改主程序检测/计数/录像/状态机. sensor-clean 插件靠
                     它逐帧自计数 (复刻 detect6 精度). ctx 字段契约见
                     tests/plugin_system/test_detection_frame_hook.py.

                   v3.31 (外部设备读数广播):
                   - ``"external_device_data"``: 称重器/传感器等外部设备每帧解析出读数后
                     广播给插件. ctx 携带 device_id / device_role / channel_id /
                     station_id / weight / parsed / raw / barcode / stable_state /
                     timestamp. **只读 observe hook** (不在 RETURNABLE_HOOK_FIELDS),
                     返回值丢弃, 不改外设数据主链路. 客户"称重投料防错/缺料判定"挂这里.

                   M1.1 末项 (v3.13, 2026-05-28 落地):
                   - ``"source_status_change"``: 5 个 lifecycle 公共方法 (pause / resume /
                     standby / resume_inference / stop) + capture_loop 3 处异常中断点都会
                     fire. 仅在 ``(is_running, is_detecting)`` 真发生变化时 fire (helper
                     dedup); reason 字段枚举 8 种触发源. ctx 含 before/after 双字段 +
                     source_type. 用于"工位开始/停止"通知 / 视频流断开联动报警 / 多工位
                     standby 同步关闭硬件等场景

        phase: 业务阶段标识, 通常等同 hook_type 但允许同 hook_type 多 phase
        when: ``"pre"`` 或 ``"post"``
        ctx: 业务上下文字典. **字段名稳定是契约的一部分** —
             改字段名等于改插件 SDK, 必须在 changelog 里明确标注.
             ctx 字段集合契约见 tests/plugin_system/test_new_hook_points_M1.py.

    Returns:
        Returnable hook 聚合 dict (M1.2a v3.13+):
        - 仅 ``RETURNABLE_HOOK_FIELDS`` 白名单内的 hook + 字段才会进入返回值
        - priority 大的 handler 覆盖 priority 小的 (后入覆盖)
        - 没 active 插件 / 没 handler / 全部 handler 抛异常 → 返回空 dict ``{}``
        - 旧调用方以语句形式调用不消费返回值, 完全向后兼容
    """
    try:
        from backend.plugin_system.manager import plugin_manager
        registry = plugin_manager.registry
        if registry is None:
            return {}  # 没 active 插件, 静默跳过 (高频路径不打日志)
        results = registry.hooks.fire(hook_type, phase, when, ctx)
        return _merge_handler_results(hook_type, results)
    except Exception as exc:
        # plugin_manager.registry.hooks.fire 自己已经 swallow 了 handler 异常,
        # 这里捕获的是 import / registry 状态等环境层异常 — 也不能抛回主流程.
        log.warning(
            "[Plugin] fire_plugin_hook(%s/%s/%s) 触发层异常 (已隔离, 主流程继续): %s",
            hook_type, phase, when, exc,
        )
        return {}
