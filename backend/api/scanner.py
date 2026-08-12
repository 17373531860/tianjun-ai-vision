"""
扫码器管理 REST API

设备 CRUD、连接测试、状态查询、扫码记录。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from backend.core.auth_deps import require_perm
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
    scan_pair_max_wait_sec: int = 0
    scan_d_geometry: str = "line"
    scan_d_line: Optional[dict] = None
    scan_d_zone: Optional[list] = None
    scan_d_gone_confirm_frames: int = 30


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
    scan_pair_max_wait_sec: Optional[int] = None
    scan_d_geometry: Optional[str] = None
    scan_d_line: Optional[dict] = None
    scan_d_zone: Optional[list] = None
    scan_d_gone_confirm_frames: Optional[int] = None


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
        "scan_pair_max_wait_sec": int(getattr(d, 'scan_pair_max_wait_sec', 0) or 0),
        "scan_d_geometry": getattr(d, 'scan_d_geometry', 'line') or 'line',
        "scan_d_line": getattr(d, 'scan_d_line', None),
        "scan_d_zone": getattr(d, 'scan_d_zone', None),
        "scan_d_gone_confirm_frames": int(getattr(d, 'scan_d_gone_confirm_frames', 30) or 30),
    }


@router.get("/devices")
def list_devices():
    db = SessionLocal()
    try:
        devices = db.query(ScannerDevice).all()
        return [_serialize_device(d) for d in devices]
    finally:
        db.close()


@router.post("/discover",
              dependencies=[Depends(require_perm("mes.scanner.edit"))])
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


@router.post("/devices",
              dependencies=[Depends(require_perm("mes.scanner.edit"))])
def create_device(body: ScannerCreate):
    db = SessionLocal()
    try:
        # v2.7.3: 同 IP+port 去重，避免一个物理扫码器在列表中出现多条
        # （现场常见：自动发现保存了一次，又点"保存到设备列表"再保存一次）
        # USB 键盘扫码枪 (usb_hid) 无网络地址 (ip=''/port=0), 不参与地址唯一校验,
        # 否则多把 USB 枪会互相撞 ":0"。
        if body.device_type != 'usb_hid':
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


@router.put("/devices/{device_id}",
             dependencies=[Depends(require_perm("mes.scanner.edit"))])
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
        if dev.device_type != 'usb_hid' and (new_ip, new_port) != (dev.ip, dev.port):
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


@router.delete("/devices/{device_id}",
                dependencies=[Depends(require_perm("mes.scanner.edit"))])
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


@router.post("/devices/test",
              dependencies=[Depends(require_perm("mes.scanner.edit"))])
def test_connection(ip: str, port: int = 55256, device_type: str = "auto"):
    """测试扫码器连接.

    device_type:
      - 'text_lon': 只走 LON/LOFF 文本路径 (省电模式, 不打开 WMax 视频流)
      - 'auto'/'wmax': 优先 WMax 三端口, 失败降级 text_lon
    """
    svc = get_scanner_service()
    return svc.test_connection(ip, port, device_type=device_type)


@router.post("/trigger",
              dependencies=[Depends(require_perm("mes.scanner.edit"))])
def trigger_scan(device_id: Optional[int] = None, ip: Optional[str] = None):
    """手动触发一次扫码（向 55256 端口发送 LON）"""
    svc = get_scanner_service()
    if device_id is not None:
        return svc.trigger_scan(device_id)
    elif ip:
        return svc.trigger_scan_by_ip(ip)
    else:
        raise HTTPException(400, "需要提供 device_id 或 ip")


@router.post("/simulate",
              dependencies=[Depends(require_perm("mes.scanner.edit"))])
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


@router.delete("/logs",
                dependencies=[Depends(require_perm("mes.scanner.edit"))])
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


# ============== v3.4.0 D 容器跨线触发: 工位项目模式校验 ==============

@router.get("/check-container-mode")
def check_container_mode(channel_id: int = Query(0)):
    """v3.4.0 前端切到 scan_mode='D' 时调本接口校验绑定工位是否容器模式项目.
    不是 → 前端弹警告 + 自动回退到 A.
    """
    try:
        from backend.api.channel_manager import channel_manager
        mgr = channel_manager.get(channel_id)
    except Exception as e:
        return {
            "channel_id": channel_id,
            "is_container_mode": False,
            "reason": f"channel_not_found: {e}",
        }
    if mgr is None or getattr(mgr, 'project_config', None) is None:
        return {
            "channel_id": channel_id,
            "is_container_mode": False,
            "reason": "no_project_assigned",
        }
    pc = mgr.project_config or {}
    logic_mode = pc.get('logic_mode', '')
    pipeline = pc.get('pipeline_config', {}) or {}
    container_label = pipeline.get('tracking_container_label', '') or ''
    is_container = (logic_mode == 'tracking') and bool(container_label)
    return {
        "channel_id": channel_id,
        "is_container_mode": is_container,
        "logic_mode": logic_mode,
        "container_label": container_label,
        "project_id": pc.get('id'),
        "project_name": pc.get('name', ''),
    }


# ============== v3.3.0 码-码闭环结算 状态查询/操作 ==============

@router.get("/scan-pair/active")
def get_scan_pair_active(channel_id: int = Query(0)):
    """v3.3.0 查询某工位 scan_pair 模式下当前窗口的开始码 (None 表示未开窗口).

    前端 Monitor 页定时拉取 → 决定停止/待机时是否要弹"丢弃/结算"提示框.
    """
    try:
        from backend.services.mes_hooks import get_mes_hook
        mes = get_mes_hook()
        return {
            "channel_id": channel_id,
            "is_scan_pair_mode": bool(mes.is_scan_pair_mode(channel_id)) if mes else False,
            "active_serial": mes.get_scan_pair_active_serial(channel_id) if mes else None,
        }
    except Exception as e:
        return {
            "channel_id": channel_id,
            "is_scan_pair_mode": False,
            "active_serial": None,
            "error": str(e),
        }


class ScanPairStopRequest(BaseModel):
    channel_id: int = 0
    discard: bool = False  # True=丢弃当前窗口, False=按曾齐过结算后清空


@router.post("/scan-pair/stop",
              dependencies=[Depends(require_perm("mes.scanner.edit"))])
def settle_scan_pair_for_stop(req: ScanPairStopRequest):
    """v3.3.0 在停止检测 / 进入待机时由前端调用, 收尾最后一码窗口.

    前端流程: 用户点击 '停止检测'/'待机' → 先调 GET /scan-pair/active 查到
    active_serial 非空 → 弹弹窗 [丢弃] [结算] (默认结算) → 调本接口 → 真正停止.
    """
    try:
        from backend.services.mes_hooks import get_mes_hook
        mes = get_mes_hook()
        if mes is None:
            return {"settled_count": 0, "discarded": req.discard}
        n = mes.settle_scan_pair_for_stop(req.channel_id, discard=req.discard)
        return {"settled_count": int(n), "discarded": req.discard}
    except Exception as e:
        raise HTTPException(500, f"settle_scan_pair_for_stop failed: {e}")


# ==================== v3.49 WS3: scan_pair 新码先上屏开关 (默认开) ====================
# 开(默认) → 扫码 B 到达先开新窗 + promote 上屏, 再结算上一窗口 (结算身份显式钳制);
# 显式关 → 旧序 (先结算后上屏), 排查回退用。存 SystemConfig(scan_pair_new_code_first)。

class ScanPairNewFirstToggle(BaseModel):
    enabled: bool = True


@router.get("/scan-pair/new-code-first", summary="读取 scan_pair 新码先上屏开关")
def get_scan_pair_new_first():
    """返回 scan_pair 配对模式「新码先上屏」开关当前值（默认开）。"""
    from backend.services.mes_hooks import get_mes_hook
    return {"enabled": get_mes_hook().get_scan_pair_new_first()}


@router.put("/scan-pair/new-code-first", summary="设置 scan_pair 新码先上屏开关",
            dependencies=[Depends(require_perm("settings.edit"))])
def set_scan_pair_new_first(body: ScanPairNewFirstToggle):
    """开=新码到达先顶替上屏、旧窗口用显式身份异步结算；关=回退旧序（先结算再上屏）。

    存 SystemConfig(scan_pair_new_code_first)，即时生效无需重启。
    """
    from backend.services.mes_hooks import get_mes_hook
    hook = get_mes_hook()
    hook.set_scan_pair_new_first(body.enabled)
    return {"status": "success", "enabled": hook.get_scan_pair_new_first()}


# ==================== v3.4.2 "禁用扫码"按工位开关 ====================

class ScannerDisableToggleRequest(BaseModel):
    channel_id: int
    disabled: bool


@router.get("/disable-status")
def get_scanner_disable_status():
    """前端 Pinia store 启动时拉一次 + 后续切换后再拉一次, 返回当前所有
    被禁用扫码的工位列表 (含联动闭包).
    """
    try:
        from backend.services.mes_hooks import get_mes_hook
        mes = get_mes_hook()
        if mes is None:
            return {"disabled_channels": []}
        return {"disabled_channels": mes.get_disabled_channels()}
    except Exception as e:
        return {"disabled_channels": [], "error": str(e)}


@router.post("/disable-toggle",
              dependencies=[Depends(require_perm("mes.scanner.edit"))])
def toggle_scanner_disable(req: ScannerDisableToggleRequest):
    """v3.4.2 按工位禁用 / 启用扫码.

    语义:
      • disabled=True  → 该工位 + 所有联动 (与同一扫码器 broadcast 共享的工位)
        全部进入禁用集合; 这些工位绑定的扫码器立即 LOFF + 关 _scanning;
        这些工位下的 4 个守门点 (on_scan_received / is_scan_pair_mode /
        has_pending_workpiece / get_current_workpiece) 全部短路, source 退回
        项目原生结算 (tracking → all_gone, 容器 → box gone-confirm)
      • disabled=False → 反向, 把联动闭包从禁用集合移除, 对仍在 detecting 的
        工位重发 LON 复活扫码器
      • 状态持久化到 backend/scanner_runtime_state.json, 重启保留

    返回 {"disabled_channels": [...], "linked": [...], "changed": bool}
    """
    try:
        from backend.services.mes_hooks import get_mes_hook
        mes = get_mes_hook()
        if mes is None:
            raise HTTPException(503, "MES Hook 未启用, 无法切换扫码禁用状态")
        result = mes.set_channel_disabled(req.channel_id, req.disabled)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"toggle_scanner_disable failed: {e}")
