"""
扫码器管理 REST API

设备 CRUD、连接测试、状态查询、扫码记录。
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from backend.db.database import SessionLocal
from backend.models.mes_models import ScannerDevice, ScanLog
from backend.services.scanner import get_scanner_service

router = APIRouter(prefix="/scanner", tags=["Scanner"])


class ScannerCreate(BaseModel):
    name: str
    ip: str
    port: int = 55256
    channel_id: Optional[int] = None
    enabled: bool = True
    parse_mode: str = "direct"
    parse_config: Optional[dict] = None
    dedup_interval_sec: int = 2
    auto_create_workpiece: bool = True
    auto_link_order: bool = True
    scan_required: bool = False
    duplicate_scan_action: str = "overwrite"
    warn_no_barcode: bool = False
    rebind_mode: str = "rescan"
    bind_timing: str = "mid_cycle"


class ScannerUpdate(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = None
    channel_id: Optional[int] = None
    enabled: Optional[bool] = None
    parse_mode: Optional[str] = None
    parse_config: Optional[dict] = None
    dedup_interval_sec: Optional[int] = None
    auto_create_workpiece: Optional[bool] = None
    auto_link_order: Optional[bool] = None
    scan_required: Optional[bool] = None
    duplicate_scan_action: Optional[str] = None
    warn_no_barcode: Optional[bool] = None
    rebind_mode: Optional[str] = None
    bind_timing: Optional[str] = None


def _serialize_device(d):
    return {
        "id": d.id, "name": d.name, "ip": d.ip, "port": d.port,
        "channel_id": d.channel_id, "enabled": d.enabled,
        "parse_mode": d.parse_mode, "parse_config": d.parse_config,
        "dedup_interval_sec": d.dedup_interval_sec,
        "auto_create_workpiece": d.auto_create_workpiece,
        "auto_link_order": d.auto_link_order,
        "scan_required": getattr(d, 'scan_required', False),
        "duplicate_scan_action": getattr(d, 'duplicate_scan_action', 'overwrite'),
        "warn_no_barcode": getattr(d, 'warn_no_barcode', False),
        "rebind_mode": getattr(d, 'rebind_mode', 'rescan'),
        "bind_timing": getattr(d, 'bind_timing', 'mid_cycle'),
    }


@router.get("/devices")
def list_devices():
    db = SessionLocal()
    try:
        devices = db.query(ScannerDevice).all()
        return [_serialize_device(d) for d in devices]
    finally:
        db.close()


@router.post("/devices")
def create_device(body: ScannerCreate):
    db = SessionLocal()
    try:
        dev = ScannerDevice(**body.model_dump())
        db.add(dev)
        db.commit()
        db.refresh(dev)

        if dev.enabled:
            svc = get_scanner_service()
            svc.add_device(dev)

        return _serialize_device(dev)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.put("/devices/{device_id}")
def update_device(device_id: int, body: ScannerUpdate):
    db = SessionLocal()
    try:
        dev = db.query(ScannerDevice).filter(ScannerDevice.id == device_id).first()
        if not dev:
            raise HTTPException(404, "设备不存在")

        data = {k: v for k, v in body.model_dump().items() if v is not None}
        for k, v in data.items():
            setattr(dev, k, v)
        db.commit()
        db.refresh(dev)

        svc = get_scanner_service()
        svc.remove_device(device_id)
        if dev.enabled:
            svc.add_device(dev)

        return _serialize_device(dev)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.delete("/devices/{device_id}")
def delete_device(device_id: int):
    db = SessionLocal()
    try:
        dev = db.query(ScannerDevice).filter(ScannerDevice.id == device_id).first()
        if not dev:
            raise HTTPException(404, "设备不存在")

        svc = get_scanner_service()
        svc.remove_device(device_id)

        db.delete(dev)
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.post("/devices/test")
def test_connection(ip: str, port: int = 55256):
    svc = get_scanner_service()
    return svc.test_connection(ip, port)


@router.post("/trigger")
def trigger_scan(device_id: Optional[int] = None, ip: Optional[str] = None):
    """手动触发一次扫码（向 55256 端口发送 LON）"""
    svc = get_scanner_service()
    if device_id is not None:
        return svc.trigger_scan(device_id)
    elif ip:
        return svc.trigger_scan_by_ip(ip)
    else:
        raise HTTPException(400, "需要提供 device_id 或 ip")


@router.get("/status")
def get_all_status():
    svc = get_scanner_service()
    return svc.get_all_status()


@router.get("/latest/{channel_id}")
def get_latest_scan(channel_id: int):
    svc = get_scanner_service()
    result = svc.get_latest_scan(channel_id)
    if not result:
        return {"serial_no": None}
    return result


@router.get("/logs")
def list_scan_logs(
    device_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = SessionLocal()
    try:
        q = db.query(ScanLog)
        if device_id:
            q = q.filter(ScanLog.device_id == device_id)
        if channel_id:
            q = q.filter(ScanLog.channel_id == channel_id)
        if date_from:
            q = q.filter(ScanLog.created_at >= date_from)
        if date_to:
            q = q.filter(ScanLog.created_at <= date_to)

        total = q.count()
        items = q.order_by(ScanLog.created_at.desc()).offset(skip).limit(limit).all()
        return {
            "items": [{
                "id": s.id, "device_id": s.device_id, "channel_id": s.channel_id,
                "raw_data": s.raw_data, "parsed_serial": s.parsed_serial,
                "parsed_order": s.parsed_order, "parsed_batch": s.parsed_batch,
                "workpiece_id": s.workpiece_id, "success": s.success,
                "error_msg": s.error_msg,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            } for s in items],
            "total": total, "skip": skip, "limit": limit,
        }
    finally:
        db.close()


@router.delete("/logs")
def clear_scan_logs():
    """清空所有扫码记录"""
    db = SessionLocal()
    try:
        count = db.query(ScanLog).count()
        db.query(ScanLog).delete()
        db.commit()
        return {"deleted": count}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))
    finally:
        db.close()
