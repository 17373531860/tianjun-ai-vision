"""诊断 / 调试接口.

历史: 这两个接口原本写在 backend/hotfix.py 里通过 monkey patch 注册,
但它们本质上是新增的正式诊断 API, 不是"补丁", 应该作为常规路由存在.
v2.7.10 清理时把它们挪到这里独立成模块.

接口列表:
  GET  /debug/channels             — 看每个通道的 source_type / 运行状态
  POST /debug/test_cluster_flow    — 用真实 cycle_id 端到端回放 mes_gateway → cluster
"""
from fastapi import APIRouter, Query

router = APIRouter()


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
