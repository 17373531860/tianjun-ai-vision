"""
集群模式 REST API

主从配置、数据上报、汇总状态查询。
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional

from backend.db.database import SessionLocal
from backend.models.mes_models import ClusterConfig, BoxAggregation, BoxSummary
from backend.services.cluster_collector import get_cluster_collector

router = APIRouter(prefix="/cluster", tags=["Cluster"])


# ---- Schemas ----

class ClusterConfigUpdate(BaseModel):
    role: Optional[str] = None
    master_url: Optional[str] = None
    station_id: Optional[str] = None
    expected_stations: Optional[list] = None
    sync_mode: Optional[str] = None
    timeout_sec: Optional[int] = None
    timeout_push: Optional[bool] = None
    enabled: Optional[bool] = None
    channel_station_map: Optional[dict] = None
    station_result_strategy: Optional[str] = None


class StationReport(BaseModel):
    station_id: str
    box_serial: str
    cycle_context: dict
    is_good: bool = True
    event_name: Optional[str] = None


class SlaveHeartbeat(BaseModel):
    station_id: str
    port: int = 8001
    hostname: str = ""
    project: str = ""
    channel_count: int = 1
    detecting: bool = False


# ---- 配置 ----

@router.get("/config")
def get_config():
    collector = get_cluster_collector()
    return collector.get_config()


@router.put("/config")
def update_config(body: ClusterConfigUpdate):
    db = SessionLocal()
    try:
        cfg = db.query(ClusterConfig).filter(ClusterConfig.id == 1).first()
        if not cfg:
            cfg = ClusterConfig(id=1)
            db.add(cfg)

        data = {k: v for k, v in body.model_dump().items() if v is not None}
        for k, v in data.items():
            setattr(cfg, k, v)
        db.commit()

        collector = get_cluster_collector()
        collector.invalidate_config_cache()

        return collector.get_config(db)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


# ---- 数据上报（从机 -> 主机） ----

@router.post("/report")
def receive_report(body: StationReport, request: Request):
    collector = get_cluster_collector()
    config = collector.get_config()

    if config["role"] not in ("master", "standalone"):
        raise HTTPException(400, "本机不是主机，不接受数据上报")

    source_addr = request.client.host if request.client else "unknown"
    result = collector.receive_station_report(
        station_id=body.station_id,
        box_serial=body.box_serial,
        cycle_context=body.cycle_context,
        source_address=source_addr,
        is_good=body.is_good,
        event_name=body.event_name,
    )

    if not result.get("success"):
        raise HTTPException(500, result.get("error", "未知错误"))
    return result


# ---- 汇总状态查询 ----

@router.get("/boxes")
def list_pending_boxes(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
):
    collector = get_cluster_collector()
    recent_data = collector.get_recent_summaries(limit=limit, skip=skip)
    return {
        "pending": collector.get_pending_boxes(),
        "recent": recent_data["items"],
        "recent_total": recent_data["total"],
    }


@router.get("/boxes/{box_serial}")
def get_box_detail(box_serial: str):
    db = SessionLocal()
    try:
        records = (
            db.query(BoxAggregation)
            .filter(BoxAggregation.box_serial == box_serial)
            .all()
        )
        summary = (
            db.query(BoxSummary)
            .filter(BoxSummary.box_serial == box_serial)
            .first()
        )
        return {
            "box_serial": box_serial,
            "stations": [{
                "station_id": r.station_id,
                "source": r.source_address,
                "channel_id": r.channel_id,
                "is_good": r.is_good,
                "event_name": r.event_name,
                "status": r.status,
                "received_at": r.received_at.isoformat() if r.received_at else None,
                "cycle_context": r.cycle_context or {},
            } for r in records],
            "summary": {
                "overall_result": summary.overall_result,
                "status": summary.status,
                "pushed_at": summary.pushed_at.isoformat() if summary.pushed_at else None,
            } if summary else None,
        }
    finally:
        db.close()


# ---- 副机心跳 ----

@router.post("/heartbeat")
def slave_heartbeat(body: SlaveHeartbeat, request: Request):
    """副机定时上报心跳，主机记录在线状态"""
    collector = get_cluster_collector()
    ip = request.client.host if request.client else "unknown"
    return collector.register_slave(
        station_id=body.station_id, ip=ip, port=body.port,
        hostname=body.hostname, project=body.project,
        channel_count=body.channel_count, detecting=body.detecting,
    )


@router.get("/slaves")
def list_connected_slaves():
    """查询当前在线的副机"""
    collector = get_cluster_collector()
    slaves = collector.get_connected_slaves()
    return {"slaves": slaves, "count": len(slaves)}


# ---- 删除记录 ----

@router.delete("/boxes/{box_serial}")
def delete_box(box_serial: str):
    """删除单个箱号的所有 BoxAggregation + BoxSummary 记录。
    用于前端集群汇总页"删除"按钮，清理测试/异常数据。
    """
    db = SessionLocal()
    try:
        n_agg = (db.query(BoxAggregation)
                 .filter(BoxAggregation.box_serial == box_serial)
                 .delete(synchronize_session=False))
        n_sum = (db.query(BoxSummary)
                 .filter(BoxSummary.box_serial == box_serial)
                 .delete(synchronize_session=False))
        db.commit()
        # 顺便释放进程内 box 锁
        try:
            get_cluster_collector()._release_box_lock(box_serial)
        except Exception:
            pass
        return {"deleted": True, "box_serial": box_serial,
                "aggregations": n_agg, "summaries": n_sum}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"删除失败: {e}")
    finally:
        db.close()


@router.delete("/boxes")
def clear_boxes(
    scope: str = Query("all", pattern="^(all|pending|recent|older)$"),
    before_days: int = Query(0, ge=0, le=3650,
                             description="仅在 scope=older 时生效，清除 N 天以前的记录"),
):
    """批量清空集群箱子记录。scope:
    - all: 清 BoxAggregation + BoxSummary 全表
    - pending: 只清 status in (received) 的 BoxAggregation（未 dispatch 的待汇总）
    - recent: 只清已 pushed/timeout 的 BoxSummary 及其对应 aggregation
    - older: 清 received_at 早于 before_days 天的记录（同时清两张表）
    """
    from datetime import datetime, timedelta
    db = SessionLocal()
    try:
        if scope == "all":
            n_agg = db.query(BoxAggregation).delete(synchronize_session=False)
            n_sum = db.query(BoxSummary).delete(synchronize_session=False)
        elif scope == "pending":
            pending_serials = [
                row[0] for row in
                db.query(BoxAggregation.box_serial)
                .filter(BoxAggregation.status == "received")
                .distinct().all()
            ]
            n_agg = (db.query(BoxAggregation)
                     .filter(BoxAggregation.box_serial.in_(pending_serials),
                             BoxAggregation.status == "received")
                     .delete(synchronize_session=False)) if pending_serials else 0
            n_sum = 0
        elif scope == "older":
            if before_days <= 0:
                raise HTTPException(400, "scope=older 必须指定 before_days > 0")
            cutoff = datetime.utcnow() - timedelta(days=before_days)
            # 找出要清的 box_serial 集合：received_at 早于 cutoff 的
            old_serials = [
                row[0] for row in
                db.query(BoxAggregation.box_serial)
                .filter(BoxAggregation.received_at < cutoff)
                .distinct().all()
            ]
            n_agg = (db.query(BoxAggregation)
                     .filter(BoxAggregation.box_serial.in_(old_serials))
                     .delete(synchronize_session=False)) if old_serials else 0
            n_sum = (db.query(BoxSummary)
                     .filter(BoxSummary.box_serial.in_(old_serials))
                     .delete(synchronize_session=False)) if old_serials else 0
        else:  # recent
            recent_serials = [
                row[0] for row in
                db.query(BoxSummary.box_serial)
                .filter(BoxSummary.status.in_(
                    ["pushed", "pushed_timeout", "timeout"]))
                .all()
            ]
            n_sum = (db.query(BoxSummary)
                     .filter(BoxSummary.box_serial.in_(recent_serials))
                     .delete(synchronize_session=False)) if recent_serials else 0
            n_agg = (db.query(BoxAggregation)
                     .filter(BoxAggregation.box_serial.in_(recent_serials))
                     .delete(synchronize_session=False)) if recent_serials else 0
        db.commit()
        return {"deleted": True, "scope": scope,
                "aggregations": n_agg, "summaries": n_sum}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"清空失败: {e}")
    finally:
        db.close()


# ---- 健康检查 ----

@router.get("/health")
def cluster_health():
    collector = get_cluster_collector()
    config = collector.get_config()
    return {
        "status": "ok",
        "role": config["role"],
        "station_id": config["station_id"],
        "enabled": config["enabled"],
    }
