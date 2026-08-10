"""LG 工时看板 — dashboard 查询路由。

挂载点: /api/v1/plugins/lg-worktime/dashboard/*

数据口径:
  - 产量/良率/CT: 查主程序 detection_cycles (权威, 只统计 end_time 非空的已结算周期)
  - LEAN 分解: 查插件自有表 p_lg_worktime_cycle_lean (cycle_end 冻结落库)
  - LEAN 平均: 只按合格轮平均 (LG 原系统口径)
  - 实时: /live 走内存快照 (hook 维护), 不打 DB

所有端点只读; 出错降级返回空结构, 绝不抛 500 拖垮看板轮询。
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter

log = logging.getLogger("tianjun.plugin")

CUSTOMER_CODE = "lg-worktime"


def _date_bounds(date_str: Optional[str]):
    """'YYYY-MM-DD' -> (day_start, day_end); 非法/缺省用今天。"""
    try:
        day = _dt.date.fromisoformat(date_str) if date_str else _dt.date.today()
    except ValueError:
        day = _dt.date.today()
    start = _dt.datetime.combine(day, _dt.time.min)
    return day, start, start + _dt.timedelta(days=1)


# ============================================================
# 设备状态卡真实数据 (v1.5.1: 温度/开机时长接真)
# ============================================================
# 温度探测链 (best-effort, 全平台零新增依赖):
#   1. pynvml GPU 温度 (部署工控机 NVIDIA, 若装了 python 包)
#   2. nvidia-smi 子进程 (NVIDIA 驱动自带, 不需 python 包 — 工控机主路径)
#   3. psutil.sensors_temperatures CPU (Linux)
#   4. macOS AppleSmartBattery SMC 电池温度 (开发机)
#   全部不可用 → None, 前端显示 "—" (不造假数)
_TEMP_CACHE = {"ts": 0.0, "temp": None, "source": None}
_TEMP_TTL_SEC = 10.0


def _probe_temperature():
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)), "gpu"
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip().splitlines()
        if out and out[0].strip().isdigit():
            return float(out[0].strip()), "gpu"
    except Exception:
        pass
    try:
        import psutil
        sensors = getattr(psutil, "sensors_temperatures", lambda: None)() or {}
        for key in ("coretemp", "k10temp", "cpu_thermal"):
            entries = sensors.get(key) or []
            if entries:
                return float(entries[0].current), "cpu"
        for entries in sensors.values():
            if entries:
                return float(entries[0].current), "cpu"
    except Exception:
        pass
    try:
        import re
        import subprocess
        out = subprocess.run(["ioreg", "-rn", "AppleSmartBattery"],
                             capture_output=True, text=True, timeout=3).stdout
        m = re.search(r'"Temperature"\s*=\s*(\d+)', out)
        if m:
            return round(int(m.group(1)) / 100.0, 1), "battery"
    except Exception:
        pass
    return None, None


def _read_temperature():
    """带 10s 缓存的温度读数 (nvidia-smi/ioreg 是子进程, 不能每次轮询都拉)。"""
    import time as _time
    now = _time.time()
    if (now - _TEMP_CACHE["ts"]) >= _TEMP_TTL_SEC:
        temp, source = _probe_temperature()
        _TEMP_CACHE.update(ts=now, temp=temp, source=source)
    return _TEMP_CACHE["temp"], _TEMP_CACHE["source"]


def build_router(
    get_host: Callable[[], Any],
    live_snapshot: Callable[[], Dict[str, Any]],
    step_values_for_project: Callable[..., Dict[str, str]],
    plugin_settings: Callable[..., Dict[str, Any]],
) -> APIRouter:
    router = APIRouter()

    def _db():
        host = get_host()
        return host.get_db_session() if host is not None else None

    # ---------- 设备状态卡真实数据 (v1.5.1) ----------
    @router.get("/device-info")
    def device_info():
        """设备卡真值: 整机开机时长 (psutil.boot_time) + 温度 (探测链, 无传感器给 null)。"""
        out: Dict[str, Any] = {"uptime_seconds": None, "boot_time": None,
                               "temperature_c": None, "temperature_source": None}
        try:
            import time as _time
            import psutil
            boot = psutil.boot_time()
            out["boot_time"] = _dt.datetime.fromtimestamp(boot).isoformat(timespec="seconds")
            out["uptime_seconds"] = int(_time.time() - boot)
        except Exception as e:
            log.warning("[Plugin][%s] 读开机时长失败: %s", CUSTOMER_CODE, e)
        try:
            temp, source = _read_temperature()
            out["temperature_c"] = temp
            out["temperature_source"] = source
        except Exception as e:
            log.warning("[Plugin][%s] 读温度失败: %s", CUSTOMER_CODE, e)
        return out

    # ---------- 插件全局设置 (v1.5.0 LG 口径全参数可配) ----------
    @router.get("/settings")
    def get_settings():
        """看板/统计口径参数 (SystemConfig KV): 默认价值/等待归类/统计范围/趋势天数。"""
        try:
            return {"status": "success", "settings": plugin_settings(force=True)}
        except Exception as e:
            log.warning("[Plugin][%s] GET /settings 异常 (降级默认): %s", CUSTOMER_CODE, e)
            from .settings import DEFAULTS
            return {"status": "success", "settings": dict(DEFAULTS)}

    @router.post("/settings")
    def post_settings(patch: Dict[str, Any]):
        """部分更新设置; 非法值静默规整回合法域, 立即全局生效 (冻结语义见各参数注释)。"""
        try:
            from .settings import save_settings
            merged = save_settings(get_host(), patch)
            log.info("[Plugin][%s] 设置已更新: %s", CUSTOMER_CODE, merged)
            return {"status": "success", "settings": merged}
        except Exception as e:
            log.warning("[Plugin][%s] POST /settings 失败: %s", CUSTOMER_CODE, e)
            return {"status": "error", "message": str(e)}

    # ---------- 实时 ----------
    @router.get("/live")
    def live():
        """内存实时快照: 各通道进行中周期 + 实时 LEAN 分解 + 上一轮结果。"""
        try:
            return live_snapshot()
        except Exception as e:
            log.warning("[Plugin][%s] /live 异常 (降级空): %s", CUSTOMER_CODE, e)
            return {"channels": {}}

    # ---------- 步骤价值配置 (只读; 写入走主程序 PUT /projects/{id}/plugin-data) ----------
    @router.get("/step-values")
    def step_values(project_id: Optional[int] = None):
        db = None
        try:
            from backend.models.models import Project

            pid = project_id
            db = _db()
            if db is None:
                return {"project_id": None, "values": {}}
            if pid is None:
                active = db.query(Project).filter(Project.is_active == True).first()  # noqa: E712
                pid = active.id if active else None
            values = step_values_for_project(pid, force=True) if pid else {}
            return {"project_id": pid, "values": values}
        except Exception as e:
            log.warning("[Plugin][%s] /step-values 异常 (降级空): %s", CUSTOMER_CODE, e)
            return {"project_id": None, "values": {}}
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    # ---------- 当日汇总 ----------
    @router.get("/summary")
    def summary(date: Optional[str] = None,
                channel_id: Optional[int] = None,
                project_id: Optional[int] = None):
        """某日汇总: 产量/良率/CT (主表) + LEAN 累计与占比 (插件表)。

        LEAN 统计范围/等待归类跟随插件设置 (出厂: 仅合格轮 + 等待归 NVA)。
        """
        day, start, end = _date_bounds(date)
        st_cfg = plugin_settings()
        scope = st_cfg["lean_scope"]
        wait_vt = st_cfg["wait_value_type"]
        out: Dict[str, Any] = {
            "date": day.isoformat(),
            "total_cycles": 0, "good_cycles": 0, "ng_cycles": 0, "yield_rate": None,
            "ct_total": 0.0, "ct_avg": None, "ct_min": None, "ct_max": None,
            "wait_total": 0.0,
            "lean": {"va": 0.0, "bva": 0.0, "nva": 0.0, "wait": 0.0, "nva_total": 0.0,
                     "good_rounds": 0, "scope": scope, "wait_value_type": wait_vt,
                     "va_ratio": None, "bva_ratio": None, "nva_ratio": None,
                     "avg_per_good": None},
        }
        db = None
        try:
            from sqlalchemy import case, func as sa_func
            from backend.models.models import DetectionCycle, DetectionSession
            from .lean import apply_wait, lean_ratios
            from .models import PluginLgWorktimeCycleLean as Lean

            db = _db()
            if db is None:
                return out

            # channel/project 在 DetectionSession 上 (cycle 无此二列), 过滤须 join
            # 良品数须 sum(case) — sum(布尔表达式) 的结果类型仍是 Boolean,
            # SQLAlchemy 会把 22 强转成 True → int 后变 1 (UAT 实测踩过)
            q = db.query(
                sa_func.count(DetectionCycle.id),
                sa_func.sum(case((DetectionCycle.is_good == True, 1), else_=0)),  # noqa: E712
                sa_func.sum(DetectionCycle.duration),
                sa_func.avg(DetectionCycle.duration),
                sa_func.min(DetectionCycle.duration),
                sa_func.max(DetectionCycle.duration),
            ).filter(
                DetectionCycle.end_time.isnot(None),
                DetectionCycle.end_time >= start,
                DetectionCycle.end_time < end,
            )
            if channel_id is not None or project_id is not None:
                q = q.join(DetectionSession, DetectionCycle.session_id == DetectionSession.id)
                if channel_id is not None:
                    q = q.filter(DetectionSession.channel_id == channel_id)
                if project_id is not None:
                    q = q.filter(DetectionSession.project_id == project_id)
            total, good, ct_sum, ct_avg, ct_min, ct_max = q.first() or (0, 0, None, None, None, None)
            total = int(total or 0)
            good = int(good or 0)
            out.update({
                "total_cycles": total,
                "good_cycles": good,
                "ng_cycles": total - good,
                "yield_rate": round(100.0 * good / total, 2) if total else None,
                "ct_total": round(float(ct_sum or 0.0), 1),
                "ct_avg": round(float(ct_avg), 2) if ct_avg is not None else None,
                "ct_min": round(float(ct_min), 2) if ct_min is not None else None,
                "ct_max": round(float(ct_max), 2) if ct_max is not None else None,
            })

            lq = db.query(
                sa_func.count(Lean.id),
                sa_func.sum(Lean.va_seconds),
                sa_func.sum(Lean.bva_seconds),
                sa_func.sum(Lean.nva_seconds),
                sa_func.sum(Lean.wait_seconds),
            ).filter(
                Lean.end_time.isnot(None),
                Lean.end_time >= start,
                Lean.end_time < end,
            )
            if scope == "good_only":  # LG 出厂口径: LEAN 累计只含合格轮 (设置可切全部轮)
                lq = lq.filter(Lean.is_good == True)  # noqa: E712
            if channel_id is not None:
                lq = lq.filter(Lean.channel_id == channel_id)
            if project_id is not None:
                lq = lq.filter(Lean.project_id == project_id)
            rounds, va, bva, nva, wait = lq.first() or (0, None, None, None, None)
            rounds = int(rounds or 0)
            # 等待归类按当前口径折算 (统计口径参数, 历史数据同样跟随)
            att = apply_wait({"va": float(va or 0.0), "bva": float(bva or 0.0),
                              "nva": float(nva or 0.0), "wait": float(wait or 0.0)}, wait_vt)
            lean_out = {
                "va": round(att["va"], 1), "bva": round(att["bva"], 1),
                "nva": round(att["nva"], 1), "wait": round(att["wait"], 1),
                "nva_total": round(att["nva_total"], 1),
                "good_rounds": rounds,  # 键名保留兼容; scope=all 时含 NG 轮
                "scope": scope, "wait_value_type": wait_vt,
                "avg_per_good": {
                    "va": round(att["va"] / rounds, 2), "bva": round(att["bva"] / rounds, 2),
                    "nva_total": round(att["nva_total"] / rounds, 2),
                } if rounds else None,
            }
            lean_out.update(lean_ratios(att["va"], att["bva"], att["nva_total"]))
            out["lean"] = lean_out

            # 全部周期(含 NG)的等待合计, 给「等待时间」KPI 用
            wq = db.query(sa_func.sum(Lean.wait_seconds)).filter(
                Lean.end_time.isnot(None),
                Lean.end_time >= start,
                Lean.end_time < end,
            )
            if channel_id is not None:
                wq = wq.filter(Lean.channel_id == channel_id)
            if project_id is not None:
                wq = wq.filter(Lean.project_id == project_id)
            out["wait_total"] = round(float((wq.scalar()) or 0.0), 1)
            return out
        except Exception as e:
            log.warning("[Plugin][%s] /summary 异常 (降级空): %s", CUSTOMER_CODE, e)
            return out
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    # ---------- 多日趋势 ----------
    @router.get("/trend")
    def trend(days: Optional[int] = None,
              channel_id: Optional[int] = None,
              project_id: Optional[int] = None):
        """近 N 天逐日: 产量/良率/CT均值 (主表) + LEAN 三类合计 (插件表)。

        days 缺省走插件设置 trend_days; LEAN 范围/等待归类同样跟随设置。
        """
        st_cfg = plugin_settings()
        scope = st_cfg["lean_scope"]
        wait_vt = st_cfg["wait_value_type"]
        days = max(1, min(int(days or st_cfg["trend_days"]), 60))
        today = _dt.date.today()
        day0 = today - _dt.timedelta(days=days - 1)
        start = _dt.datetime.combine(day0, _dt.time.min)
        out: Dict[str, Any] = {"days": [], "trend_days": days,
                               "scope": scope, "wait_value_type": wait_vt}
        db = None
        try:
            from sqlalchemy import case, func as sa_func
            from backend.models.models import DetectionCycle, DetectionSession
            from .lean import apply_wait
            from .models import PluginLgWorktimeCycleLean as Lean

            db = _db()
            if db is None:
                return out

            q = db.query(
                sa_func.date(DetectionCycle.end_time),
                sa_func.count(DetectionCycle.id),
                sa_func.sum(case((DetectionCycle.is_good == True, 1), else_=0)),  # noqa: E712
                sa_func.avg(DetectionCycle.duration),
            ).filter(
                DetectionCycle.end_time.isnot(None),
                DetectionCycle.end_time >= start,
            )
            if channel_id is not None or project_id is not None:
                q = q.join(DetectionSession, DetectionCycle.session_id == DetectionSession.id)
                if channel_id is not None:
                    q = q.filter(DetectionSession.channel_id == channel_id)
                if project_id is not None:
                    q = q.filter(DetectionSession.project_id == project_id)
            prod_by_day = {str(d): (int(t or 0), int(g or 0), a)
                           for d, t, g, a in q.group_by(sa_func.date(DetectionCycle.end_time)).all()}

            lq = db.query(
                sa_func.date(Lean.end_time),
                sa_func.sum(Lean.va_seconds),
                sa_func.sum(Lean.bva_seconds),
                sa_func.sum(Lean.nva_seconds),
                sa_func.sum(Lean.wait_seconds),
            ).filter(
                Lean.end_time.isnot(None),
                Lean.end_time >= start,
            )
            if scope == "good_only":
                lq = lq.filter(Lean.is_good == True)  # noqa: E712
            if channel_id is not None:
                lq = lq.filter(Lean.channel_id == channel_id)
            if project_id is not None:
                lq = lq.filter(Lean.project_id == project_id)
            lean_by_day = {str(d): (float(v or 0), float(b or 0), float(n or 0), float(w or 0))
                           for d, v, b, n, w in lq.group_by(sa_func.date(Lean.end_time)).all()}

            for i in range(days):
                day = day0 + _dt.timedelta(days=i)
                key = day.isoformat()
                total, good, ct_avg = prod_by_day.get(key, (0, 0, None))
                va, bva, nva, wait = lean_by_day.get(key, (0.0, 0.0, 0.0, 0.0))
                att = apply_wait({"va": va, "bva": bva, "nva": nva, "wait": wait}, wait_vt)
                out["days"].append({
                    "date": key,
                    "total": total, "good": good, "ng": total - good,
                    "yield_rate": round(100.0 * good / total, 2) if total else None,
                    "ct_avg": round(float(ct_avg), 2) if ct_avg is not None else None,
                    "va": round(att["va"], 1), "bva": round(att["bva"], 1),
                    "nva_total": round(att["nva_total"], 1),
                })
            return out
        except Exception as e:
            log.warning("[Plugin][%s] /trend 异常 (降级空): %s", CUSTOMER_CODE, e)
            return out
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    # ---------- 步骤统计 ----------
    @router.get("/step-averages")
    def step_averages(date: Optional[str] = None,
                      channel_id: Optional[int] = None,
                      project_id: Optional[int] = None):
        """某日按步骤 label 聚合: 次数 / 平均·最短·最长耗时 / 平均等待 + 动作价值。"""
        day, start, end = _date_bounds(date)
        out: Dict[str, Any] = {"date": day.isoformat(), "steps": []}
        db = None
        try:
            from sqlalchemy import func as sa_func
            from backend.models.models import DetectionCycle, DetectionSession, StepRecord, Project
            from .lean import normalize_value_type

            db = _db()
            if db is None:
                return out

            cq = db.query(DetectionCycle.id).filter(
                DetectionCycle.end_time.isnot(None),
                DetectionCycle.end_time >= start,
                DetectionCycle.end_time < end,
            )
            if channel_id is not None or project_id is not None:
                cq = cq.join(DetectionSession, DetectionCycle.session_id == DetectionSession.id)
                if channel_id is not None:
                    cq = cq.filter(DetectionSession.channel_id == channel_id)
                if project_id is not None:
                    cq = cq.filter(DetectionSession.project_id == project_id)

            rows = db.query(
                StepRecord.step_label,
                sa_func.count(StepRecord.id),
                sa_func.avg(StepRecord.duration),
                sa_func.min(StepRecord.duration),
                sa_func.max(StepRecord.duration),
                sa_func.avg(StepRecord.interval_from_prev),
            ).filter(
                StepRecord.cycle_id.in_(cq)
            ).group_by(StepRecord.step_label).all()

            pid = project_id
            order_labels: list = []
            if pid is None:
                active = db.query(Project).filter(Project.is_active == True).first()  # noqa: E712
                pid = active.id if active else None
            values = step_values_for_project(pid) if pid else {}
            # 步骤序对齐项目 SOP (group_by 后序不确定, 否则底表与流程带对不上)
            if pid is not None:
                proj = db.query(Project).filter(Project.id == pid).first()
                conf = (proj.steps_config if proj is not None else None) or []
                if isinstance(conf, list):
                    order_labels = [
                        s.get("label") for s in conf
                        if isinstance(s, dict) and s.get("label")
                        and s.get("enabled", True) is not False and not s.get("is_backup")
                    ]

            dvt = plugin_settings()["default_value_type"]
            by_label = {}
            for label, count, avg_d, min_d, max_d, avg_w in rows:
                by_label[str(label)] = {
                    "label": label,
                    "value_type": normalize_value_type(values.get(str(label)), default=dvt),
                    "count": int(count or 0),
                    "avg_duration": round(float(avg_d), 2) if avg_d is not None else None,
                    "min_duration": round(float(min_d), 2) if min_d is not None else None,
                    "max_duration": round(float(max_d), 2) if max_d is not None else None,
                    "avg_wait": round(float(avg_w), 2) if avg_w is not None else None,
                }
            if order_labels:
                seen = set()
                for lb in order_labels:
                    if lb in by_label:
                        out["steps"].append(by_label[lb])
                        seen.add(lb)
                for lb, item in by_label.items():
                    if lb not in seen:
                        out["steps"].append(item)
            else:
                out["steps"] = list(by_label.values())
            return out
        except Exception as e:
            log.warning("[Plugin][%s] /step-averages 异常 (降级空): %s", CUSTOMER_CODE, e)
            return out
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    @router.post("/demo-toast")
    def demo_toast(channel_id: int = 0, kind: str = "both"):
        """演示用: 触发宿主 OK/NG Toast (走 mgr._trigger_event → events_log → Monitor).

        kind=ok|ng|both。仅看板验收/客户演示, 不改业务状态机结算。
        """
        try:
            from backend.api.channel_manager import channel_manager
            mgr = channel_manager.channels.get(int(channel_id))
            if mgr is None:
                return {"status": "error", "message": f"channel {channel_id} 不存在"}
            fired = []
            k = (kind or "both").lower()
            if k in ("ok", "both"):
                mgr._trigger_event(1, "演示合格 · 顺序正确完成")
                fired.append("ok")
            if k in ("ng", "both"):
                mgr._trigger_event(2, "演示不合格 · 周期不完整")
                fired.append("ng")
            return {"status": "success", "fired": fired, "channel_id": channel_id}
        except Exception as e:
            log.warning("[Plugin][%s] /demo-toast 失败: %s", CUSTOMER_CODE, e)
            return {"status": "error", "message": str(e)}

    @router.post("/demo-frame")
    def demo_frame(channel_id: int = 0):
        """演示用: 把一帧静图灌进当前通道 step_screenshots, 流程带缩略图立刻有画面。"""
        try:
            import base64
            from pathlib import Path
            from backend.api.channel_manager import channel_manager
            mgr = channel_manager.channels.get(int(channel_id))
            if mgr is None:
                return {"status": "error", "message": f"channel {channel_id} 不存在"}
            # 插件包内静态帧 → 找不到则回落 uploads
            candidates = [
                Path(__file__).resolve().parents[1] / "frontend" / "dist" / "demo_frame.jpg",
                Path(__file__).resolve().parents[3] / "backend" / "uploads" / "images" / "lgwt_demo_frame.jpg",
            ]
            img_path = next((p for p in candidates if p.is_file()), None)
            if img_path is None:
                return {"status": "error", "message": "demo_frame.jpg 不存在"}
            b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
            labels = []
            steps = getattr(mgr, "steps_config", None) or []
            if isinstance(steps, list):
                labels = [s.get("label") for s in steps if isinstance(s, dict) and s.get("label")]
            if not labels:
                labels = list((getattr(mgr, "step_screenshots", None) or {}).keys()) or [
                    "拿取5号盒", "检查5号盒", "拿取6号盒", "检查6号盒",
                    "拿取4号盒", "检查4号盒", "拿取2号盒", "检查2号盒", "放入",
                ]
            if not hasattr(mgr, "step_screenshots") or mgr.step_screenshots is None:
                mgr.step_screenshots = {}
            for lb in labels:
                mgr.step_screenshots[lb] = b64
            return {"status": "success", "labels": labels, "frame": str(img_path.name)}
        except Exception as e:
            log.warning("[Plugin][%s] /demo-frame 失败: %s", CUSTOMER_CODE, e)
            return {"status": "error", "message": str(e)}

    return router
