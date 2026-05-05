"""
v3.5.0 自定义导出系统 — 数据上下文构造

三种构造模式：

1. build_cycle_context(cycle_id, ...)
   单周期场景。实时规则（cycle_end Hook）和单 cycle 自定义导出都用它。
   返回 dict 包含 cycle/steps/workpiece/order/operator/defects 等所有单周期字段。

2. build_range_context(start_date, end_date, ...)
   范围场景。批量导出（今日/本周/本月/自定义日期范围）用它。
   返回 dict 包含 stats/aggregations/cycles[*]/sessions[*] 等聚合字段。

3. build_system_context()
   仅系统级（无周期数据）。比如导出"软件信息卡片"时用。

设计契约：
- 三个函数都返回完整 302 字段骨架，缺失值置 None / [] / {} 但 key 必有
- Jinja2 模板用 {{ x | default('-') }} 兜底，不会因为字段缺失炸渲染
- 实时模式下若没传 live_state，live.* 全为 None；批量模式下 live.* 一律 None

实时数据 live_state 由 source.get_detection_results() 注入：
  Step 1.5 完成后 live_state['tracking'] 才会带 stack_state / max_recognized 等字段；
  在那之前 live.tracking.* 全部为 None（前端字段树拖出来会显示但渲染时是空）。
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, date as _date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session as DBSession

from backend.core.config import BASE_DIR


# ============================================================
# 全局缓存（启动期一次性读，每次构造直接返回）
# ============================================================

_app_info_cache: Optional[Dict[str, Any]] = None
_system_info_cache: Optional[Dict[str, Any]] = None


def _read_app_info() -> Dict[str, Any]:
    """从 electron/package.json 读软件元数据 — 启动后基本不变，缓存"""
    global _app_info_cache
    if _app_info_cache is not None:
        return _app_info_cache

    pkg_path = os.path.normpath(os.path.join(BASE_DIR, "..", "electron", "package.json"))
    info = {
        "name": "天骏视觉",
        "version": "0.0.0",
        "product_name": "Tianjun AI Vision",
        "brand_name": "天骏",
        "build_date": "",
        "commit_hash": "",
    }
    try:
        if os.path.exists(pkg_path):
            with open(pkg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            info["version"] = data.get("version") or info["version"]
            info["product_name"] = data.get("description") or data.get("name") or info["product_name"]
            author = data.get("author") or {}
            if isinstance(author, dict):
                info["brand_name"] = author.get("name") or info["brand_name"]
            elif isinstance(author, str):
                info["brand_name"] = author
            # build_date / commit_hash 由 CI 注入到 package.json 自定义字段
            info["build_date"] = data.get("buildDate", "")
            info["commit_hash"] = data.get("commitHash", "")
    except Exception as e:
        print(f"[ExportContext] 读 package.json 失败: {e}")

    _app_info_cache = info
    return info


def _read_system_info() -> Dict[str, Any]:
    """读硬件 / 系统信息 — 启动后基本不变，缓存"""
    global _system_info_cache
    if _system_info_cache is not None:
        return _system_info_cache

    info = {
        "hostname": "",
        "os": "",
        "cpu": "",
        "gpu": "",
        "gpu_count": 0,
        "memory_gb": 0.0,
        "disk_free_gb": 0.0,
        "uptime_seconds": 0,
        "data_dir": str(BASE_DIR),
    }
    try:
        info["hostname"] = socket.gethostname()
    except Exception:
        pass
    try:
        import platform
        info["os"] = f"{platform.system()} {platform.release()}"
    except Exception:
        pass
    try:
        import psutil  # type: ignore
        info["memory_gb"] = round(psutil.virtual_memory().total / (1024**3), 2)
        try:
            info["disk_free_gb"] = round(psutil.disk_usage(str(BASE_DIR)).free / (1024**3), 2)
        except Exception:
            pass
        info["uptime_seconds"] = int(datetime.now().timestamp() - psutil.boot_time())
    except ImportError:
        pass
    except Exception as e:
        print(f"[ExportContext] psutil 读失败: {e}")
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            info["gpu_count"] = torch.cuda.device_count()
            info["gpu"] = torch.cuda.get_device_name(0) if info["gpu_count"] > 0 else ""
    except Exception:
        pass
    try:
        import platform
        info["cpu"] = platform.processor() or info["cpu"]
    except Exception:
        pass

    _system_info_cache = info
    return info


def _read_display_info(db: DBSession) -> Dict[str, Any]:
    """从 SystemConfig KV 读 display 字段 — 每次构造都读（运行时可能更新）

    Step 1.7 完成后前端 useSystemStore 保存时会同步落 SystemConfig，
    在那之前这里返回空字典让 Jinja2 用 default 兜底。
    """
    out = {
        "brand_name": None,
        "app_name": None,
        "inspector_name": None,
        "device_number": None,
        "factory_name": None,
        "line_name": None,
    }
    try:
        from backend.models.models import SystemConfig
        rows = db.query(SystemConfig).filter(
            SystemConfig.key.like("display.%")
        ).all()
        for r in rows:
            short_key = r.key.split(".", 1)[1] if "." in r.key else r.key
            if short_key in out:
                out[short_key] = r.value
    except Exception:
        pass

    # 兜底：如果 display.brand_name 还没落库，用 app.brand_name 顶上
    if out["brand_name"] is None:
        out["brand_name"] = _read_app_info().get("brand_name")
    if out["app_name"] is None:
        out["app_name"] = _read_app_info().get("name")
    return out


def _read_license_info(db: DBSession,
                       license_payload: Optional[Dict[str, Any]] = None
                       ) -> Dict[str, Any]:
    """License 信息

    优先级（参考用户决议 1B - 前端 IPC 送过来）：
    1. 显式传入的 license_payload（API 调用方从前端 IPC 拿到后注入）
    2. SystemConfig KV (key='license.cache') — 前端启动后写一次缓存
    3. 全空（实时规则触发时前端不在场，license 字段读不到）
    """
    out = {
        "customer": None,
        "machine_id": None,
        "expires_at": None,
        "is_perpetual": False,
        "days_remaining": None,
        "features": [],
    }
    if license_payload:
        for k in out:
            if k in license_payload:
                out[k] = license_payload[k]
        return out

    try:
        from backend.models.models import SystemConfig
        row = db.query(SystemConfig).filter(SystemConfig.key == "license.cache").first()
        if row and row.value:
            cached = json.loads(row.value)
            for k in out:
                if k in cached:
                    out[k] = cached[k]
    except Exception:
        pass

    # 计算 days_remaining
    if out["expires_at"] and not out["is_perpetual"]:
        try:
            exp = datetime.fromisoformat(str(out["expires_at"]).replace("Z", "+00:00"))
            delta = exp.date() - datetime.now().date()
            out["days_remaining"] = delta.days
        except Exception:
            pass

    return out


def _shift_label(now: datetime) -> str:
    """简单的班次判定：08:00-20:00 day, 其他 night
    后期可改为读 SystemConfig.shift_schedule"""
    return "day" if 8 <= now.hour < 20 else "night"


def _now_dict() -> Dict[str, Any]:
    """构造时间字段子树（time 分组的 8 个字段顶层放，命名见 registry）"""
    n = datetime.now()
    return {
        "now": n,
        "now_date": n.strftime("%Y-%m-%d"),
        "now_time": n.strftime("%H:%M:%S"),
        "now_ymdhms": n.strftime("%Y%m%d_%H%M%S"),
        "now_unix": int(n.timestamp()),
        "now_unix_ms": int(n.timestamp() * 1000),
        "now_iso": n.isoformat(),
        "today_shift": _shift_label(n),
    }


# ============================================================
# 完整骨架（保证 302 字段都有 key）
# ============================================================

def _empty_steps_item() -> Dict[str, Any]:
    return {
        "index": None, "label": None, "is_good": None,
        "result": None, "result_pass_fail": None,
        "duration": None, "interval_to_next": None,
        "confidence": None, "frame_count": None,
        "start_time": None, "end_time": None,
        "event": None, "ng_reason": None,
    }


def _empty_app_section() -> Dict[str, Any]:
    return {
        "name": None, "version": None, "product_name": None,
        "brand_name": None, "build_date": None, "commit_hash": None,
    }


def _empty_display_section() -> Dict[str, Any]:
    return {
        "brand_name": None, "app_name": None, "inspector_name": None,
        "device_number": None, "factory_name": None, "line_name": None,
    }


def _empty_license_section() -> Dict[str, Any]:
    return {
        "customer": None, "machine_id": None, "expires_at": None,
        "is_perpetual": False, "days_remaining": None, "features": [],
    }


def _empty_system_section() -> Dict[str, Any]:
    return {
        "hostname": None, "os": None, "cpu": None, "gpu": None,
        "gpu_count": 0, "memory_gb": 0.0, "disk_free_gb": 0.0,
        "uptime_seconds": 0, "data_dir": None,
    }


def _empty_channel_section() -> Dict[str, Any]:
    return {
        "id": None, "name": None, "station_id": None,
        "video_source_type": None, "video_source_url": None,
        "resolution_w": None, "resolution_h": None,
        "gpu_index": None,
    }


def _empty_project_section() -> Dict[str, Any]:
    return {
        "id": None, "name": None, "task_type": None, "logic_mode": None,
        "model_format": None, "model_id": None, "model_name": None,
        "model_version": None, "model_file": None, "is_active": None,
        "created_at": None, "updated_at": None,
        "steps_count": 0, "events_count": 0, "counters_count": 0,
        "steps_config": None, "events_config": None,
        "counters_config": None, "alarm_config": None,
        "detection_config": None, "data_config": None,
        "pipeline_config": None,
        "confidence_threshold": None, "iou_threshold": None, "imgsz": None,
    }


def _empty_session_section() -> Dict[str, Any]:
    return {
        "id": None, "session_uuid": None, "name": None,
        "channel_id": None, "shift_label": None, "status": None,
        "start_time": None, "end_time": None,
        "duration": None, "duration_human": None,
        "total_cycles": 0, "good_cycles": 0, "ng_cycles": 0,
        "yield_rate": None, "yield_ratio": None,
        "avg_cycle_time": None, "min_cycle_time": None, "max_cycle_time": None,
        "avg_cycle_interval": None,
        "project_id": None, "operator_id": None, "order_id": None,
    }


def _empty_cycle_section() -> Dict[str, Any]:
    return {
        "id": None, "session_id": None, "channel_id": None,
        "cycle_number": None,
        "start_time": None, "end_time": None,
        "duration": None, "duration_ms": None,
        "interval_to_next": None,
        "is_good": None, "result": None, "result_pass_fail": None,
        "ng_step": None, "ng_step_index": None,
        "ng_reason": None, "ng_code": None,
        "total_steps": 0, "good_steps": 0, "ng_steps_count": 0,
        "recognized_max": None,
        "video_clip_path": None, "video_clip_url": None,
        "test_values": [],
        "barcode": None,
    }


def _empty_workpiece_section() -> Dict[str, Any]:
    return {
        "id": None, "serial_no": None, "batch_no": None,
        "product_code": None, "product_name": None, "status": None,
        "is_good": None,
        "first_seen_at": None, "last_inspection_at": None,
        "inspection_count": 0, "rework_count": 0, "defect_count": 0,
        "barcode_parse": None,
        "work_order_id": None, "order_no": None,
    }


def _empty_order_section() -> Dict[str, Any]:
    return {
        "id": None, "order_no": None, "external_id": None,
        "product_name": None, "product_code": None, "product_spec": None,
        "planned_qty": 0, "completed_qty": 0,
        "good_qty": 0, "ng_qty": 0, "rework_qty": 0, "scrap_qty": 0,
        "yield_rate": None, "progress_rate": None,
        "priority": None, "status": None,
        "binding_scope": None, "target_channels": [], "target_stations": [],
        "source": None,
        "planned_start": None, "planned_end": None,
        "actual_start": None, "actual_end": None,
        "created_at": None, "updated_at": None,
    }


def _empty_operator_section() -> Dict[str, Any]:
    return {
        "id": None, "name": None, "employee_no": None,
        "role": None, "shift": None, "login_time": None,
    }


def _empty_box_section() -> Dict[str, Any]:
    return {
        "id": None, "box_serial": None, "status": None,
        "target_count": None, "actual_count": 0,
        "good_count": 0, "ng_count": 0, "is_good": None,
        "stations_reported": [], "station_results": {},
        "start_time": None, "end_time": None, "duration": None,
        "workpieces": [],
    }


def _empty_scanner_section() -> Dict[str, Any]:
    return {
        "last_barcode": None, "last_scan_time": None,
        "scan_count_today": 0, "duplicate_scan_count": 0,
        "connected": None, "device_name": None, "device_ip": None,
        "scan_mode": None,
        "last_weight": None, "last_external_value": None,
    }


def _empty_live_section() -> Dict[str, Any]:
    """实时数据子树。批量模式下整段为 None/空"""
    return {
        "fps": None, "fps_inference": None, "latency_ms": None,
        "frame_index": None,
        "detections": [],
        "frame_w": None, "frame_h": None,
        "recent_events": [],
        "last_event_type": None, "last_event_step": None, "last_event_time": None,
        "is_inferring": None, "is_recording": None,
        "current_step_index": None, "current_step_label": None,
        "current_step_elapsed": None,
        # tracking 子树（Step 1.5 暴露的内部状态，命名贴近 source 实际变量）
        "tracking": {
            "cycle_active": None, "container_mode": None,
            # Stack
            "stack_states": {}, "stack_counters": {},
            # Tracking
            "active_count": None, "locked_count": None, "lost_count": None,
            "prev_count": None, "was_complete": None,
            "class_counters": {}, "tracked_objects": {}, "item_checklist": {},
            # Event Counter
            "event_counters": {}, "event_states": {},
            # Container
            "box_counter": None, "boxes": {},
            "settled_boxes": None, "settled_ok": None, "settled_ng": None,
            # Scan-D
            "scan_d_armed_box": None,
        },
    }


def _empty_counters_section() -> Dict[str, Any]:
    return {
        "_all": {}, "_keys": [],
        "cycle_total": 0, "cycle_good": 0, "cycle_ng": 0,
    }


def _empty_mes_section() -> Dict[str, Any]:
    return {
        "workpiece": None, "order": None, "scanner": None,
        "barcode": None, "box_serial": None, "station_id": None,
        "is_box_complete": None, "gateway_pushed": None,
        "gateway_response": None, "external_devices": {},
    }


def _empty_stats_section() -> Dict[str, Any]:
    return {
        "total_cycles": 0, "good_cycles": 0, "ng_cycles": 0,
        "yield_rate": None,
        "avg_cycle_time": None, "min_cycle_time": None, "max_cycle_time": None,
        "avg_cycle_interval": None,
        "step_averages": [],
        "ng_distribution": {},
        "events_count": {},
        "cycles": [],
    }


def _empty_aggregations_section() -> Dict[str, Any]:
    return {
        "date_range": None, "total_sessions": 0,
        "total_cycles": 0, "total_good": 0, "total_ng": 0,
        "yield_rate": None,
        "daily_stats": [],
        "sessions": [],
    }


def _empty_context() -> Dict[str, Any]:
    """完整 302 字段骨架 — 所有构造函数都从这里出发"""
    ctx: Dict[str, Any] = {}
    ctx.update(_now_dict())            # 时间字段顶层平铺
    ctx["app"] = _empty_app_section()
    ctx["display"] = _empty_display_section()
    ctx["license"] = _empty_license_section()
    ctx["system"] = _empty_system_section()
    ctx["channel"] = _empty_channel_section()
    ctx["project"] = _empty_project_section()
    ctx["session"] = _empty_session_section()
    ctx["cycle"] = _empty_cycle_section()
    ctx["steps"] = []
    ctx["ng_steps"] = []                # 兼容 mes_gateway 同名字段
    ctx["defects"] = []
    ctx["workpiece"] = _empty_workpiece_section()
    ctx["order"] = _empty_order_section()
    ctx["operator"] = _empty_operator_section()
    ctx["box"] = _empty_box_section()
    ctx["scanner"] = _empty_scanner_section()
    ctx["live"] = _empty_live_section()
    ctx["counters"] = _empty_counters_section()
    ctx["mes"] = _empty_mes_section()
    ctx["stats"] = _empty_stats_section()
    ctx["aggregations"] = _empty_aggregations_section()
    return ctx


# ============================================================
# 通用 fillers
# ============================================================

def _fill_app_display_license_system(ctx: Dict[str, Any], db: DBSession,
                                     license_payload: Optional[Dict[str, Any]] = None
                                     ) -> None:
    """填 app/display/license/system 这四个全局子树"""
    ctx["app"].update(_read_app_info())
    ctx["display"].update(_read_display_info(db))
    ctx["license"].update(_read_license_info(db, license_payload))
    ctx["system"].update(_read_system_info())


def _fill_project(ctx: Dict[str, Any], db: DBSession, project_id: Optional[int]) -> None:
    if not project_id:
        return
    from backend.models.models import Project, Model
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj:
        return

    p = ctx["project"]
    p["id"] = proj.id
    p["name"] = proj.name
    p["task_type"] = getattr(proj, "task_type", "detection")
    p["logic_mode"] = proj.logic_mode
    p["model_format"] = proj.model_format
    p["is_active"] = proj.is_active
    p["created_at"] = proj.created_at.isoformat() if proj.created_at else None
    p["updated_at"] = proj.updated_at.isoformat() if proj.updated_at else None
    p["steps_config"] = proj.steps_config
    p["events_config"] = proj.events_config
    p["counters_config"] = proj.counters_config
    p["alarm_config"] = proj.alarm_config
    p["detection_config"] = proj.detection_config
    p["data_config"] = proj.data_config
    p["pipeline_config"] = proj.pipeline_config
    p["steps_count"] = len(proj.steps_config or [])
    p["events_count"] = len(proj.events_config or [])
    p["counters_count"] = len(proj.counters_config or [])

    pipeline = proj.pipeline_config or {}
    if isinstance(pipeline, dict):
        p["confidence_threshold"] = pipeline.get("conf_threshold") or pipeline.get("confidence")
        p["iou_threshold"] = pipeline.get("iou_threshold") or pipeline.get("iou")
        p["imgsz"] = pipeline.get("imgsz") or pipeline.get("img_size")

    if proj.default_model_id:
        m = db.query(Model).filter(Model.id == proj.default_model_id).first()
        if m:
            p["model_id"] = m.id
            p["model_name"] = m.name
            p["model_version"] = m.version
            p["model_file"] = m.file_name


def _fill_channel(ctx: Dict[str, Any], channel_id: Optional[int]) -> None:
    if channel_id is None:
        return
    ctx["channel"]["id"] = channel_id
    try:
        from backend.api.channel_manager import channel_manager
        sources = channel_manager.get_channel_sources()
        ch_cfg = sources.get(str(channel_id), {}) or sources.get(channel_id, {})
        if ch_cfg:
            ctx["channel"]["video_source_type"] = ch_cfg.get("type")
            ctx["channel"]["video_source_url"] = ch_cfg.get("url") or ch_cfg.get("path")
            ctx["channel"]["name"] = ch_cfg.get("name") or f"工位{channel_id + 1}"
        else:
            ctx["channel"]["name"] = f"工位{channel_id + 1}"
    except Exception:
        ctx["channel"]["name"] = f"工位{channel_id + 1}"


def _serialize_cycle_row(c: Any) -> Dict[str, Any]:
    """把 DetectionCycle ORM 对象序列化成 cycle 子树需要的格式"""
    out = _empty_cycle_section()
    out["id"] = c.id
    out["session_id"] = c.session_id
    out["channel_id"] = getattr(c, "channel_id", None)
    out["cycle_number"] = getattr(c, "cycle_number", None)
    out["start_time"] = c.start_time.isoformat() if c.start_time else None
    out["end_time"] = c.end_time.isoformat() if c.end_time else None
    out["duration"] = c.duration
    if c.duration is not None:
        out["duration_ms"] = int(c.duration * 1000)
    out["interval_to_next"] = getattr(c, "interval_to_next", None)
    out["is_good"] = bool(c.is_good) if c.is_good is not None else None
    if c.is_good is not None:
        out["result"] = "良品" if c.is_good else "NG"
        out["result_pass_fail"] = "Pass" if c.is_good else "Fail"
    # cycle 表没存 ng_step（首个 NG 步骤名），单 cycle 上下文里 _fill_cycle_steps_defects 反查 step_records 后再填
    out["ng_step"] = None
    out["ng_reason"] = getattr(c, "result_reason", None)
    out["video_clip_path"] = getattr(c, "video_path", None)
    return out


def _fill_cycle_steps_defects(ctx: Dict[str, Any], db: DBSession, cycle_id: int) -> None:
    """填 cycle.* / steps[*] / defects[*] / ng_steps[*]"""
    from backend.models.models import DetectionCycle, StepRecord
    c = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
    if not c:
        return

    ctx["cycle"].update(_serialize_cycle_row(c))

    # steps
    steps = db.query(StepRecord).filter(
        StepRecord.cycle_id == cycle_id
    ).order_by(StepRecord.step_order, StepRecord.id).all()

    good_count = 0
    ng_count = 0
    first_ng_label = None
    first_ng_index = None
    for idx, s in enumerate(steps):
        is_good = bool(s.is_valid) if s.is_valid is not None else None
        item = _empty_steps_item()
        item["index"] = s.step_order if s.step_order is not None else idx
        item["label"] = s.step_label
        item["is_good"] = is_good
        if is_good is not None:
            item["result"] = "OK" if is_good else "NG"
            item["result_pass_fail"] = "Pass" if is_good else "Fail"
        item["duration"] = s.duration
        item["interval_to_next"] = s.interval_to_next
        item["confidence"] = s.confidence
        item["start_time"] = s.start_time.isoformat() if s.start_time else None
        item["end_time"] = s.end_time.isoformat() if s.end_time else None
        ctx["steps"].append(item)
        if is_good is True:
            good_count += 1
        elif is_good is False:
            ng_count += 1
            if first_ng_label is None:
                first_ng_label = s.step_label
                first_ng_index = item["index"]

    ctx["cycle"]["total_steps"] = len(steps)
    ctx["cycle"]["good_steps"] = good_count
    ctx["cycle"]["ng_steps_count"] = ng_count
    ctx["cycle"]["ng_step"] = first_ng_label
    ctx["cycle"]["ng_step_index"] = first_ng_index
    ctx["ng_steps"] = [s for s in ctx["steps"] if s.get("is_good") is False]

    # ng_code 从 project.events_config 反查（label → code 映射）
    proj_evt = ctx["project"].get("events_config") or []
    if first_ng_label and isinstance(proj_evt, list):
        for evt in proj_evt:
            if isinstance(evt, dict) and evt.get("step_label") == first_ng_label:
                ctx["cycle"]["ng_code"] = evt.get("ng_code") or evt.get("code")
                ctx["cycle"]["ng_reason"] = (ctx["cycle"]["ng_reason"]
                                             or evt.get("ng_reason"))
                break

    # defects (来自 MES 缺陷记录)
    try:
        from backend.models.mes_models import DefectRecord
        defects = db.query(DefectRecord).filter(DefectRecord.cycle_id == cycle_id).all()
        for d in defects:
            ctx["defects"].append({
                "id": d.id,
                "defect_type": getattr(d, "defect_category", None),
                "defect_code": getattr(d, "defect_code", None),
                "severity": getattr(d, "severity", None),
                "confidence": getattr(d, "confidence", None),
                "position": getattr(d, "position", None),
                "step_label": getattr(d, "step_label", None),
                "source": getattr(d, "source", None),
                "operator_id": getattr(d, "operator_id", None),
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "image_path": getattr(d, "image_path", None),
                "notes": getattr(d, "notes", None),
            })
        ctx["workpiece"]["defect_count"] = len(ctx["defects"])
    except Exception:
        pass


def _fill_workpiece_order_operator(ctx: Dict[str, Any], db: DBSession,
                                   workpiece_id: Optional[int],
                                   order_id: Optional[int],
                                   operator_id: Optional[int]) -> None:
    if workpiece_id:
        try:
            from backend.models.mes_models import Workpiece
            wp = db.query(Workpiece).filter(Workpiece.id == workpiece_id).first()
            if wp:
                w = ctx["workpiece"]
                w["id"] = wp.id
                w["serial_no"] = wp.serial_no
                w["batch_no"] = getattr(wp, "batch_no", None)
                w["product_code"] = getattr(wp, "product_code", None)
                w["product_name"] = getattr(wp, "product_name", None)
                w["status"] = wp.status
                w["is_good"] = (wp.status == "good") if wp.status else None
                w["first_seen_at"] = (wp.first_seen_at.isoformat()
                                      if getattr(wp, "first_seen_at", None) else None)
                w["last_inspection_at"] = (wp.last_inspection_at.isoformat()
                                           if getattr(wp, "last_inspection_at", None) else None)
                w["inspection_count"] = wp.inspection_count
                w["rework_count"] = getattr(wp, "rework_count", 0) or 0
                w["barcode_parse"] = getattr(wp, "barcode_parse", None)
                w["work_order_id"] = getattr(wp, "work_order_id", None)
                ctx["cycle"]["barcode"] = wp.serial_no
        except Exception as e:
            print(f"[ExportContext] fill_workpiece 失败: {e}")

    if order_id:
        try:
            from backend.services.work_order import WorkOrderService
            summary = WorkOrderService().get_order_summary(db, order_id)
            if summary:
                # summary 字段尽量与 _empty_order_section 对齐
                ctx["order"].update({
                    k: v for k, v in summary.items() if k in ctx["order"]
                })
                # 完成率
                if summary.get("planned_qty"):
                    ctx["order"]["progress_rate"] = round(
                        (summary.get("completed_qty") or 0) / summary["planned_qty"], 4
                    )
                if ctx["workpiece"]["work_order_id"] == order_id:
                    ctx["workpiece"]["order_no"] = ctx["order"].get("order_no")
        except Exception as e:
            print(f"[ExportContext] fill_order 失败: {e}")

    if operator_id:
        try:
            from backend.models.models import Operator
            op = db.query(Operator).filter(Operator.id == operator_id).first()
            if op:
                ctx["operator"].update({
                    "id": op.id,
                    "name": op.name,
                    "employee_no": getattr(op, "employee_no", None),
                    "role": getattr(op, "role", None),
                })
        except Exception:
            pass


def _fill_session(ctx: Dict[str, Any], db: DBSession, session_id: int) -> None:
    from backend.models.models import DetectionSession
    s = db.query(DetectionSession).filter(DetectionSession.id == session_id).first()
    if not s:
        return
    sec = ctx["session"]
    sec["id"] = s.id
    sec["session_uuid"] = s.session_uuid
    sec["name"] = getattr(s, "name", None)
    sec["channel_id"] = getattr(s, "channel_id", 0)
    sec["shift_label"] = getattr(s, "shift_label", None)
    sec["status"] = s.status
    sec["start_time"] = s.start_time.isoformat() if s.start_time else None
    sec["end_time"] = s.end_time.isoformat() if s.end_time else None
    sec["total_cycles"] = s.total_cycles or 0
    sec["good_cycles"] = s.good_cycles or 0
    sec["ng_cycles"] = s.ng_cycles or 0
    sec["avg_cycle_time"] = s.avg_cycle_time
    sec["min_cycle_time"] = s.min_cycle_time
    sec["max_cycle_time"] = s.max_cycle_time
    sec["project_id"] = s.project_id
    sec["operator_id"] = getattr(s, "operator_id", None)
    sec["order_id"] = getattr(s, "order_id", None)

    if s.start_time and s.end_time:
        dur = (s.end_time - s.start_time).total_seconds()
        sec["duration"] = dur
        h = int(dur // 3600); m = int((dur % 3600) // 60); sec_s = int(dur % 60)
        sec["duration_human"] = f"{h}h {m}m {sec_s}s"

    if sec["total_cycles"]:
        sec["yield_ratio"] = round(sec["good_cycles"] / sec["total_cycles"], 4)
        sec["yield_rate"] = round(sec["yield_ratio"] * 100, 2)


def _fill_counters(ctx: Dict[str, Any], db: DBSession, cycle_id: Optional[int],
                   live_state: Optional[Dict[str, Any]] = None) -> None:
    """填 counters 子树

    优先级：
    1. live_state['counters']  (实时模式 from source.get_counters_state)
    2. cycle.counters_snapshot (DB 持久化 snapshot)
    3. session.cycle_total/good/ng（从 _fill_session 已经填了）
    """
    if live_state and isinstance(live_state.get("counters"), dict):
        all_counters = dict(live_state["counters"])
    else:
        all_counters = {}
        if cycle_id:
            try:
                from backend.models.models import DetectionCycle
                c = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
                if c and getattr(c, "counters_snapshot", None):
                    if isinstance(c.counters_snapshot, dict):
                        all_counters = dict(c.counters_snapshot)
            except Exception:
                pass

    ctx["counters"]["_all"] = all_counters
    ctx["counters"]["_keys"] = list(all_counters.keys())
    for k, v in all_counters.items():
        ctx["counters"][k] = v

    s = ctx["session"]
    ctx["counters"]["cycle_total"] = s.get("total_cycles") or 0
    ctx["counters"]["cycle_good"] = s.get("good_cycles") or 0
    ctx["counters"]["cycle_ng"] = s.get("ng_cycles") or 0


def _fill_live_from_state(ctx: Dict[str, Any],
                          live_state: Optional[Dict[str, Any]]) -> None:
    """实时模式下注入 live_state（来自 source.get_detection_results()）"""
    if not live_state:
        return
    L = ctx["live"]
    L["fps"] = live_state.get("fps")
    L["fps_inference"] = live_state.get("fps_inference") or live_state.get("fps_inf")
    L["latency_ms"] = live_state.get("latency") or live_state.get("latency_ms")
    L["frame_index"] = live_state.get("frame_index")
    L["frame_w"] = live_state.get("frame_w") or live_state.get("frame_width")
    L["frame_h"] = live_state.get("frame_h") or live_state.get("frame_height")
    L["detections"] = live_state.get("detections") or []
    L["recent_events"] = live_state.get("recent_events") or []
    L["is_inferring"] = live_state.get("is_inferring")
    L["is_recording"] = live_state.get("is_recording")
    L["current_step_index"] = live_state.get("current_step_index")
    L["current_step_label"] = live_state.get("current_step_label")
    L["current_step_elapsed"] = live_state.get("current_step_elapsed")

    if L["recent_events"]:
        last = L["recent_events"][-1]
        if isinstance(last, dict):
            L["last_event_type"] = last.get("type")
            L["last_event_step"] = last.get("step") or last.get("step_label")
            L["last_event_time"] = last.get("time") or last.get("timestamp")

    tracking = live_state.get("tracking") or {}
    if isinstance(tracking, dict):
        T = ctx["live"]["tracking"]
        for k in list(T.keys()):
            if k in tracking:
                T[k] = tracking[k]


def _fill_mes_subtree(ctx: Dict[str, Any]) -> None:
    """构造 mes.* — 多数是 workpiece/order 的别名 + 顶层 barcode/box_serial 等"""
    M = ctx["mes"]
    M["workpiece"] = ctx["workpiece"]
    M["order"] = ctx["order"]
    M["scanner"] = ctx["scanner"]
    M["barcode"] = ctx["workpiece"].get("serial_no") or ctx["cycle"].get("barcode")


# ============================================================
# 入口 1: 单 cycle 上下文
# ============================================================

def build_cycle_context(db: DBSession, cycle_id: int,
                        live_state: Optional[Dict[str, Any]] = None,
                        license_payload: Optional[Dict[str, Any]] = None,
                        ) -> Dict[str, Any]:
    """构造单 cycle 完整上下文

    实时规则 (cycle_end Hook) 和"自定义导出 → 选某轮"都用它。

    参数:
        db: 已开的 DB session
        cycle_id: DetectionCycle.id
        live_state: 可选 — 当前 source.get_detection_results() 返回值，
                    实时模式下传入以填充 live.* 子树（包括 tracking）
        license_payload: 可选 — 前端 IPC 解析到的 license 信息

    返回完整 302 字段 dict
    """
    ctx = _empty_context()

    _fill_app_display_license_system(ctx, db, license_payload)

    from backend.models.models import DetectionCycle
    c = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
    if not c:
        ctx["cycle"]["id"] = cycle_id
        return ctx

    project_id = None
    workpiece_id = None
    operator_id = getattr(c, "operator_id", None)
    order_id = getattr(c, "order_id", None)

    if c.session_id:
        from backend.models.models import DetectionSession
        s = db.query(DetectionSession).filter(DetectionSession.id == c.session_id).first()
        if s:
            project_id = s.project_id
            if not operator_id:
                operator_id = getattr(s, "operator_id", None)
            if not order_id:
                order_id = getattr(s, "order_id", None)
        _fill_session(ctx, db, c.session_id)

    _fill_project(ctx, db, project_id)
    _fill_channel(ctx, getattr(c, "channel_id", None) or ctx["session"].get("channel_id"))
    _fill_cycle_steps_defects(ctx, db, cycle_id)

    # workpiece_id 由 inspection 表反查 (cycle ↔ workpiece 没强外键)
    try:
        from backend.models.mes_models import WorkpieceInspection
        ins = db.query(WorkpieceInspection).filter(
            WorkpieceInspection.cycle_id == cycle_id
        ).order_by(WorkpieceInspection.id.desc()).first()
        if ins:
            workpiece_id = ins.workpiece_id
    except Exception:
        pass

    _fill_workpiece_order_operator(ctx, db, workpiece_id, order_id, operator_id)
    _fill_counters(ctx, db, cycle_id, live_state)
    _fill_live_from_state(ctx, live_state)
    _fill_mes_subtree(ctx)

    return ctx


# ============================================================
# 入口 2: 范围上下文
# ============================================================

def build_range_context(db: DBSession,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None,
                        session_id: Optional[int] = None,
                        project_id: Optional[int] = None,
                        channel_id: Optional[int] = None,
                        include_cycles: bool = False,
                        license_payload: Optional[Dict[str, Any]] = None,
                        ) -> Dict[str, Any]:
    """构造范围上下文

    - session_id 优先（单 session 导出）
    - 否则按 start_date/end_date 取所有 session

    include_cycles=True 时把全部 cycle 序列化进 stats.cycles （范围大时慎用）

    返回完整 302 字段 dict (cycle.* / steps 留空，业务实体取 session 内首条做代表)
    """
    from backend.models.models import DetectionSession, DetectionCycle, StepRecord
    from sqlalchemy import func as sa_func

    ctx = _empty_context()
    _fill_app_display_license_system(ctx, db, license_payload)

    # 构造 session 列表
    sq = db.query(DetectionSession)
    if session_id is not None:
        sq = sq.filter(DetectionSession.id == session_id)
    else:
        if start_date:
            sq = sq.filter(DetectionSession.start_time >= start_date)
        if end_date:
            sq = sq.filter(DetectionSession.start_time <= end_date + " 23:59:59")
        if project_id is not None:
            sq = sq.filter(DetectionSession.project_id == project_id)
        if channel_id is not None:
            sq = sq.filter(DetectionSession.channel_id == channel_id)

    sessions: List[Any] = sq.order_by(DetectionSession.start_time).all()
    sids = [s.id for s in sessions]

    # 单 session 时填 session.* 主体
    if session_id and sessions:
        _fill_session(ctx, db, session_id)
        _fill_project(ctx, db, sessions[0].project_id)
        _fill_channel(ctx, getattr(sessions[0], "channel_id", 0))
    elif sessions and project_id is None:
        first = sessions[0]
        _fill_project(ctx, db, first.project_id)
        _fill_channel(ctx, getattr(first, "channel_id", 0))
    elif project_id is not None:
        _fill_project(ctx, db, project_id)

    # 跨 session 聚合 — aggregations.*
    A = ctx["aggregations"]
    A["total_sessions"] = len(sessions)
    A["sessions"] = []
    total_cycles = total_good = total_ng = 0
    for s in sessions:
        total_cycles += (s.total_cycles or 0)
        total_good += (s.good_cycles or 0)
        total_ng += (s.ng_cycles or 0)
        A["sessions"].append({
            "id": s.id, "session_uuid": s.session_uuid,
            "channel_id": getattr(s, "channel_id", 0),
            "start_time": s.start_time.isoformat() if s.start_time else None,
            "end_time": s.end_time.isoformat() if s.end_time else None,
            "total_cycles": s.total_cycles or 0,
            "good_cycles": s.good_cycles or 0,
            "ng_cycles": s.ng_cycles or 0,
            "yield_rate": (round(100.0 * (s.good_cycles or 0) / s.total_cycles, 2)
                           if s.total_cycles else None),
        })
    A["total_cycles"] = total_cycles
    A["total_good"] = total_good
    A["total_ng"] = total_ng
    if total_cycles:
        A["yield_rate"] = round(100.0 * total_good / total_cycles, 2)

    if start_date and end_date:
        A["date_range"] = f"{start_date} ~ {end_date}"
    elif start_date:
        A["date_range"] = f"{start_date} ~"
    elif end_date:
        A["date_range"] = f"~ {end_date}"
    elif sessions:
        first_t = sessions[0].start_time
        last_t = sessions[-1].end_time or sessions[-1].start_time
        if first_t and last_t:
            A["date_range"] = f"{first_t.date()} ~ {last_t.date()}"

    # 按日聚合 daily_stats[]
    if sids:
        rows = db.query(
            sa_func.date(DetectionCycle.start_time).label("d"),
            sa_func.count(DetectionCycle.id),
            sa_func.sum(DetectionCycle.is_good == True),  # noqa: E712
        ).filter(DetectionCycle.session_id.in_(sids)).group_by("d").order_by("d").all()
        for d, total, good in rows:
            good = int(good or 0)
            total = int(total or 0)
            A["daily_stats"].append({
                "date": str(d),
                "total": total,
                "good": good,
                "ng": total - good,
                "yield_rate": (round(100.0 * good / total, 2) if total else None),
            })

    # 单 session 聚合 stats.*
    S = ctx["stats"]
    S["total_cycles"] = total_cycles
    S["good_cycles"] = total_good
    S["ng_cycles"] = total_ng
    if total_cycles:
        S["yield_rate"] = round(100.0 * total_good / total_cycles, 2)

    if sids:
        cyc_rows = db.query(
            sa_func.avg(DetectionCycle.duration),
            sa_func.min(DetectionCycle.duration),
            sa_func.max(DetectionCycle.duration),
            sa_func.avg(DetectionCycle.interval_to_next),
        ).filter(
            DetectionCycle.session_id.in_(sids),
            DetectionCycle.duration.is_not(None),
        ).first()
        if cyc_rows:
            S["avg_cycle_time"] = float(cyc_rows[0]) if cyc_rows[0] is not None else None
            S["min_cycle_time"] = float(cyc_rows[1]) if cyc_rows[1] is not None else None
            S["max_cycle_time"] = float(cyc_rows[2]) if cyc_rows[2] is not None else None
            S["avg_cycle_interval"] = float(cyc_rows[3]) if cyc_rows[3] is not None else None

        # NG 分布（cycle 表没存 ng_step 字段，反查 step_records.is_valid=False）
        ng_rows = db.query(
            StepRecord.step_label,
            sa_func.count(sa_func.distinct(StepRecord.cycle_id))
        ).filter(
            StepRecord.cycle_id.in_(
                db.query(DetectionCycle.id).filter(DetectionCycle.session_id.in_(sids))
            ),
            StepRecord.is_valid == False,  # noqa: E712
        ).group_by(StepRecord.step_label).all()
        S["ng_distribution"] = {label: int(cnt) for label, cnt in ng_rows if label}

        # step_averages — 复用 sessions_stats 的逻辑（后续 1.6 完善 confidence）
        step_rows = db.query(
            StepRecord.step_label,
            sa_func.count(StepRecord.id),
            sa_func.sum(StepRecord.is_valid == True),  # noqa: E712
            sa_func.avg(StepRecord.duration),
            sa_func.min(StepRecord.duration),
            sa_func.max(StepRecord.duration),
            sa_func.avg(StepRecord.interval_to_next),
            sa_func.avg(StepRecord.confidence),
            sa_func.min(StepRecord.confidence),
            sa_func.max(StepRecord.confidence),
        ).filter(
            StepRecord.cycle_id.in_(
                db.query(DetectionCycle.id).filter(DetectionCycle.session_id.in_(sids))
            )
        ).group_by(StepRecord.step_label).all()

        for label, count, good_cnt, avg_d, min_d, max_d, avg_i, avg_c, min_c, max_c in step_rows:
            count = int(count or 0)
            good_cnt = int(good_cnt or 0)
            S["step_averages"].append({
                "label": label,
                "count": count,
                "good_count": good_cnt,
                "ng_count": count - good_cnt,
                "avg_duration": float(avg_d) if avg_d is not None else None,
                "min_duration": float(min_d) if min_d is not None else None,
                "max_duration": float(max_d) if max_d is not None else None,
                "avg_interval": float(avg_i) if avg_i is not None else None,
                "avg_confidence": float(avg_c) if avg_c is not None else None,
                "min_confidence": float(min_c) if min_c is not None else None,
                "max_confidence": float(max_c) if max_c is not None else None,
            })

        if include_cycles:
            cycles = db.query(DetectionCycle).filter(
                DetectionCycle.session_id.in_(sids)
            ).order_by(DetectionCycle.start_time).all()
            for c in cycles:
                S["cycles"].append(_serialize_cycle_row(c))

    # session 主体 yield_rate
    if total_cycles:
        ctx["session"]["yield_rate"] = round(100.0 * total_good / total_cycles, 2)
        ctx["session"]["yield_ratio"] = round(total_good / total_cycles, 4)

    return ctx


# ============================================================
# 入口 3: 系统级上下文
# ============================================================

def build_system_context(db: DBSession,
                         license_payload: Optional[Dict[str, Any]] = None
                         ) -> Dict[str, Any]:
    """仅系统级 — 软件信息卡片、License 报告等场景"""
    ctx = _empty_context()
    _fill_app_display_license_system(ctx, db, license_payload)
    return ctx
