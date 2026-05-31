"""福建金龙 双工位检测主页 — 后端插件 (v1.1.8 新增「步骤耗时三档」).

客户需求 (郑经理 2026-05-31 确认):
  对每个检测步骤设三个时间阈值, 全部由插件自管 (项目原生「最短/最长时间」对这些
  步骤留空, 否则主程序的「超范围即丢弃」过滤器会抢先把步骤删掉, 和本插件「超范围即
  判废」相冲突):

    1) 最短时间 (min_sec):  步骤走完时若实际耗时 < 最短 → 当场报警 + 本周期判 NG
    2) 警告时间 (warn_sec): 步骤进行中超过警告时间 (介于最短与最长之间) → 只报警,
                            不判 NG (提醒操作员"快了")
    3) 最长时间 (max_sec):  步骤进行中超过最长时间 → 当场报警 + 本周期判 NG

  "实时" = 进行中当场响, 不等步骤/周期结束. 靠主程序 RFC 12 的 step_tick 计时广播
  (~1Hz) 拿到"这一步已持续几秒", 越线就调主程序报警. 最终结算只产出 OK / NG.

技术实现:
  - step_tick     → 进行中实时判 警告/最长 (dedup, 每档每步每周期只响一次)
  - step_change   → 步骤走完时判 最短 (太快 → NG)
  - pre_cycle_end → 本周期有任一 NG 标记 → 返回 override_result="NG" 强制判废
  - cycle_start   → 复位本通道周期状态 + 刷新阈值配置

  主程序不内嵌任何阈值策略 (step_tick 是只读 observe hook), 策略全在本文件;
  报警走 PluginHost.trigger_alarm (需 runtime.alarm_trigger capability).

阈值配置:
  存 SystemConfig 表 key=plugin_internal_demo_step_durations (JSON), 经插件路由
  GET/PUT /api/v1/plugins/internal-demo/step-durations 读写 (前端设置页可接).
  配置缺失时用内置默认值, 不阻塞主流程.

错误隔离底线: 所有 hook handler 内部异常 swallow, 绝不抛回主程序推理热路径.
"""
from __future__ import annotations

import json
import threading
import time
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger("tianjun.plugin")

CUSTOMER_CODE = "internal-demo"
CONFIG_KEY = "plugin_internal_demo_step_durations"
CONFIG_TTL_SEC = 3.0

# ============================================================
# 内置默认阈值 (配置缺失时兜底)
# ============================================================
_DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    # 缺省档: 没在 steps 里单独配的步骤都用这个
    "default": {"min_sec": 0, "warn_sec": 0, "max_sec": 0},
    # 每个步骤 label 单独配 {min_sec, warn_sec, max_sec}; 任一项为 0/缺失 = 该档不启用
    "steps": {},
    # 报警事件类型 (必须是主程序 alarm.config 已配置的字符串)
    "alarm_event": {"warn": "event2", "ng": "event2"},
}

# ============================================================
# 模块级运行时 (插件加载时由 register_plugin 注入 host)
# ============================================================
_HOST = None                      # PluginHost
_LOCK = threading.Lock()

# (channel_id, cycle_id) -> {"ng": bool, "alarmed": {label: set("warn"/"max")}}
# 按"通道+周期"键 (而非仅通道): 避免新周期的 cycle_start 抢先清掉上一周期还没结算
# 消费的 NG 标记 (主程序结算时序是 end_cycle→pre_cycle_end 然后才 start 新周期, 但
# 按周期键更稳, 不依赖该时序). pre_cycle_end 消费后 pop, 另加上限防泄漏.
_STATE: Dict[tuple, Dict[str, Any]] = {}
_STATE_MAX = 256

# 配置缓存 (TTL)
_cfg_cache: Dict[str, Any] = {"value": None, "ts": 0.0}


# ============================================================
# 配置加载 (TTL 缓存, 缺失走默认)
# ============================================================
def _normalize_config(raw: Any) -> Dict[str, Any]:
    """把任意输入规整成完整 config dict (容错: 类型不对就退默认)."""
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))  # deep copy
    if not isinstance(raw, dict):
        return cfg
    cfg["enabled"] = bool(raw.get("enabled", True))
    if isinstance(raw.get("default"), dict):
        cfg["default"].update({k: raw["default"].get(k) for k in ("min_sec", "warn_sec", "max_sec")
                               if raw["default"].get(k) is not None})
    if isinstance(raw.get("steps"), dict):
        clean_steps = {}
        for label, v in raw["steps"].items():
            if isinstance(v, dict):
                clean_steps[str(label)] = {
                    "min_sec": v.get("min_sec") or 0,
                    "warn_sec": v.get("warn_sec") or 0,
                    "max_sec": v.get("max_sec") or 0,
                }
        cfg["steps"] = clean_steps
    if isinstance(raw.get("alarm_event"), dict):
        cfg["alarm_event"].update({
            "warn": raw["alarm_event"].get("warn") or cfg["alarm_event"]["warn"],
            "ng": raw["alarm_event"].get("ng") or cfg["alarm_event"]["ng"],
        })
    return cfg


def _load_config(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    if (not force) and _cfg_cache["value"] is not None and (now - _cfg_cache["ts"]) < CONFIG_TTL_SEC:
        return _cfg_cache["value"]
    raw = None
    if _HOST is not None:
        try:
            txt = _HOST.read_system_config(CONFIG_KEY)
            if txt:
                raw = json.loads(txt)
        except Exception as e:
            log.warning("[Plugin][%s] 读阈值配置失败, 走默认: %s", CUSTOMER_CODE, e)
    cfg = _normalize_config(raw)
    _cfg_cache["value"] = cfg
    _cfg_cache["ts"] = now
    return cfg


def _thresholds_for(cfg: Dict[str, Any], label: str) -> Dict[str, float]:
    """取某步骤的三档阈值 (步骤级覆盖缺省级)."""
    t = dict(cfg.get("default") or {})
    step_t = (cfg.get("steps") or {}).get(label)
    if isinstance(step_t, dict):
        for k in ("min_sec", "warn_sec", "max_sec"):
            if step_t.get(k):
                t[k] = step_t[k]
    return {
        "min_sec": float(t.get("min_sec") or 0),
        "warn_sec": float(t.get("warn_sec") or 0),
        "max_sec": float(t.get("max_sec") or 0),
    }


# ============================================================
# 状态机 helper
# ============================================================
def _state_for(channel_id: int, cycle_id: Optional[int]) -> Dict[str, Any]:
    """取 (按需新建) 某 (通道,周期) 的状态. 不clobber其它周期."""
    key = (channel_id, cycle_id)
    st = _STATE.get(key)
    if st is None:
        if len(_STATE) >= _STATE_MAX:
            # 防泄漏: 超上限丢最早一条 (正常流程 pre_cycle_end 会 pop, 走不到这)
            try:
                _STATE.pop(next(iter(_STATE)))
            except StopIteration:
                pass
        st = {"ng": False, "alarmed": {}}
        _STATE[key] = st
    return st


def _fire_alarm(channel_id: int, kind: str, reason: str, cfg: Dict[str, Any]) -> None:
    if _HOST is None:
        return
    event_type = (cfg.get("alarm_event") or {}).get(kind) or "event2"
    try:
        _HOST.trigger_alarm(channel_id=channel_id, event_type=event_type, reason=reason)
    except Exception as e:
        log.warning("[Plugin][%s] trigger_alarm 失败 (隔离): %s", CUSTOMER_CODE, e)


# ============================================================
# Hook handlers (纯逻辑, 可单测)
# ============================================================
def on_cycle_start(ctx: Dict[str, Any]) -> None:
    try:
        ch = ctx.get("channel_id")
        if ch is None:
            return
        cid = ctx.get("cycle_id")
        with _LOCK:
            # 一个通道同时只有一个活动周期: 新周期开始 → 清掉该通道所有旧周期状态.
            # 防泄漏 + 防去重串场: pre_cycle_end 正常会 pop, 但若上一周期被丢弃(没走
            # end_cycle, 如测试合成源)或 cycle_id 复用, 旧的 alarmed/ng 标记会污染本周期.
            # 这里用通道维度兜底清扫, 再建本周期的全新干净状态.
            for k in [k for k in _STATE if k[0] == ch]:
                _STATE.pop(k, None)
            _STATE[(ch, cid)] = {"ng": False, "alarmed": {}}
        _load_config(force=True)  # 周期开始刷新阈值, 让设置页改完下一周期即生效
    except Exception as e:
        log.warning("[Plugin][%s] on_cycle_start 异常 (隔离): %s", CUSTOMER_CODE, e)


def on_step_tick(ctx: Dict[str, Any]) -> None:
    """进行中实时判: 越过警告线 → 只报警; 越过最长线 → 报警 + 标记 NG. 每档只响一次."""
    try:
        cfg = _load_config()
        if not cfg.get("enabled"):
            return
        ch = ctx.get("channel_id")
        label = ctx.get("step_label")
        elapsed = float(ctx.get("elapsed_sec") or 0)
        if ch is None or not label:
            return
        th = _thresholds_for(cfg, label)
        warn_sec, max_sec = th["warn_sec"], th["max_sec"]
        name = ctx.get("step_name") or label

        with _LOCK:
            st = _state_for(ch, ctx.get("cycle_id"))
            done = st["alarmed"].setdefault(label, set())

            # 最长线优先 (一旦超最长, 警告就不再单独提)
            if max_sec > 0 and elapsed >= max_sec and "max" not in done:
                done.add("max")
                done.add("warn")  # 抑制后续警告重复
                st["ng"] = True
                _fire_alarm(ch, "ng", f"步骤[{name}]超过最长时间 ({elapsed:.1f}s≥{max_sec:.1f}s)", cfg)
                log.info("[Plugin][%s] ch%s 步骤[%s] 超最长 %.1fs≥%.1fs → 报警+NG",
                         CUSTOMER_CODE, ch, name, elapsed, max_sec)
                return
            if warn_sec > 0 and elapsed >= warn_sec and "warn" not in done:
                done.add("warn")
                _fire_alarm(ch, "warn", f"步骤[{name}]超过警告时间 ({elapsed:.1f}s≥{warn_sec:.1f}s)", cfg)
                log.info("[Plugin][%s] ch%s 步骤[%s] 超警告 %.1fs≥%.1fs → 仅报警",
                         CUSTOMER_CODE, ch, name, elapsed, warn_sec)
    except Exception as e:
        log.warning("[Plugin][%s] on_step_tick 异常 (隔离): %s", CUSTOMER_CODE, e)


def on_step_change(ctx: Dict[str, Any]) -> None:
    """步骤走完: 实际耗时 < 最短 → 报警 + 标记 NG."""
    try:
        cfg = _load_config()
        if not cfg.get("enabled"):
            return
        ch = ctx.get("channel_id")
        label = ctx.get("step_label")
        duration = ctx.get("duration")
        if ch is None or not label or duration is None:
            return
        th = _thresholds_for(cfg, label)
        min_sec = th["min_sec"]
        name = ctx.get("step_name") or label
        if min_sec > 0 and float(duration) < min_sec:
            with _LOCK:
                st = _state_for(ch, ctx.get("cycle_id"))
                st["ng"] = True
            _fire_alarm(ch, "ng", f"步骤[{name}]未达最短时间 ({float(duration):.1f}s<{min_sec:.1f}s)", cfg)
            log.info("[Plugin][%s] ch%s 步骤[%s] 未达最短 %.1fs<%.1fs → 报警+NG",
                     CUSTOMER_CODE, ch, name, float(duration), min_sec)
    except Exception as e:
        log.warning("[Plugin][%s] on_step_change 异常 (隔离): %s", CUSTOMER_CODE, e)


def on_pre_cycle_end(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """周期结算前: 本周期有任一耗时 NG 标记 → 强制 override_result=NG."""
    try:
        cfg = _load_config()
        ch = ctx.get("channel_id")
        if ch is None:
            return {}
        with _LOCK:
            st = _STATE.pop((ch, ctx.get("cycle_id")), None)  # 消费即清理本周期状态
            ng = bool(st and st.get("ng"))
        if cfg.get("enabled") and ng:
            log.info("[Plugin][%s] ch%s cycle=%s 耗时三档命中 → 强制 NG",
                     CUSTOMER_CODE, ch, ctx.get("cycle_id"))
            return {"override_result": "NG"}
        return {}
    except Exception as e:
        log.warning("[Plugin][%s] on_pre_cycle_end 异常 (隔离): %s", CUSTOMER_CODE, e)
        return {}


# ============================================================
# 配置路由 (前端设置页接)
# ============================================================
class _StepThreshold(BaseModel):
    min_sec: float = 0
    warn_sec: float = 0
    max_sec: float = 0


class _DurationConfig(BaseModel):
    enabled: bool = True
    default: _StepThreshold = _StepThreshold()
    steps: Dict[str, _StepThreshold] = {}
    alarm_event: Dict[str, str] = {"warn": "event2", "ng": "event2"}


def _build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/step-durations")
    def get_durations():
        return _load_config(force=True)

    @router.put("/step-durations")
    def put_durations(cfg: _DurationConfig):
        payload = json.loads(cfg.model_dump_json())
        norm = _normalize_config(payload)
        ok = False
        if _HOST is not None:
            ok = _HOST.write_system_config(
                CONFIG_KEY, json.dumps(norm, ensure_ascii=False),
                description="福建金龙 步骤耗时三档阈值",
            )
        _load_config(force=True)
        return {"saved": ok, "config": norm}

    @router.get("/live-stats")
    def live_stats():
        """权威实时合格/不良 (按每通道最近一次会话查数据库 is_good).

        为什么要这个: 主程序检测页那张实时「合格/不良」计数卡走的是事件计数动作,
        结算时序是"先记合格 → 再由本插件 pre_cycle_end 把判定改写成 NG", 计数动作
        没回头看改写结果, 于是被本插件判废的周期在那张卡上仍被记成合格 (主程序刻意
        把改写只作用于库/MES, 计数语义改写推迟到平台后续里程碑). 本插件自带的检测页
        覆盖层照搬了主程序的 chData.ok/ng, 同样偏. 这里直接从数据库 (改写已落库的
        权威 is_good) 重算, 供插件前端覆盖那张卡, 不动主程序一行.

        取哪条会话: 每个通道取 start_time 最新的那条 (不限运行中) — 与主程序计数卡
        语义对齐 (停止检测后那张卡仍显示本次会话数字, 直到清零/新开, 不会清空).
        口径: 只统计 end_time 非空 (已结算) 的周期, 与会话汇总 / 报表一致.
        返回: {"channels": {"0": {total, ok, ng, yield_rate}, ...}}
        """
        out: Dict[str, Any] = {"channels": {}}
        if _HOST is None:
            return out
        db = None
        try:
            from backend.models.models import DetectionSession, DetectionCycle
            db = _HOST.get_db_session()
            sessions = db.query(DetectionSession).all()
            # 每通道取 start_time 最新的会话 (运行中的天然最新; 停止后也仍指向本次会话)
            latest_by_ch: Dict[int, Any] = {}
            for s in sessions:
                ch = int(s.channel_id or 0)
                cur = latest_by_ch.get(ch)
                if cur is None or (s.start_time and cur.start_time and s.start_time > cur.start_time):
                    latest_by_ch[ch] = s
            for ch, s in latest_by_ch.items():
                rows = (
                    db.query(DetectionCycle.is_good)
                    .filter(
                        DetectionCycle.session_id == s.id,
                        DetectionCycle.end_time.isnot(None),
                    )
                    .all()
                )
                total = len(rows)
                ng = sum(1 for (g,) in rows if not g)
                ok = total - ng
                out["channels"][str(ch)] = {
                    "total": total,
                    "ok": ok,
                    "ng": ng,
                    "yield_rate": round(ok / total * 100) if total else 0,
                }
        except Exception as e:  # 只读统计, 出错降级成空 (前端回退主程序计数), 绝不抛
            log.warning("[Plugin][%s] live-stats 异常 (降级): %s", CUSTOMER_CODE, e)
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
        return out

    return router


# ============================================================
# 入口
# ============================================================
def register_plugin(app, registry, license_payload, host):
    global _HOST
    _HOST = host

    registry.hooks.register("cycle_start", "post_cycle_start", "post", 50, on_cycle_start)
    registry.hooks.register("step_tick", "step_in_progress", "post", 50, on_step_tick)
    registry.hooks.register("step_change", "post_step", "post", 50, on_step_change)
    registry.hooks.register("pre_cycle_end", "pre_cycle", "pre", 50, on_pre_cycle_end)

    registry.routes.include_router(_build_router(), subpath="durations", tags=["Plugin:internal-demo"])

    log.info("[Plugin][%s] 步骤耗时三档后端已注册 (cycle_start/step_tick/step_change/pre_cycle_end + 配置路由)",
             CUSTOMER_CODE)
