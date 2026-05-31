"""项目 pipeline_config 归一化 — API/脚本创建的项目常缺 sequence_order."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_default_sequence_order(steps_config: Optional[List[dict]]) -> List[dict]:
    """从 steps_config 生成默认步骤顺序（与前端 _buildDefaultSequenceOrder 对齐）。"""
    if not steps_config:
        return []
    result: List[dict] = []
    for step in steps_config:
        if not step:
            continue
        if step.get("enabled", True) is False:
            continue
        if step.get("is_backup") or step.get("backup_for"):
            continue
        sid = step.get("id")
        if sid is not None:
            result.append({"step_id": sid})
    return result


def ensure_sequence_orders(
    pipeline_config: Optional[dict],
    logic_mode: Optional[str],
    steps_config: Optional[List[dict]],
) -> dict:
    """顺序模式且 sequence_order 为空时，从 steps_config 自动补全。"""
    cfg: Dict[str, Any] = dict(pipeline_config or {})
    mode = logic_mode or cfg.get("logic_mode", "sequential")
    custom_based_on = cfg.get("custom_based_on", "sequential")
    default_seq = build_default_sequence_order(steps_config)
    if not default_seq:
        return cfg

    if mode == "custom" and custom_based_on == "sequential":
        if not cfg.get("custom_sequence_order"):
            cfg["custom_sequence_order"] = default_seq
    elif mode == "sequential":
        if not cfg.get("sequence_order"):
            cfg["sequence_order"] = default_seq
    return cfg
