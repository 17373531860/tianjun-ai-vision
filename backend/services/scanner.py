"""
VS600 扫码器 TCP 通讯服务

管理多台威码视 VS600 固定式读码器的 TCP 连接:
- 每台设备一个独立的监听线程
- 自动重连 (指数退避)
- 扫码去重
- 收到数据后调用 MESHookManager.on_scan_received
"""
import socket
import threading
import time
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime

from backend.db.database import SessionLocal
from backend.models.mes_models import ScannerDevice
from backend.services.barcode_parser import BarcodeParser, ParseResult


@dataclass
class ScannerConnection:
    """单个 VS600 的连接状态"""
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

    status: str = "disconnected"
    last_scan: str = ""
    last_scan_time: float = 0
    last_error: str = ""
    _socket: Optional[socket.socket] = field(default=None, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)


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
        """加载所有已配置的设备并启动连接"""
        db = SessionLocal()
        try:
            devices = db.query(ScannerDevice).filter(
                ScannerDevice.enabled == True
            ).all()
            for dev in devices:
                self._start_device(dev)
            print(f"[Scanner] 已启动 {len(devices)} 台扫码器连接", flush=True)
        except Exception as e:
            print(f"[Scanner] 启动失败: {e}", flush=True)
        finally:
            db.close()

    def stop_all(self):
        for conn in self._connections.values():
            self._stop_connection(conn)
        self._connections.clear()
        print("[Scanner] 所有扫码器已断开", flush=True)

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
                "last_scan": conn.last_scan,
                "last_scan_time": datetime.fromtimestamp(conn.last_scan_time).isoformat()
                                  if conn.last_scan_time else None,
                "last_error": conn.last_error,
            })
        return results

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
        """测试 TCP 连接到扫码器"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((ip, port))
            sock.sendall(b"LON\r\n")
            time.sleep(0.5)
            sock.close()
            return {"success": True, "message": f"连接 {ip}:{port} 成功"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    # ---- 内部方法 ----

    def _start_device(self, dev: ScannerDevice):
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
        )
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
        """单个设备的监听循环, 含自动重连"""
        retry_delay = 1.0
        max_delay = 30.0

        while not conn._stop_event.is_set():
            try:
                conn.status = "connecting"
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((conn.ip, conn.port))
                conn._socket = sock
                conn.status = "connected"
                conn.last_error = ""
                retry_delay = 1.0

                sock.sendall(b"LON\r\n")
                print(f"[Scanner] {conn.name} ({conn.ip}:{conn.port}) 已连接",
                      flush=True)

                sock.settimeout(2.0)
                buffer = b""

                while not conn._stop_event.is_set():
                    try:
                        data = sock.recv(4096)
                        if not data:
                            break
                        buffer += data

                        while b"\r\n" in buffer or b"\n" in buffer:
                            sep = b"\r\n" if b"\r\n" in buffer else b"\n"
                            line, buffer = buffer.split(sep, 1)
                            text = line.decode("utf-8", errors="ignore").strip()
                            if text:
                                self._on_data_received(conn, text)

                    except socket.timeout:
                        continue
                    except OSError:
                        break

            except Exception as e:
                conn.last_error = str(e)
                conn.status = "error"

            if conn._socket:
                try:
                    conn._socket.close()
                except Exception:
                    pass
                conn._socket = None

            conn.status = "disconnected"
            if not conn._stop_event.is_set():
                conn._stop_event.wait(timeout=retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

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
            print(f"[Scanner] {conn.name} 解析失败: {result.error} (原始: {raw_data})",
                  flush=True)
            return

        project_id = None
        if self._project_id_getter:
            try:
                project_id = self._project_id_getter(conn.channel_id)
            except Exception:
                pass

        if project_id and self._mes_hook and conn.auto_create_workpiece:
            self._mes_hook.on_scan_received(
                channel_id=conn.channel_id,
                serial_no=result.serial_no,
                raw_data=raw_data,
                project_id=project_id,
                device_id=conn.device_id,
            )

        print(f"[Scanner] {conn.name}: {result.serial_no}", flush=True)


# 全局单例
_scanner_instance: Optional[ScannerService] = None


def get_scanner_service() -> ScannerService:
    global _scanner_instance
    if _scanner_instance is None:
        _scanner_instance = ScannerService()
    return _scanner_instance
