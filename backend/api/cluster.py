"""
集群模式 REST API

主从配置、数据上报、箱子汇总状态查询。
"""
from fastapi import APIRouter, HTTPException, Request
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
    enabled: Optional[bool] = None


class StationReport(BaseModel):
    station_id: str
    box_serial: str
    cycle_context: dict
    is_good: bool = True
    event_name: Optional[str] = None


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


# ---- 箱子状态查询 ----

@router.get("/boxes")
def list_pending_boxes():
    collector = get_cluster_collector()
    return {
        "pending": collector.get_pending_boxes(),
        "recent": collector.get_recent_summaries(limit=20),
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
            } for r in records],
            "summary": {
                "overall_result": summary.overall_result,
                "status": summary.status,
                "pushed_at": summary.pushed_at.isoformat() if summary.pushed_at else None,
            } if summary else None,
        }
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
