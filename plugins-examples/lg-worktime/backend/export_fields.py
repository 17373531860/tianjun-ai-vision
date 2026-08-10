"""LG 工时看板 — 自定义导出字段 (F8 注册进主程序中央仓库)。

模板引用方式 (前端字段树「插件字段」分组可拖拽):
  单周期 (cycle_end 实时规则 / 选某轮导出):
    {{ plugin.lg_worktime.cycle_va_seconds }} 等 cycle_* 字段
  会话/范围导出 (LG「LEAN_统计汇总」复刻):
    {{ plugin.lg_worktime.session_va_seconds }} 等 session_* 字段
    (LG 口径: 只累计合格轮; 等待归 NVA)

值来源: p_lg_worktime_cycle_lean (cycle_end 冻结落库), provider 按导出上下文里的
cycle.id / session.id 反查。查不到 (如插件启用前的历史周期) 时给 None, 模板渲染为空。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

log = logging.getLogger("tianjun.plugin")

_NS = "plugin.lg_worktime."


def _f(name: str, label: str, type_: str = "float", example: str = "", notes: str = "") -> Dict[str, Any]:
    return {"path": _NS + name, "label": label, "type": type_,
            "example": example, "notes": notes}


EXPORT_FIELDS = [
    # ---- 单周期 LEAN 分解 ----
    _f("cycle_va_seconds", "本周期VA时长(秒)", example="12.5"),
    _f("cycle_bva_seconds", "本周期BVA时长(秒)", example="3.4"),
    _f("cycle_nva_seconds", "本周期NVA时长(秒,不含等待)", example="1.1"),
    _f("cycle_wait_seconds", "本周期等待时长(秒)", example="2.8", notes="步骤间等待, 归 NVA"),
    _f("cycle_nva_total_seconds", "本周期NVA合计(秒,含等待)", example="3.9"),
    _f("cycle_va_ratio", "本周期VA占比(%)", example="63.1"),
    _f("cycle_bva_ratio", "本周期BVA占比(%)", example="17.2"),
    _f("cycle_nva_ratio", "本周期NVA占比(%,含等待)", example="19.7"),
    # ---- 会话累计 (LG LEAN_统计汇总 口径: 仅合格轮) ----
    _f("session_good_rounds", "会话合格轮数", type_="int", example="126"),
    _f("session_va_seconds", "会话累计VA(秒,合格轮)", example="1520.3"),
    _f("session_bva_seconds", "会话累计BVA(秒,合格轮)", example="410.6"),
    _f("session_nva_seconds", "会话累计NVA(秒,合格轮,含等待)", example="493.2"),
    _f("session_wait_seconds", "会话累计等待(秒,合格轮)", example="352.9"),
    _f("session_va_ratio", "会话VA占比(%)", example="62.7"),
    _f("session_bva_ratio", "会话BVA占比(%)", example="16.9"),
    _f("session_nva_ratio", "会话NVA占比(%)", example="20.4"),
    _f("session_lean_total_seconds", "会话LEAN总计(秒,合格轮)", example="2424.1"),
    # ---- 按周期映射 (范围导出逐行取用, 键=cycle_id) ----
    _f("cycles", "各周期LEAN映射{cycle_id:{va,bva,nva,wait,nva_total}}", type_="json",
       example="{10056: {va: 12.5, ...}}",
       notes="范围导出模板里 {% set pl = plugin.lg_worktime.cycles.get(c.id) %} 逐行取"),
]


def export_provider(db, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """导出上下文构造时被主程序调用 (异常已由中央仓库隔离)。

    db 是主程序已开的 SQLAlchemy session (与插件表同 engine), 只读复用, 不 close。
    """
    from sqlalchemy import func as sa_func
    from .lean import lean_ratios
    from .models import PluginLgWorktimeCycleLean as Lean

    out: Dict[str, Any] = {}

    cycle_id = (ctx.get("cycle") or {}).get("id")
    if cycle_id:
        row = db.query(Lean).filter(Lean.cycle_id == cycle_id).order_by(Lean.id.desc()).first()
        if row is not None:
            nva_total = (row.nva_seconds or 0.0) + (row.wait_seconds or 0.0)
            ratios = lean_ratios(row.va_seconds or 0.0, row.bva_seconds or 0.0, nva_total)
            out.update({
                "cycle_va_seconds": row.va_seconds,
                "cycle_bva_seconds": row.bva_seconds,
                "cycle_nva_seconds": row.nva_seconds,
                "cycle_wait_seconds": row.wait_seconds,
                "cycle_nva_total_seconds": round(nva_total, 3),
                "cycle_va_ratio": ratios["va_ratio"],
                "cycle_bva_ratio": ratios["bva_ratio"],
                "cycle_nva_ratio": ratios["nva_ratio"],
            })

    # 取数范围: 单 session 导出用 session.id; 日期范围导出兜 aggregations.sessions
    session_id = (ctx.get("session") or {}).get("id")
    if session_id:
        session_ids = [session_id]
    else:
        session_ids = [s.get("id") for s in (ctx.get("aggregations") or {}).get("sessions") or []
                       if s.get("id")]
    if session_ids:
        # 逐周期映射: 范围导出 (include_cycles) 时模板按 c.id 逐行取 lean 列
        rows = db.query(Lean).filter(Lean.session_id.in_(session_ids)).all()
        out["cycles"] = {
            r.cycle_id: {
                "va": r.va_seconds, "bva": r.bva_seconds, "nva": r.nva_seconds,
                "wait": r.wait_seconds,
                "nva_total": round((r.nva_seconds or 0.0) + (r.wait_seconds or 0.0), 3),
            }
            for r in rows
        }
        rounds, va, bva, nva, wait = (
            db.query(
                sa_func.count(Lean.id),
                sa_func.sum(Lean.va_seconds),
                sa_func.sum(Lean.bva_seconds),
                sa_func.sum(Lean.nva_seconds),
                sa_func.sum(Lean.wait_seconds),
            )
            .filter(Lean.session_id.in_(session_ids), Lean.is_good == True)  # noqa: E712
            .first()
        ) or (0, None, None, None, None)
        rounds = int(rounds or 0)
        va = float(va or 0.0)
        bva = float(bva or 0.0)
        nva_total = float(nva or 0.0) + float(wait or 0.0)
        ratios = lean_ratios(va, bva, nva_total)
        out.update({
            "session_good_rounds": rounds,
            "session_va_seconds": round(va, 1),
            "session_bva_seconds": round(bva, 1),
            "session_nva_seconds": round(nva_total, 1),
            "session_wait_seconds": round(float(wait or 0.0), 1),
            "session_va_ratio": ratios["va_ratio"],
            "session_bva_ratio": ratios["bva_ratio"],
            "session_nva_ratio": ratios["nva_ratio"],
            "session_lean_total_seconds": round(va + bva + nva_total, 1),
        })

    return out
