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


# ---- WMax RPT 激活状态 (合并自 hotfix v2.7.7c) ----
# 抓包 (1111.pcapng) 证实: 官方 IDManager 连上扫码器后立刻发 GetConfigOpt +
# TurnOnOffVideo(on=True), 扫码器进入"常开扫描+条码推 RPT 端口"状态.
# v2.7.7 曾误以为这会让扫码器持续闪光, 改为 ondemand 单次 trigger_on, 结果现场
# 设备根本不识别 (固件不支持 Trigger 单次触发); v2.7.7c 回到"常开"策略, 现场验证
# 扫码器并不会闪, 与 IDManager 行为一致.
#
# 每个 WMaxDevice 实例只激活一次, 断开时通过 dev.on_disconnected 清理.
_rpt_activated_devs: set = set()
_rpt_activate_lock = threading.Lock()


def _activate_rpt_once(dev, ip: str, cause: str = ""):
    """连接后异步发 GetConfigOpt + TurnOnOffVideo(on=True) 激活 RPT 上报.

    同一 dev 对象只激活一次 (按 id(dev) 去重); dev.disconnect 时自动清理.
    放在后台线程跑避免阻塞调用方 (start_device / trigger_on 都在主线程发).
    """
    key = id(dev)
    with _rpt_activate_lock:
        if key in _rpt_activated_devs:
            return
        _rpt_activated_devs.add(key)

    # 注册断开回调: dev 一断开就从 set 里移除, 下次重连会重新激活
    try:
        prev_cb = dev.on_disconnected
    except AttributeError:
        prev_cb = None

    def _on_disc():
        with _rpt_activate_lock:
            _rpt_activated_devs.discard(key)
        if prev_cb is not None:
            try:
                prev_cb()
            except Exception as e:
                logger.warning("[Scanner] %s 原 on_disconnected 回调异常: %s", ip, e)

    try:
        dev.on_disconnected = _on_disc
    except Exception:
        pass

    def _run():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                print(f"[Scanner/WMax] {ip} 激活 RPT 上报 (cause={cause}) ...",
                      flush=True)
                ok = loop.run_until_complete(dev.activate_rpt_reporting())
                print(f"[Scanner/WMax] {ip} activate_rpt_reporting 返回 {ok} "
                      f"(True=成功, False=扫码器未响应 TurnOnOffVideo)",
                      flush=True)
            finally:
                loop.close()
        except Exception as e:
            print(f"[Scanner/WMax] {ip} activate_rpt_reporting 异常: "
                  f"{e}\n{traceback.format_exc()}", flush=True)
            with _rpt_activate_lock:
                _rpt_activated_devs.discard(key)

    threading.Thread(target=_run, daemon=True,
                     name=f"scanner-rpt-activate-{ip}").start()


def _ensure_wmax_connected(ip: str, cause: str = ""):
    """确保 ip:CMD_PORT 上有一个已连接的 WMaxDevice, 返回 dev 或 None.

    - 已连接 → 确保已激活 RPT 上报 (同一 dev 只激活一次), 返回 dev
    - 未连接 → 主动 mgr.connect() 重连一次, 成功后激活 RPT, 返回 dev
    - 重连失败 → 返回 None, 并打印诊断日志
    """
    try:
        from backend.services.wmax.manager import get_wmax_manager
        from backend.services.wmax.device import DEFAULT_PORT as WMAX_CMD_PORT
        mgr = get_wmax_manager()
        dev = mgr.get_device(ip, WMAX_CMD_PORT)
        if dev is not None and getattr(dev.state, "connected", False):
            _activate_rpt_once(dev, ip, cause=f"{cause}/already_connected")
            return dev

        print(f"[Scanner/WMax] {ip} 未连接 (cause={cause}), 尝试重连...", flush=True)
        result = mgr.connect(ip, WMAX_CMD_PORT)
        if result.get("success"):
            dev = mgr.get_device(ip, WMAX_CMD_PORT)
            if dev is not None and getattr(dev.state, "connected", False):
                print(f"[Scanner/WMax] {ip} 重连成功", flush=True)
                _activate_rpt_once(dev, ip, cause=f"{cause}/reconnect")
                return dev
        print(f"[Scanner/WMax] {ip} 重连失败: {result}", flush=True)
        return None
    except Exception as e:
        print(f"[Scanner/WMax] {ip} 重连异常: {e}", flush=True)
        return None


def _is_expected_connection_error(exc: Exception) -> bool:
    """设备离线/未插线时的预期网络错误，不需要每次打印整段堆栈。"""
    return isinstance(exc, (
        ConnectionRefusedError,
        ConnectionResetError,
        ConnectionAbortedError,
        TimeoutError,
        socket.timeout,
        OSError,
    ))


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
    external_only: bool = False
    pairing_group: Optional[str] = None
    ok_rescan_cooldown_sec: int = 0
    late_scan_bind_window_sec: int = 3
    # v2.7.16 扫描模式 (仅 text_lon 协议生效):
    #   "continuous"     : 默认, 持续 LON 续发, 灯一直闪等下一码
    #   "throttled"      : 同 continuous 但每次续 LON 等 throttle_idle_ms 毫秒
    #   "once_per_cycle" : 扫到码 LOFF 灯灭, 等周期结束 (cycle_end) 再续 LON
    scan_mode: str = "continuous"
    throttle_idle_ms: int = 500

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
    # v2.7.16 once_per_cycle 模式专用: 扫到码后置 True, 阻止 listen loop 自动续 LON;
    # cycle_end 时被 resume_after_cycle() 清掉, listen loop 自动续 LON.
    _wait_cycle_resume: bool = False
    # v2.7.16 throttled 模式: 下一次允许续 LON 的时间戳 (time.monotonic()), 0 = 立即.
    _next_lon_after: float = 0.0


class ScannerService:

    def __init__(self):
        self._connections: dict[int, ScannerConnection] = {}
        self._parser = BarcodeParser()
        self._mes_hook = None
        self._project_id_getter = None
        # 合并 2s 内多次 text_lon→auto 升级触发, 避免反复跑 UDP 发现
        self._wmax_discover_pending = {"flag": False, "lock": threading.Lock()}
        # 未匹配来源扫码的重试去抖：同一(ip,barcode)短时间只安排一个重试任务
        self._unmatched_retry_lock = threading.Lock()
        self._unmatched_retry_keys: set[tuple[str, str]] = set()
        # "扫码器测试"期间忽略入码: 用户在 UI 点"测试"会触发扫码器扫一次,
        # 但这只是连通性验证, 不应进 MES / 外部设备 / 工件登记.
        # 这里维护正在测试的 IP 集合, _on_data_received 入口直接丢弃匹配的 IP 上来的码.
        self._testing_ips_lock = threading.Lock()
        self._testing_ips: set[str] = set()

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

    def _trigger_wmax_discover_once(self):
        """合并并发触发: 2 秒内只跑一次 _auto_discover_wmax_bg.

        用于 _start_device 里 text_lon→auto 升级场景: 批量加设备时会连续调多次
        _start_device, 这里合并只跑一次发现, 避免 UDP 广播泛滥.
        """
        with self._wmax_discover_pending["lock"]:
            if self._wmax_discover_pending["flag"]:
                return
            self._wmax_discover_pending["flag"] = True

        def _run():
            time.sleep(2.0)
            try:
                self._auto_discover_wmax_bg()
                print("[Scanner] 已触发 WMax 三端口自动发现 (来自 text_lon 升级)", flush=True)
            except Exception as e:
                print(f"[Scanner] 触发 WMax 发现失败: {e}", flush=True)
            finally:
                with self._wmax_discover_pending["lock"]:
                    self._wmax_discover_pending["flag"] = False

        threading.Thread(target=_run, daemon=True,
                         name="scanner-wmax-discover-trigger").start()

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
                            # v2.7.7c 合并: mgr.connect 是同步 API, 不会自动激活 RPT.
                            # 这里手动激活一次, 让扫码器进入常开上报状态 (与官方 IDManager 一致).
                            dev_just_connected = mgr.get_device(ip, WMAX_CMD_PORT)
                            if dev_just_connected is not None:
                                _activate_rpt_once(dev_just_connected, ip,
                                                   cause="auto_discover/direct_connect")
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

    def _text_lon_send(self, conn: "ScannerConnection", payload: bytes, label: str) -> bool:
        """对 text_lon 设备直发命令 (LON/LOFF). 同步, 带详细诊断日志.

        v2.7.16: 修复"_lon_sent 状态残留导致 LON 发不出去"的 bug.
        之前依赖 listen loop 的状态机 (`if _scanning and not _lon_sent: send`),
        线程重启时 `_scanning=True` 但 `_lon_sent=True` 残留 → 永远不发 LON,
        前端日志只看到 start_scanning 调用、看不到 "LON 已发送" — 抓包确认零字节
        出去. 现在直接 sendall, 不再依赖状态.
        """
        sock = conn._socket
        if sock is None:
            print(f"[Scanner/text_lon] {conn.name} {label} 未发送: socket 为空 "
                  f"(status={conn.status})", flush=True)
            return False
        try:
            fd = sock.fileno()
        except Exception:
            fd = -1
        try:
            sock.sendall(payload)
            print(f"[Scanner/text_lon] {conn.name} {label} 已发送 "
                  f"({len(payload)}B → fd={fd})", flush=True)
            return True
        except OSError as e:
            print(f"[Scanner/text_lon] {conn.name} {label} 发送失败: {e} (fd={fd})",
                  flush=True)
            return False

    def _wmax_trigger(self, conn: ScannerConnection, on: bool):
        """给 auto/wmax 类型的扫码器发 trigger_on/off.

        语义 (v2.7.7c 合并后): 连接后已通过 _activate_rpt_once 激活 RPT 常开上报,
        所以严格说 trigger_on/off 不是必须的 (扫码器会持续识别). 保留这个 fire-and-forget
        命令做三件事:
          1. 兼容需要单次触发的固件变体
          2. 在 start_scanning/stop_scanning 边界留下可观测日志
          3. 如果 dev 在空闲期被扫码器主动断开, trigger_on 时自动重连并重新激活 RPT
        """
        if conn.device_type not in ("auto", "wmax", "wmax_scan"):
            print(f"[Scanner/WMax] skip trigger_{('on' if on else 'off')} '{conn.name}' "
                  f"(device_type={conn.device_type})", flush=True)
            return
        try:
            if on:
                dev = _ensure_wmax_connected(conn.ip, cause="trigger_on")
            else:
                from backend.services.wmax.device import DEFAULT_PORT as WMAX_CMD_PORT
                from backend.services.wmax.manager import get_wmax_manager
                dev = get_wmax_manager().get_device(conn.ip, WMAX_CMD_PORT)
                if dev is None or not getattr(dev.state, "connected", False):
                    print(f"[Scanner/WMax] trigger_off '{conn.name}' SKIPPED: 设备离线，不为停止动作同步重连", flush=True)
                    return
            if dev is None:
                print(f"[Scanner/WMax] trigger_{('on' if on else 'off')} '{conn.name}' "
                      f"SKIPPED: 无法建立 WMax 连接", flush=True)
                return

            print(f"[Scanner/WMax] trigger_{('on' if on else 'off')} '{conn.name}' "
                  f"({conn.ip}) → 发送 TurnOnOffVideo + Trigger + LON/LOFF ...", flush=True)
            if on:
                dev.trigger_on()
            else:
                dev.trigger_off()
            print(f"[Scanner/WMax] trigger_{('on' if on else 'off')} '{conn.name}' "
                  f"命令已发送 (fire-and-forget)", flush=True)
        except Exception as e:
            print(f"[Scanner/WMax] trigger_{('on' if on else 'off')} '{conn.name}' "
                  f"异常: {e}\n{traceback.format_exc()}", flush=True)

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
            if conn.device_type == "text_lon":
                # text_lon: 直接同步发 LON, 不依赖 listen loop 的 _lon_sent 状态机
                if self._text_lon_send(conn, b"LON\r\n", "LON (开始扫码)"):
                    conn._lon_sent = True
            else:
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
            if conn.device_type == "text_lon":
                # text_lon: 直接同步发 LOFF, 不依赖 listen loop 的 _lon_sent 状态机
                if self._text_lon_send(conn, b"LOFF\r\n", "LOFF (停止扫码)"):
                    conn._lon_sent = False
            else:
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

    def resume_after_cycle(self, channel_id: int) -> list[str]:
        """v2.7.16: cycle_end 时调用, 让 once_per_cycle 模式的扫码器恢复扫描.

        once_per_cycle 模式下, 扫到码后 listen loop 会发 LOFF 并 set _wait_cycle_resume=True,
        阻止自动续 LON. 周期结束(无论 OK/NG/作废)时调本方法解除阻塞,
        listen loop 下一轮就会自动 LON, 扫码器灯重新亮起等下一码.

        返回被恢复的扫码器名列表.
        """
        resumed = []
        for conn in self._connections.values():
            if conn.device_type != "text_lon":
                continue
            if (conn.scan_mode or "continuous") != "once_per_cycle":
                continue
            bound = self._resolve_bound_channels(conn)
            if channel_id not in bound:
                continue
            if not getattr(conn, '_wait_cycle_resume', False):
                continue
            conn._wait_cycle_resume = False
            conn._lon_sent = False
            conn._next_lon_after = 0.0
            resumed.append(conn.name)
        if resumed:
            print(f"[Scanner] resume_after_cycle(ch={channel_id}) → {resumed} "
                  f"(once_per_cycle 模式, 周期结束恢复扫描)", flush=True)
        return resumed

    def clear_last_scan(self, channel_id: int) -> list[str]:
        """清除该工位绑定的扫码器的 last_scan / last_scan_time.

        v2.7.16: 配合前端"清除本次扫码"按钮 - 之前只清 MES 那边的状态,
        scanner 本身的去重缓存没动 → 用户清除后重扫同码会被
        `_on_data_received` 的 dedup_interval_sec(默认 2s) 静默吞掉,
        前端看不到"扫码成功". 现在两边都清, 用户立即可重扫同码.

        返回被清除的扫码器名列表 (主要用于日志).
        """
        cleared = []
        for conn in self._connections.values():
            bound = self._resolve_bound_channels(conn)
            if channel_id in bound and conn.last_scan:
                cleared.append(f"{conn.name}({conn.last_scan})")
                conn.last_scan = ""
                conn.last_scan_time = 0
        return cleared

    def test_connection(self, ip: str, port: int, timeout: float = 3.0,
                        device_type: str = "auto") -> dict:
        """测试扫码器, 行为受 device_type 控制:

        - device_type='text_lon': **只走** 55256 LON/LOFF 文本路径 (LON 后 5s 必发 LOFF)
          适用于在 UI 选了"省电模式 / 单次触发"的场景, 不应去打开 WMax 视频流.
        - device_type='auto'/'wmax': 优先 WMax 三端口路径 (GetConfigOpt + TurnOnOffVideo on),
          5s 后**强制关闭视频流** (TurnOnOffVideo off), 否则灯会一直亮.
          失败时再降级到文本 LON/LOFF.

        历史背景: v2.7.7c 改成默认走 WMax, 但**没在测试结束时关视频流**, 导致 WMax 设备
        点了一次"测试"后灯就一直亮, 必须重启设备/断网才能恢复. 这里彻底修掉.
        """
        print(f"[Scanner/WMax] test_connection({ip}:{port}, device_type={device_type}) "
              f"开始 ...", flush=True)

        # 用户在 UI 明确选了 text_lon: 严格只走文本路径, 不要去打开 WMax 视频流
        if device_type == "text_lon":
            return self._test_text_lon(ip, port, timeout)

        # 测试期间标记 IP, 让 RPT 注入路径上扫到的码被丢弃, 不进 MES
        with self._testing_ips_lock:
            self._testing_ips.add(ip)
        wmax_path_done = False
        try:
            dev = _ensure_wmax_connected(ip, cause="test_connection")
            if dev is None:
                print(f"[Scanner/WMax] test_connection({ip}) → WMax 路径不通, 降级文本 LON/LOFF",
                      flush=True)
            else:
                print(f"[Scanner/WMax] test_connection({ip}) → "
                      f"activate_rpt (含 GetConfigOpt) + 收码 5s ...", flush=True)

                collected: list = []
                seen: set = set()
                orig_cb = dev.on_code_received

                def _tap(code_info: dict):
                    try:
                        for c in code_info.get("codes", []):
                            raw = (c.get("data") or "").strip()
                            if raw and raw not in seen:
                                seen.add(raw)
                                collected.append(raw)
                                print(f"[Scanner/WMax] test_connection({ip}) "
                                      f"收到条码: {raw}", flush=True)
                    except Exception:
                        pass
                    if orig_cb is not None:
                        try:
                            orig_cb(code_info)
                        except Exception:
                            pass

                dev.on_code_received = _tap
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    try:
                        ok = loop.run_until_complete(dev.activate_rpt_reporting())
                        print(f"[Scanner/WMax] test_connection({ip}) "
                              f"activate_rpt_reporting={ok}", flush=True)
                    except Exception as e:
                        print(f"[Scanner/WMax] test_connection({ip}) "
                              f"activate_rpt 异常: {e}", flush=True)
                    loop.run_until_complete(asyncio.sleep(5.0))
                    # 关键: 测试结束必须关掉视频流, 否则 WMax 灯会一直亮
                    try:
                        loop.run_until_complete(dev.turn_on_video(on=False, bank_id=1))
                        print(f"[Scanner/WMax] test_connection({ip}) "
                              f"已发送 TurnOnOffVideo off=true, 灯应已熄灭", flush=True)
                    except Exception as e:
                        print(f"[Scanner/WMax] test_connection({ip}) "
                              f"关闭视频流失败: {e}", flush=True)
                finally:
                    try:
                        dev.on_code_received = orig_cb
                    except Exception:
                        pass
                    loop.close()

                print(f"[Scanner/WMax] test_connection({ip}) 收码结束, "
                      f"采集 {len(collected)} 条码", flush=True)
                wmax_path_done = True
                if collected:
                    return {
                        "success": True,
                        "message": f"WMax {ip} 5 秒内扫到 {len(collected)} 条: "
                                   f"{', '.join(collected[:5])}",
                        "device_type": "wmax",
                        "scanned": collected,
                    }
                return {
                    "success": True,
                    "message": f"WMax {ip} 5 秒内未扫到条码 (请把条码对准扫码器再试)",
                    "device_type": "wmax",
                    "scanned": [],
                }
        except Exception as e:
            logger.warning("[Scanner] WMax test_connection 失败, 降级 text_lon: %s", e)
        finally:
            # WMax 路径走完(成功或异常)后, 清掉测试标记;
            # 走 fallthrough 到 _test_text_lon 时让其自行 add (它内部会管 discard).
            with self._testing_ips_lock:
                self._testing_ips.discard(ip)
            if wmax_path_done:
                print(f"[Scanner/Test] {ip} WMax 测试结束, 已恢复正常入码", flush=True)

        # 2) 文本模式降级: 走老 LON/LOFF (仅对真正的 55256 文本模式扫码器有效)
        return self._test_text_lon(ip, port, timeout)

    def _test_text_lon(self, ip: str, port: int, timeout: float) -> dict:
        """文本模式测试: 发 LON, 等 5 秒, 发 LOFF, 关 socket. LON/LOFF 严格配对.

        测试期间在 _testing_ips 标记此 IP, 让常驻 listener 收到的"测试码"被丢弃,
        不要被当成业务扫码登进 MES.
        """
        # 先标记测试中, 防止 connect 完成到线程启动之间的窗口里
        # listener 抢到设备主动推送的码 (不大可能但廉价).
        with self._testing_ips_lock:
            self._testing_ips.add(ip)
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
                    # 多给 0.5s 缓冲, 让 listener 把可能在管线上还没读完的字节排掉,
                    # 再把 ip 移出测试集合, 避免边界码漏丢.
                    time.sleep(0.5)
                    with self._testing_ips_lock:
                        self._testing_ips.discard(ip)
                    print(f"[Scanner/Test] {ip} 测试结束, 已恢复正常入码", flush=True)

            threading.Thread(target=_flash_and_stop, daemon=True,
                             name=f"scanner-test-{ip}").start()
            return {
                "success": True,
                "message": f"连接 {ip}:{port} 成功 (LON/LOFF 闪灯 5 秒, 文本模式)",
                "device_type": "text_lon",
            }
        except Exception as e:
            with self._testing_ips_lock:
                self._testing_ips.discard(ip)
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
            from backend.services.wmax.protocol import pack, Command, CmdType
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
        # v2.7.8: 协议选择交还给用户。前端"扫码器编辑"表单上有"协议"下拉,
        #   - text_lon: 走 55256 LON/LOFF 文本协议(默认,省电模式)
        #              检测开始时发 LON,设备亮灯扫码;停止时发 LOFF,设备灭灯
        #              不开 IMG 视频流,所以扫码器不会一直闪光
        #   - auto / wmax: 走 WMax 三端口 (55266 CMD + 55276 IMG + 55286 RPT)
        #              IMG 视频流持续推送,设备补光灯一直亮
        #              适合需要图像流 / 跨工位 RPT 上报的场景
        # v2.7.7c 之前曾强制把 text_lon 升级成 auto, 是基于"WMax 不吃 LON"的误判
        # (实测 192.168.0.100 完全吃 LON\r\n)。现在保留用户选择,不再自动升级。
        upgraded_from_text_lon = False
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
            external_only=bool(getattr(dev, 'external_only', False)),
            pairing_group=(getattr(dev, 'pairing_group', None) or None),
            ok_rescan_cooldown_sec=int(getattr(dev, 'ok_rescan_cooldown_sec', 0) or 0),
            late_scan_bind_window_sec=int(getattr(dev, 'late_scan_bind_window_sec', 3) or 0),
            scan_mode=(getattr(dev, 'scan_mode', None) or 'continuous'),
            throttle_idle_ms=int(getattr(dev, 'throttle_idle_ms', 500) or 500),
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

        # 如果本次是 text_lon→auto 升级, 且 WMaxDeviceManager 已经连上同 IP 的设备,
        # 直接把 conn.status 标为 connected (前端立刻看到"已连接"), 并触发一次自动发现
        # 让新升级的连接补齐管理通道/激活 RPT 上报.
        if upgraded_from_text_lon:
            try:
                from backend.services.wmax.manager import get_wmax_manager
                from backend.services.wmax.device import DEFAULT_PORT as WMAX_CMD_PORT
                mgr = get_wmax_manager()
                existing_dev = mgr.get_device(conn.ip, WMAX_CMD_PORT)
                if existing_dev is not None and getattr(existing_dev.state, "connected", False):
                    conn.status = "connected"
                    conn.last_error = ""
                    print(f"[Scanner] '{conn.name}' WMax 管理连接已存在, "
                          f"conn.status 直接标为 connected", flush=True)
                    _activate_rpt_once(existing_dev, conn.ip,
                                       cause="start_device/upgraded")
            except Exception as e:
                print(f"[Scanner] 同步 '{conn.name}' conn.status 失败 "
                      f"(不影响后续发现): {e}", flush=True)
            # 触发一次 WMax 自动发现 (合并并发触发)
            self._trigger_wmax_discover_once()

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
                if _is_expected_connection_error(e):
                    logger.warning("[Scanner] %s 连接失败，将继续重试: %s", conn.name, e)
                else:
                    logger.error("[Scanner] %s 连接异常: %s\n%s",
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
        """LON/LOFF 模式监听 — 检测开始时发 LON,停止时发 LOFF。

        实测某些 WMax 型号 (192.168.0.100) 在 55256 端口回的条码是**裸字节,无 \\r\\n
        结尾**。所以这里既支持按行切 (老款 LON/LOFF 扫码枪),也支持
        idle-timeout 切帧 (新款 WMax 文本端口):
          - 收到数据后如果 80ms 内没有更多字节进来,把缓冲整段当一条码送出
          - 仍然优先按 \\r\\n / \\n 切, 已带换行的设备零成本兼容

        另外,设备对"无码"返回 'ERROR' 字符串,该值不会被当作条码上报。
        """
        sock.settimeout(0.08)  # 80ms 短轮询: 既驱动 LON/LOFF 状态机, 又用作 idle-timeout
        buffer = b""
        last_byte_time = 0.0
        last_activity = time.time()
        idle_flush_ms = 0.08

        def _schedule_next_lon():
            """根据 scan_mode 设置下次 LON 续发的时机标志."""
            mode = getattr(conn, 'scan_mode', 'continuous') or 'continuous'
            if mode == "once_per_cycle":
                # 扫到码后 LOFF 灯灭, 等 cycle_end 由 resume_after_cycle() 解锁.
                # 这里也发一次 LOFF 让扫码器立刻熄灭, 反馈"已成功"+"等下一周期".
                try:
                    sock.sendall(b"LOFF\r\n")
                    print(f"[Scanner/text_lon] {conn.name} once_per_cycle: "
                          f"已 LOFF, 等周期结束再开扫", flush=True)
                except OSError as e:
                    print(f"[Scanner/text_lon] {conn.name} once_per_cycle LOFF 失败: {e}",
                          flush=True)
                conn._lon_sent = False
                conn._wait_cycle_resume = True
                return
            if mode == "throttled":
                conn._next_lon_after = time.monotonic() + max(0, conn.throttle_idle_ms) / 1000.0
            else:
                conn._next_lon_after = 0.0
            conn._lon_sent = False

        def _emit(raw: bytes):
            text = raw.decode("utf-8", errors="ignore").strip()
            if not text:
                return
            # 'ERROR' = 扫码器本轮无码超时, 不是条码 → 不进 _on_data_received.
            # 三种模式下都要续 LON 否则扫码器会"睡死". once_per_cycle 模式下
            # ERROR 也续 LON (因为 ERROR 不算"扫到一个有效码", 不该锁住等周期结束).
            if text.upper() == "ERROR":
                if getattr(conn, '_wait_cycle_resume', False):
                    # 罕见: 已经在等周期, 又收到一个 ERROR, 不动.
                    return
                mode = getattr(conn, 'scan_mode', 'continuous') or 'continuous'
                if mode == "throttled":
                    conn._next_lon_after = time.monotonic() + max(0, conn.throttle_idle_ms) / 1000.0
                else:
                    conn._next_lon_after = 0.0
                conn._lon_sent = False
                print(f"[Scanner/text_lon] {conn.name} 收到 ERROR (本轮无码, "
                      f"将自动续发 LON, mode={mode})", flush=True)
                return
            self._on_data_received(conn, text)
            # 扫到一个真码 → 按 scan_mode 决定下次行为.
            _schedule_next_lon()

        # v2.7.16: LON/LOFF 发送主入口在 service 层 (start_scanning / stop_scanning).
        # listen loop 只负责"续发 LON"维持扫描状态, 三种模式控制续发节奏:
        #   continuous     : 收到 ERROR / 码后, 立刻续 LON
        #   throttled      : 同上但每次延迟 throttle_idle_ms 毫秒, 灯闪慢一点
        #   once_per_cycle : 扫到一个真码后 LOFF + 等 cycle_end 解锁
        while not conn._stop_event.is_set():
            if (conn._scanning
                    and not getattr(conn, '_lon_sent', False)
                    and not getattr(conn, '_wait_cycle_resume', False)
                    and time.monotonic() >= getattr(conn, '_next_lon_after', 0.0)):
                try:
                    sock.sendall(b"LON\r\n")
                    conn._lon_sent = True
                    mode = getattr(conn, 'scan_mode', 'continuous') or 'continuous'
                    print(f"[Scanner/text_lon] {conn.name} LON 续发 (mode={mode})",
                          flush=True)
                except OSError as e:
                    print(f"[Scanner/text_lon] {conn.name} LON 续发失败: {e}",
                          flush=True)
                    break

            try:
                data = sock.recv(4096)
                if not data:
                    break
                buffer += data
                last_byte_time = time.time()
                last_activity = last_byte_time

                # 优先按行切 (兼容真·LON/LOFF 扫码枪, 它们带 \r\n)
                while b"\r\n" in buffer or b"\n" in buffer:
                    sep = b"\r\n" if b"\r\n" in buffer else b"\n"
                    line, buffer = buffer.split(sep, 1)
                    _emit(line)

            except socket.timeout:
                # idle 切帧: 缓冲里有数据 + 短 idle 后没新字节 -> 当一条整码 flush
                if buffer and last_byte_time and \
                        (time.time() - last_byte_time) >= idle_flush_ms:
                    chunk, buffer = buffer, b""
                    _emit(chunk)
                    last_byte_time = 0.0

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
            from backend.services.wmax.protocol import DataReceiver, CmdType
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
        # v2.7.16: 关键节点全部 print, 不依赖 log-level (uvicorn 默认 warning 过滤 INFO).
        print(f"[Scanner/recv] {conn.name} 原始数据: '{raw_data}' ({len(raw_data)}B)",
              flush=True)

        # 测试期间任何途径(listener / WMax RPT 注入)收到的码都丢弃,
        # 不要让"测试连通性"的 LON 误触发工件登记.
        with self._testing_ips_lock:
            if conn.ip in self._testing_ips:
                print(f"[Scanner/recv] {conn.name} 测试期间, 已忽略 (不进 MES)",
                      flush=True)
                return
        if (raw_data == conn.last_scan
                and (now - conn.last_scan_time) < conn.dedup_interval_sec):
            # v2.7.16: dedup 命中时打印日志, 让用户能区分"扫码器没扫到"和
            # "扫到了被去重". 之前静默 return → 用户以为扫码器没工作.
            elapsed = now - conn.last_scan_time
            print(f"[Scanner/recv] {conn.name} 同码去重: '{raw_data}' "
                  f"(距上次 {elapsed:.1f}s < dedup {conn.dedup_interval_sec}s, 已忽略). "
                  f"如需强制重扫, 点前端 [清除本次扫码] 按钮.", flush=True)
            # v2.7.16: dedup 命中时**滑动**时间戳, 否则同码一直在视野里的话,
            # 每隔 dedup_interval_sec 就会"漏放一次"重复触发 MES, 引发
            # workpiece_inspections UNIQUE 冲突. 现在只要持续看到同码就一直拦,
            # 直到用户挪开码 >= dedup_interval_sec 才允许同码再次触发.
            conn.last_scan_time = now
            return

        conn.last_scan = raw_data
        conn.last_scan_time = now

        result: ParseResult = self._parser.parse(raw_data, conn.parse_config)
        if not result.success:
            print(f"[Scanner/recv] {conn.name} 解析失败: {result.error} "
                  f"(原始: '{raw_data}', parse_config={conn.parse_config})",
                  flush=True)
            return
        print(f"[Scanner/recv] {conn.name} 解析成功 → serial_no='{result.serial_no}'",
              flush=True)

        # external_only=True 时扫码只用来喂外部设备（秤等），不触发任何视觉 cycle。
        # 适用于"扫完放秤"这类和视觉检测完全解耦的扫码枪。
        if conn.external_only:
            self._inject_barcode_to_external_devices(conn, result.serial_no)
            print(f"[Scanner/recv] {conn.name} external_only=True, "
                  f"仅喂外部设备, 不触发视觉/MES", flush=True)
            return

        channels = conn.broadcast_channels if conn.broadcast_channels else [conn.channel_id]

        for ch_id in channels:
            project_id = None
            if self._project_id_getter:
                try:
                    project_id = self._project_id_getter(ch_id)
                except Exception as _e:
                    print(f"[Scanner/recv] {conn.name} 获取 ch{ch_id} project_id 异常: {_e}",
                          flush=True)

            if not project_id:
                print(f"[Scanner/recv] {conn.name} ch{ch_id}: project_id 为空, "
                      f"前端不会显示扫码成功 (检查工位是否激活了项目)", flush=True)
                continue
            if not self._mes_hook:
                print(f"[Scanner/recv] {conn.name} ch{ch_id}: MES Hook 未启用, "
                      f"扫码不进 MES (检查 MES 配置)", flush=True)
                continue
            if not conn.auto_create_workpiece:
                print(f"[Scanner/recv] {conn.name} ch{ch_id}: auto_create_workpiece=False, "
                      f"扫码不进 MES (检查扫码器配置)", flush=True)
                continue

            print(f"[Scanner/recv] {conn.name} → MES.on_scan_received "
                  f"(ch={ch_id}, serial={result.serial_no}, project={project_id})",
                  flush=True)
            self._mes_hook.on_scan_received(
                channel_id=ch_id,
                serial_no=result.serial_no,
                raw_data=raw_data,
                project_id=project_id,
                device_id=conn.device_id,
            )

        self._inject_barcode_to_external_devices(conn, result.serial_no)

        if len(channels) > 1:
            print(f"[Scanner] {conn.name}: 扫码 → {result.serial_no} "
                  f"(广播到 channels {channels})", flush=True)
        else:
            print(f"[Scanner] {conn.name}: 扫码 → {result.serial_no}", flush=True)

    def _inject_barcode_to_external_devices(self, conn: ScannerConnection, serial_no: str):
        """扫码后把条码注入给和扫码枪配对的外部设备（如称重器）。

        v2.8.1 配对规则：
          - 若扫码枪填了"设备分组号"，只匹配"设备分组号"相同的外设；
          - 否则沿用旧逻辑——匹配"绑定工位"（channel_id）相同、且自身未设分组号的外设。
        """
        try:
            from backend.services.external_device import get_external_device_service
            ext_svc = get_external_device_service()
            if not ext_svc:
                return
            scanner_group = (conn.pairing_group or "").strip() or None
            target_channel = conn.channel_id
            injected = []
            for dev_conn in ext_svc._connections.values():
                if not dev_conn.enabled:
                    continue
                dev_group = (getattr(dev_conn, "pairing_group", None) or "").strip() or None
                if scanner_group is not None:
                    matched = (dev_group == scanner_group)
                else:
                    matched = (dev_group is None and dev_conn.channel_id == target_channel)
                if matched:
                    ext_svc.set_barcode(dev_conn.device_id, serial_no)
                    injected.append(dev_conn.name)
            if injected:
                logger.info("[Scanner] %s: 条码 %s → 注入外部设备: %s (分组=%s)",
                            conn.name, serial_no, injected, scanner_group or f"ch:{target_channel}")
        except Exception as e:
            logger.debug("[Scanner] 注入外部设备条码失败: %s", e)

    def simulate_scan(self, barcode: str, device_id: int = None, channel_id: int = 0,
                      external_only: bool = False, pairing_group: str = None) -> dict:
        """调试入口：不连真实硬件，按真实扫码处理链路注入一条条码。"""
        conn = self._connections.get(device_id) if device_id is not None else None
        if conn is None:
            conn = ScannerConnection(
                device_id=device_id or -999001,
                name="AUTO_QA_虚拟扫码器",
                ip=f"virtual-scanner-{channel_id}",
                port=0,
                channel_id=channel_id,
                enabled=True,
                parse_config={},
                dedup_interval_sec=0,
                auto_create_workpiece=not external_only,
                auto_link_order=True,
                scan_required=False,
                duplicate_scan_action="overwrite",
                warn_no_barcode=False,
                rebind_mode="rescan",
                bind_timing="mid_cycle",
                broadcast_channels=[],
                device_type="virtual",
                external_only=external_only,
                pairing_group=pairing_group or None,
            )

        result = self._parser.parse(barcode, conn.parse_config)
        self._on_data_received(conn, barcode)
        return {
            "success": bool(result.success),
            "device_id": conn.device_id,
            "device_name": conn.name,
            "channel_id": conn.channel_id,
            "barcode": barcode,
            "serial_no": result.serial_no if result.success else barcode,
            "external_only": bool(conn.external_only),
        }

    def inject_scan_result(self, ip: str, barcode: str, _retry_once: bool = False):
        """外部注入扫码结果（WMax RPT 端口转发用）"""
        conn = None
        for c in self._connections.values():
            if c.ip == ip:
                conn = c
                break
        if conn:
            self._on_data_received(conn, barcode)
        else:
            logger.warning("[Scanner] WMax 注入扫码来源未匹配: ip=%s data=%s retry=%s",
                           ip, barcode, _retry_once)
            if not _retry_once:
                retry_key = (ip, barcode)
                should_schedule = False
                with self._unmatched_retry_lock:
                    if retry_key not in self._unmatched_retry_keys:
                        self._unmatched_retry_keys.add(retry_key)
                        should_schedule = True
                if should_schedule:
                    def _run_retry():
                        try:
                            self.inject_scan_result(ip, barcode, _retry_once=True)
                        finally:
                            with self._unmatched_retry_lock:
                                self._unmatched_retry_keys.discard(retry_key)

                    t = threading.Timer(0.8, _run_retry)
                    t.daemon = True
                    t.start()
                return

            if not self._mes_hook:
                return

            try:
                from backend.api.channel_manager import channel_manager
                active_channels = list(channel_manager.active_channels())
            except Exception as e:
                logger.warning("[Scanner] 无法获取活跃工位，未匹配扫码已丢弃: ip=%s err=%s", ip, e)
                return

            # 安全降级策略：
            # - 单工位：允许注入该唯一工位，避免单机场景漏码
            # - 多工位：不再广播，避免串工位；留日志等待来源修复
            if len(active_channels) != 1:
                logger.error("[Scanner] 未匹配来源扫码未分发（多工位保护）: ip=%s active=%s data=%s",
                             ip, active_channels, barcode)
                return

            ch_id = active_channels[0]
            project_id = None
            if self._project_id_getter:
                try:
                    project_id = self._project_id_getter(ch_id)
                except Exception:
                    pass
            if not project_id:
                logger.warning("[Scanner] 未匹配来源扫码未分发（缺少项目）: ip=%s ch=%s data=%s",
                               ip, ch_id, barcode)
                return

            result = self._parser.parse(barcode, {})
            serial_no = result.serial_no if result.success else barcode
            self._mes_hook.on_scan_received(
                channel_id=ch_id,
                serial_no=serial_no,
                raw_data=barcode,
                project_id=project_id,
            )
            logger.warning("[Scanner] 未匹配来源扫码按单工位降级分发: ip=%s ch=%s serial=%s",
                           ip, ch_id, serial_no)


# 全局单例
_scanner_instance: Optional[ScannerService] = None


def get_scanner_service() -> ScannerService:
    global _scanner_instance
    if _scanner_instance is None:
        _scanner_instance = ScannerService()
    return _scanner_instance
