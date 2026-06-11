"""诊断 / 调试接口.

历史: 这两个接口原本写在 backend/hotfix.py 里通过 monkey patch 注册,
但它们本质上是新增的正式诊断 API, 不是"补丁", 应该作为常规路由存在.
v2.7.10 清理时把它们挪到这里独立成模块.

接口列表:
  GET  /debug/channels             — 看每个通道的 source_type / 运行状态
  POST /debug/test_cluster_flow    — 用真实 cycle_id 端到端回放 mes_gateway → cluster
  GET  /debug/flags                — 调试中心类别开关 + 类别目录 (调试设置页渲染用)
  PUT  /debug/flags                — 批量写开关 (内存态, 重启归零)
  GET  /debug/logs                 — 增量拉取调试日志环形缓冲 (1s 轮询)
  POST /debug/logs/clear           — 清空缓冲
  POST /debug/client-log           — 前端埋点回传 (无鉴权, 前端开关自行守门)

鉴权说明: 调试端点与既有 /debug/* 保持一致不挂 require_perm —
入口 UI 在开发者模式门控之后, 且开关默认全关、重启归零, 无业务副作用.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Query

from backend.core import debug_center

router = APIRouter()


# ==================== 调试中心: 开关 / 日志 ====================

@router.get("/debug/flags")
def get_debug_flags():
    """返回当前开关状态 + 类别目录(label/group), 前端开关矩阵据此渲染"""
    return {
        "flags": debug_center.get_flags(),
        "catalog": debug_center.BACKEND_CATEGORIES,
    }


@router.put("/debug/flags")
def set_debug_flags(payload: Dict[str, Any] = Body(default=None)):
    """批量写开关: {"flags": {"backend.mes": true, ...}}; 未注册类别忽略"""
    flags = (payload or {}).get("flags") or {}
    return {"flags": debug_center.set_flags(flags)}


@router.get("/debug/logs")
def get_debug_logs(since_seq: int = Query(0), categories: Optional[str] = Query(None), limit: int = Query(500, le=2000)):
    """增量拉取: since_seq 之后的日志; categories 逗号分隔可选过滤"""
    cats = [c for c in categories.split(",") if c] if categories else None
    return debug_center.get_logs(since_seq=since_seq, categories=cats, limit=limit)


@router.post("/debug/logs/clear")
def clear_debug_logs():
    debug_center.clear_logs()
    return {"status": "success"}


@router.post("/debug/client-log")
def debug_client_log(payload: Dict[str, Any] = Body(default=None)):
    """前端埋点回传: 前端开关开启时才会调, 这里直接入缓冲+落盘.
    不挂鉴权 (与 /plugins/client-log 同理由): 仅写日志无副作用, 字段已截断."""
    data = payload or {}
    try:
        debug_center.ingest_client(
            category=str(data.get("category", "frontend")),
            action=str(data.get("action", "")),
            detail=str(data.get("detail", "")),
        )
    except Exception:
        pass
    return {"status": "ok"}


@router.get("/debug/channels")
def debug_channels():
    """返回每个已注册通道的当前状态, 故障排查时快速查看到底是哪条通道挂了."""
    from backend.api.channel_manager import channel_manager
    result = {"channel_count": channel_manager.channel_count, "channels": {}}
    for cid in channel_manager.active_channels():
        try:
            mgr = channel_manager.get(cid)
            result["channels"][str(cid)] = {
                "source_type": mgr.source_type,
                "is_running": mgr.is_running,
                "is_detecting": mgr.is_detecting,
                "fps_actual": mgr.fps_actual,
                "model_loaded": mgr.model is not None,
            }
        except Exception as e:
            result["channels"][str(cid)] = {"error": str(e)}
    return result


@router.post("/debug/test_cluster_flow")
def debug_test_cluster_flow(cycle_id: int = Query(...), channel_id: int = Query(0)):
    """端到端回放: 从真实 DetectionCycle 喂给 build_context + receive_station_report.

    用于验证 mes_hooks._handle_cycle_end 后半段(context 构造 + 集群入库)
    整条链路在真实数据下不抛异常、box_aggregations 正确落库.
    """
    from backend.db.database import SessionLocal
    from backend.services.mes_gateway import get_mes_gateway
    from backend.services.cluster_collector import get_cluster_collector
    from backend.models.models import DetectionCycle
    from backend.models.mes_models import Workpiece, WorkpieceInspection

    result = {"cycle_id": cycle_id, "channel_id": channel_id, "steps": []}
    db = SessionLocal()
    try:
        cycle = db.query(DetectionCycle).filter(DetectionCycle.id == cycle_id).first()
        if not cycle:
            return {"success": False, "error": f"cycle {cycle_id} 不存在"}
        result["steps"].append("cycle loaded")

        insp = (db.query(WorkpieceInspection)
                  .filter(WorkpieceInspection.cycle_id == cycle_id)
                  .first())
        wp_id = insp.workpiece_id if insp else None
        wp = db.query(Workpiece).filter(Workpiece.id == wp_id).first() if wp_id else None
        box_serial = wp.serial_no if wp else None
        result["workpiece_id"] = wp_id
        result["box_serial"] = box_serial
        result["steps"].append("workpiece resolved")

        gw = get_mes_gateway()
        ctx = gw.build_context_from_cycle(
            db, cycle_id,
            workpiece_id=wp_id,
            order_id=None,
            is_good=bool(cycle.is_good),
            event_name=cycle.event_name,
            result_reason=getattr(cycle, 'result_reason', None),
            duration=getattr(cycle, 'duration', None),
            step_sequence=getattr(cycle, 'step_sequence', None),
            project_id=getattr(cycle, 'project_id', None),
        )
        result["steps"].append("build_context ok")
        result["context_keys"] = list(ctx.keys())
        result["steps_count"] = len(ctx.get("steps", []))
        result["ng_steps_count"] = len(ctx.get("ng_steps", []))

        if not box_serial:
            result["cluster_skipped"] = "no_box_serial"
            return {"success": True, **result}

        collector = get_cluster_collector()
        config = collector.get_config()
        if config["role"] not in ("master", "standalone"):
            result["cluster_skipped"] = f"role={config['role']}"
            return {"success": True, **result}

        station_id = config["station_id"]
        from backend.api.channel_manager import channel_manager
        if channel_manager.channel_count > 1:
            station_id = f"{station_id}-{channel_id}"
        result["station_id"] = station_id

        cluster_result = collector.receive_station_report(
            station_id=station_id,
            box_serial=box_serial,
            cycle_context=ctx,
            source_address=f"local:{channel_id}",
            channel_id=channel_id,
            is_good=bool(cycle.is_good),
            event_name=cycle.event_name,
        )
        result["cluster_result"] = cluster_result
        result["steps"].append("receive_station_report ok")
        return {"success": True, **result}
    except Exception as e:
        import traceback
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        return {"success": False, **result}
    finally:
        db.close()
