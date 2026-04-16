"""
外部设备管理 REST API

设备 CRUD、连接测试、状态查询、数据日志、条码注入。
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from backend.db.database import SessionLocal
from backend.models.mes_models import ExternalDevice, ExternalDeviceLog
from backend.services.external_device import get_external_device_service

router = APIRouter(prefix="/external-devices", tags=["External Devices"])


class DeviceCreate(BaseModel):
    name: str
    device_role: str = "weight"
    protocol: str = "tcp"
    ip: Optional[str] = None
    port: Optional[int] = None
    serial_port: Optional[str] = None
    serial_baud: Optional[int] = 9600
    protocol_config: Optional[dict] = None
    parse_mode: str = "direct"
    parse_config: Optional[dict] = None
    station_id: Optional[str] = None
    channel_id: Optional[int] = None
    data_target: str = "cluster"
    validation_rules: Optional[dict] = None
    enabled: bool = True


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    device_role: Optional[str] = None
    protocol: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = None
    serial_port: Optional[str] = None
    serial_baud: Optional[int] = None
    protocol_config: Optional[dict] = None
    parse_mode: Optional[str] = None
    parse_config: Optional[dict] = None
    station_id: Optional[str] = None
    channel_id: Optional[int] = None
    data_target: Optional[str] = None
    validation_rules: Optional[dict] = None
    enabled: Optional[bool] = None


class TestRequest(BaseModel):
    protocol: str = "tcp"
    ip: Optional[str] = None
    port: Optional[int] = None
    serial_port: Optional[str] = None
    serial_baud: Optional[int] = 9600
    protocol_config: Optional[dict] = None


class BarcodeInject(BaseModel):
    device_id: int
    barcode: str


def _serialize(d):
    return {
        "id": d.id, "name": d.name,
        "device_role": d.device_role, "protocol": d.protocol,
        "ip": d.ip, "port": d.port,
        "serial_port": d.serial_port, "serial_baud": d.serial_baud,
        "protocol_config": d.protocol_config,
        "parse_mode": d.parse_mode, "parse_config": d.parse_config,
        "station_id": d.station_id, "channel_id": d.channel_id,
        "data_target": d.data_target,
        "validation_rules": d.validation_rules,
        "enabled": d.enabled,
    }


@router.get("/")
def list_devices():
    db = SessionLocal()
    try:
        devices = db.query(ExternalDevice).all()
        return [_serialize(d) for d in devices]
    finally:
        db.close()


@router.post("/")
def create_device(body: DeviceCreate):
    db = SessionLocal()
    try:
        dev = ExternalDevice(**body.model_dump())
        db.add(dev)
        db.commit()
        db.refresh(dev)
        if dev.enabled:
            svc = get_external_device_service()
            svc.add_device(dev)
        return _serialize(dev)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.put("/{device_id}")
def update_device(device_id: int, body: DeviceUpdate):
    db = SessionLocal()
    try:
        dev = db.query(ExternalDevice).filter(ExternalDevice.id == device_id).first()
        if not dev:
            raise HTTPException(404, "设备不存在")
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        for k, v in data.items():
            setattr(dev, k, v)
        db.commit()
        db.refresh(dev)

        svc = get_external_device_service()
        svc.remove_device(device_id)
        if dev.enabled:
            svc.add_device(dev)
        return _serialize(dev)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.delete("/{device_id}")
def delete_device(device_id: int):
    db = SessionLocal()
    try:
        dev = db.query(ExternalDevice).filter(ExternalDevice.id == device_id).first()
        if not dev:
            raise HTTPException(404, "设备不存在")
        svc = get_external_device_service()
        svc.remove_device(device_id)
        db.delete(dev)
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.get("/status")
def get_status():
    svc = get_external_device_service()
    return svc.get_all_status()


@router.post("/test")
def test_connection(body: TestRequest):
    svc = get_external_device_service()
    return svc.test_connection(
        protocol=body.protocol,
        ip=body.ip,
        port=body.port,
        serial_port=body.serial_port,
        serial_baud=body.serial_baud or 9600,
        protocol_config=body.protocol_config,
    )


@router.post("/barcode")
def inject_barcode(body: BarcodeInject):
    """为没有自带扫码器的设备注入条码"""
    svc = get_external_device_service()
    svc.set_barcode(body.device_id, body.barcode)
    return {"success": True, "device_id": body.device_id, "barcode": body.barcode}




@router.get("/logs")
def list_logs(
    device_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = SessionLocal()
    try:
        q = db.query(ExternalDeviceLog)
        if device_id:
            q = q.filter(ExternalDeviceLog.device_id == device_id)
        total = q.count()
        items = q.order_by(ExternalDeviceLog.created_at.desc()).offset(skip).limit(limit).all()
        return {
            "items": [{
                "id": l.id, "device_id": l.device_id,
                "raw_data": l.raw_data,
                "parsed_data": l.parsed_data,
                "box_serial": l.box_serial,
                "is_valid": l.is_valid,
                "error_msg": l.error_msg,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            } for l in items],
            "total": total,
        }
    finally:
        db.close()


@router.delete("/logs")
def clear_logs():
    db = SessionLocal()
    try:
        count = db.query(ExternalDeviceLog).count()
        db.query(ExternalDeviceLog).delete()
        db.commit()
        return {"deleted": count}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))
    finally:
        db.close()
