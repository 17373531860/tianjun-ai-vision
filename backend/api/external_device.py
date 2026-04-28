"""
外部设备管理 REST API

设备 CRUD、连接测试、状态查询、数据日志、条码注入。
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from backend.db.database import SessionLocal
from backend.models.mes_models import ExternalDevice, ExternalDeviceLog
from backend.services.external_device import get_external_device_service

logger = logging.getLogger(__name__)

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
    pairing_group: Optional[str] = None
    data_target: str = "cluster"
    validation_rules: Optional[dict] = None
    enabled: bool = True
    # v2.7.5 稳定值判定
    stable_enabled: Optional[bool] = True
    stable_delta: Optional[float] = 0.05
    stable_count: Optional[int] = 5
    zero_threshold: Optional[float] = 0.05
    # v2.7.5 有重无码告警
    weight_no_barcode_alarm_enabled: Optional[bool] = False
    weight_no_barcode_alarm_delay_sec: Optional[int] = 10
    # v3.1.1 配对模式: stable (默认, 等稳定值) / instant (扫码瞬间立即绑最近读数)
    pairing_mode: Optional[str] = "stable"


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
    pairing_group: Optional[str] = None
    data_target: Optional[str] = None
    validation_rules: Optional[dict] = None
    enabled: Optional[bool] = None
    stable_enabled: Optional[bool] = None
    stable_delta: Optional[float] = None
    stable_count: Optional[int] = None
    zero_threshold: Optional[float] = None
    weight_no_barcode_alarm_enabled: Optional[bool] = None
    weight_no_barcode_alarm_delay_sec: Optional[int] = None
    pairing_mode: Optional[str] = None


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


class SimulateData(BaseModel):
    raw_data: str
    device_id: Optional[int] = None
    barcode: Optional[str] = None
    full_chain: bool = False
    repeat_count: int = 1
    channel_id: int = 0


def _serialize(d):
    return {
        "id": d.id, "name": d.name,
        "device_role": d.device_role, "protocol": d.protocol,
        "ip": d.ip, "port": d.port,
        "serial_port": d.serial_port, "serial_baud": d.serial_baud,
        "protocol_config": d.protocol_config,
        "parse_mode": d.parse_mode, "parse_config": d.parse_config,
        "station_id": d.station_id, "channel_id": d.channel_id,
        "pairing_group": getattr(d, "pairing_group", None),
        "data_target": d.data_target,
        "validation_rules": d.validation_rules,
        "enabled": d.enabled,
        "stable_enabled": bool(getattr(d, "stable_enabled", True)),
        "stable_delta": float(getattr(d, "stable_delta", 0.05) or 0.05),
        "stable_count": int(getattr(d, "stable_count", 5) or 5),
        "zero_threshold": float(getattr(d, "zero_threshold", 0.05) or 0.05),
        "weight_no_barcode_alarm_enabled": bool(getattr(d, "weight_no_barcode_alarm_enabled", False)),
        "weight_no_barcode_alarm_delay_sec": int(getattr(d, "weight_no_barcode_alarm_delay_sec", 10) or 10),
        "pairing_mode": str(getattr(d, "pairing_mode", "stable") or "stable").lower(),
    }


@router.get("/")
def list_devices():
    db = SessionLocal()
    try:
        devices = db.query(ExternalDevice).all()
        return [_serialize(d) for d in devices]
    finally:
        db.close()


def _sanitize_device_payload(d: dict) -> dict:
    """去掉字符串字段首尾空格，避免 ' /dev/ttyUSB0' 这种肉眼看不见的坑。"""
    for k in ("name", "serial_port", "ip", "station_id"):
        v = d.get(k)
        if isinstance(v, str):
            d[k] = v.strip()
    return d


@router.post("/")
def create_device(body: DeviceCreate):
    db = SessionLocal()
    try:
        payload = _sanitize_device_payload(body.model_dump())
        dev = ExternalDevice(**payload)
        db.add(dev)
        db.commit()
        db.refresh(dev)
    except Exception as e:
        db.rollback()
        db.close()
        raise HTTPException(400, f"保存失败: {type(e).__name__}: {e}")

    warning = None
    if dev.enabled:
        try:
            svc = get_external_device_service()
            svc.add_device(dev)
        except Exception as e:
            logger.warning("[ExtDev] 设备已保存但连接失败 id=%s: %s", dev.id, e)
            warning = f"连接失败（可稍后在设备卡片上重试）: {e}"

    try:
        payload = _serialize(dev)
    finally:
        db.close()
    if warning:
        payload["warning"] = warning
    return payload


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


@router.post("/simulate")
def simulate_data(body: SimulateData):
    """调试用：模拟外部设备原始数据，不需要真实称重器/传感器硬件。"""
    raw = (body.raw_data or "").strip()
    if not raw:
        raise HTTPException(400, "raw_data 不能为空")
    svc = get_external_device_service()
    return svc.simulate_raw_data(
        raw=raw,
        device_id=body.device_id,
        barcode=(body.barcode or "").strip() or None,
        full_chain=body.full_chain,
        repeat_count=body.repeat_count,
        channel_id=body.channel_id,
    )




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
def clear_logs(device_id: Optional[int] = None):
    """清空外部设备数据日志

    - device_id=None: 清空所有设备的日志
    - device_id=N:    只清空指定设备的日志

    - synchronize_session=False 避免 SQLAlchemy ↔ SQLite 并发写锁冲突
    - 若 ORM 删除失败，回退到 raw SQL 再试一次（不依赖 ORM session 状态）
    - OperationalError(database is locked) 做 3 次重试，间隔 0.3s
    """
    import time
    from sqlalchemy.exc import OperationalError

    last_err: Optional[Exception] = None
    for attempt in range(3):
        db = SessionLocal()
        try:
            q = db.query(ExternalDeviceLog)
            if device_id is not None:
                q = q.filter(ExternalDeviceLog.device_id == device_id)
            count = q.count()
            q.delete(synchronize_session=False)
            db.commit()
            logger.info("[ExtDev] 清空数据日志: deleted=%d, device_id=%s, attempt=%d",
                        count, device_id, attempt + 1)
            return {"deleted": count}
        except OperationalError as e:
            db.rollback()
            last_err = e
            logger.warning("[ExtDev] 清空数据日志遇到锁/IO错误，第 %d 次重试: %s", attempt + 1, e)
            time.sleep(0.3)
        except Exception as e:
            db.rollback()
            last_err = e
            logger.exception("[ExtDev] ORM 删除失败，尝试 raw SQL 兜底: %s", e)
            try:
                if device_id is None:
                    db.execute("DELETE FROM external_device_logs")
                else:
                    db.execute(
                        "DELETE FROM external_device_logs WHERE device_id = :did",
                        {"did": device_id},
                    )
                db.commit()
                return {"deleted": -1, "note": "raw sql fallback"}
            except Exception as e2:
                db.rollback()
                last_err = e2
                logger.exception("[ExtDev] raw SQL 兜底也失败: %s", e2)
                break
        finally:
            db.close()

    raise HTTPException(500, f"清空失败: {type(last_err).__name__ if last_err else 'Unknown'}: {last_err}")


# ============================================================
# 动态路径必须放在所有静态路径之后，否则会抢先匹配
# 例如 DELETE /logs 会被 DELETE /{device_id} 误吞（device_id="logs" 解析失败 422）
# ============================================================

@router.put("/{device_id}")
def update_device(device_id: int, body: DeviceUpdate):
    db = SessionLocal()
    try:
        dev = db.query(ExternalDevice).filter(ExternalDevice.id == device_id).first()
        if not dev:
            db.close()
            raise HTTPException(404, "设备不存在")
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        data = _sanitize_device_payload(data)
        for k, v in data.items():
            setattr(dev, k, v)
        db.commit()
        db.refresh(dev)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        db.close()
        raise HTTPException(400, f"保存失败: {type(e).__name__}: {e}")

    warning = None
    try:
        svc = get_external_device_service()
        svc.remove_device(device_id)
        if dev.enabled:
            svc.add_device(dev)
    except Exception as e:
        logger.warning("[ExtDev] 设备已更新但连接失败 id=%s: %s", dev.id, e)
        warning = f"连接失败（可稍后在设备卡片上重试）: {e}"

    try:
        payload = _serialize(dev)
    finally:
        db.close()
    if warning:
        payload["warning"] = warning
    return payload


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
