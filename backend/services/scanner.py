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
from backend.core import debug_center

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
                print(f"[Scanner/WMax] {ip} activating RPT reporting (cause={cause}) ...",
                      flush=True)
                ok = loop.run_until_complete(dev.activate_rpt_reporting())
                print(f"[Scanner/WMax] {ip} activate_rpt_reporting returned {ok} "
                      f"(True=成功, False=扫码器未响应 TurnOnOffVideo)",
                      flush=True)
            finally:
                loop.close()
        except Exception as e:
            print(f"[Scanner/WMax] {ip} activate_rpt_reporting error: "
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

        print(f"[Scanner/WMax] {ip} not connected (cause={cause}), reconnecting...", flush=True)
        result = mgr.connect(ip, WMAX_CMD_PORT)
        if result.get("success"):
            dev = mgr.get_device(ip, WMAX_CMD_PORT)
            if dev is not None and getattr(dev.state, "connected", False):
                print(f"[Scanner/WMax] {ip} reconnect success", flush=True)
                _activate_rpt_once(dev, ip, cause=f"{cause}/reconnect")
                return dev
        print(f"[Scanner/WMax] {ip} reconnect failed: {result}", flush=True)
        return None
    except Exception as e:
        print(f"[Scanner/WMax] {ip} reconnect error: {e}", flush=True)
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
    #   "D"              : v3.4.0 容器跨线/区域触发 (几何驱动 LON)
    #   "E"              : v3.50.1 码-合格-码闭环 — 扫到码 LOFF, 只有全部合格
    #                      结算 (广播枪 = 所有在检工位都 OK) / 人工恢复才重新
    #                      亮灯; 重新亮灯时机强制按 ok_only 处理 (无视 resume_on
    #                      存的值), 跨线等几何触发一律不点灯
    scan_mode: str = "continuous"
    throttle_idle_ms: int = 500

    # v3.1.2 多工位广播结算联动 (仅当 broadcast_channels 非空时生效).
    #   "independent" = 各广播工位独立结算 (默认, 旧行为).
    #   "primary"     = 由 primary_settle_channel 指定的工位作为节拍源,
    #                    它结算时强制带动其他广播工位结算当前周期, 实现大小件同步.
    broadcast_settle_mode: str = "independent"
    primary_settle_channel: Optional[int] = None
    # 跟随结算的件数门槛: 工位当前已检出物品数 < 该值时跳过 (避免空箱被冤判 NG).
    primary_settle_min_items: int = 1

    # v3.3.0 码-码闭环结算 (bind_timing="scan_pair") 专用. 扫码 A 后等待 B 的最大秒数,
    # 0 = 不超时, > 0 = 到时强制 NG 结算并重置窗口.
    scan_pair_max_wait_sec: int = 0

    # v3.4.0 D 容器跨线/区域触发扫码 (scan_mode='D' 时生效). 几何由前端配置,
    # source 检测帧循环按几何判定 box 跨线/进区域 → 主动调 scanner._text_lon_send
    # 发 LON; 扫到码后由 mes_hooks 调发 LOFF.
    scan_d_geometry: str = "line"
    scan_d_line: Optional[dict] = None
    scan_d_zone: Optional[list] = None
    scan_d_gone_confirm_frames: int = 30

    # v3.50 扫码器生命周期 (捷昌二期"码-合格-码"闭环), 默认值 = 现状行为:
    #   resume_on: 周期结束后重新亮灯时机 ('cycle_end'=OK/NG 都亮 / 'ok_only'=仅
    #              OK 自动亮, NG 保持灭灯等人工恢复)
    #   rearm_forget_last: 重新亮灯时作废未绑定旧码 + 重置物理去重缓存
    #   strict_ok_dedup: 已判 OK 的条码永久拒绝 (mes_hooks 侧读取)
    resume_on: str = "cycle_end"
    rearm_forget_last: bool = False
    strict_ok_dedup: bool = False

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
    # v3.50 resume_on='ok_only': NG 结算后 resume 被拦下时置 True, 供前端露出
    # "恢复扫码"按钮; 任何真正 resume (OK/手动/重新开始检测) 都清掉.
    _resume_blocked: bool = False
    # v3.50.0a ok_only + 广播多工位: "仅合格"= 这把枪覆盖的所有工位都 OK 才亮灯
    # (捷昌 B 站: 一把枪广播大件+小件双通道清点同一箱, 不能先 OK 的通道抢跑亮灯).
    # 已 OK 工位集合按 _ok_ready_marker (= 本轮箱码 last_scan) 锚定, 换码自动作废,
    # 防上一轮的部分 OK 残留导致下一轮提前亮灯. 单通道枪不走此路径, 零差异.
    _ok_ready_marker: Optional[str] = field(default=None, repr=False)
    _ok_ready_channels: set = field(default_factory=set, repr=False)


class ScannerService:

    def __init__(self):
        self._connections: dict[int, ScannerConnection] = {}
        # USB 键盘扫码枪 (usb_hid) 不建网络连接, 但配置面要与网络枪对齐:
        # 按落库配置构造完整 ScannerConnection 当纯配置载体 (不开 socket/线程),
        # 供 simulate_scan 注入链路与 mes_hooks 按工位配置检索使用。
        self._usb_devices: dict[int, ScannerConnection] = {}
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
            print("[Scanner] all devices in LON/LOFF mode, skip WMax auto-discovery")

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
                print("[Scanner] triggered WMax 3-port auto-discovery (from text_lon upgrade)", flush=True)
            except Exception as e:
                print(f"[Scanner] trigger WMax discovery failed: {e}", flush=True)
            finally:
                with self._wmax_discover_pending["lock"]:
                    self._wmax_discover_pending["flag"] = False

        threading.Thread(target=_run, daemon=True,
                         name="scanner-wmax-discover-trigger").start()

    def _auto_discover_wmax_bg(self):
        """后台线程: UDP 自动发现 + 数据库已知 IP 直连 WMax 管理端口"""
        def _run():
            time.sleep(2)
            print("[Scanner] starting WMax device auto-discovery...")
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

                print(f"[Scanner] WMax UDP discovered {len(results)}, connected {len(connected_mgmt_ips)}")

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
                    print(f"[Scanner] DB device {ip} not found via UDP, trying direct WMax mgmt port {WMAX_CMD_PORT}")
                    try:
                        result = mgr.connect(ip, WMAX_CMD_PORT)
                        if result.get("success"):
                            print(f"[Scanner] {ip}:{WMAX_CMD_PORT} direct connect success")
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
                            print(f"[Scanner] {ip}:{WMAX_CMD_PORT} direct connect failed: {result.get('message', '')}")
                            for c in self._connections.values():
                                if c.ip == ip and c.device_type in ("auto", "wmax"):
                                    c.status = "error"
                                    c.last_error = result.get("message", "WMax 连接失败")
                    except Exception as e:
                        print(f"[Scanner] {ip}:{WMAX_CMD_PORT} direct connect error: {e}")
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

                print(f"[Scanner] WMax auto-discovery done: total mgmt connections {len(connected_mgmt_ips)}")

            except Exception as e:
                import traceback as tb
                print(f"[Scanner] WMax auto-discovery error: {e}\n{tb.format_exc()}")

        t = threading.Thread(target=_run, daemon=True, name="wmax-auto-discover")
        t.start()

    def stop_all(self):
        for conn in self._connections.values():
            self._stop_connection(conn)
        self._connections.clear()
        self._usb_devices.clear()
        logger.info("[Scanner] 所有扫码器已断开")

    def add_device(self, device: ScannerDevice):
        self._start_device(device)

    def remove_device(self, device_id: int):
        self._usb_devices.pop(device_id, None)
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

    # v3.4.2 hotfix: 降级警告"已打过的 (conn_name, bad_ch, ch_count)"快照,
    # 防止 _resolve_bound_channels 被 status 轮询/帧循环高频调用时刷屏 (实测
    # 终端 500 行里 498 行都是同一条警告, 真信号被淹没).
    _resolve_warned_keys: set[tuple] = set()

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
                # 同一 (扫码器, 错配 ch, 系统工位数) 的警告只打一次,
                # 系统工位数变化时会自然重打 (因为 key 包含 ch_count).
                _key = (conn.name, b, ch_count)
                if _key not in cls._resolve_warned_keys:
                    cls._resolve_warned_keys.add(_key)
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
            print(f"[Scanner/text_lon] {conn.name} {label} not sent: socket is empty "
                  f"(status={conn.status})", flush=True)
            return False
        try:
            fd = sock.fileno()
        except Exception:
            fd = -1
        try:
            sock.sendall(payload)
            print(f"[Scanner/text_lon] {conn.name} {label} sent "
                  f"({len(payload)}B → fd={fd})", flush=True)
            return True
        except OSError as e:
            print(f"[Scanner/text_lon] {conn.name} {label} send failed: {e} (fd={fd})",
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

    def _catch_up_lon_for_detecting_channels(self, conn: "ScannerConnection"):
        """v3.4.2: 扫码器连上时回扫 detecting 中的 channel, 补发 LON.

        修 reload 时序 bug: 后端 reload 后 ChannelManager 先恢复 detecting +
        调 start_scanning, 此时 ScannerService 还没建立 TCP, "无匹配设备已跳过";
        扫码器后到的连接事件就需要主动补发 LON, 否则灯永远不亮也不 ERROR.
        """
        if conn.device_type != "text_lon":
            return
        try:
            from backend.api.channel_manager import get_channel_manager
            cm = get_channel_manager()
        except Exception:
            return
        # v3.4.2 尊重"扫码禁用"持久化状态: 如果该 conn 的 broadcast 闭包里
        # 有任何 ch 处于 _disabled_channels, 整个 conn 都不补 LON (因为禁用
        # 是按"扫码器联动"语义的, 一个 ch 禁则该扫码器整体禁).
        try:
            from backend.services.mes_hooks import get_mes_hook
            _mh = get_mes_hook()
        except Exception:
            _mh = None
        bound = self._resolve_bound_channels(conn)
        if _mh is not None:
            for _b in bound:
                if _mh.is_channel_scan_disabled(_b):
                    print(f"[Scanner] catch-up skip {conn.name}: "
                          f"ch={_b} 已被用户禁用扫码", flush=True)
                    return
        for cid in list(getattr(cm, 'channels', {}).keys()):
            if cid not in bound:
                continue
            mgr = cm.channels.get(cid)
            if mgr is None or not getattr(mgr, 'is_detecting', False):
                continue
            # v3.50: ok_only 拦停中 (NG 等人工恢复) 不做 catch-up 亮灯,
            # 否则断线重连会把"NG 灭灯等人工"状态偷偷解掉.
            if getattr(conn, '_resume_blocked', False):
                continue
            # 同步本端 _scanning 标志 (start_scanning 的等价副作用), 这样
            # listen loop 主循环和 _emit 续 LON 路径都会工作.
            conn._scanning = True
            conn._wait_cycle_resume = False
            conn._next_lon_after = 0.0
            try:
                if self._text_lon_send(conn, b"LON\r\n",
                                        f"LON (catch-up ch={cid})"):
                    conn._lon_sent = True
                    print(f"[Scanner] catch-up LON: {conn.name} ch={cid} "
                          f"在 detecting 中, 补发 LON", flush=True)
            except Exception as e:
                print(f"[Scanner] catch-up LON error ch={cid}: {e}", flush=True)

    def apply_channel_disable_change(self, channels, disabled: bool):
        """v3.4.2 由 mes_hooks.set_channel_disabled 调用.

        给 broadcast 与 channels 集合有交集的所有扫码器物理操作:
          • disabled=True  → text_lon: 立即发 LOFF + _scanning=False, 阻断
            listen loop 续 LON; wmax: trigger off
          • disabled=False → 逐个 ch 调 start_scanning, 对仍在 detecting 的
            工位重新发 LON 复活扫码器 (跟"开始检测"一致)
        """
        try:
            chset = {int(x) for x in channels} if channels else set()
        except Exception:
            chset = set()

        affected_off = []
        affected_on_chs = []

        for conn in list(self._connections.values()):
            if conn.status != "connected":
                continue
            bound = set(self._resolve_bound_channels(conn))
            if not bound & chset:
                continue
            if disabled:
                conn._scanning = False
                conn._wait_cycle_resume = False
                conn._resume_blocked = False
                conn._next_lon_after = 0.0
                conn._lon_sent = False
                if conn.device_type == "text_lon":
                    try:
                        self._text_lon_send(
                            conn, b"LOFF\r\n",
                            "LOFF [scanner-disabled]",
                        )
                    except Exception as _e:
                        print(f"[ScannerDisable] LOFF failed {conn.name}: {_e}",
                              flush=True)
                else:
                    try:
                        self._wmax_trigger(conn, on=False)
                    except Exception:
                        pass
                affected_off.append(conn.name)
            else:
                # 启用: 逐个匹配 ch 调 start_scanning 复活. start_scanning 内部
                # 会按 detecting 检查, 没在跑的工位不会乱亮灯.
                for ch in sorted(bound & chset):
                    affected_on_chs.append(ch)

        if disabled and affected_off:
            print(f"[ScannerDisable] LOFF done: {affected_off} (channels={sorted(chset)})",
                  flush=True)
        if not disabled and affected_on_chs:
            for ch in sorted(set(affected_on_chs)):
                try:
                    self.start_scanning(ch)
                except Exception as e:
                    print(f"[ScannerDisable] start_scanning(ch={ch}) error: {e}",
                          flush=True)

    def start_scanning(self, channel_id: int = None):
        """检测开始时调用: 让绑定的扫码器发 LON 进入扫码状态 (ondemand: 灯亮 + 开始扫)"""
        # v3.4.2 尊重"扫码禁用": ch 被禁则跳过, 不让扫码器亮
        try:
            from backend.services.mes_hooks import get_mes_hook
            _mh = get_mes_hook()
        except Exception:
            _mh = None
        if (channel_id is not None and _mh is not None
                and _mh.is_channel_scan_disabled(channel_id)):
            print(f"[Scanner] start_scanning(ch={channel_id}) scanning disabled, skip",
                  flush=True)
            return

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
            # v3.4.2 conn 的 bound 闭包里有任何禁用 ch → 整个 conn 跳过
            if _mh is not None:
                _bnd = self._resolve_bound_channels(conn)
                if any(_mh.is_channel_scan_disabled(_b) for _b in _bnd):
                    skipped.append(
                        f"{conn.name}(disabled in bound={_bnd})"
                    )
                    continue
            targets.append(conn)

        for conn in targets:
            conn._scanning = True
            if conn.device_type == "text_lon":
                # v3.4.2: start_scanning 必须清掉残留的 _wait_cycle_resume + 重置
                # _next_lon_after, 否则上一次会话扫到码后设的"等周期" flag 会拦
                # 本次会话的 ERROR 续 LON, 导致扫码器 LON 5s → LOFF → 永不再亮
                # 的死锁. once_per_cycle / D 模式都依赖这个清理.
                conn._wait_cycle_resume = False
                # v3.50: 用户主动"开始检测" = 人工意志, 清掉 ok_only NG 拦停
                conn._resume_blocked = False
                conn._next_lon_after = 0.0
                # v3.50.1: 广播全 OK 门控的已 OK 集合跨会话作废 — 停止后重开
                # 并重扫同一箱码时, 上一轮某工位的 OK 不得残留计入本轮
                conn._ok_ready_marker = None
                conn._ok_ready_channels = set()
                # 各 scan_mode (含 D) 开始检测都先发首次 LON, 灯立即亮.
                # 后续 ERROR 由 listen loop 自动续 LON 维持工作.
                if self._text_lon_send(conn, b"LON\r\n", "LON (开始扫码)"):
                    conn._lon_sent = True
            else:
                self._wmax_trigger(conn, on=True)
        if targets:
            names = [f"{c.name}[type={c.device_type}]" for c in targets]
            print(f"[Scanner] start_scanning(ch={channel_id}) → {names}")
        else:
            print(f"[Scanner] start_scanning(ch={channel_id}) no matching device, skipped: {skipped}")

    def send_lon_for_channel(self, channel_id: int, reason: str = "") -> int:
        """v3.4.0 D 模式 (容器跨线/区域触发) 专用: 主动发 LON 给绑定该工位的所有
        text_lon 扫码器. 跟 start_scanning 的区别:
          - start_scanning: 检测启动时一次性发, 状态机置 _scanning=True
          - send_lon_for_channel: 帧循环里按需脉冲式发, 不动 _scanning 状态
        返回真正发出 LON 的连接数.

        v3.4.2: box 跨线触发时同步清掉 _wait_cycle_resume + _next_lon_after,
        否则 5s 后 ERROR 进 _emit 会被 _wait_cycle_resume=True 拦截 → 不续 LON
        → 灯亮 5s 又灭, 用户视觉上"亮一下又熄". 清完后让 listen loop 自动续 LON.

        v3.4.2 hotfix: 守门 → 该 ch 或扫码器闭包里任意 ch 被用户禁用就 NOOP.
        否则容器跨线会绕过 disable, 让"禁用扫码"瞬间扫码器又亮.
        """
        try:
            from backend.services.mes_hooks import get_mes_hook
            _mh = get_mes_hook()
        except Exception:
            _mh = None
        if _mh is not None and _mh.is_channel_scan_disabled(channel_id):
            return 0

        cnt = 0
        for conn in list(self._connections.values()):
            if conn.status != "connected":
                continue
            bound = self._resolve_bound_channels(conn)
            if channel_id not in bound:
                continue
            if conn.device_type != "text_lon":
                continue
            if _mh is not None and any(
                _mh.is_channel_scan_disabled(_b) for _b in bound
            ):
                continue
            # v3.50: ok_only 拦停中 (NG 等人工恢复), 新 box 跨线也不亮灯,
            # 出口只有人工恢复 (监控页按钮/触发中心/开始检测).
            if getattr(conn, '_resume_blocked', False):
                continue
            # v3.50.1: "仅合格"枪 (含 E 码-合格-码模式) 的亮灯权独占给 OK 结算 /
            # 人工恢复 — 跨线一律不点灯不解锁. 否则上一箱还没结算时新箱提前跨线
            # 会抢跑亮灯, 破坏"码-合格-码"闭环. OK 后 resume_after_cycle 解锁,
            # listen loop 80ms 内自动续 LON, 不需要跨线补灯; cycle_end 行为不变.
            if self._effective_resume_on(conn) == 'ok_only':
                continue
            if self._text_lon_send(conn, b"LON\r\n",
                                    f"LON [scan_d {reason}]"):
                conn._lon_sent = True
                conn._wait_cycle_resume = False
                conn._next_lon_after = 0.0
                cnt += 1
        return cnt

    def send_loff_for_channel(self, channel_id: int, reason: str = "") -> int:
        """v3.4.0 D 模式专用: 主动发 LOFF 给绑定该工位的所有 text_lon 扫码器.

        v3.4.2 hotfix: LOFF 不需要守门 (即使禁用了, LOFF 也是把灯关掉, 安全).
        但禁用状态下设备本来就不该 LON, 这里发 LOFF 也没意义, 仍然允许 (兜底).
        """
        cnt = 0
        for conn in list(self._connections.values()):
            if conn.status != "connected":
                continue
            bound = self._resolve_bound_channels(conn)
            if channel_id not in bound:
                continue
            if conn.device_type != "text_lon":
                continue
            if self._text_lon_send(conn, b"LOFF\r\n",
                                    f"LOFF [scan_d {reason}]"):
                conn._lon_sent = False
                cnt += 1
        return cnt

    def is_scan_d_for_channel(self, channel_id: int) -> bool:
        """v3.4.0 查询绑定该工位的扫码器是否处于 scan_mode='D' 模式.

        v3.4.2 hotfix: 该 ch 被用户禁用 → 返回 False, 让 source 的
        _scan_d_update 早返回, D 模式状态机彻底休眠 (不会 armed/跨线/触发 LON).
        否则即使灯不亮, 状态机仍会在每帧跑, 一旦下次启用就可能触发"幽灵 LON".
        """
        try:
            from backend.services.mes_hooks import get_mes_hook
            _mh = get_mes_hook()
        except Exception:
            _mh = None
        if _mh is not None and _mh.is_channel_scan_disabled(channel_id):
            return False

        for conn in list(self._connections.values()):
            bound = self._resolve_bound_channels(conn)
            if channel_id not in bound:
                continue
            if (conn.scan_mode or "continuous") == "D":
                return True
        return False

    def get_scan_d_config_for_channel(self, channel_id: int) -> Optional[dict]:
        """v3.4.0 拿到该工位 D 模式的几何配置 (供 source 帧循环判跨线/进区域).
        返回首个绑定该工位且 scan_mode='D' 的扫码器配置. None 表示未配置."""
        for conn in list(self._connections.values()):
            bound = self._resolve_bound_channels(conn)
            if channel_id not in bound:
                continue
            if (conn.scan_mode or "continuous") != "D":
                continue
            return {
                "geometry": getattr(conn, 'scan_d_geometry', 'line') or 'line',
                "line": getattr(conn, 'scan_d_line', None),
                "zone": getattr(conn, 'scan_d_zone', None),
                "gone_confirm_frames": int(
                    getattr(conn, 'scan_d_gone_confirm_frames', 30) or 30
                ),
            }
        return None

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
                # v3.4.2: 一并清 D / once_per_cycle 残留 flag, 避免下次 start
                # 时被上一轮状态污染 (上一轮扫到码 → _wait_cycle_resume=True,
                # stop 不清 → 下次 start ERROR 续 LON 被拦 → 永久灭灯).
                conn._wait_cycle_resume = False
                conn._resume_blocked = False
                conn._next_lon_after = 0.0
                # v3.50.1: 同 start_scanning — 已 OK 集合不跨会话
                conn._ok_ready_marker = None
                conn._ok_ready_channels = set()
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
                    print(f"[Scanner] {conn.name} WMax not connected, cannot trigger")
                    return
                dev.trigger_on()
                print(f"[Scanner] {conn.name} WMax trigger LON")
                scan_before = conn.last_scan_time
                deadline = time.time() + 10
                while time.time() < deadline:
                    if conn.last_scan_time > scan_before:
                        break
                    time.sleep(0.1)
                try:
                    dev.trigger_off()
                except Exception as e:
                    print(f"[Scanner] {conn.name} LOFF failed: {e}")
                if conn.last_scan_time > scan_before:
                    print(f"[Scanner] {conn.name} WMax trigger: scanned {conn.last_scan}, LOFF sent")
                else:
                    print(f"[Scanner] {conn.name} WMax trigger: 10s timeout, LOFF sent")
            except Exception as e:
                print(f"[Scanner] {conn.name} WMax trigger error: {e}")
        threading.Thread(target=_run, daemon=True, name=f"trigger-wmax-{conn.device_id}").start()

    def _do_trigger_once(self, conn: ScannerConnection):
        """文本模式单次触发: 发 LON, 扫到码后或 10 秒超时后自动发 LOFF"""
        def _run():
            try:
                conn._socket.sendall(b"LON\r\n")
                print(f"[Scanner] {conn.name} manual trigger LON")
            except OSError as e:
                print(f"[Scanner] {conn.name} manual LON failed: {e}")
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
                    print(f"[Scanner] {conn.name} manual trigger: scanned, LOFF sent")
                else:
                    print(f"[Scanner] {conn.name} manual trigger: 10s timeout, LOFF sent")
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

    def resume_after_cycle(self, channel_id: int, is_good: bool = None,
                           manual: bool = False) -> list[str]:
        """v2.7.16: cycle_end 时调用, 让"扫到码后停灯"模式 (once_per_cycle / D)
        的扫码器恢复扫描.

        once_per_cycle: 扫到码后 listen loop 发 LOFF + _wait_cycle_resume=True,
        阻止自动续 LON. 周期结束(无论 OK/NG/作废)时调本方法解除阻塞.

        v3.4.2: D 模式同样需要 (扫到码 LOFF + _wait_cycle_resume=True), 解锁
        信号源是"box gone-confirm 完成", 由 _scan_d_update 调本方法. 之前漏了
        D 模式 → 调用无效, _wait_cycle_resume 死锁导致灯永熄, 这是 D 模式
        "扫到码后再也不亮"的根因.

        v3.50 扫码器生命周期 (resume_on 分流):
          - is_good: 本次周期结算结果. None = 调用方不知道结果 (cycle_start
            死锁兜底 / D 模式 box gone 等), 视同"非 OK 确认".
          - manual: True = 人工恢复 (监控页按钮 / API / 触发中心 resume_scanner),
            无条件放行.
          - conn.resume_on == 'ok_only' 时: 仅 is_good=True 或 manual=True 才恢复,
            NG/未知结果保持灭灯并置 _resume_blocked=True (前端露出恢复按钮).
          - conn.resume_on == 'cycle_end' (默认): 行为与历史完全一致, 全部恢复.
          - conn.rearm_forget_last: 真正恢复时作废未绑定旧码 (mes_hooks
            clear_pending_scan) + 重置物理去重缓存, 保证旧码不自动挂新周期.

        返回被恢复的扫码器名列表.
        """
        resumed = []
        for conn in self._connections.values():
            if conn.device_type != "text_lon":
                continue
            if (conn.scan_mode or "continuous") not in ("once_per_cycle", "D", "E"):
                continue
            bound = self._resolve_bound_channels(conn)
            if channel_id not in bound:
                continue
            if not getattr(conn, '_wait_cycle_resume', False):
                continue
            # v3.50: ok_only 分流 — NG/未知结果不自动恢复, 等人工出口
            # (E 码-合格-码模式强制 ok_only, 见 _effective_resume_on)
            if (not manual
                    and self._effective_resume_on(conn) == 'ok_only'
                    and is_good is not True):
                # v3.50.1: 只有"明确 NG 结算"才标 _resume_blocked (前端弹"恢复
                # 扫码"). is_good=None 只是"这次调用不知道结果", 不等于 NG —
                # 来源有 start_cycle 的 v2.7.17 死锁兜底 (每开一个周期都调一次)
                # 和 D 模式 box gone. 之前一律标 blocked, 导致 E 枪扫码开周期
                # 当场就挂出"恢复扫码"按钮 (现场实测), 操作员一点就在本单还没
                # 结算时放行下一码, 闭环破掉. 未知结果只保持灭灯, 不惊动人。
                if is_good is False and not getattr(conn, '_resume_blocked', False):
                    conn._resume_blocked = True
                    print(f"[Scanner] resume_after_cycle(ch={channel_id}) "
                          f"{conn.name}: resume_on=ok_only 且结果为 NG → 保持灭灯, "
                          f"等人工恢复 (监控页按钮/触发中心 resume_scanner)",
                          flush=True)
                continue
            # v3.50.0a: ok_only + 广播多工位 — "仅合格"的正确语义是这把枪覆盖的
            # 所有工位都 OK 才恢复亮灯 (捷昌 B 站: 大件+小件双通道清点同一箱,
            # 先凑齐的通道不能抢跑亮灯). 已 OK 集合按本轮箱码 (last_scan) 锚定,
            # 换码自动作废. 单通道枪 len(bound)<=1 不进此分支, 行为零差异.
            # 任一通道 NG 走上面的 blocked 分支 → 集合永远集不齐 → 等人工恢复.
            if (not manual
                    and self._effective_resume_on(conn) == 'ok_only'
                    and len(set(bound)) > 1):
                # 分母 = 广播工位里"当前正在检测"的那些; 没在检测的工位永远不会
                # 出 OK, 不过滤会把灯锁死 (B 站只开一个通道跑时的现场陷阱).
                _required = self._detecting_channels(bound)
                _marker = conn.last_scan or ""
                if getattr(conn, '_ok_ready_marker', None) != _marker:
                    conn._ok_ready_marker = _marker
                    conn._ok_ready_channels = set()
                conn._ok_ready_channels.add(channel_id)
                _missing = _required - conn._ok_ready_channels
                if _missing:
                    print(f"[Scanner] resume_after_cycle(ch={channel_id}) "
                          f"{conn.name}: resume_on=ok_only 广播枪, 本工位 OK "
                          f"但工位 {sorted(_missing)} 还没 OK → 继续灭灯等全 OK",
                          flush=True)
                    continue
            conn._ok_ready_marker = None
            conn._ok_ready_channels = set()
            conn._wait_cycle_resume = False
            conn._resume_blocked = False
            conn._lon_sent = False
            conn._next_lon_after = 0.0
            resumed.append(conn.name)
            # v3.50: 重新亮灯时作废旧码 + 重置物理去重缓存
            if getattr(conn, 'rearm_forget_last', False):
                self._rearm_forget(conn, channel_id)
        if resumed:
            print(f"[Scanner] resume_after_cycle(ch={channel_id}) → {resumed} "
                  f"(once_per_cycle/D 模式, 周期或 box 结束恢复扫描"
                  f"{', 人工恢复' if manual else ''})", flush=True)
        return resumed

    @staticmethod
    def _effective_resume_on(conn) -> str:
        """v3.50.1: E 码-合格-码模式重新亮灯时机强制 ok_only (无视存的 resume_on);
        其余模式按配置, 缺省 cycle_end。"""
        if (conn.scan_mode or "") == "E":
            return "ok_only"
        return getattr(conn, 'resume_on', 'cycle_end') or 'cycle_end'

    def _detecting_channels(self, bound) -> set:
        """v3.50.0a: 广播工位里当前正在检测的子集 (全 OK 亮灯门控的分母).

        ChannelManager 拿不到时退化为全部 bound (宁可多等不可漏等).
        """
        try:
            from backend.api.channel_manager import get_channel_manager
            cm = get_channel_manager()
            out = set()
            for b in bound:
                mgr = cm.get(b)
                if mgr is not None and getattr(mgr, 'is_detecting', False):
                    out.add(b)
            # 全都没在检测 (理论上到不了这, cycle_end 只会来自 detecting 通道):
            # 退化为全部 bound, 避免空分母直接放行.
            return out or set(bound)
        except Exception:
            return set(bound)

    def _rearm_forget(self, conn: "ScannerConnection", channel_id: int):
        """v3.50 rearm_forget_last: 重新亮灯时作废未绑定旧码 + 重置物理去重.

        - 物理去重缓存: conn.last_scan/last_scan_time 归零, 让工人有意重扫同码
          不被 dedup_interval_sec 吞掉.
        - 未绑定旧码: mes_hooks.clear_pending_scan(force=False) 清 pending
          工件/队列/last_scan_event, 已绑入周期的不动.
        """
        conn.last_scan = ""
        conn.last_scan_time = 0
        try:
            if self._mes_hook is not None:
                cleared = self._mes_hook.clear_pending_scan(channel_id, force=False)
                if cleared.get("pending_workpiece_id") or cleared.get("pending_queue"):
                    print(f"[Scanner] rearm_forget_last: {conn.name} ch{channel_id} "
                          f"作废旧码 {cleared}", flush=True)
        except Exception as e:
            print(f"[Scanner] rearm_forget_last error ({conn.name} ch{channel_id}): {e}",
                  flush=True)

    def resume_scanning_manual(self, channel_id: int) -> list[str]:
        """v3.50 人工恢复扫码 (resume_on='ok_only' 下 NG 的出口).

        监控页按钮 / POST /scanner/resume / 触发中心 resume_scanner 动作共用.
        """
        return self.resume_after_cycle(channel_id, manual=True)

    def is_resume_blocked(self, channel_id: int) -> bool:
        """v3.50: 该工位是否有扫码器因 resume_on='ok_only' + NG 被拦在灭灯态."""
        for conn in self._connections.values():
            if conn.device_type != "text_lon":
                continue
            if not getattr(conn, '_resume_blocked', False):
                continue
            if channel_id in self._resolve_bound_channels(conn):
                return True
        return False

    def notify_cycle_settled(self, channel_id: int) -> list[int]:
        """v3.1.2: 某工位刚完成结算 → 检查是否要带动其他广播工位强制结算.

        触发条件 (任一扫码器满足):
          - broadcast_channels 包含 channel_id
          - broadcast_settle_mode == "primary"
          - primary_settle_channel == channel_id

        命中后, 把同 broadcast_channels 里其他工位调 force_settle_pending_cycle().
        防重入: 来源 channel 本身正处于"被联动结算"状态时跳过, 由调用方在 source 侧
                  设置 _force_settling_in_progress 标志保证不会循环.

        返回真正被强制结算的 channel_id 列表.
        """
        triggered: list[int] = []
        for conn in list(self._connections.values()):
            if (conn.broadcast_settle_mode or "independent") != "primary":
                continue
            if conn.primary_settle_channel != channel_id:
                continue
            broadcast = list(conn.broadcast_channels or [])
            if not broadcast:
                continue
            min_items = max(0, int(conn.primary_settle_min_items or 0))
            for other_ch in broadcast:
                if other_ch == channel_id:
                    continue
                ok = self._dispatch_force_settle(other_ch, min_items, source_channel=channel_id, scanner_name=conn.name)
                if ok:
                    triggered.append(other_ch)
        return triggered

    def _dispatch_force_settle(self, target_channel: int, min_items: int,
                                source_channel: int, scanner_name: str) -> bool:
        """通过 ChannelManager 找目标 channel 的 VideoSourceManager 并强制结算."""
        try:
            from backend.api.channel_manager import get_channel_manager
        except Exception as e:
            print(f"[Scanner] notify_cycle_settled: import ChannelManager failed: {e}", flush=True)
            return False

        try:
            mgr = get_channel_manager().get(target_channel)
        except Exception:
            return False
        if mgr is None:
            return False

        force_fn = getattr(mgr, "force_settle_pending_cycle", None)
        if not callable(force_fn):
            return False

        try:
            triggered = force_fn(min_items=min_items, reason=f"primary_ch{source_channel}_via_{scanner_name}")
        except Exception as e:
            print(f"[Scanner] force_settle_pending_cycle ch={target_channel} failed: {e}", flush=True)
            return False

        if triggered:
            print(f"[Scanner] '{scanner_name}' 主工位 ch{source_channel} 结算 → "
                  f"带动 ch{target_channel} 强制结算 (min_items={min_items}, 已结算={triggered})", flush=True)
            return True
        return False

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
                print(f"[Scanner/WMax] test_connection({ip}) -> WMax path down, fallback text LON/LOFF",
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

                print(f"[Scanner/WMax] test_connection({ip}) scan receive ended, "
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
                print(f"[Scanner/Test] {ip} WMax test ended, normal scan input restored", flush=True)

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
                    print(f"[Scanner/Test] {ip} test ended, normal scan input restored", flush=True)

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
        # USB 键盘式扫码枪 (usb_hid): 插上即键盘, 由前端全局键盘捕获处理。后端不建网络
        # 连接、不起监听线程, 仅作为设备表记录存在 (用途/工位/连接配置在 parse_config.usb),
        # 供前端读取。放在最前面 return, 避免走下面的 socket 连接逻辑 (它没有 IP)。
        if raw_type == 'usb_hid':
            # v3.46: USB 键盘枪按落库真实配置构造完整连接对象(不开 socket, 纯配置载体),
            # 注入扫码时走与网络枪同一条处理链(_on_data_received: 去重/解析/自动建
            # 工件/进 MES), mes_hooks 的按工位配置检索也一并查它 —— 与网络扫码器
            # 行为对齐(先扫后检/无码告警/重复扫码策略/OK冷却/迟到补绑等)。
            # 仅"绑工件"类用途参与闸门 (拉工单/报警确认按钮的枪与周期绑定无关,
            # 即使 DB 字段被误置 true 也不拦周期)。
            usb_cfg = (dev.parse_config or {}).get('usb') or {}
            usage = usb_cfg.get('usage') or 'pull'
            gate_applicable = usage in ('bind', 'both')
            self._usb_devices[dev.id] = ScannerConnection(
                device_id=dev.id,
                name=dev.name,
                ip='',
                port=0,
                channel_id=dev.channel_id or 0,
                enabled=bool(dev.enabled),
                parse_config=dev.parse_config or {},
                dedup_interval_sec=dev.dedup_interval_sec if dev.dedup_interval_sec is not None else 2,
                auto_create_workpiece=dev.auto_create_workpiece,
                auto_link_order=dev.auto_link_order,
                scan_required=bool(getattr(dev, 'scan_required', False)) and gate_applicable,
                duplicate_scan_action=getattr(dev, 'duplicate_scan_action', 'overwrite') or 'overwrite',
                warn_no_barcode=bool(getattr(dev, 'warn_no_barcode', False)) and gate_applicable,
                rebind_mode=getattr(dev, 'rebind_mode', 'rescan') or 'rescan',
                bind_timing=getattr(dev, 'bind_timing', 'mid_cycle') or 'mid_cycle',
                broadcast_channels=list(dev.broadcast_channels or []),
                external_only=bool(getattr(dev, 'external_only', False)),
                pairing_group=getattr(dev, 'pairing_group', None),
                ok_rescan_cooldown_sec=int(getattr(dev, 'ok_rescan_cooldown_sec', 0) or 0),
                late_scan_bind_window_sec=int(getattr(dev, 'late_scan_bind_window_sec', 3) or 0),
                scan_pair_max_wait_sec=int(getattr(dev, 'scan_pair_max_wait_sec', 0) or 0),
                # v3.50: USB 键盘枪无灯控, resume_on/rearm 不适用, 但强制去重
                # 由 mes_hooks 按连接配置判定, USB 枪同样生效
                strict_ok_dedup=bool(getattr(dev, 'strict_ok_dedup', False)),
                status='usb',
                device_type='usb_hid',
            )
            logger.info("[Scanner] %s 为 USB 键盘扫码枪, 后端跳过网络连接 "
                        "(先扫后检=%s 无码告警=%s 重复扫码=%s 去重=%ss)", dev.name,
                        self._usb_devices[dev.id].scan_required,
                        self._usb_devices[dev.id].warn_no_barcode,
                        self._usb_devices[dev.id].duplicate_scan_action,
                        self._usb_devices[dev.id].dedup_interval_sec)
            return
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
            broadcast_settle_mode=(getattr(dev, 'broadcast_settle_mode', None) or 'independent'),
            primary_settle_channel=(getattr(dev, 'primary_settle_channel', None) if getattr(dev, 'primary_settle_channel', None) is not None else None),
            primary_settle_min_items=int(getattr(dev, 'primary_settle_min_items', 1) or 1),
            scan_pair_max_wait_sec=int(getattr(dev, 'scan_pair_max_wait_sec', 0) or 0),
            scan_d_geometry=(getattr(dev, 'scan_d_geometry', None) or 'line'),
            scan_d_line=getattr(dev, 'scan_d_line', None),
            scan_d_zone=getattr(dev, 'scan_d_zone', None),
            scan_d_gone_confirm_frames=int(getattr(dev, 'scan_d_gone_confirm_frames', 30) or 30),
            resume_on=(getattr(dev, 'resume_on', None) or 'cycle_end'),
            rearm_forget_last=bool(getattr(dev, 'rearm_forget_last', False)),
            strict_ok_dedup=bool(getattr(dev, 'strict_ok_dedup', False)),
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
                print(f"[Scanner] sync '{conn.name}' conn.status failed "
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
                if debug_center.is_on("backend.scanner"):
                    debug_center.dbg("backend.scanner", "扫码器连接成功", f"name={conn.name} addr={conn.ip}:{conn.port} type={conn.device_type}")

                if conn.device_type == "text_lon":
                    print(f"[Scanner] {conn.name} ({conn.ip}:{conn.port}) connected (LON/LOFF mode)")
                    # v3.4.2: 修 reload 时序 bug. 后端 reload 时 ChannelManager
                    # 自动恢复 detecting 状态会先调 start_scanning, 但那时
                    # ScannerService 还没连上扫码器 → "无匹配设备已跳过", 扫码器
                    # 后来才连上但没人发 LON → 灯不亮, 也不会 ERROR 续 LON.
                    # 这里连上后回扫 detecting 中的 channel, 主动补一次 LON.
                    try:
                        self._catch_up_lon_for_detecting_channels(conn)
                    except Exception as _e:
                        print(f"[Scanner] catch-up LON error: {_e}", flush=True)
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
                if debug_center.is_on("backend.scanner"):
                    debug_center.dbg("backend.scanner", "监听循环异常", f"name={conn.name} addr={conn.ip}:{conn.port} err={e}")
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
            if debug_center.is_on("backend.scanner"):
                debug_center.dbg("backend.scanner", "扫码器断开,等待重连", f"name={conn.name} status={conn.status} held={connected_duration:.0f}s next_retry={retry_delay:.0f}s")
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
            # v3.4.2: D 模式扫到码后行为同 once_per_cycle (LOFF + 等 cycle_end
            # 由 resume_after_cycle 解锁). 容器模式下还会有 source._scan_d_update
            # 的 box 跨线 send_lon_for_channel 在 cycle 之外提前触发 LON.
            if mode in ("once_per_cycle", "D", "E"):
                # 灭灯动作统一走 service 方法 (幂等): _on_data_received 已灭过灯,
                # 这里只是兜底, 不会重复发 LOFF.
                self._loff_on_code_received(conn)
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
            # 各 scan_mode 都要续 LON 否则扫码器会"睡死". once_per_cycle 模式下
            # ERROR 也续 LON (因为 ERROR 不算"扫到一个有效码", 不该锁住等周期结束).
            # v3.4.2: D 模式 ERROR 改回续 LON (与 continuous 一致). 之前 v3.4.1
            # 设计意图是"按 box 跨线脉冲触发", 但实际产线用户期望"开始检测扫码器
            # 立即并持续工作", 所以放弃精细脉冲式控制. send_lon_for_channel 在
            # box 跨线时仍然调用, 但已在亮态时是 no-op, 不影响行为.
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
                print(f"[Scanner/text_lon] {conn.name} received ERROR (no code this round, "
                      f"将自动续发 LON, mode={mode})", flush=True)
                return
            self._on_data_received(conn, text)
            # 扫到一个真码 → 按 scan_mode 决定下次行为.
            _schedule_next_lon()

        # v2.7.16: LON/LOFF 发送主入口在 service 层 (start_scanning / stop_scanning).
        # listen loop 只负责"续发 LON"维持扫描状态, 各模式控制续发节奏:
        #   continuous     : 收到 ERROR / 码后, 立刻续 LON
        #   throttled      : 同上但每次延迟 throttle_idle_ms 毫秒, 灯闪慢一点
        #   once_per_cycle : 扫到一个真码后 LOFF + 等 cycle_end 解锁
        #   D              : v3.4.2 续 LON 行为同 continuous (开始检测后持续工作);
        #                    box 跨线时 source._scan_d_update 仍会调
        #                    send_lon_for_channel, 但灯本来就在亮, 实际是 no-op
        while not conn._stop_event.is_set():
            if (conn._scanning
                    and not getattr(conn, '_lon_sent', False)
                    and not getattr(conn, '_wait_cycle_resume', False)
                    and time.monotonic() >= getattr(conn, '_next_lon_after', 0.0)):
                try:
                    sock.sendall(b"LON\r\n")
                    conn._lon_sent = True
                    mode = getattr(conn, 'scan_mode', 'continuous') or 'continuous'
                    print(f"[Scanner/text_lon] {conn.name} LON re-sent (mode={mode})",
                          flush=True)
                except OSError as e:
                    print(f"[Scanner/text_lon] {conn.name} LON re-send failed: {e}",
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

    def _loff_on_code_received(self, conn: "ScannerConnection") -> bool:
        """v3.50.1: 扫到真码 → 灭灯 + 锁住续 LON ("扫到码灭灯"的 C/D/E 三模式)。

        幂等: 已在"等恢复"态直接返回, 重复调用不重发 LOFF.

        为什么放在 service 方法而不是 listen loop 的闭包里: 监听线程在
        _init_mes_services 阶段就启动了 (远早于热补丁应用), 已进入循环的线程
        用的是旧闭包, 重绑 _text_lon_listen_loop 救不到它 — 而本方法由
        _on_data_received 按实例查找调用, 重绑后当场生效.
        """
        if conn.device_type != "text_lon":
            return False
        mode = getattr(conn, 'scan_mode', 'continuous') or 'continuous'
        if mode not in ("once_per_cycle", "D", "E"):
            return False
        if getattr(conn, '_wait_cycle_resume', False):
            return False
        ok = self._text_lon_send(conn, b"LOFF\r\n",
                                 f"LOFF (扫到码灭灯, mode={mode})")
        conn._lon_sent = False
        conn._wait_cycle_resume = True
        return ok

    def _on_data_received(self, conn: ScannerConnection, raw_data: str):
        """收到扫码数据的处理"""
        now = time.time()
        # v2.7.16: 关键节点全部 print, 不依赖 log-level (uvicorn 默认 warning 过滤 INFO).
        print(f"[Scanner/recv] {conn.name} raw data: '{raw_data}' ({len(raw_data)}B)",
              flush=True)
        if debug_center.is_on("backend.scanner"):
            debug_center.dbg("backend.scanner", "收到条码", f"device={conn.device_id} name={conn.name} barcode={raw_data or '-'}")

        # 测试期间任何途径(listener / WMax RPT 注入)收到的码都丢弃,
        # 不要让"测试连通性"的 LON 误触发工件登记.
        with self._testing_ips_lock:
            if conn.ip in self._testing_ips:
                print(f"[Scanner/recv] {conn.name} during test, ignored (not into MES)",
                      flush=True)
                return
        # v3.50.1: 物理层面"扫到码就灭灯", 与去重/绑定结果无关 (与 listen loop
        # 原语义一致). 提到 dedup 判定之前, 否则去重命中直接 return 就漏灭灯.
        self._loff_on_code_received(conn)
        if (raw_data == conn.last_scan
                and (now - conn.last_scan_time) < conn.dedup_interval_sec):
            # v2.7.16: dedup 命中时打印日志, 让用户能区分"扫码器没扫到"和
            # "扫到了被去重". 之前静默 return → 用户以为扫码器没工作.
            elapsed = now - conn.last_scan_time
            print(f"[Scanner/recv] {conn.name} duplicate dedup: '{raw_data}' "
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
            print(f"[Scanner/recv] {conn.name} parse failed: {result.error} "
                  f"(原始: '{raw_data}', parse_config={conn.parse_config})",
                  flush=True)
            if debug_center.is_on("backend.scanner"):
                debug_center.dbg("backend.scanner", "条码解析失败", f"name={conn.name} barcode={raw_data or '-'} err={getattr(result, 'error', None) or '-'}")
            return
        print(f"[Scanner/recv] {conn.name} parse success -> serial_no='{result.serial_no}'",
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
                    print(f"[Scanner/recv] {conn.name} get ch{ch_id} project_id error: {_e}",
                          flush=True)

            if not project_id:
                print(f"[Scanner/recv] {conn.name} ch{ch_id}: project_id empty, "
                      f"前端不会显示扫码成功 (检查工位是否激活了项目)", flush=True)
                continue
            if not self._mes_hook:
                print(f"[Scanner/recv] {conn.name} ch{ch_id}: MES Hook not enabled, "
                      f"扫码不进 MES (检查 MES 配置)", flush=True)
                continue
            if not conn.auto_create_workpiece:
                print(f"[Scanner/recv] {conn.name} ch{ch_id}: auto_create_workpiece=False, "
                      f"扫码不进 MES (检查扫码器配置)", flush=True)
                continue

            print(f"[Scanner/recv] {conn.name} → MES.on_scan_received "
                  f"(ch={ch_id}, serial={result.serial_no}, project={project_id})",
                  flush=True)
            if debug_center.is_on("backend.scanner"):
                debug_center.dbg("backend.scanner", "条码注入 MES", f"channel={ch_id} serial={result.serial_no or '-'} project={project_id} device={conn.device_id}")
            self._mes_hook.on_scan_received(
                channel_id=ch_id,
                serial_no=result.serial_no,
                raw_data=raw_data,
                project_id=project_id,
                device_id=conn.device_id,
            )

        self._inject_barcode_to_external_devices(conn, result.serial_no)

        if len(channels) > 1:
            print(f"[Scanner] {conn.name}: scan -> {result.serial_no} "
                  f"(广播到 channels {channels})", flush=True)
        else:
            print(f"[Scanner] {conn.name}: scan -> {result.serial_no}", flush=True)

        # v3.13 M1.1: scan_received 插件 hook — 主路径已完成 (MES on_scan_received 已广播
        # 给所有目标通道 + 外部设备已注入条码). dedup/解析失败/external_only/test 等早返回
        # 路径**不** fire (扫码事件本身未完成接收语义).
        try:
            from backend.plugin_system.hook_dispatch import fire_plugin_hook
            fire_plugin_hook("scan_received", "post_scan", "post", {
                "scanner_device_id": conn.device_id,
                "scanner_name": conn.name,
                "scanner_ip": conn.ip,
                "serial_no": result.serial_no,
                "raw": raw_data,
                "broadcast_channel_ids": list(channels),
            })
        except Exception as e:
            print(f"[Plugin] scan_received hook error (isolated, main flow continues): {e}")

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
                if debug_center.is_on("backend.scanner"):
                    debug_center.dbg("backend.scanner", "条码注入外部设备", f"name={conn.name} serial={serial_no or '-'} devices={injected}")
        except Exception as e:
            logger.debug("[Scanner] 注入外部设备条码失败: %s", e)

    def simulate_scan(self, barcode: str, device_id: int = None, channel_id: int = 0,
                      external_only: bool = False, pairing_group: str = None) -> dict:
        """调试入口：不连真实硬件，按真实扫码处理链路注入一条条码。

        v3.46: USB 键盘枪的码也从这里进来(前端全局键盘捕获→本端点)。解析顺序:
          1) 显式 device_id → 网络连接表, 再查 USB 枪登记表
          2) 无 device_id → 按工位匹配启用的 USB 枪 (让 USB 枪吃到自己的
             真实落库配置: 去重/解析/重复策略/自动建工件等, 与网络枪同链路)
          3) 都没有 → 临时虚拟连接 (QA/调试兜底, 行为与历史一致)
        """
        conn = self._connections.get(device_id) if device_id is not None else None
        if conn is None and device_id is not None:
            conn = self._usb_devices.get(device_id)
        if conn is None:
            for rec in self._usb_devices.values():
                usage = ((rec.parse_config or {}).get('usb') or {}).get('usage') or 'pull'
                if usage not in ('bind', 'both'):
                    continue  # 拉工单/确认按钮用途的枪不承担绑定链路配置
                chs = rec.broadcast_channels if rec.broadcast_channels else [rec.channel_id]
                if rec.enabled and channel_id in chs:
                    conn = rec
                    break
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
        if debug_center.is_on("backend.scanner"):
            debug_center.dbg("backend.scanner", "WMax RPT 注入条码", f"ip={ip or '-'} barcode={barcode or '-'} retry={_retry_once}")
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
