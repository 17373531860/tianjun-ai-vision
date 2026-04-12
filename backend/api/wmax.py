"""
WMax IDManager 扫码器 REST API

提供 WMax 设备的发现、连接、参数读写、图像获取、触发控制、
码制配置、ROI、读取模式、IO配置、数据处理、读码率测试等全功能接口。
"""
import asyncio
import logging
import traceback

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from typing import Optional
import base64

from backend.services.wmax.manager import get_wmax_manager
from backend.services.wmax.virtual_device import VirtualWMaxDevice, VIRTUAL_IP

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scanner/wmax", tags=["WMax Scanner"])


# ── 请求模型 ─────────────────────────────────────────────

class ConnectRequest(BaseModel):
    ip: str
    port: int = 55266


class DeviceRequest(BaseModel):
    ip: str
    port: int = 55266


class TriggerRequest(BaseModel):
    ip: str
    port: int = 55266
    on: bool = True


class RunModeRequest(BaseModel):
    ip: str
    port: int = 55266
    mode: int = 0
    bank_id: int = 0
    start: bool = True
    image: bool = True


class FullParamsRequest(BaseModel):
    ip: str
    port: int = 55266
    sensor_params: Optional[dict] = None
    light_params: Optional[dict] = None
    common_params: Optional[dict] = None
    code_params: Optional[dict] = None
    reading_params: Optional[dict] = None
    input_params: Optional[dict] = None
    output_params: Optional[dict] = None
    indicator_params: Optional[dict] = None
    data_output_format_params: Optional[dict] = None


class OutputConfigRequest(BaseModel):
    ip: str
    port: int = 55266
    signal_mask: int = 0
    duration_ms: int = 150
    save: bool = False


class IndicatorConfigRequest(BaseModel):
    ip: str
    port: int = 55266
    mode: int = 3
    save: bool = False


class PresetRequest(BaseModel):
    ip: str
    port: int = 55266
    config_id: int = 0


# ── 设备发现 ─────────────────────────────────────────────

@router.get("/discover")
async def discover_devices(timeout: float = Query(2.0, ge=0.5, le=10.0)):
    """UDP 广播发现局域网内的 WMax 设备"""
    logger.info("[WMaxAPI] GET /discover timeout=%.1f", timeout)
    try:
        mgr = get_wmax_manager()
        devices = await mgr.discover_devices(timeout=timeout)
        logger.info("[WMaxAPI] 发现 %d 台设备", len(devices))
        return {"devices": devices, "count": len(devices)}
    except Exception as e:
        logger.error("[WMaxAPI] discover 异常: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"设备发现失败: {e}")


@router.post("/auto-discover")
async def auto_discover_and_connect(timeout: float = Query(3.0, ge=1.0, le=10.0)):
    """自动发现并连接所有 WMax 设备（跟启动时相同逻辑）"""
    logger.info("[WMaxAPI] POST /auto-discover timeout=%.1f", timeout)
    try:
        mgr = get_wmax_manager()
        results = await mgr.auto_discover_and_connect(timeout=timeout)

        from backend.services.scanner import get_scanner_service
        svc = get_scanner_service()

        existing_ips = set()
        for conn in svc._connections.values():
            existing_ips.add(f"{conn.ip}:{conn.port}")

        auto_id_base = -9000
        injected = 0
        for r in results:
            if r.get("action") not in ("connected", "already_connected"):
                continue
            key = f"{r['ip']}:{r['port']}"
            if key in existing_ips:
                continue

            auto_id = auto_id_base
            while auto_id in svc._connections:
                auto_id -= 1

            from backend.services.scanner import ScannerConnection
            conn = ScannerConnection(
                device_id=auto_id,
                name=r.get("name", f"WMax-{r['ip']}"),
                ip=r["ip"], port=r["port"],
                channel_id=0, enabled=True,
                status="connected", device_type="wmax",
            )
            svc._connections[auto_id] = conn
            auto_id_base = auto_id - 1
            injected += 1

        logger.info("[WMaxAPI] auto-discover 完成: %d 发现, %d 新注入", len(results), injected)
        return {"results": results, "total": len(results), "injected": injected}
    except Exception as e:
        logger.error("[WMaxAPI] auto-discover 异常: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"自动发现失败: {e}")


@router.get("/discovered")
def get_discovered_devices():
    """获取最近一次自动发现的设备列表"""
    mgr = get_wmax_manager()
    return {"devices": mgr.get_discovered()}


# ── 连接管理 ─────────────────────────────────────────────

@router.post("/connect")
def connect_device(body: ConnectRequest):
    logger.info("[WMaxAPI] POST /connect %s:%d", body.ip, body.port)
    try:
        mgr = get_wmax_manager()
        result = mgr.connect(body.ip, body.port)
        logger.info("[WMaxAPI] connect 结果: %s", result.get("message", result))
        return result
    except Exception as e:
        logger.error("[WMaxAPI] connect 异常: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"连接失败: {e}")


@router.post("/disconnect")
def disconnect_device(body: DeviceRequest):
    logger.info("[WMaxAPI] POST /disconnect %s:%d", body.ip, body.port)
    mgr = get_wmax_manager()
    return mgr.disconnect(body.ip, body.port)


@router.get("/status")
def get_all_wmax_status():
    logger.debug("[WMaxAPI] GET /status")
    mgr = get_wmax_manager()
    return mgr.get_all_status()


# ── 握手与初始化 ──────────────────────────────────────────

@router.post("/handshake")
async def handshake(body: DeviceRequest):
    logger.info("[WMaxAPI] POST /handshake %s:%d", body.ip, body.port)
    try:
        mgr = get_wmax_manager()
        result = await mgr.handshake(body.ip, body.port)
        logger.info("[WMaxAPI] handshake 结果: ret_code=%s",
                    result.get("response", {}).get("ret_code"))
        return result
    except Exception as e:
        logger.error("[WMaxAPI] handshake 异常: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"握手失败: {e}")


@router.post("/load-config")
async def load_config(body: DeviceRequest, config_id: int = Query(-1)):
    """读取设备全部配置（包含 sensor/light/common/code/reading/dataEdit/IO）"""
    logger.info("[WMaxAPI] POST /load-config %s:%d config_id=%d", body.ip, body.port, config_id)
    try:
        mgr = get_wmax_manager()
        result = await mgr.load_config(body.ip, body.port, config_id)
        sections = [k for k in result if k not in ("raw_fields", "error") and result[k]]
        logger.info("[WMaxAPI] load-config 结果: %s", ", ".join(sections) if sections else "空/错误")
        return result
    except Exception as e:
        logger.error("[WMaxAPI] load-config 异常: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"读取配置失败: {e}")


@router.post("/device-features")
async def get_device_features(body: DeviceRequest):
    logger.info("[WMaxAPI] POST /device-features %s:%d", body.ip, body.port)
    mgr = get_wmax_manager()
    return await mgr.get_device_features(body.ip, body.port)


# ── 全参数读写 ────────────────────────────────────────────

@router.put("/params")
async def set_params(body: FullParamsRequest):
    """下发所有类型参数（不保存到设备 Flash）"""
    param_types = [k for k in ("sensor_params", "light_params", "common_params",
                               "code_params", "reading_params",
                               "input_params", "output_params", "indicator_params",
                               "data_output_format_params")
                   if getattr(body, k) is not None]
    logger.info("[WMaxAPI] PUT /params %s:%d → %s", body.ip, body.port, param_types)
    try:
        mgr = get_wmax_manager()
        result = await mgr.set_params(
            body.ip, body.port,
            sensor_params=body.sensor_params,
            light_params=body.light_params,
            common_params=body.common_params,
            code_params=body.code_params,
            reading_params=body.reading_params,
            input_params=body.input_params,
            output_params=body.output_params,
            indicator_params=body.indicator_params,
            data_output_format_params=body.data_output_format_params,
        )
        logger.info("[WMaxAPI] set_params 结果: %s", result)
        return result
    except Exception as e:
        logger.error("[WMaxAPI] set_params 异常: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"下发参数失败: {e}")


@router.post("/save-params")
async def save_params(body: FullParamsRequest):
    """保存参数到设备 Flash"""
    param_types = [k for k in ("sensor_params", "light_params", "common_params",
                               "code_params", "reading_params",
                               "input_params", "output_params", "indicator_params",
                               "data_output_format_params")
                   if getattr(body, k) is not None]
    logger.info("[WMaxAPI] POST /save-params %s:%d → %s", body.ip, body.port, param_types)
    try:
        mgr = get_wmax_manager()
        result = await mgr.save_params(
            body.ip, body.port,
            sensor_params=body.sensor_params,
            light_params=body.light_params,
            common_params=body.common_params,
            code_params=body.code_params,
            reading_params=body.reading_params,
            input_params=body.input_params,
            output_params=body.output_params,
            indicator_params=body.indicator_params,
            data_output_format_params=body.data_output_format_params,
        )
        logger.info("[WMaxAPI] save_params 结果: %s", result)
        return result
    except Exception as e:
        logger.error("[WMaxAPI] save_params 异常: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"保存参数失败: {e}")


# ── 输出端子与指示灯 ──────────────────────────────────────

@router.put("/output-config")
async def set_output_config(body: OutputConfigRequest):
    """设置输出端子: signal_mask (0=无, 1=OK, 4=错误, 5=OK+错误, 1024=触发器忙), duration_ms"""
    logger.info("[WMaxAPI] PUT /output-config %s:%d signal=0x%X dur=%dms save=%s",
                body.ip, body.port, body.signal_mask, body.duration_ms, body.save)
    try:
        mgr = get_wmax_manager()
        output_params = {
            "signal_mask": body.signal_mask,
            "duration_ms": body.duration_ms,
        }
        if body.save:
            result = await mgr.save_params(body.ip, body.port, output_params=output_params)
        else:
            result = await mgr.set_params(body.ip, body.port, output_params=output_params)
        return result
    except Exception as e:
        logger.error("[WMaxAPI] output-config 异常: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"设置输出端子失败: {e}")


@router.put("/indicator-config")
async def set_indicator_config(body: IndicatorConfigRequest):
    """设置指示灯模式: mode (1=手动亮灯, 3=仅扫描时自动亮灯)"""
    logger.info("[WMaxAPI] PUT /indicator-config %s:%d mode=%d save=%s",
                body.ip, body.port, body.mode, body.save)
    try:
        mgr = get_wmax_manager()
        indicator_params = {"mode": body.mode}
        if body.save:
            result = await mgr.save_params(body.ip, body.port, indicator_params=indicator_params)
        else:
            result = await mgr.set_params(body.ip, body.port, indicator_params=indicator_params)
        return result
    except Exception as e:
        logger.error("[WMaxAPI] indicator-config 异常: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"设置指示灯失败: {e}")


# ── 参数预设（4组） ───────────────────────────────────────

@router.post("/preset/load")
async def load_preset(body: PresetRequest):
    """加载指定预设组（config_id: 0-3）"""
    logger.info("[WMaxAPI] POST /preset/load %s:%d config_id=%d", body.ip, body.port, body.config_id)
    mgr = get_wmax_manager()
    return await mgr.load_config(body.ip, body.port, body.config_id)


@router.post("/preset/save")
async def save_preset(body: PresetRequest):
    """保存当前参数到指定预设组"""
    logger.info("[WMaxAPI] POST /preset/save %s:%d config_id=%d", body.ip, body.port, body.config_id)
    mgr = get_wmax_manager()
    return await mgr.save_params(body.ip, body.port, config_id=body.config_id)


# ── 自动对焦与调参 ────────────────────────────────────────

@router.post("/autofocus")
async def auto_focus(body: DeviceRequest, start: bool = Query(True)):
    logger.info("[WMaxAPI] POST /autofocus %s:%d start=%s", body.ip, body.port, start)
    mgr = get_wmax_manager()
    return await mgr.auto_focus(body.ip, body.port, start)


@router.post("/autotune")
async def start_autotune(body: DeviceRequest):
    logger.info("[WMaxAPI] POST /autotune %s:%d", body.ip, body.port)
    mgr = get_wmax_manager()
    return await mgr.start_tune(body.ip, body.port)


@router.post("/cancel-tune")
async def cancel_autotune(body: DeviceRequest):
    logger.info("[WMaxAPI] POST /cancel-tune %s:%d", body.ip, body.port)
    mgr = get_wmax_manager()
    return await mgr.cancel_tune(body.ip, body.port)


# ── 图像 ─────────────────────────────────────────────────

@router.post("/video")
async def turn_on_video(body: TriggerRequest):
    logger.info("[WMaxAPI] POST /video %s:%d on=%s", body.ip, body.port, body.on)
    mgr = get_wmax_manager()
    return await mgr.turn_on_video(body.ip, body.port, body.on)


@router.post("/trigger-image")
async def turn_on_trigger_image(body: TriggerRequest):
    logger.info("[WMaxAPI] POST /trigger-image %s:%d on=%s", body.ip, body.port, body.on)
    mgr = get_wmax_manager()
    return await mgr.turn_on_trigger_image(body.ip, body.port, body.on)


@router.get("/image")
def get_image(ip: str, port: int = Query(55266)):
    logger.debug("[WMaxAPI] GET /image %s:%d", ip, port)
    mgr = get_wmax_manager()
    img = mgr.get_image(ip, port)
    if not img:
        raise HTTPException(404, "无图像数据")
    return img


@router.get("/image/raw")
def get_image_raw(ip: str, port: int = Query(55266)):
    logger.debug("[WMaxAPI] GET /image/raw %s:%d", ip, port)
    mgr = get_wmax_manager()
    img = mgr.get_image(ip, port)
    if not img:
        raise HTTPException(404, "无图像数据")
    data = base64.b64decode(img["data_base64"])
    fmt = img.get("format", 0)
    media = "image/jpeg" if fmt in (1, 2) else "application/octet-stream"
    return Response(content=data, media_type=media)


@router.get("/stream")
def mjpeg_stream(ip: str, port: int = Query(55266), fps: int = Query(10)):
    """MJPEG 实时视频流 — 浏览器 <img> 原生支持"""
    mgr = get_wmax_manager()
    dev = mgr.get_device(ip, port)
    if not dev or not dev.state.connected:
        raise HTTPException(404, "设备未连接")

    interval = 1.0 / max(1, min(fps, 30))
    BOUNDARY = b"--frame\r\n"

    async def generate():
        last_ts = 0
        while True:
            img = dev.state.last_image
            if img and img.image_data and img.timestamp != last_ts:
                last_ts = img.timestamp
                jpeg = img.image_data
                yield (BOUNDARY
                       + b"Content-Type: image/jpeg\r\n"
                       + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                       + jpeg + b"\r\n")
            await asyncio.sleep(interval)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ── 触发控制 ──────────────────────────────────────────────

@router.post("/trigger")
def trigger_control(body: TriggerRequest):
    logger.info("[WMaxAPI] POST /trigger %s:%d on=%s", body.ip, body.port, body.on)
    mgr = get_wmax_manager()
    if body.on:
        return mgr.trigger_on(body.ip, body.port)
    else:
        return mgr.trigger_off(body.ip, body.port)


# ── 读码结果 ─────────────────────────────────────────────

@router.get("/last-code")
def get_last_code(ip: str, port: int = Query(55266)):
    mgr = get_wmax_manager()
    code = mgr.get_last_code(ip, port)
    if not code:
        return {"codes": [], "img_timestamp": None}
    return code


# ── 运行模式 ─────────────────────────────────────────────

@router.put("/run-mode")
async def set_run_mode(body: RunModeRequest):
    logger.info("[WMaxAPI] PUT /run-mode %s:%d mode=%d", body.ip, body.port, body.mode)
    mgr = get_wmax_manager()
    return await mgr.set_run_mode(body.ip, body.port,
                                  body.mode, body.bank_id,
                                  body.start, body.image)


# ── 读码率测试 ───────────────────────────────────────────

@router.post("/read-rate/start")
async def start_read_rate_test(body: DeviceRequest):
    """开始读码率测试"""
    logger.info("[WMaxAPI] POST /read-rate/start %s:%d", body.ip, body.port)
    mgr = get_wmax_manager()
    return await mgr.start_read_rate_test(body.ip, body.port)


@router.post("/read-rate/stop")
async def stop_read_rate_test(body: DeviceRequest):
    """停止读码率测试"""
    logger.info("[WMaxAPI] POST /read-rate/stop %s:%d", body.ip, body.port)
    mgr = get_wmax_manager()
    return await mgr.stop_read_rate_test(body.ip, body.port)


@router.get("/read-rate/result")
def get_read_rate_result(ip: str, port: int = Query(55266)):
    """获取读码率测试结果"""
    mgr = get_wmax_manager()
    result = mgr.get_read_rate_result(ip, port)
    if not result:
        return {"total_count": 0, "success_count": 0, "fail_count": 0, "rate": 0, "is_running": False}
    return result


# ── 设备控制 ─────────────────────────────────────────────

@router.post("/reboot")
async def reboot_device(body: DeviceRequest):
    logger.warning("[WMaxAPI] POST /reboot %s:%d", body.ip, body.port)
    mgr = get_wmax_manager()
    return await mgr.reboot(body.ip, body.port)


@router.post("/reset")
async def reset_device(body: DeviceRequest):
    logger.warning("[WMaxAPI] POST /reset %s:%d", body.ip, body.port)
    mgr = get_wmax_manager()
    return await mgr.reset_to_default(body.ip, body.port)


@router.post("/indicate")
async def indicate_device(body: DeviceRequest):
    logger.info("[WMaxAPI] POST /indicate %s:%d", body.ip, body.port)
    mgr = get_wmax_manager()
    return await mgr.indicate_device(body.ip, body.port)


# ── 虚拟设备（演示用） ────────────────────────────────────

@router.post("/virtual/create")
def create_virtual_device():
    """创建虚拟 WMax 设备用于 UI 预览"""
    logger.info("[WMaxAPI] POST /virtual/create")
    try:
        mgr = get_wmax_manager()
        key = mgr._key(VIRTUAL_IP, 55266)
        if key in mgr._devices:
            logger.info("[WMaxAPI] 虚拟设备已存在")
            return {"success": True, "message": "虚拟设备已存在", "ip": VIRTUAL_IP}
        vdev = VirtualWMaxDevice(VIRTUAL_IP, 55266)
        mgr._devices[key] = vdev
        _inject_virtual_scanner_status()
        logger.info("[WMaxAPI] 虚拟设备已创建 ip=%s", VIRTUAL_IP)
        return {"success": True, "message": "虚拟设备已创建", "ip": VIRTUAL_IP}
    except Exception as e:
        logger.error("[WMaxAPI] 创建虚拟设备失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"创建虚拟设备失败: {e}")


@router.post("/virtual/delete")
def delete_virtual_device():
    """删除虚拟 WMax 设备"""
    logger.info("[WMaxAPI] POST /virtual/delete")
    try:
        mgr = get_wmax_manager()
        key = mgr._key(VIRTUAL_IP, 55266)
        dev = mgr._devices.pop(key, None)
        _remove_virtual_scanner_status()
        if dev:
            logger.info("[WMaxAPI] 虚拟设备已删除")
            return {"success": True, "message": "虚拟设备已删除"}
        logger.warning("[WMaxAPI] 虚拟设备不存在")
        return {"success": False, "message": "虚拟设备不存在"}
    except Exception as e:
        logger.error("[WMaxAPI] 删除虚拟设备失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"删除虚拟设备失败: {e}")


@router.get("/debug/config-raw")
async def debug_config_raw(ip: str, port: int = Query(55266)):
    """诊断: 返回设备配置的原始 protobuf 字段树"""
    from backend.services.wmax.protocol import CmdType
    from backend.services.wmax import messages as msg
    mgr = get_wmax_manager()
    dev = mgr.get_device(ip, port)
    if not dev:
        raise HTTPException(404, "设备未连接")
    resp = await dev.send_and_wait(CmdType.GetConfigOpt, timeout=5.0)
    if not resp or not resp.data_part:
        return {"error": "无响应"}
    raw = resp.data_part

    def decode_tree(data: bytes, depth: int = 0) -> dict:
        if depth > 5:
            return {"_hex": data[:100].hex()}
        fields = msg.decode_message(data)
        tree = {}
        for fid, vals in fields.items():
            if not isinstance(vals, list):
                vals = [vals]
            items = []
            for v in vals:
                if isinstance(v, int):
                    items.append(v)
                elif isinstance(v, bytes):
                    if len(v) > 4:
                        try:
                            items.append(decode_tree(v, depth + 1))
                        except Exception:
                            items.append(f"bytes({len(v)})")
                    else:
                        items.append(v.hex())
                else:
                    items.append(str(v))
            tree[str(fid)] = items[0] if len(items) == 1 else items
        return tree

    return {"size": len(raw), "tree": decode_tree(raw)}


def _inject_virtual_scanner_status():
    """在 ScannerService 中注入虚拟 wmax 连接状态，使 Tab 出现"""
    try:
        from backend.services.scanner import get_scanner_service, ScannerConnection
        svc = get_scanner_service()
        virtual_id = -999
        if virtual_id not in svc._connections:
            conn = ScannerConnection(
                device_id=virtual_id, name="WMax VS600 (虚拟)",
                ip=VIRTUAL_IP, port=55266, channel_id=0,
                enabled=True, status="connected", device_type="wmax",
            )
            svc._connections[virtual_id] = conn
            logger.info("[WMaxAPI] 虚拟设备状态已注入 ScannerService")
        else:
            logger.debug("[WMaxAPI] 虚拟设备状态已存在于 ScannerService")
    except Exception as e:
        logger.error("[WMaxAPI] 注入虚拟状态失败: %s", e)


def _remove_virtual_scanner_status():
    try:
        from backend.services.scanner import get_scanner_service
        svc = get_scanner_service()
        removed = svc._connections.pop(-999, None)
        if removed:
            logger.info("[WMaxAPI] 虚拟设备状态已从 ScannerService 移除")
    except Exception as e:
        logger.error("[WMaxAPI] 移除虚拟状态失败: %s", e)


@router.get("/debug-config")
async def debug_config(ip: str = Query(...), port: int = Query(55266)):
    """调试: 返回设备原始配置的 NormalConfig 字段列表"""
    from backend.services.wmax import messages as msg
    mgr = get_wmax_manager()
    dev = mgr.get_device(ip, port)
    if not dev:
        return {"error": "设备未连接"}
    await dev.load_config()
    raw = dev._raw_config_data
    if not raw:
        return {"error": "无缓存配置"}
    top = msg.decode_message(raw)
    normal_raw = msg.get_bytes(top, 2)
    if not normal_raw:
        return {"error": "无 NormalConfig"}
    fields_info = []
    for fn, wt, val in msg._parse_raw_fields(normal_raw):
        label = {3:'BankOpt', 4:'InputOpt', 5:'OutputOpt', 8:'ReadingOpt',
                 9:'IndicatorOpt', 10:'DataOutputFormat'}.get(fn, f'Unknown-{fn}')
        entry = {"field": fn, "label": label, "wire_type": wt, "size": len(val)}
        if fn not in (3, 4, 5, 8):
            entry["hex"] = val[:300].hex()
        if fn == 9:
            entry["hex"] = val.hex()
            sub_fields = []
            for fn2, wt2, val2 in msg._parse_raw_fields(val):
                sub = {"field": fn2, "wt": wt2, "hex": val2.hex()[:200]}
                if wt2 == 2 and len(val2) > 2:
                    inner = []
                    for fn3, wt3, val3 in msg._parse_raw_fields(val2):
                        inner.append({"field": fn3, "wt": wt3, "hex": val3.hex()[:100]})
                    sub["inner"] = inner
                sub_fields.append(sub)
            entry["sub_fields"] = sub_fields
        fields_info.append(entry)
    return {"normal_config_size": len(normal_raw), "fields": fields_info}
