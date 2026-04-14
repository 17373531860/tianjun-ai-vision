"""
通用外部设备接入服务

支持协议:
- tcp: TCP 文本流监听（称重器、传感器等主动推送数据）
- modbus_tcp: Modbus TCP 轮询寄存器
- serial: 串口监听（RS232/RS485/USB转串口）
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
                        serial_port: str = None, protocol_config: dict = None,
                        timeout: float = 5.0) -> dict:
        if protocol == "tcp":
            return self._test_tcp(ip, port, timeout)
        elif protocol == "modbus_tcp":
            return self._test_modbus(ip, port, protocol_config or {}, timeout)
        elif protocol == "serial":
            return self._test_serial(serial_port, timeout)
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

    def _test_serial(self, serial_port, timeout):
        try:
            import serial
            ser = serial.Serial(port=serial_port, baudrate=9600, timeout=timeout)
            ser.close()
            return {"success": True, "message": f"串口 {serial_port} 打开成功"}
        except ImportError:
            return {"success": False, "message": "pyserial 未安装"}
        except Exception as e:
            return {"success": False, "message": str(e)}

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
