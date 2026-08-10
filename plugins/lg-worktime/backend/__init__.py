"""LG 工时看板 — 后端插件入口 (v1.0.0)。

产品定位 (2026-08 需求):
  LG 原「视觉AI工时测量系统」是领导当看板用的独立桌面软件 (数据大于检测画面)。
  本插件把它收编进主程序: 检测/计时/落库全部复用主程序状态机, 插件只做三件事:

    1) LEAN 价值分析 — 步骤级 VA/BVA/NVA 动作价值 (存 Project steps_config[i]
       .plugin_data["lg-worktime"].value_type), cycle_end 时按当时配置把步骤耗时
       归类 + 步骤间等待归 NVA, 冻结写入自有表 p_lg_worktime_cycle_lean
    2) 领导看板数据面 — dashboard 路由 (live 实时 / summary 汇总 / trend 趋势 /
       step-averages 步骤统计), 前端整页覆盖 monitor.layout.body
    3) 导出字段 — Lean 字段注册进自定义导出中央仓库 (F8), 客户模板可引用
       {{ plugin.lg_worktime.* }} 复刻 LG 日明细 / LEAN 汇总

错误隔离底线: 所有 hook handler 内部异常 swallow, 绝不抛回主程序推理热路径。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

log = logging.getLogger("tianjun.plugin")

CUSTOMER_CODE = "lg-worktime"

# 步骤价值配置缓存 TTL (秒) — cycle_end 频率低, 3s 足够新鲜又不打爆 DB
_STEP_VALUES_TTL_SEC = 3.0

# ============================================================
# 模块级运行时 (register_plugin 注入)
# ============================================================
_HOST = None
_LOCK = threading.RLock()

# channel_id -> 本周期实时状态 (看板 live 轮询消费)
#   {cycle_id, cycle_number, project_id, session_id, started_ts,
#    steps_done: [{label, name, duration, interval_from_prev}],
#    current_step: {label, name, elapsed} | None,
#    last_cycle: {cycle_id, is_good, duration, lean{...}} | None}
_LIVE: Dict[int, Dict[str, Any]] = {}

# project_id -> {"values": {label: vt}, "ts": float} 步骤价值配置缓存
_STEP_VALUES_CACHE: Dict[int, Dict[str, Any]] = {}


# ============================================================
# 步骤价值配置读取 (Project.steps_config[i].plugin_data["lg-worktime"])
# ============================================================
def _step_values_for_project(project_id: Optional[int], force: bool = False) -> Dict[str, str]:
    from .lean import step_values_from_steps_config

    if not project_id:
        return {}
    now = time.time()
    cached = _STEP_VALUES_CACHE.get(project_id)
    if (not force) and cached and (now - cached["ts"]) < _STEP_VALUES_TTL_SEC:
        return cached["values"]

    values: Dict[str, str] = {}
    db = None
    try:
        from backend.models.models import Project
        db = _HOST.get_db_session() if _HOST else None
        if db is not None:
            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj is not None:
                values = step_values_from_steps_config(proj.steps_config, CUSTOMER_CODE)
    except Exception as e:
        log.warning("[Plugin][%s] 读步骤价值配置失败 (走空映射=全 VA): %s", CUSTOMER_CODE, e)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    _STEP_VALUES_CACHE[project_id] = {"values": values, "ts": now}
    return values


# ============================================================
# Hook handlers
# ============================================================
def on_cycle_start(ctx: Dict[str, Any]) -> None:
    try:
        ch = ctx.get("channel_id")
        if ch is None:
            return
        with _LOCK:
            prev = _LIVE.get(ch) or {}
            _LIVE[ch] = {
                "cycle_id": ctx.get("cycle_id"),
                "cycle_number": ctx.get("cycle_number"),
                "project_id": ctx.get("project_id"),
                "session_id": ctx.get("session_id"),
                "started_ts": time.time(),
                "steps_done": [],
                "current_step": None,
                "last_cycle": prev.get("last_cycle"),  # 保留上一轮结果供看板显示
            }
        _step_values_for_project(ctx.get("project_id"), force=True)
    except Exception as e:
        log.warning("[Plugin][%s] on_cycle_start 异常 (隔离): %s", CUSTOMER_CODE, e)


def on_step_tick(ctx: Dict[str, Any]) -> None:
    try:
        ch = ctx.get("channel_id")
        label = ctx.get("step_label")
        if ch is None or not label:
            return
        with _LOCK:
            st = _LIVE.get(ch)
            if st is None or st.get("cycle_id") != ctx.get("cycle_id"):
                return
            st["current_step"] = {
                "label": label,
                "name": ctx.get("step_name") or label,
                "elapsed": float(ctx.get("elapsed_sec") or 0.0),
            }
    except Exception as e:
        log.warning("[Plugin][%s] on_step_tick 异常 (隔离): %s", CUSTOMER_CODE, e)


def on_step_change(ctx: Dict[str, Any]) -> None:
    try:
        ch = ctx.get("channel_id")
        label = ctx.get("step_label")
        if ch is None or not label:
            return
        with _LOCK:
            st = _LIVE.get(ch)
            if st is None or st.get("cycle_id") != ctx.get("cycle_id"):
                return
            st["steps_done"].append({
                "label": label,
                "name": ctx.get("step_name") or label,
                "duration": ctx.get("duration"),
                "interval_from_prev": ctx.get("interval_from_prev"),
            })
            cur = st.get("current_step")
            if cur and cur.get("label") == label:
                st["current_step"] = None
    except Exception as e:
        log.warning("[Plugin][%s] on_step_change 异常 (隔离): %s", CUSTOMER_CODE, e)


def on_cycle_end(ctx: Dict[str, Any]) -> None:
    """周期写库后: 按当时价值配置做 LEAN 分解, 冻结写入 p_lg_worktime_cycle_lean。"""
    try:
        cycle_id = ctx.get("cycle_id")
        if cycle_id is None:
            return
        ch = ctx.get("channel_id")
        project_id = ctx.get("project_id")
        step_values = _step_values_for_project(project_id)
        settings = _plugin_settings()

        db = None
        lean = None
        cycle_number = None
        try:
            from backend.models.models import StepRecord, DetectionCycle
            from .lean import compute_lean
            from .models import PluginLgWorktimeCycleLean

            db = _HOST.get_db_session() if _HOST else None
            if db is None:
                return
            rows = (
                db.query(StepRecord)
                .filter(StepRecord.cycle_id == cycle_id)
                .order_by(StepRecord.step_order, StepRecord.id)
                .all()
            )
            steps = [{
                "label": r.step_label,
                "duration": r.duration,
                "interval_from_prev": r.interval_from_prev,
            } for r in rows]
            # 冻结期参数: 未配置步骤按全局默认价值归类 (改设置不回写历史)
            lean = compute_lean(steps, step_values,
                                default_value_type=settings["default_value_type"])

            end_time = None
            cyc = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
            if cyc is not None:
                end_time = cyc.end_time
                cycle_number = cyc.cycle_number

            # cycle_id 唯一: hook 重入时更新而非插重复行 (防 LEAN 累计虚高)
            row = (
                db.query(PluginLgWorktimeCycleLean)
                .filter(PluginLgWorktimeCycleLean.cycle_id == cycle_id)
                .first()
            )
            fields = dict(
                session_id=ctx.get("session_id"),
                channel_id=ch,
                project_id=project_id,
                cycle_number=cycle_number,
                is_good=ctx.get("is_good"),
                cycle_duration=ctx.get("duration"),
                va_seconds=lean["va"],
                bva_seconds=lean["bva"],
                nva_seconds=lean["nva"],
                wait_seconds=lean["wait"],
                end_time=end_time,
            )
            if row is None:
                db.add(PluginLgWorktimeCycleLean(cycle_id=cycle_id, **fields))
            else:
                for k, v in fields.items():
                    setattr(row, k, v)
            db.commit()
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

        # live 状态收尾: 记录上一轮结果, 清空进行中数据
        # (last_cycle.lean 是展示用途 → 按当前等待归类口径折算)
        if ch is not None:
            from .lean import apply_wait
            with _LOCK:
                st = _LIVE.get(ch)
                if st is not None and st.get("cycle_id") == cycle_id:
                    st["last_cycle"] = {
                        "cycle_id": cycle_id,
                        "cycle_number": cycle_number if cycle_number is not None
                            else st.get("cycle_number"),
                        "is_good": ctx.get("is_good"),
                        "duration": ctx.get("duration"),
                        "lean": apply_wait(lean, settings["wait_value_type"]),
                    }
                    st["cycle_id"] = None
                    st["steps_done"] = []
                    st["current_step"] = None
        log.info("[Plugin][%s] cycle=%s LEAN 已落库: %s", CUSTOMER_CODE, cycle_id, lean)
    except Exception as e:
        log.warning("[Plugin][%s] on_cycle_end 异常 (隔离): %s", CUSTOMER_CODE, e)


def _plugin_settings(force: bool = False) -> Dict[str, Any]:
    """插件全局设置 (SystemConfig KV, 带 TTL 缓存; 出错降级出厂默认)。"""
    from .settings import get_settings
    return get_settings(_HOST, force=force)


def _live_snapshot() -> Dict[str, Any]:
    """看板 live 轮询用的实时快照 (含进行中周期的实时 LEAN 分解)。"""
    from .lean import apply_wait, compute_lean, normalize_value_type

    settings = _plugin_settings()
    dvt = settings["default_value_type"]
    out: Dict[str, Any] = {"channels": {}, "settings": settings}
    with _LOCK:
        live_copy = {ch: dict(st) for ch, st in _LIVE.items()}
    for ch, st in live_copy.items():
        step_values = _step_values_for_project(st.get("project_id"))
        steps = list(st.get("steps_done") or [])
        cur = st.get("current_step")
        if cur:
            steps = steps + [{
                "label": cur.get("label"),
                "duration": cur.get("elapsed"),
                "interval_from_prev": None,
            }]
        lean_live = apply_wait(
            compute_lean(steps, step_values, default_value_type=dvt),
            settings["wait_value_type"],
        )
        in_cycle = st.get("cycle_id") is not None
        out["channels"][str(ch)] = {
            "in_cycle": in_cycle,
            "cycle_id": st.get("cycle_id"),
            "cycle_number": st.get("cycle_number"),
            "cycle_elapsed": (round(time.time() - st["started_ts"], 1)
                              if in_cycle and st.get("started_ts") else 0),
            "current_step": (dict(cur, value_type=normalize_value_type(
                step_values.get(str(cur.get("label"))), default=dvt)) if cur else None),
            "steps_done": [
                dict(s, value_type=normalize_value_type(
                    step_values.get(str(s.get("label"))), default=dvt))
                for s in (st.get("steps_done") or [])
            ],
            "lean_live": lean_live,
            "last_cycle": st.get("last_cycle"),
        }
    return out


# ============================================================
# 导出模板植入 (LG 日明细 / LEAN_统计汇总 复刻, xlsx 路线 A)
# ============================================================
_EXPORT_TEMPLATES = [
    # (文件名, 模板名, 描述)
    ("lg_daily_detail.csv.j2", "LG 日明细 (lg-worktime)",
     "LG 工时系统「日明细 Excel」复刻: 逐轮次 CT + VA/BVA/NVA/等待 + 步骤明细。"
     "需勾选「包含周期明细」(include_cycles) 导出。"),
    ("lg_lean_summary.csv.j2", "LG LEAN统计汇总 (lg-worktime)",
     "LG 工时系统「LEAN_统计汇总 Excel」复刻: 仅累计合格轮, 等待归 NVA。"
     "按日期范围或单 session 导出。"),
]


def _seed_export_templates() -> None:
    """激活时按名字幂等植入两份 xlsx 模板 (已存在则不动, 不覆盖用户修改)。"""
    import os

    tpl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "templates")
    db = None
    try:
        from backend.models.export_models import ExportTemplate

        db = _HOST.get_db_session() if _HOST else None
        if db is None:
            return
        for fname, name, desc in _EXPORT_TEMPLATES:
            path = os.path.join(tpl_dir, fname)
            if not os.path.isfile(path):
                log.warning("[Plugin][%s] 模板文件缺失, 跳过: %s", CUSTOMER_CODE, path)
                continue
            if db.query(ExportTemplate).filter(ExportTemplate.name == name).first():
                continue
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            db.add(ExportTemplate(name=name, description=desc, format="xlsx",
                                  content=content, scope="batch"))
            log.info("[Plugin][%s] 已植入导出模板: %s", CUSTOMER_CODE, name)
        db.commit()
    except Exception as e:
        log.warning("[Plugin][%s] 导出模板植入失败 (隔离, 可在导出页手动建): %s",
                    CUSTOMER_CODE, e)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


# ============================================================
# 入口
# ============================================================
def register_plugin(app, registry, license_payload, host):
    global _HOST
    _HOST = host

    from .models import PluginLgWorktimeCycleLean
    from .routes import build_router
    from .export_fields import EXPORT_FIELDS, export_provider

    registry.tables.register(PluginLgWorktimeCycleLean)

    # phase/when 必须与主程序 fire 三元组一致 (对齐 fujian-jinlong)
    registry.hooks.register("cycle_start", "post_cycle_start", "post", 50, on_cycle_start)
    registry.hooks.register("step_tick", "step_in_progress", "post", 50, on_step_tick)
    registry.hooks.register("step_change", "post_step", "post", 50, on_step_change)
    registry.hooks.register("cycle_end", "post_cycle", "post", 50, on_cycle_end)

    registry.routes.include_router(
        build_router(lambda: _HOST, _live_snapshot, _step_values_for_project,
                     _plugin_settings),
        subpath="dashboard", tags=["Plugin:lg-worktime"],
    )

    # F8: Lean 字段进自定义导出中央仓库
    registry.export_fields.register(fields=EXPORT_FIELDS, provider=export_provider)

    # LG 日明细 / LEAN 汇总两份 xlsx 模板 (幂等植入)
    _seed_export_templates()

    log.info(
        "[Plugin][%s] 工时看板后端已注册 (cycle_start/step_tick/step_change/cycle_end"
        " + dashboard 路由 + %d 个导出字段)",
        CUSTOMER_CODE, len(EXPORT_FIELDS),
    )
    return {"name": "lg-worktime-dashboard", "customer_code": CUSTOMER_CODE}
