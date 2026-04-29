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
    broadcast_channels: Optional[list] = None
    device_type: str = "text_lon"
    external_only: bool = False
    pairing_group: Optional[str] = None
    ok_rescan_cooldown_sec: int = 0
    late_scan_bind_window_sec: int = 3
    scan_mode: str = "continuous"
    throttle_idle_ms: int = 500
    broadcast_settle_mode: str = "independent"
    primary_settle_channel: Optional[int] = None
    primary_settle_min_items: int = 1


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
    broadcast_channels: Optional[list] = None
    device_type: Optional[str] = None
    external_only: Optional[bool] = None
    pairing_group: Optional[str] = None
    ok_rescan_cooldown_sec: Optional[int] = None
    late_scan_bind_window_sec: Optional[int] = None
    scan_mode: Optional[str] = None
    throttle_idle_ms: Optional[int] = None
    broadcast_settle_mode: Optional[str] = None
    primary_settle_channel: Optional[int] = None
    primary_settle_min_items: Optional[int] = None


class ScannerSimulate(BaseModel):
    barcode: str
    device_id: Optional[int] = None
    channel_id: int = 0
    external_only: bool = False
    pairing_group: Optional[str] = None


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
        "broadcast_channels": getattr(d, 'broadcast_channels', None) or [],
        "device_type": getattr(d, 'device_type', 'auto') or 'auto',
        "external_only": bool(getattr(d, 'external_only', False)),
        "pairing_group": getattr(d, 'pairing_group', None),
        "ok_rescan_cooldown_sec": int(getattr(d, 'ok_rescan_cooldown_sec', 0) or 0),
        "late_scan_bind_window_sec": int(getattr(d, 'late_scan_bind_window_sec', 3) or 0),
        "scan_mode": getattr(d, 'scan_mode', 'continuous') or 'continuous',
        "throttle_idle_ms": int(getattr(d, 'throttle_idle_ms', 500) or 500),
        "broadcast_settle_mode": getattr(d, 'broadcast_settle_mode', 'independent') or 'independent',
        "primary_settle_channel": getattr(d, 'primary_settle_channel', None),
        "primary_settle_min_items": int(getattr(d, 'primary_settle_min_items', 1) or 1),
    }


@router.get("/devices")
def list_devices():
    db = SessionLocal()
    try:
        devices = db.query(ScannerDevice).all()
        return [_serialize_device(d) for d in devices]
    finally:
        db.close()


@router.post("/discover")
def discover_scanners(subnet: str = Query("192.168.0", description="子网前缀"),
                      port: int = Query(55256), timeout: float = Query(0.3)):
    """TCP 端口扫描发现局域网内的扫码器（不走 WMax 协议，不会触发闪光）"""
    import socket
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _probe(ip):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((ip, port))
            s.close()
            return ip
        except (socket.timeout, ConnectionRefusedError, OSError):
            return None

    found = []
    ips = [f"{subnet}.{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = {pool.submit(_probe, ip): ip for ip in ips}
        for f in as_completed(futures):
            result = f.result()
            if result:
                found.append({"ip": result, "port": port, "name": f"扫码器-{result}"})

    return {"results": found, "total": len(found)}


@router.post("/devices")
def create_device(body: ScannerCreate):
    db = SessionLocal()
    try:
        # v2.7.3: 同 IP+port 去重，避免一个物理扫码器在列表中出现多条
        # （现场常见：自动发现保存了一次，又点"保存到设备列表"再保存一次）
        existing = db.query(ScannerDevice).filter(
            ScannerDevice.ip == body.ip,
            ScannerDevice.port == body.port,
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"该地址 {body.ip}:{body.port} 已被设备 "
                    f"\"{existing.name}\" (ID={existing.id}) 占用，"
                    f"请改用其他地址，或编辑/删除已有设备"
                ),
            )

        dev = ScannerDevice(**body.model_dump())
        db.add(dev)
        db.commit()
        db.refresh(dev)

        if dev.enabled:
            svc = get_scanner_service()
            svc.add_device(dev)

        return _serialize_device(dev)
    except HTTPException:
        raise
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

        # v2.7.3: 改 IP/port 时检查目标地址是否已被其他设备占用
        new_ip = data.get('ip', dev.ip)
        new_port = data.get('port', dev.port)
        if (new_ip, new_port) != (dev.ip, dev.port):
            conflict = db.query(ScannerDevice).filter(
                ScannerDevice.ip == new_ip,
                ScannerDevice.port == new_port,
                ScannerDevice.id != device_id,
            ).first()
            if conflict:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"该地址 {new_ip}:{new_port} 已被设备 "
                        f"\"{conflict.name}\" (ID={conflict.id}) 占用"
                    ),
                )

        for k, v in data.items():
            setattr(dev, k, v)
        db.commit()
        db.refresh(dev)

        svc = get_scanner_service()
        svc.remove_device(device_id)
        if dev.enabled:
            svc.add_device(dev)

        return _serialize_device(dev)
    except HTTPException:
        raise
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
def test_connection(ip: str, port: int = 55256, device_type: str = "auto"):
    """测试扫码器连接.

    device_type:
      - 'text_lon': 只走 LON/LOFF 文本路径 (省电模式, 不打开 WMax 视频流)
      - 'auto'/'wmax': 优先 WMax 三端口, 失败降级 text_lon
    """
    svc = get_scanner_service()
    return svc.test_connection(ip, port, device_type=device_type)


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


@router.post("/simulate")
def simulate_scan(body: ScannerSimulate):
    """调试用：模拟扫码结果，不需要真实扫码器硬件。"""
    barcode = (body.barcode or "").strip()
    if not barcode:
        raise HTTPException(400, "barcode 不能为空")
    svc = get_scanner_service()
    result = svc.simulate_scan(
        barcode=barcode,
        device_id=body.device_id,
        channel_id=body.channel_id,
        external_only=body.external_only,
        pairing_group=body.pairing_group,
    )

    # 真实扫码日志通常在 MES Hook 中异步补齐。模拟入口额外落一条可见日志，
    # 让前端点"刷新"能立刻看到按钮确实生效。
    db = SessionLocal()
    try:
        log = ScanLog(
            device_id=body.device_id,
            channel_id=body.channel_id,
            raw_data=barcode,
            parsed_serial=result.get("serial_no"),
            success=result.get("success", True),
            error_msg="AUTO_QA_SIMULATED",
        )
        db.add(log)
        db.commit()
    finally:
        db.close()
    return result


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
