"""
扫码器 TCP 通讯服务

管理多台扫码器的 TCP 连接:
- 支持普通 TCP 文本模式 (VS600 等) 和 WMax 二进制协议模式
- 自动检测设备类型: 连接时尝试 WMax 握手，失败则回退到文本模式
- 每台设备一个独立的监听线程
- 自动重连 (指数退避)
- 扫码去重
- 收到数据后调用 MESHookManager.on_scan_received
"""
import asyncio
import logging
import socket
import threading
import time
import traceback
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime

from backend.db.database import SessionLocal
from backend.models.mes_models import ScannerDevice
from backend.services.barcode_parser import BarcodeParser, ParseResult

logger = logging.getLogger(__name__)


@dataclass
class ScannerConnection:
    """单个扫码器的连接状态"""
    device_id: int
    name: str
    ip: str
    port: int
    channel_id: int
    enabled: bool
    parse_config: dict = field(default_factory=dict)
    dedup_interval_sec: int = 2
    auto_create_workpiece: bool = True
    auto_link_order: bool = True
    scan_required: bool = False
    duplicate_scan_action: str = "overwrite"
    warn_no_barcode: bool = False
    rebind_mode: str = "rescan"
    bind_timing: str = "mid_cycle"
    broadcast_channels: list = field(default_factory=list)

    status: str = "disconnected"
    device_type: str = "text_lon"  # "text_lon"(默认), "auto", "text", "wmax"
    last_scan: str = ""
    last_scan_time: float = 0
    last_error: str = ""
    _socket: Optional[socket.socket] = field(default=None, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _wmax_device: Optional[object] = field(default=None, repr=False)
    _scanning: bool = False


class ScannerService:

    def __init__(self):
        self._connections: dict[int, ScannerConnection] = {}
        self._parser = BarcodeParser()
        self._mes_hook = None
        self._project_id_getter = None

    def set_mes_hook(self, hook):
        self._mes_hook = hook

    def set_project_id_getter(self, getter):
        """传入获取当前项目 ID 的函数 (来自 VideoSourceManager)"""
        self._project_id_getter = getter

    def start_all(self):
        """加载所有已配置的设备并启动连接，然后自动发现 WMax"""
        db = SessionLocal()
        try:
            devices = db.query(ScannerDevice).filter(
                ScannerDevice.enabled == True
            ).all()
            for dev in devices:
                self._start_device(dev)
            logger.info("[Scanner] 已启动 %d 台扫码器连接", len(devices))
        except Exception as e:
            logger.error("[Scanner] 启动失败: %s\n%s", e, traceback.format_exc())
        finally:
            db.close()

        has_wmax = any(c.device_type in ("wmax", "wmax_scan", "auto")
                       for c in self._connections.values())
        if has_wmax:
            self._auto_discover_wmax_bg()
        else:
            print("[Scanner] 所有设备均为 LON/LOFF 模式，跳过 WMax 自动发现")

    def _auto_discover_wmax_bg(self):
        """后台线程: UDP 自动发现 + 数据库已知 IP 直连 WMax 管理端口"""
        def _run():
            time.sleep(2)
            print("[Scanner] 开始自动发现 WMax 设备...")
            try:
                from backend.services.wmax.manager import get_wmax_manager
                from backend.services.wmax.device import DEFAULT_PORT as WMAX_CMD_PORT
                mgr = get_wmax_manager()

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    results = loop.run_until_complete(mgr.auto_discover_and_connect(timeout=3.0))
                finally:
                    loop.close()

                connected_mgmt_ips = set()
                for r in results:
                    if r.get("action") in ("connected", "already_connected"):
                        connected_mgmt_ips.add(r["ip"])

                print(f"[Scanner] WMax UDP 发现 {len(results)} 台, 已连接 {len(connected_mgmt_ips)} 台")

                db_ips = set()
                for conn in self._connections.values():
                    db_ips.add(f"{conn.ip}:{conn.port}")

                db_scanner_ips = set()
                for conn in self._connections.values():
                    db_scanner_ips.add(conn.ip)

                # UDP 发现已连上的 IP: 直接把对应 scanner conn.status 标为 connected
                # (manager.auto_discover_and_connect 内部已调用 activate_rpt_reporting)
                for ip in list(connected_mgmt_ips):
                    for c in self._connections.values():
                        if c.ip == ip and c.device_type in ("auto", "wmax"):
                            c.status = "connected"
                            c.last_error = ""

                for ip in db_scanner_ips:
                    if ip in connected_mgmt_ips:
                        continue
                    print(f"[Scanner] 数据库设备 {ip} 未被 UDP 发现，尝试直连 WMax 管理端口 {WMAX_CMD_PORT}")
                    try:
                        result = mgr.connect(ip, WMAX_CMD_PORT)
                        if result.get("success"):
                            print(f"[Scanner] {ip}:{WMAX_CMD_PORT} 直连成功")
                            connected_mgmt_ips.add(ip)
                            # 触发式 (ondemand): 不调 activate_rpt_reporting, 只建三端口 TCP.
                            # 扫码由 trigger_scan/_do_trigger_once_wmax 按需触发.
                            for c in self._connections.values():
                                if c.ip == ip and c.device_type in ("auto", "wmax"):
                                    c.status = "connected"
                                    c.last_error = ""
                        else:
                            print(f"[Scanner] {ip}:{WMAX_CMD_PORT} 直连失败: {result.get('message', '')}")
                            for c in self._connections.values():
                                if c.ip == ip and c.device_type in ("auto", "wmax"):
                                    c.status = "error"
                                    c.last_error = result.get("message", "WMax 连接失败")
                    except Exception as e:
                        print(f"[Scanner] {ip}:{WMAX_CMD_PORT} 直连异常: {e}")
                        for c in self._connections.values():
                            if c.ip == ip and c.device_type in ("auto", "wmax"):
                                c.status = "error"
                                c.last_error = str(e)

                WMAX_SCAN_PORT = 55256
                auto_id_base = -9000
                for ip in connected_mgmt_ips:
                    scan_key = f"{ip}:{WMAX_SCAN_PORT}"
                    if scan_key in db_ips:
                        logger.info("[Scanner] %s 已在数据库中，跳过注入", scan_key)
                        continue

                    existing = None
                    for conn in self._connections.values():
                        if conn.ip == ip and conn.port == WMAX_SCAN_PORT:
                            existing = conn
                            break
                    if existing:
                        existing.status = "connected"
                        logger.info("[Scanner] %s 已存在连接", scan_key)
                        continue

                    auto_id = auto_id_base
                    while auto_id in self._connections:
                        auto_id -= 1

                    conn = ScannerConnection(
                        device_id=auto_id,
                        name=f"WMax-{ip}",
                        ip=ip,
                        port=WMAX_SCAN_PORT,
                        channel_id=0,
                        enabled=True,
                        device_type="wmax_scan",
                    )
                    self._connections[auto_id] = conn
                    conn._stop_event.clear()
                    conn._thread = threading.Thread(
                        target=self._listen_loop, args=(conn,),
                        daemon=True, name=f"scanner-wmax-{auto_id}"
                    )
                    conn._thread.start()
                    auto_id_base = auto_id - 1
                    logger.info("[Scanner] 自动注入 WMax 扫码: %s → id=%d (55256文本模式)",
                                scan_key, auto_id)

                print(f"[Scanner] WMax 自动发现完成: 总管理连接 {len(connected_mgmt_ips)} 台")

            except Exception as e:
                import traceback as tb
                print(f"[Scanner] WMax 自动发现异常: {e}\n{tb.format_exc()}")

        t = threading.Thread(target=_run, daemon=True, name="wmax-auto-discover")
        t.start()

    def stop_all(self):
        for conn in self._connections.values():
            self._stop_connection(conn)
        self._connections.clear()
        logger.info("[Scanner] 所有扫码器已断开")

    def add_device(self, device: ScannerDevice):
        self._start_device(device)

    def remove_device(self, device_id: int):
        conn = self._connections.pop(device_id, None)
        if conn:
            self._stop_connection(conn)

    def get_all_status(self) -> list[dict]:
        results = []
        for conn in self._connections.values():
            results.append({
                "device_id": conn.device_id,
                "name": conn.name,
                "ip": conn.ip,
                "port": conn.port,
                "channel_id": conn.channel_id,
                "status": conn.status,
                "device_type": conn.device_type,
                "last_scan": conn.last_scan,
                "last_scan_time": datetime.fromtimestamp(conn.last_scan_time).isoformat()
                                  if conn.last_scan_time else None,
                "last_error": conn.last_error,
            })
        return results

    @staticmethod
    def _known_channel_count() -> int:
        """读取当前系统实际工位数（默认 1）。用于 scanner 绑定 ch 超界时降级。"""
        try:
            from backend.api.channel_manager import get_channel_manager
            return max(1, int(get_channel_manager().channel_count or 1))
        except Exception:
            return 1

    @classmethod
    def _resolve_bound_channels(cls, conn: "ScannerConnection") -> list:
        """解析扫码器实际绑定的 channel 列表。

        处理规则：
        - broadcast_channels 非空 → 用它
        - 否则用 [channel_id]
        - 任意元素超出系统已知工位数或 <0 → 降级到 0，并打告警
          （常见场景：UI 里"绑定工位"填成 1 但单工位系统只有 ch0）
        """
        bound = list(conn.broadcast_channels) if conn.broadcast_channels else [conn.channel_id]
        ch_count = cls._known_channel_count()
        fixed: list = []
        for b in bound:
            if b is None:
                continue
            if b >= ch_count or b < 0:
                if 0 not in fixed:
                    fixed.append(0)
                print(
                    f"[Scanner] 扫码器 '{conn.name}' 的 channel={b} 超出系统工位数 {ch_count}, "
                    f"已自动降级到 ch0。建议在 MES→扫码器面板 把'绑定工位'改为 0"
                )
            elif b not in fixed:
                fixed.append(b)
        return fixed or [0]

    def _wmax_trigger(self, conn: ScannerConnection, on: bool):
        """给 auto/wmax 类型的扫码器发 LON/LOFF (ondemand 模式)."""
        if conn.device_type not in ("auto", "wmax", "wmax_scan"):
            return
        try:
            from backend.services.wmax.manager import get_wmax_manager
            from backend.services.wmax.device import DEFAULT_PORT as WMAX_CMD_PORT
            mgr = get_wmax_manager()
            dev = mgr.get_device(conn.ip, WMAX_CMD_PORT)
            if dev is None or not dev.state.connected:
                return
            if on:
                dev.trigger_on()
            else:
                dev.trigger_off()
        except Exception as e:
            logger.warning("[Scanner] %s WMax trigger_%s 异常: %s",
                           conn.name, "on" if on else "off", e)

    def start_scanning(self, channel_id: int = None):
        """检测开始时调用: 让绑定的扫码器发 LON 进入扫码状态 (ondemand: 灯亮 + 开始扫)"""
        targets = []
        skipped = []
        for conn in self._connections.values():
            if conn.status != "connected":
                skipped.append(f"{conn.name}(status={conn.status})")
                continue
            if channel_id is not None:
                bound = self._resolve_bound_channels(conn)
                if channel_id not in bound:
                    skipped.append(
                        f"{conn.name}(ch_conf={conn.channel_id},"
                        f"bcast={conn.broadcast_channels},resolved={bound})"
                    )
                    continue
            targets.append(conn)

        for conn in targets:
            conn._scanning = True
            self._wmax_trigger(conn, on=True)
        if targets:
            names = [f"{c.name}[type={c.device_type}]" for c in targets]
            print(f"[Scanner] start_scanning(ch={channel_id}) → {names}")
        else:
            print(f"[Scanner] start_scanning(ch={channel_id}) 无匹配设备, 已跳过: {skipped}")

    def stop_scanning(self, channel_id: int = None):
        """检测停止时调用: 让绑定的扫码器发 LOFF 停止扫码 (ondemand: 灯灭 + 停扫)"""
        targets = []
        for conn in self._connections.values():
            if channel_id is not None:
                bound = self._resolve_bound_channels(conn)
                if channel_id not in bound:
                    continue
            targets.append(conn)

        for conn in targets:
            conn._scanning = False
            self._wmax_trigger(conn, on=False)
        if targets:
            names = [c.name for c in targets]
            print(f"[Scanner] stop_scanning(ch={channel_id}) → {names}")

    def trigger_scan(self, device_id: int) -> dict:
        """手动触发一次扫码: LON → 扫到码或超时后自动 LOFF"""
        conn = self._connections.get(device_id)
        if not conn:
            return {"success": False, "message": f"设备 {device_id} 未连接"}
        # WMax/auto 类型走 WMaxDevice 的 trigger_on/off, 不依赖 conn._socket
        if conn.device_type in ("auto", "wmax", "wmax_scan"):
            self._do_trigger_once_wmax(conn)
            return {"success": True, "message": f"已向 {conn.name} 发送 WMax 触发指令"}
        if conn.status != "connected" or not conn._socket:
            return {"success": False, "message": f"设备 {conn.name} 未连接 (status={conn.status})"}
        self._do_trigger_once(conn)
        return {"success": True, "message": f"已向 {conn.name} 发送触发指令"}

    def trigger_scan_by_ip(self, ip: str) -> dict:
        """通过 IP 查找并触发扫码"""
        for conn in self._connections.values():
            if conn.ip != ip:
                continue
            if conn.device_type in ("auto", "wmax", "wmax_scan"):
                self._do_trigger_once_wmax(conn)
                return {"success": True, "message": f"已向 {conn.name} 发送 WMax 触发指令"}
            if conn.status == "connected" and conn._socket:
                self._do_trigger_once(conn)
                return {"success": True, "message": f"已向 {conn.name} 发送触发指令"}
        return {"success": False, "message": f"未找到 IP={ip} 的已连接设备"}

    def _do_trigger_once_wmax(self, conn: ScannerConnection):
        """WMax 触发式扫码: 发 LON → 等扫到或 10s 超时 → LOFF."""
        def _run():
            try:
                from backend.services.wmax.manager import get_wmax_manager
                from backend.services.wmax.device import DEFAULT_PORT as WMAX_CMD_PORT
                mgr = get_wmax_manager()
                dev = mgr.get_device(conn.ip, WMAX_CMD_PORT)
                if dev is None or not dev.state.connected:
                    print(f"[Scanner] {conn.name} WMax 未连接, 无法触发")
                    return
                dev.trigger_on()
                print(f"[Scanner] {conn.name} WMax 触发 LON")
                scan_before = conn.last_scan_time
                deadline = time.time() + 10
                while time.time() < deadline:
                    if conn.last_scan_time > scan_before:
                        break
                    time.sleep(0.1)
                try:
                    dev.trigger_off()
                except Exception as e:
                    print(f"[Scanner] {conn.name} LOFF 失败: {e}")
                if conn.last_scan_time > scan_before:
                    print(f"[Scanner] {conn.name} WMax 触发: 扫到 {conn.last_scan}, LOFF 已发")
                else:
                    print(f"[Scanner] {conn.name} WMax 触发: 10秒超时, LOFF 已发")
            except Exception as e:
                print(f"[Scanner] {conn.name} WMax 触发异常: {e}")
        threading.Thread(target=_run, daemon=True, name=f"trigger-wmax-{conn.device_id}").start()

    def _do_trigger_once(self, conn: ScannerConnection):
        """文本模式单次触发: 发 LON, 扫到码后或 10 秒超时后自动发 LOFF"""
        def _run():
            try:
                conn._socket.sendall(b"LON\r\n")
                print(f"[Scanner] {conn.name} 手动触发 LON")
            except OSError as e:
                print(f"[Scanner] {conn.name} 手动 LON 失败: {e}")
                return
            scan_before = conn.last_scan_time
            deadline = time.time() + 10
            while time.time() < deadline:
                if conn.last_scan_time > scan_before:
                    break
                time.sleep(0.2)
            try:
                conn._socket.sendall(b"LOFF\r\n")
                if conn.last_scan_time > scan_before:
                    print(f"[Scanner] {conn.name} 手动触发: 扫到码, LOFF 已发送")
                else:
                    print(f"[Scanner] {conn.name} 手动触发: 10秒超时, LOFF 已发送")
            except OSError:
                pass
        threading.Thread(target=_run, daemon=True, name=f"trigger-{conn.device_id}").start()

    def get_latest_scan(self, channel_id: int) -> Optional[dict]:
        for conn in self._connections.values():
            if conn.channel_id == channel_id and conn.last_scan:
                return {
                    "serial_no": conn.last_scan,
                    "time": conn.last_scan_time,
                    "device_name": conn.name,
                }
        return None

    def test_connection(self, ip: str, port: int, timeout: float = 3.0) -> dict:
        """测试扫码器: 优先走 WMax 协议 (闪光 5s + 同步采集条码), 失败时降级到文本 LON/LOFF."""
        # 1) WMax 路径: 如果 WMaxDeviceManager 已连上这台设备, 直接走 flash_and_scan
        try:
            from backend.services.wmax.manager import get_wmax_manager
            from backend.services.wmax.device import DEFAULT_PORT as WMAX_CMD_PORT
            mgr = get_wmax_manager()
            dev = mgr.get_device(ip, WMAX_CMD_PORT)
            if dev is None or not dev.state.connected:
                # 未连接就先试着连一次, 连上了再 flash
                conn_result = mgr.connect(ip, WMAX_CMD_PORT)
                if conn_result.get("success"):
                    dev = mgr.get_device(ip, WMAX_CMD_PORT)
            if dev is not None and dev.state.connected:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # 直接 flash_and_scan: LON 闪 5s + 同步收集 RPT 条码 + LOFF 关灯.
                    # 不调 activate_rpt_reporting: 那会发 turn_on_video(on=True) 让扫码器
                    # 进入持续扫描模式, 灯一直闪无法关闭.
                    codes = loop.run_until_complete(dev.flash_and_scan(duration=5.0))
                finally:
                    loop.close()
                if codes:
                    return {
                        "success": True,
                        "message": f"WMax {ip} 闪光 5 秒, 扫到 {len(codes)} 条: {', '.join(codes[:5])}",
                        "device_type": "wmax",
                        "scanned": codes,
                    }
                return {
                    "success": True,
                    "message": f"WMax {ip} 闪光 5 秒, 未扫到条码 (请靠近条码再试)",
                    "device_type": "wmax",
                    "scanned": [],
                }
        except Exception as e:
            logger.warning("[Scanner] WMax test_connection 失败, 降级 text_lon: %s", e)

        # 2) 文本模式降级: 走老 LON/LOFF (仅对真正的 55256 文本模式扫码器有效)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((ip, port))

            def _flash_and_stop():
                try:
                    sock.sendall(b"LON\r\n")
                    time.sleep(5)
                    sock.sendall(b"LOFF\r\n")
                    time.sleep(0.2)
                except OSError:
                    pass
                finally:
                    sock.close()

            threading.Thread(target=_flash_and_stop, daemon=True).start()
            return {
                "success": True,
                "message": f"连接 {ip}:{port} 成功 (闪灯 5 秒, 文本模式)",
                "device_type": "text_lon",
            }
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _detect_device_type(self, sock: socket.socket, timeout: float = 2.0) -> str:
        """通过发送 WMax 握手帧来检测设备类型"""
        peer = "unknown"
        try:
            peer = f"{sock.getpeername()[0]}:{sock.getpeername()[1]}"
        except OSError:
            pass

        logger.debug("[Scanner] 检测设备类型: %s", peer)
        try:
            from backend.services.wmax.protocol import (
                pack, Command, CmdType, DataReceiver, DEFAULT_FLAG,
            )
            handshake_cmd = Command(cmd_type=CmdType.HandShake)
            frame = pack(handshake_cmd)
            sock.sendall(frame)
            logger.debug("[Scanner] 发送 WMax 握手帧 %dB → %s", len(frame), peer)

            sock.settimeout(timeout)
            try:
                data = sock.recv(4096)
                if data and len(data) >= 10 and data[0] == 0x5A and data[1] == 0x5A:
                    logger.info("[Scanner] %s 检测为 WMax 设备 (响应 %dB)", peer, len(data))
                    return "wmax"
                else:
                    logger.debug("[Scanner] %s WMax 握手无效响应: %dB",
                                 peer, len(data) if data else 0)
            except socket.timeout:
                logger.debug("[Scanner] %s WMax 握手超时", peer)
        except ImportError as e:
            logger.warning("[Scanner] WMax 模块导入失败: %s", e)

        try:
            sock.sendall(b"LON\r\n")
            time.sleep(0.3)
        except OSError:
            pass
        logger.info("[Scanner] %s 检测为文本模式设备", peer)
        return "text"

    # ---- 内部方法 ----

    def _start_device(self, dev: ScannerDevice):
        raw_type = (getattr(dev, 'device_type', None) or 'text_lon').strip() or 'text_lon'
        # 注: 'auto' / 'wmax' 走 WMaxDeviceManager 三端口协议 (55266 CMD + 55276 IMG + 55286 RPT),
        #      条码由 _auto_discover_wmax_bg + wmax.manager 的 on_code_received 回调 → inject_scan_result 进来.
        #      不再降级成 text_lon (那条路径发 ASCII 'LON\r\n' 到 55256, WMax 根本不吃).
        db_device_type = raw_type
        conn = ScannerConnection(
            device_id=dev.id,
            name=dev.name,
            ip=dev.ip,
            port=dev.port,
            channel_id=dev.channel_id or 0,
            enabled=dev.enabled,
            parse_config=dev.parse_config or {},
            dedup_interval_sec=dev.dedup_interval_sec or 2,
            auto_create_workpiece=dev.auto_create_workpiece,
            auto_link_order=dev.auto_link_order,
            scan_required=getattr(dev, 'scan_required', False),
            duplicate_scan_action=getattr(dev, 'duplicate_scan_action', 'overwrite'),
            warn_no_barcode=getattr(dev, 'warn_no_barcode', False),
            rebind_mode=getattr(dev, 'rebind_mode', 'rescan'),
            bind_timing=getattr(dev, 'bind_timing', 'mid_cycle'),
            broadcast_channels=getattr(dev, 'broadcast_channels', None) or [],
        )
        conn.device_type = db_device_type
        conn.parse_config["parse_mode"] = dev.parse_mode or "direct"

        self._connections[dev.id] = conn
        conn._stop_event.clear()
        conn._thread = threading.Thread(
            target=self._listen_loop, args=(conn,),
            daemon=True, name=f"scanner-{dev.id}"
        )
        conn._thread.start()

    def _stop_connection(self, conn: ScannerConnection):
        conn._stop_event.set()
        if conn._socket:
            try:
                conn._socket.close()
            except Exception:
                pass
        if conn._thread and conn._thread.is_alive():
            conn._thread.join(timeout=3)

    def _listen_loop(self, conn: ScannerConnection):
        """单个设备的监听循环, 含自动重连和设备类型自动检测"""
        retry_delay = 1.0
        max_delay = 30.0
        keepalive_interval = 15.0

        # WMax 三端口协议由 WMaxDeviceManager 统一管理 (55266 CMD / 55276 IMG / 55286 RPT).
        # 条码会通过 wmax.manager._register_scan_callback → scanner.inject_scan_result 进来,
        # 这里不需要再建一条本地 TCP, 避免占用端口导致 WMax 管理连接失败.
        if conn.device_type in ("auto", "wmax"):
            conn.status = "pending_wmax"
            logger.info("[Scanner] %s (%s) device_type=%s → 交由 WMaxDeviceManager 三端口管理",
                        conn.name, conn.ip, conn.device_type)
            while not conn._stop_event.is_set():
                conn._stop_event.wait(timeout=1.0)
            return

        while not conn._stop_event.is_set():
            conn_start = time.time()
            try:
                conn.status = "connecting"
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((conn.ip, conn.port))

                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    if hasattr(socket, 'TCP_KEEPIDLE'):
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                except OSError:
                    pass

                conn._socket = sock
                conn.status = "connected"
                conn.last_error = ""
                conn_start = time.time()

                if conn.device_type == "text_lon":
                    print(f"[Scanner] {conn.name} ({conn.ip}:{conn.port}) 已连接 (LON/LOFF 模式)")
                    self._text_lon_listen_loop(conn, sock)
                elif conn.device_type == "wmax_scan":
                    logger.info("[Scanner] %s (%s:%d) WMax 扫码数据端口，被动监听",
                                conn.name, conn.ip, conn.port)
                    self._wmax_scan_listen_loop(conn, sock)
                elif conn.device_type == "auto":
                    conn.device_type = self._detect_device_type(sock)
                    if conn.device_type == "wmax":
                        logger.info("[Scanner] %s (%s:%d) 检测为 WMax 设备，进入二进制监听",
                                    conn.name, conn.ip, conn.port)
                        self._wmax_listen_loop(conn, sock)
                    else:
                        logger.info("[Scanner] %s (%s:%d) 已连接 (文本模式，被动监听)",
                                    conn.name, conn.ip, conn.port)
                        self._text_listen_loop(conn, sock, keepalive_interval)
                elif conn.device_type == "wmax":
                    logger.info("[Scanner] %s (%s:%d) 检测为 WMax 设备，进入二进制监听",
                                conn.name, conn.ip, conn.port)
                    self._wmax_listen_loop(conn, sock)
                else:
                    logger.info("[Scanner] %s (%s:%d) 已连接 (文本模式，被动监听)",
                                conn.name, conn.ip, conn.port)
                    self._text_listen_loop(conn, sock, keepalive_interval)

            except Exception as e:
                conn.last_error = str(e)
                conn.status = "error"
                logger.error("[Scanner] %s 连接失败: %s\n%s",
                             conn.name, e, traceback.format_exc())

            if conn._socket:
                try:
                    conn._socket.close()
                except Exception:
                    pass
                conn._socket = None

            if conn.status != "error":
                conn.status = "disconnected"
            connected_duration = time.time() - conn_start
            if connected_duration > 10:
                retry_delay = 1.0
            logger.info("[Scanner] %s 已断开 (status=%s, held=%.0fs, next_retry=%.0fs)",
                        conn.name, conn.status, connected_duration, retry_delay)
            if not conn._stop_event.is_set():
                conn._stop_event.wait(timeout=retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

    def _text_lon_listen_loop(self, conn: ScannerConnection, sock: socket.socket):
        """LON/LOFF 模式监听：保持 TCP 连接，检测开始时发 LON，停止时发 LOFF"""
        sock.settimeout(2.0)
        buffer = b""
        last_activity = time.time()

        while not conn._stop_event.is_set():
            if conn._scanning and not getattr(conn, '_lon_sent', False):
                try:
                    sock.sendall(b"LON\r\n")
                    conn._lon_sent = True
                    print(f"[Scanner] {conn.name} LON 已发送 (开始扫码)")
                except OSError as e:
                    print(f"[Scanner] {conn.name} LON 发送失败: {e}")
                    break

            if not conn._scanning and getattr(conn, '_lon_sent', False):
                try:
                    sock.sendall(b"LOFF\r\n")
                    conn._lon_sent = False
                    print(f"[Scanner] {conn.name} LOFF 已发送 (停止扫码)")
                except OSError as e:
                    print(f"[Scanner] {conn.name} LOFF 发送失败: {e}")
                    break

            try:
                data = sock.recv(4096)
                if not data:
                    break
                buffer += data
                last_activity = time.time()

                while b"\r\n" in buffer or b"\n" in buffer:
                    sep = b"\r\n" if b"\r\n" in buffer else b"\n"
                    line, buffer = buffer.split(sep, 1)
                    text = line.decode("utf-8", errors="ignore").strip()
                    if text:
                        self._on_data_received(conn, text)

            except socket.timeout:
                if time.time() - last_activity > 30.0:
                    try:
                        sock.getpeername()
                        last_activity = time.time()
                    except OSError:
                        break
                continue
            except OSError:
                break

        conn._lon_sent = False

    def _text_listen_loop(self, conn: ScannerConnection,
                          sock: socket.socket, keepalive_interval: float):
        """文本模式 TCP 监听（原有逻辑）"""
        sock.settimeout(2.0)
        buffer = b""
        last_activity = time.time()

        while not conn._stop_event.is_set():
            try:
                data = sock.recv(4096)
                if not data:
                    break
                buffer += data
                last_activity = time.time()

                while b"\r\n" in buffer or b"\n" in buffer:
                    sep = b"\r\n" if b"\r\n" in buffer else b"\n"
                    line, buffer = buffer.split(sep, 1)
                    text = line.decode("utf-8", errors="ignore").strip()
                    if text:
                        self._on_data_received(conn, text)

            except socket.timeout:
                if time.time() - last_activity > keepalive_interval:
                    try:
                        sock.getpeername()
                        last_activity = time.time()
                    except OSError:
                        break
                continue
            except OSError:
                break

    def _wmax_scan_listen_loop(self, conn: ScannerConnection, sock: socket.socket):
        """WMax 设备的 55256 扫码数据端口 — 被动 TCP 文本监听

        该端口只在设备被外部触发或通过 55266 管理端口发 LON 后才会推送条码。
        这里不主动发 LON（避免闪光），靠设备自身触发或 WMax 管理端口控制。
        如果连接空闲超过 60s，发一个空字节检测连接存活。
        """
        sock.settimeout(2.0)
        buffer = b""
        last_activity = time.time()
        recv_total = 0
        code_count = 0

        logger.info("[Scanner] %s WMax 扫码端口 (55256) 被动监听启动", conn.name)

        while not conn._stop_event.is_set():
            try:
                data = sock.recv(4096)
                if not data:
                    logger.warning("[Scanner] %s 55256 连接关闭 (EOF)", conn.name)
                    break
                recv_total += len(data)
                last_activity = time.time()
                buffer += data

                while b"\r\n" in buffer or b"\n" in buffer:
                    sep = b"\r\n" if b"\r\n" in buffer else b"\n"
                    line, buffer = buffer.split(sep, 1)
                    text = line.decode("utf-8", errors="ignore").strip()
                    if text:
                        code_count += 1
                        logger.info("[Scanner] %s 55256 扫码结果: %s", conn.name, text)
                        self._on_data_received(conn, text)

            except socket.timeout:
                idle = time.time() - last_activity
                if idle > 60.0:
                    try:
                        sock.sendall(b"\r\n")
                        last_activity = time.time()
                    except OSError:
                        logger.warning("[Scanner] %s 55256 心跳探测失败", conn.name)
                        break
                continue
            except OSError as e:
                logger.error("[Scanner] %s 55256 socket 错误: %s", conn.name, e)
                break

        logger.info("[Scanner] %s 55256 监听退出，总收 %d 字节，扫码 %d 次",
                    conn.name, recv_total, code_count)

    def _wmax_listen_loop(self, conn: ScannerConnection, sock: socket.socket):
        """WMax 二进制协议监听模式"""
        try:
            from backend.services.wmax.protocol import (
                DataReceiver, CmdType, parse_new_image, parse_old_image,
            )
            from backend.services.wmax.messages import decode_rpt_code
        except ImportError as e:
            logger.error("[Scanner] WMax 模块导入失败: %s — 回退到文本模式", e)
            conn.device_type = "text"
            sock.sendall(b"LON\r\n")
            self._text_listen_loop(conn, sock, 15.0)
            return

        receiver = DataReceiver()
        sock.settimeout(2.0)
        last_activity = time.time()
        recv_total = 0
        code_count = 0

        sock.sendall(b"LON\r\n")
        logger.info("[Scanner] %s WMax 监听启动", conn.name)

        while not conn._stop_event.is_set():
            try:
                data = sock.recv(65536)
                if not data:
                    logger.warning("[Scanner] %s WMax 收到空数据，连接关闭", conn.name)
                    break
                recv_total += len(data)
                last_activity = time.time()
                cmds = receiver.feed(data)
                for cmd in cmds:
                    if cmd.cmd_type == CmdType.RptCode and cmd.data_part:
                        try:
                            code_info = decode_rpt_code(cmd.data_part)
                            for code in code_info.get("codes", []):
                                barcode = code.get("data", "")
                                if barcode:
                                    code_count += 1
                                    logger.debug("[Scanner] %s WMax 扫码: %s",
                                                 conn.name, barcode)
                                    self._on_data_received(conn, barcode)
                        except Exception as e:
                            logger.error("[Scanner] %s WMax RptCode 解码失败: %s",
                                         conn.name, e)

            except socket.timeout:
                idle = time.time() - last_activity
                if idle > 15.0:
                    try:
                        sock.sendall(b"LON\r\n")
                        last_activity = time.time()
                        logger.debug("[Scanner] %s WMax 发送心跳 (空闲 %.1fs)",
                                     conn.name, idle)
                    except OSError as e:
                        logger.error("[Scanner] %s WMax 心跳失败: %s", conn.name, e)
                        break
                continue
            except OSError as e:
                logger.error("[Scanner] %s WMax socket 错误: %s", conn.name, e)
                break

        logger.info("[Scanner] %s WMax 监听退出，总收 %d 字节，扫码 %d 次",
                    conn.name, recv_total, code_count)

    def _on_data_received(self, conn: ScannerConnection, raw_data: str):
        """收到扫码数据的处理"""
        now = time.time()
        if (raw_data == conn.last_scan
                and (now - conn.last_scan_time) < conn.dedup_interval_sec):
            return

        conn.last_scan = raw_data
        conn.last_scan_time = now

        result: ParseResult = self._parser.parse(raw_data, conn.parse_config)
        if not result.success:
            logger.warning("[Scanner] %s 解析失败: %s (原始: %s)",
                           conn.name, result.error, raw_data)
            return

        channels = conn.broadcast_channels if conn.broadcast_channels else [conn.channel_id]

        for ch_id in channels:
            project_id = None
            if self._project_id_getter:
                try:
                    project_id = self._project_id_getter(ch_id)
                except Exception:
                    pass

            if project_id and self._mes_hook and conn.auto_create_workpiece:
                self._mes_hook.on_scan_received(
                    channel_id=ch_id,
                    serial_no=result.serial_no,
                    raw_data=raw_data,
                    project_id=project_id,
                    device_id=conn.device_id,
                )

        self._inject_barcode_to_external_devices(conn, result.serial_no)

        if len(channels) > 1:
            logger.info("[Scanner] %s: 扫码 → %s (广播到 channels %s)", conn.name, result.serial_no, channels)
        else:
            logger.info("[Scanner] %s: 扫码 → %s", conn.name, result.serial_no)

    def _inject_barcode_to_external_devices(self, conn: ScannerConnection, serial_no: str):
        """扫码后自动把条码注入给同绑定工位的外部设备（如称重器）"""
        try:
            from backend.services.external_device import get_external_device_service
            ext_svc = get_external_device_service()
            if not ext_svc:
                return
            target_channel = conn.channel_id
            injected = []
            for dev_conn in ext_svc._connections.values():
                if dev_conn.channel_id == target_channel and dev_conn.enabled:
                    ext_svc.set_barcode(dev_conn.device_id, serial_no)
                    injected.append(dev_conn.name)
            if injected:
                logger.info("[Scanner] %s: 条码 %s → 注入外部设备: %s",
                            conn.name, serial_no, injected)
        except Exception as e:
            logger.debug("[Scanner] 注入外部设备条码失败: %s", e)

    def inject_scan_result(self, ip: str, barcode: str):
        """外部注入扫码结果（WMax RPT 端口转发用）"""
        conn = None
        for c in self._connections.values():
            if c.ip == ip:
                conn = c
                break
        if conn:
            self._on_data_received(conn, barcode)
        else:
            logger.info("[Scanner] WMax 注入扫码 (无匹配设备): ip=%s data=%s", ip, barcode)
            if self._mes_hook:
                from backend.api.channel_manager import channel_manager
                result = self._parser.parse(barcode, {})
                serial_no = result.serial_no if result.success else barcode
                for ch_id in channel_manager.active_channels():
                    project_id = None
                    if self._project_id_getter:
                        try:
                            project_id = self._project_id_getter(ch_id)
                        except Exception:
                            pass
                    if project_id:
                        self._mes_hook.on_scan_received(
                            channel_id=ch_id,
                            serial_no=serial_no,
                            raw_data=barcode,
                            project_id=project_id,
                        )
                logger.info("[Scanner] WMax 注入扫码 → 广播到 %s: %s",
                            channel_manager.active_channels(), serial_no)


# 全局单例
_scanner_instance: Optional[ScannerService] = None


def get_scanner_service() -> ScannerService:
    global _scanner_instance
    if _scanner_instance is None:
        _scanner_instance = ScannerService()
    return _scanner_instance
