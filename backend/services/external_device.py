"""
通用外部设备接入服务

支持协议:
- tcp: TCP 文本流监听（称重器、传感器等主动推送数据）
- modbus_tcp: Modbus TCP 轮询寄存器
- serial: 串口监听（RS232/RS485/USB转串口）
- serial_modbus_ascii: 串口 Modbus ASCII 主从模式（定时发请求读取寄存器）
- serial_continuous: 串口连续接收模式（设备主动推送数据，被动接收）
- http_poll: HTTP 轮询外部 API

数据流向:
1. 设备发数据 → 协议驱动收到原始数据
2. 按 parse_mode 解析出结构化字段 (如 {"weight": 25.3, "barcode": "BOX-001"})
3. 按 validation_rules 校验合法性
4. 按 data_target 注入到 ClusterCollector 或 MES extra_fields
"""
import json
import logging
import re
import socket
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable

from backend.db.database import SessionLocal
from backend.models.mes_models import ExternalDevice, ExternalDeviceLog

logger = logging.getLogger(__name__)


@dataclass
class DeviceConnection:
    device_id: int
    name: str
    device_role: str
    protocol: str
    ip: Optional[str]
    port: Optional[int]
    serial_port: Optional[str]
    serial_baud: int
    protocol_config: dict
    parse_mode: str
    parse_config: dict
    station_id: Optional[str]
    channel_id: Optional[int]
    data_target: str
    validation_rules: dict
    enabled: bool

    status: str = "disconnected"
    last_data: Optional[str] = None
    last_data_time: float = 0
    last_parsed: Optional[dict] = None
    last_error: str = ""
    _thread: Optional[threading.Thread] = field(default=None, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)


class ExternalDeviceService:

    def __init__(self):
        self._connections: dict[int, DeviceConnection] = {}
        self._lock = threading.Lock()
        self._barcode_buffer: dict[int, str] = {}

    def start_all(self):
        db = SessionLocal()
        try:
            devices = db.query(ExternalDevice).filter(ExternalDevice.enabled == True).all()
            for dev in devices:
                self._start_device(dev)
            logger.info("[ExtDev] 已启动 %d 台外部设备", len(devices))
        except Exception as e:
            logger.error("[ExtDev] 启动失败: %s", e)
        finally:
            db.close()

    def stop_all(self):
        for conn in list(self._connections.values()):
            self._stop_connection(conn)
        self._connections.clear()
        logger.info("[ExtDev] 所有外部设备已断开")

    def add_device(self, dev: ExternalDevice):
        self._start_device(dev)

    def remove_device(self, device_id: int):
        conn = self._connections.pop(device_id, None)
        if conn:
            self._stop_connection(conn)

    def get_all_status(self) -> list:
        results = []
        for conn in self._connections.values():
            results.append({
                "device_id": conn.device_id,
                "name": conn.name,
                "device_role": conn.device_role,
                "protocol": conn.protocol,
                "ip": conn.ip,
                "port": conn.port,
                "serial_port": conn.serial_port,
                "station_id": conn.station_id,
                "status": conn.status,
                "last_data": conn.last_data,
                "last_data_time": datetime.fromtimestamp(conn.last_data_time).isoformat()
                                  if conn.last_data_time else None,
                "last_parsed": conn.last_parsed,
                "last_error": conn.last_error,
            })
        return results

    def set_barcode(self, device_id: int, barcode: str):
        """外部设置条码（当设备本身不带扫码器时，由扫码器回调注入）"""
        self._barcode_buffer[device_id] = barcode

    def test_connection(self, protocol: str, ip: str = None, port: int = None,
                        serial_port: str = None, serial_baud: int = 9600,
                        protocol_config: dict = None,
                        timeout: float = 5.0) -> dict:
        if protocol == "tcp":
            return self._test_tcp(ip, port, timeout)
        elif protocol == "modbus_tcp":
            return self._test_modbus(ip, port, protocol_config or {}, timeout)
        elif protocol == "serial":
            return self._test_serial(serial_port, serial_baud, timeout)
        elif protocol in ("serial_modbus_ascii", "serial_continuous"):
            return self._test_serial_modbus(serial_port, serial_baud,
                                            protocol_config or {}, protocol, timeout)
        elif protocol == "http_poll":
            return self._test_http(protocol_config or {}, timeout)
        return {"success": False, "message": f"未知协议: {protocol}"}

    # ---- 内部方法 ----

    def _start_device(self, dev: ExternalDevice):
        conn = DeviceConnection(
            device_id=dev.id,
            name=dev.name,
            device_role=dev.device_role,
            protocol=dev.protocol,
            ip=dev.ip,
            port=dev.port,
            serial_port=dev.serial_port,
            serial_baud=dev.serial_baud or 9600,
            protocol_config=dev.protocol_config or {},
            parse_mode=dev.parse_mode or "direct",
            parse_config=dev.parse_config or {},
            station_id=dev.station_id,
            channel_id=dev.channel_id,
            data_target=dev.data_target or "cluster",
            validation_rules=dev.validation_rules or {},
            enabled=dev.enabled,
        )
        self._connections[dev.id] = conn
        conn._stop_event.clear()
        conn._thread = threading.Thread(
            target=self._device_loop, args=(conn,),
            daemon=True, name=f"extdev-{dev.id}"
        )
        conn._thread.start()

    def _stop_connection(self, conn: DeviceConnection):
        conn._stop_event.set()
        if conn._thread and conn._thread.is_alive():
            conn._thread.join(timeout=5)

    def _device_loop(self, conn: DeviceConnection):
        retry_delay = 2.0
        max_delay = 30.0

        while not conn._stop_event.is_set():
            try:
                if conn.protocol == "tcp":
                    self._tcp_loop(conn)
                elif conn.protocol == "modbus_tcp":
                    self._modbus_loop(conn)
                elif conn.protocol == "serial":
                    self._serial_loop(conn)
                elif conn.protocol == "serial_modbus_ascii":
                    self._serial_modbus_ascii_loop(conn)
                elif conn.protocol == "serial_continuous":
                    self._serial_continuous_loop(conn)
                elif conn.protocol == "http_poll":
                    self._http_poll_loop(conn)
                else:
                    logger.error("[ExtDev] %s 未知协议: %s", conn.name, conn.protocol)
                    break
            except Exception as e:
                conn.last_error = str(e)
                conn.status = "error"
                logger.error("[ExtDev] %s 异常: %s", conn.name, e)

            conn.status = "disconnected"
            if not conn._stop_event.is_set():
                conn._stop_event.wait(timeout=retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

    # ---- TCP 协议 ----

    def _tcp_loop(self, conn: DeviceConnection):
        conn.status = "connecting"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        try:
            sock.connect((conn.ip, conn.port))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except Exception as e:
            conn.last_error = str(e)
            conn.status = "error"
            sock.close()
            raise

        conn.status = "connected"
        conn.last_error = ""
        delimiter = (conn.protocol_config.get("delimiter", "\r\n")
                     .encode().decode("unicode_escape").encode())
        sock.settimeout(2.0)
        buffer = b""

        try:
            while not conn._stop_event.is_set():
                try:
                    data = sock.recv(4096)
                    if not data:
                        break
                    buffer += data
                    while delimiter in buffer:
                        line, buffer = buffer.split(delimiter, 1)
                        text = line.decode("utf-8", errors="ignore").strip()
                        if text:
                            self._on_raw_data(conn, text)
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            sock.close()

    # ---- Modbus TCP 协议 ----

    def _modbus_loop(self, conn: DeviceConnection):
        try:
            from pymodbus.client import ModbusTcpClient
        except ImportError:
            logger.error("[ExtDev] %s pymodbus 未安装，无法使用 Modbus", conn.name)
            conn.status = "error"
            conn.last_error = "pymodbus 未安装"
            return

        cfg = conn.protocol_config
        unit_id = cfg.get("unit_id", 1)
        register = cfg.get("register", 0)
        count = cfg.get("count", 2)
        func = cfg.get("func", "holding")
        poll_interval = cfg.get("poll_interval", 1.0)

        conn.status = "connecting"
        client = ModbusTcpClient(conn.ip, port=conn.port or 502, timeout=5)
        if not client.connect():
            conn.status = "error"
            conn.last_error = "Modbus 连接失败"
            return

        conn.status = "connected"
        conn.last_error = ""

        try:
            while not conn._stop_event.is_set():
                try:
                    if func == "input":
                        result = client.read_input_registers(register, count, slave=unit_id)
                    else:
                        result = client.read_holding_registers(register, count, slave=unit_id)

                    if result.isError():
                        logger.warning("[ExtDev] %s Modbus 读取错误: %s", conn.name, result)
                    else:
                        raw = str(result.registers)
                        self._on_raw_data(conn, raw)
                except Exception as e:
                    logger.error("[ExtDev] %s Modbus 轮询错误: %s", conn.name, e)

                conn._stop_event.wait(timeout=poll_interval)
        finally:
            client.close()

    # ---- 串口协议 ----

    def _serial_loop(self, conn: DeviceConnection):
        try:
            import serial
        except ImportError:
            logger.error("[ExtDev] %s pyserial 未安装", conn.name)
            conn.status = "error"
            conn.last_error = "pyserial 未安装"
            return

        cfg = conn.protocol_config
        conn.status = "connecting"
        try:
            ser = serial.Serial(
                port=conn.serial_port,
                baudrate=conn.serial_baud,
                bytesize=cfg.get("bytesize", 8),
                parity=cfg.get("parity", "N"),
                stopbits=cfg.get("stopbits", 1),
                timeout=2.0,
            )
        except Exception as e:
            conn.status = "error"
            conn.last_error = str(e)
            raise

        conn.status = "connected"
        conn.last_error = ""
        buffer = b""
        delimiter = (cfg.get("delimiter", "\r\n")
                     .encode().decode("unicode_escape").encode())

        try:
            while not conn._stop_event.is_set():
                try:
                    data = ser.read(1024)
                    if data:
                        buffer += data
                        while delimiter in buffer:
                            line, buffer = buffer.split(delimiter, 1)
                            text = line.decode("utf-8", errors="ignore").strip()
                            if text:
                                self._on_raw_data(conn, text)
                except Exception as e:
                    logger.error("[ExtDev] %s 串口错误: %s", conn.name, e)
                    break
        finally:
            ser.close()

    # ---- 串口 Modbus ASCII 主从模式 ----

    @staticmethod
    def _modbus_ascii_lrc(data: bytes) -> int:
        """Modbus ASCII LRC 校验"""
        return (-sum(data)) & 0xFF

    @staticmethod
    def _build_modbus_ascii_read(slave: int, register: int, count: int) -> bytes:
        """构建 Modbus ASCII 读保持寄存器请求帧 (功能码 03)

        Modbus 寄存器地址约定: 说明书地址 4xxxx → 实际寄存器地址 = (4xxxx - 40001)
        例如 41201 → register = 1200 (0x04B0)
        """
        func_code = 0x03
        pdu = bytes([slave, func_code,
                      (register >> 8) & 0xFF, register & 0xFF,
                      (count >> 8) & 0xFF, count & 0xFF])
        lrc = ExternalDeviceService._modbus_ascii_lrc(pdu)
        hex_str = pdu.hex().upper() + f"{lrc:02X}"
        return f":{hex_str}\r\n".encode("ascii")

    @staticmethod
    def _parse_modbus_ascii_response(frame: str, expected_slave: int = 1) -> Optional[list]:
        """解析 Modbus ASCII 响应帧，返回寄存器值列表"""
        frame = frame.strip()
        if not frame.startswith(":"):
            return None
        hex_str = frame[1:]
        try:
            raw = bytes.fromhex(hex_str)
        except ValueError:
            return None
        if len(raw) < 4:
            return None
        payload = raw[:-1]
        lrc_recv = raw[-1]
        lrc_calc = (-sum(payload)) & 0xFF
        if lrc_recv != lrc_calc:
            return None
        slave = payload[0]
        func = payload[1]
        if slave != expected_slave or func != 0x03:
            return None
        byte_count = payload[2]
        data = payload[3:3 + byte_count]
        registers = []
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                registers.append((data[i] << 8) | data[i + 1])
        return registers

    def _serial_modbus_ascii_loop(self, conn: DeviceConnection):
        """串口 Modbus ASCII 主从模式 — 定时发读取请求，解析响应"""
        try:
            import serial
        except ImportError:
            conn.status = "error"
            conn.last_error = "pyserial 未安装"
            logger.error("[ExtDev] %s pyserial 未安装", conn.name)
            return

        cfg = conn.protocol_config
        slave_id = cfg.get("slave_id", 1)
        doc_register = cfg.get("register", 41201)
        count = cfg.get("count", 2)
        poll_interval = cfg.get("poll_interval", 0.5)
        byte_order = cfg.get("byte_order", "H4H3L2L1")
        data_scale = cfg.get("data_scale", 1.0)

        modbus_register = doc_register - 40001 if doc_register >= 40001 else doc_register

        conn.status = "connecting"
        try:
            ser = serial.Serial(
                port=conn.serial_port,
                baudrate=conn.serial_baud,
                bytesize=cfg.get("bytesize", 8),
                parity=cfg.get("parity", "N"),
                stopbits=cfg.get("stopbits", 1),
                timeout=1.0,
            )
        except Exception as e:
            conn.status = "error"
            conn.last_error = str(e)
            raise

        conn.status = "connected"
        conn.last_error = ""
        logger.info("[ExtDev] %s Modbus ASCII 主从模式启动 (slave=%d, reg=%d, count=%d)",
                    conn.name, slave_id, modbus_register, count)

        try:
            while not conn._stop_event.is_set():
                request = self._build_modbus_ascii_read(slave_id, modbus_register, count)
                try:
                    ser.reset_input_buffer()
                    ser.write(request)
                    time.sleep(0.1)
                    response = b""
                    deadline = time.time() + 2.0
                    while time.time() < deadline:
                        chunk = ser.read(256)
                        if chunk:
                            response += chunk
                            if b"\r\n" in response:
                                break
                        elif response:
                            break

                    if response:
                        text = response.decode("ascii", errors="ignore").strip()
                        registers = self._parse_modbus_ascii_response(text, slave_id)
                        if registers is not None and len(registers) >= 2:
                            if byte_order in ("H4H3L2L1", "big"):
                                raw_val = (registers[0] << 16) | registers[1]
                            elif byte_order in ("L2L1H4H3", "little"):
                                raw_val = (registers[1] << 16) | registers[0]
                            elif byte_order == "H3H4L1L2":
                                raw_val = (((registers[0] & 0xFF) << 24) |
                                           ((registers[0] >> 8) << 16) |
                                           ((registers[1] & 0xFF) << 8) |
                                           (registers[1] >> 8))
                            else:
                                raw_val = (registers[0] << 16) | registers[1]

                            if raw_val >= 0x80000000:
                                raw_val -= 0x100000000
                            weight = raw_val * data_scale
                            self._on_raw_data(conn, f"{weight:.1f}")
                        else:
                            logger.debug("[ExtDev] %s Modbus 响应解析失败: %s",
                                         conn.name, text[:60])
                    else:
                        logger.debug("[ExtDev] %s Modbus 无响应", conn.name)

                except Exception as e:
                    logger.error("[ExtDev] %s Modbus ASCII 通信错误: %s", conn.name, e)

                conn._stop_event.wait(timeout=poll_interval)
        finally:
            ser.close()

    # ---- 串口连续接收模式 ----

    def _serial_continuous_loop(self, conn: DeviceConnection):
        """串口连续接收模式 — 设备主动推送数据（需设备端配置为连续发送模式）"""
        try:
            import serial
        except ImportError:
            conn.status = "error"
            conn.last_error = "pyserial 未安装"
            logger.error("[ExtDev] %s pyserial 未安装", conn.name)
            return

        cfg = conn.protocol_config
        conn.status = "connecting"
        try:
            ser = serial.Serial(
                port=conn.serial_port,
                baudrate=conn.serial_baud,
                bytesize=cfg.get("bytesize", 8),
                parity=cfg.get("parity", "N"),
                stopbits=cfg.get("stopbits", 1),
                timeout=2.0,
            )
        except Exception as e:
            conn.status = "error"
            conn.last_error = str(e)
            raise

        conn.status = "connected"
        conn.last_error = ""
        delimiter = (cfg.get("delimiter", "\r\n")
                     .encode().decode("unicode_escape").encode())

        data_format = cfg.get("data_format", "ascii")
        logger.info("[ExtDev] %s 连续接收模式启动 (format=%s)", conn.name, data_format)

        buffer = b""
        try:
            while not conn._stop_event.is_set():
                try:
                    data = ser.read(1024)
                    if not data:
                        continue
                    buffer += data

                    if data_format == "modbus_ascii":
                        while b"\r\n" in buffer:
                            line, buffer = buffer.split(b"\r\n", 1)
                            text = line.decode("ascii", errors="ignore").strip()
                            if text.startswith(":"):
                                cfg_slave = cfg.get("slave_id", 1)
                                registers = self._parse_modbus_ascii_response(
                                    text, cfg_slave)
                                if registers is not None and len(registers) >= 2:
                                    byte_order = cfg.get("byte_order", "H4H3L2L1")
                                    data_scale = cfg.get("data_scale", 1.0)
                                    if byte_order in ("H4H3L2L1", "big"):
                                        raw_val = (registers[0] << 16) | registers[1]
                                    else:
                                        raw_val = (registers[1] << 16) | registers[0]
                                    if raw_val >= 0x80000000:
                                        raw_val -= 0x100000000
                                    weight = raw_val * data_scale
                                    self._on_raw_data(conn, f"{weight:.1f}")
                            elif text:
                                self._on_raw_data(conn, text)
                    else:
                        while delimiter in buffer:
                            line, buffer = buffer.split(delimiter, 1)
                            text = line.decode("utf-8", errors="ignore").strip()
                            if text:
                                self._on_raw_data(conn, text)
                except Exception as e:
                    logger.error("[ExtDev] %s 串口连续接收错误: %s", conn.name, e)
                    break
        finally:
            ser.close()

    # ---- HTTP 轮询协议 ----

    def _http_poll_loop(self, conn: DeviceConnection):
        import requests as req

        cfg = conn.protocol_config
        url = cfg.get("url")
        method = cfg.get("method", "GET").upper()
        interval = cfg.get("interval", 2.0)
        headers = cfg.get("headers", {})

        if not url:
            conn.status = "error"
            conn.last_error = "未配置 URL"
            return

        conn.status = "connected"
        conn.last_error = ""

        while not conn._stop_event.is_set():
            try:
                if method == "POST":
                    resp = req.post(url, json=cfg.get("body"), headers=headers, timeout=5)
                else:
                    resp = req.get(url, headers=headers, timeout=5)

                if resp.status_code == 200:
                    raw = resp.text
                    self._on_raw_data(conn, raw)
                else:
                    logger.warning("[ExtDev] %s HTTP %d", conn.name, resp.status_code)
            except Exception as e:
                conn.last_error = str(e)
                logger.error("[ExtDev] %s HTTP 请求失败: %s", conn.name, e)

            conn._stop_event.wait(timeout=interval)

    # ---- 数据处理核心 ----

    def _on_raw_data(self, conn: DeviceConnection, raw: str):
        conn.last_data = raw
        conn.last_data_time = time.time()

        parsed = self._parse_data(conn, raw)
        if parsed is None:
            self._log_data(conn, raw, None, False, "解析失败")
            return

        conn.last_parsed = parsed

        is_valid, error = self._validate(conn, parsed)
        barcode = parsed.get("barcode") or self._barcode_buffer.get(conn.device_id)
        self._log_data(conn, raw, parsed, is_valid, error, barcode)
        if not is_valid:
            logger.warning("[ExtDev] %s 校验失败（仍发送）: %s", conn.name, error)
        self._dispatch(conn, parsed, barcode)

    def _parse_data(self, conn: DeviceConnection, raw: str) -> Optional[dict]:
        mode = conn.parse_mode
        cfg = conn.parse_config

        try:
            if mode == "direct":
                return self._parse_direct(raw, conn.device_role)
            elif mode == "regex":
                return self._parse_regex(raw, cfg)
            elif mode == "split":
                return self._parse_split(raw, cfg)
            elif mode == "json_path":
                return self._parse_json(raw, cfg)
            else:
                logger.warning("[ExtDev] %s 未知 parse_mode: %s", conn.name, mode)
                return None
        except Exception as e:
            logger.error("[ExtDev] %s 解析异常: %s", conn.name, e)
            return None

    def _parse_direct(self, raw: str, device_role: str) -> dict:
        raw = raw.strip()
        if device_role == "weight":
            num = re.search(r"[-+]?\d*\.?\d+", raw)
            if num:
                return {"weight": float(num.group()), "raw": raw}
        return {"value": raw, "raw": raw}

    def _parse_regex(self, raw: str, cfg: dict) -> Optional[dict]:
        pattern = cfg.get("pattern", "")
        fields = cfg.get("fields", {})
        m = re.search(pattern, raw)
        if not m:
            return None
        result = {"raw": raw}
        for name, group_idx in fields.items():
            try:
                val = m.group(int(group_idx))
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    pass
                result[name] = val
            except (IndexError, TypeError):
                pass
        return result

    def _parse_split(self, raw: str, cfg: dict) -> Optional[dict]:
        delimiter = cfg.get("delimiter", ",")
        fields = cfg.get("fields", {})
        parts = raw.split(delimiter)
        result = {"raw": raw}
        for name, idx in fields.items():
            idx = int(idx)
            if 0 <= idx < len(parts):
                val = parts[idx].strip()
                try:
                    val = float(val)
                except ValueError:
                    pass
                result[name] = val
        return result

    def _parse_json(self, raw: str, cfg: dict) -> Optional[dict]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        fields = cfg.get("fields", {})
        result = {"raw": raw}
        for name, path in fields.items():
            val = data
            for key in path.split("."):
                if isinstance(val, dict):
                    val = val.get(key)
                else:
                    val = None
                    break
            result[name] = val
        return result

    def _validate(self, conn: DeviceConnection, parsed: dict) -> tuple:
        rules = conn.validation_rules
        if not rules:
            return True, None

        for field_name, rule in rules.items():
            val = parsed.get(field_name)
            if val is None:
                continue
            try:
                val = float(val)
            except (ValueError, TypeError):
                continue
            min_val = rule.get("min")
            max_val = rule.get("max")
            if min_val is not None and val < min_val:
                return False, f"{field_name}={val} < 最小值{min_val}"
            if max_val is not None and val > max_val:
                return False, f"{field_name}={val} > 最大值{max_val}"
        return True, None

    def _dispatch(self, conn: DeviceConnection, parsed: dict, barcode: str = None):
        target = conn.data_target

        if target in ("cluster", "both"):
            self._dispatch_to_cluster(conn, parsed, barcode)

        if target in ("extra_fields", "both"):
            self._dispatch_to_extra(conn, parsed)

    def _dispatch_to_cluster(self, conn: DeviceConnection, parsed: dict, barcode: str = None):
        if not conn.station_id:
            logger.warning("[ExtDev] %s 未配置 station_id，无法注入集群", conn.name)
            return
        if not barcode:
            logger.debug("[ExtDev] %s 无条码，暂存数据等待扫码", conn.name)
            return

        try:
            from backend.services.cluster_collector import get_cluster_collector
            collector = get_cluster_collector()
            config = collector.get_config()
            if not config.get("enabled"):
                return

            is_good = True
            event_name = "ok"
            rules = conn.validation_rules
            if rules:
                for field_name, rule in rules.items():
                    val = parsed.get(field_name)
                    if val is None:
                        continue
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        continue
                    min_val = rule.get("min")
                    max_val = rule.get("max")
                    if (min_val is not None and val < min_val) or \
                       (max_val is not None and val > max_val):
                        is_good = False
                        event_name = f"{field_name}_out_of_range"
                        break

            cycle_context = {
                "cycle": {
                    "result": "OK" if is_good else "NG",
                    "is_good": is_good,
                },
                "workpiece": {"serial_no": barcode},
                "device_data": parsed,
                "device_name": conn.name,
                "device_role": conn.device_role,
            }

            collector.receive_station_report(
                station_id=conn.station_id,
                box_serial=barcode,
                cycle_context=cycle_context,
                source_address=f"{conn.protocol}:{conn.ip or conn.serial_port}:{conn.port or ''}",
                channel_id=conn.channel_id,
                is_good=is_good,
                event_name=event_name,
            )
            logger.info("[ExtDev] %s → 集群汇总: %s = %s (%s)",
                        conn.name, barcode, parsed, "OK" if is_good else "NG")
        except Exception as e:
            logger.error("[ExtDev] %s 集群注入失败: %s", conn.name, e)

    def _dispatch_to_extra(self, conn: DeviceConnection, parsed: dict):
        try:
            from backend.services.mes_gateway import get_mes_gateway
            gw = get_mes_gateway()
            ch = conn.channel_id or 0
            current = gw.get_extra_fields(ch)
            current.update({k: v for k, v in parsed.items() if k != "raw"})
            gw.set_extra_fields(ch, current)
            logger.info("[ExtDev] %s → extra_fields channel %d: %s",
                        conn.name, ch, parsed)
        except Exception as e:
            logger.error("[ExtDev] %s extra_fields 注入失败: %s", conn.name, e)

    def _log_data(self, conn: DeviceConnection, raw: str, parsed: dict,
                  is_valid: bool, error: str = None, barcode: str = None):
        try:
            db = SessionLocal()
            log = ExternalDeviceLog(
                device_id=conn.device_id,
                raw_data=raw[:1024] if raw else None,
                parsed_data=parsed,
                box_serial=barcode,
                is_valid=is_valid,
                error_msg=error,
            )
            db.add(log)
            db.commit()
            db.close()
        except Exception:
            pass

    # ---- 连接测试 ----

    def _test_tcp(self, ip, port, timeout):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((ip, port))
            sock.close()
            return {"success": True, "message": f"TCP {ip}:{port} 连接成功"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _test_modbus(self, ip, port, config, timeout):
        try:
            from pymodbus.client import ModbusTcpClient
            client = ModbusTcpClient(ip, port=port or 502, timeout=timeout)
            if client.connect():
                reg = config.get("register", 0)
                result = client.read_holding_registers(reg, 1, slave=config.get("unit_id", 1))
                client.close()
                if result.isError():
                    return {"success": False, "message": f"Modbus 连接成功但读寄存器失败: {result}"}
                return {"success": True, "message": f"Modbus {ip}:{port} 连接成功, 寄存器值={result.registers}"}
            return {"success": False, "message": "Modbus 连接失败"}
        except ImportError:
            return {"success": False, "message": "pymodbus 未安装"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _test_serial(self, serial_port, baud, timeout):
        try:
            import serial
            ser = serial.Serial(port=serial_port, baudrate=baud, timeout=timeout)
            ser.close()
            return {"success": True, "message": f"串口 {serial_port} 打开成功 (baud={baud})"}
        except ImportError:
            return {"success": False, "message": "pyserial 未安装"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _test_serial_modbus(self, serial_port, baud, config, protocol, timeout):
        """测试串口 Modbus ASCII 连接并尝试读取一次"""
        try:
            import serial
        except ImportError:
            return {"success": False, "message": "pyserial 未安装"}
        try:
            ser = serial.Serial(port=serial_port, baudrate=baud, timeout=timeout)
        except Exception as e:
            return {"success": False, "message": f"串口打开失败: {e}"}

        if protocol == "serial_continuous":
            time.sleep(2.0)
            data = ser.read(1024)
            ser.close()
            if data:
                text = data.decode("ascii", errors="ignore").strip()
                return {"success": True,
                        "message": f"连续接收模式收到数据: {text[:100]}"}
            return {"success": True,
                    "message": f"串口 {serial_port} 打开成功，但 2 秒内未收到数据（设备是否配置为连续发送？）"}

        slave_id = config.get("slave_id", 1)
        doc_reg = config.get("register", 41201)
        modbus_reg = doc_reg - 40001 if doc_reg >= 40001 else doc_reg
        count = config.get("count", 2)

        request = self._build_modbus_ascii_read(slave_id, modbus_reg, count)
        try:
            ser.reset_input_buffer()
            ser.write(request)
            time.sleep(0.3)
            response = ser.read(256)
            ser.close()
            if response:
                text = response.decode("ascii", errors="ignore").strip()
                registers = self._parse_modbus_ascii_response(text, slave_id)
                if registers is not None:
                    return {"success": True,
                            "message": f"Modbus ASCII 测试成功，寄存器值: {registers}"}
                return {"success": True,
                        "message": f"串口有响应但解析失败: {text[:60]}"}
            return {"success": True,
                    "message": f"串口 {serial_port} 打开成功，但 Modbus 无响应（检查从站地址和接线）"}
        except Exception as e:
            ser.close()
            return {"success": False, "message": f"通信失败: {e}"}

    def _test_http(self, config, timeout):
        try:
            import requests as req
            url = config.get("url", "")
            resp = req.get(url, timeout=timeout)
            return {"success": True, "message": f"HTTP {resp.status_code}, body length={len(resp.text)}"}
        except Exception as e:
            return {"success": False, "message": str(e)}


_service_instance: Optional[ExternalDeviceService] = None


def get_external_device_service() -> ExternalDeviceService:
    global _service_instance
    if _service_instance is None:
        _service_instance = ExternalDeviceService()
    return _service_instance
