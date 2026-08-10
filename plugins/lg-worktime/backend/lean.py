"""LG 工时看板 — LEAN 价值分解纯逻辑 (无 DB / 无 host 依赖, 可直接单测)。

口径 (对齐 LG 原系统 lg_20260514.py, v1.5.0 起全部参数可配):
  - 每步骤配「动作价值」VA / BVA / NVA (项目管理·步骤设置), 未配置走全局默认
    default_value_type (插件设置, 出厂 VA) — 冻结期参数, 改了不回写历史
  - 步骤耗时记入对应价值类
  - 步骤间等待 (interval_from_prev) 原始值恒存 wait_seconds;
    归入哪一类由 wait_value_type 决定 (NVA/BVA/VA/EXCLUDE, 出厂 NVA)
    — 统计口径参数, 查询/展示时折算 (apply_wait), 历史数据同样跟随新口径
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

VALUE_TYPES = ("VA", "BVA", "NVA")
DEFAULT_VALUE_TYPE = "VA"

# 等待归类可选值: 三类价值 + 不计入统计
WAIT_VALUE_TYPES = ("VA", "BVA", "NVA", "EXCLUDE")
DEFAULT_WAIT_VALUE_TYPE = "NVA"


def normalize_value_type(raw: Any, default: str = DEFAULT_VALUE_TYPE) -> str:
    """任意输入规整为合法动作价值; 非法值退 default (全局默认价值可配)。"""
    if isinstance(raw, str) and raw.strip().upper() in VALUE_TYPES:
        return raw.strip().upper()
    return default if default in VALUE_TYPES else DEFAULT_VALUE_TYPE


def normalize_wait_type(raw: Any) -> str:
    """等待归类规整; 非法值退出厂 NVA。"""
    if isinstance(raw, str) and raw.strip().upper() in WAIT_VALUE_TYPES:
        return raw.strip().upper()
    return DEFAULT_WAIT_VALUE_TYPE


def step_values_from_steps_config(steps_config: Any, customer_code: str = "lg-worktime") -> Dict[str, str]:
    """从 Project.steps_config 提取 {step_label: value_type}。

    价值配置存放点: steps_config[i].plugin_data["lg-worktime"]["value_type"]
    (经主程序 PUT /projects/{id}/plugin-data scope=steps_config 写入)。

    ⚠ 只收录显式配置且合法的步骤 — 未配置/非法的步骤不进映射,
    让消费方按全局 default_value_type 兜底 (否则全局默认永远不生效)。
    """
    out: Dict[str, str] = {}
    if not isinstance(steps_config, list):
        return out
    for item in steps_config:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not label:
            continue
        plugin_data = item.get("plugin_data") or {}
        mine = plugin_data.get(customer_code) if isinstance(plugin_data, dict) else None
        raw = mine.get("value_type") if isinstance(mine, dict) else None
        if isinstance(raw, str) and raw.strip().upper() in VALUE_TYPES:
            out[str(label)] = raw.strip().upper()
    return out


def compute_lean(steps: List[Dict[str, Any]], step_values: Dict[str, str],
                 default_value_type: str = DEFAULT_VALUE_TYPE) -> Dict[str, float]:
    """按步骤明细 + 价值映射计算 LEAN 分解 (原始四桶, 等待不折算)。

    参数:
        steps: [{label, duration, interval_from_prev}, ...] (缺字段容错为 0)
        step_values: {label: "VA"/"BVA"/"NVA"}, 缺失走 default_value_type
        default_value_type: 未配置步骤的全局默认价值 (插件设置, 冻结期参数)

    返回:
        {va, bva, nva, wait, nva_total, total}
        nva_total/total 按出厂口径 (等待归 NVA) 给出; 展示前应过 apply_wait 折算
    """
    va = bva = nva = wait = 0.0
    dvt = normalize_value_type(default_value_type)
    for s in steps:
        if not isinstance(s, dict):
            continue
        vt = normalize_value_type(step_values.get(str(s.get("label") or "")), default=dvt)
        try:
            d = float(s.get("duration") or 0.0)
        except (TypeError, ValueError):
            d = 0.0
        if d > 0:
            if vt == "BVA":
                bva += d
            elif vt == "NVA":
                nva += d
            else:
                va += d
        try:
            w = float(s.get("interval_from_prev") or 0.0)
        except (TypeError, ValueError):
            w = 0.0
        if w > 0:
            wait += w
    nva_total = nva + wait
    total = va + bva + nva_total
    return {
        "va": round(va, 3),
        "bva": round(bva, 3),
        "nva": round(nva, 3),
        "wait": round(wait, 3),
        "nva_total": round(nva_total, 3),
        "total": round(total, 3),
    }


def apply_wait(lean: Dict[str, Any], wait_value_type: str = DEFAULT_WAIT_VALUE_TYPE) -> Dict[str, float]:
    """把原始四桶 {va,bva,nva,wait} 按等待归类口径折算成展示/占比用的分解。

    返回键与 compute_lean 一致 (va/bva/nva/wait/nva_total/total):
      - wait 键恒为原始等待秒数 (KPI 单列展示用)
      - nva 键恒为步骤级 NVA (不含等待)
      - 归 NVA (出厂): nva_total = nva + wait
      - 归 BVA / VA:  wait 折入对应桶, nva_total = nva
      - EXCLUDE:      等待不计入任何桶, total 也不含等待
    """
    va = float(lean.get("va") or 0.0)
    bva = float(lean.get("bva") or 0.0)
    nva = float(lean.get("nva") or 0.0)
    wait = float(lean.get("wait") or 0.0)
    wvt = normalize_wait_type(wait_value_type)
    if wvt == "VA":
        va += wait
        nva_total = nva
    elif wvt == "BVA":
        bva += wait
        nva_total = nva
    elif wvt == "EXCLUDE":
        nva_total = nva
    else:  # NVA (出厂)
        nva_total = nva + wait
    total = va + bva + nva_total
    return {
        "va": round(va, 3),
        "bva": round(bva, 3),
        "nva": round(nva, 3),
        "wait": round(wait, 3),
        "nva_total": round(nva_total, 3),
        "total": round(total, 3),
    }


def lean_ratios(va: float, bva: float, nva_total: float) -> Dict[str, Optional[float]]:
    """按 LG LEAN 汇总口径算三类占比(%). 总量为 0 时返回 None (前端显示 —)。"""
    total = (va or 0.0) + (bva or 0.0) + (nva_total or 0.0)
    if total <= 0:
        return {"va_ratio": None, "bva_ratio": None, "nva_ratio": None}
    return {
        "va_ratio": round(100.0 * va / total, 2),
        "bva_ratio": round(100.0 * bva / total, 2),
        "nva_ratio": round(100.0 * nva_total / total, 2),
    }
