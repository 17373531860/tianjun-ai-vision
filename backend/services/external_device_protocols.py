"""外设协议接入循环 mixin (TCP/Modbus/Serial/HTTP)"""
import json
import logging
import re
import socket
import threading
import time
from datetime import datetime
from typing import Optional

from backend.db.database import SessionLocal
from backend.models.mes_models import ExternalDevice, ExternalDeviceLog
from backend.services.external_device_models import DeviceConnection

logger = logging.getLogger(__name__)

class ExternalDeviceProtocolsMixin:
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

    def _open_serial_with_retry(port: str, baudrate: int, *,
                                 bytesize: int = 8, parity: str = "N",
                                 stopbits: int = 1, timeout: float = 2.0,
                                 max_retries: int = 3, retry_delay: float = 1.0):
        """打开串口，失败自动重试。解决 Windows 下 PermissionError(拒绝访问)。

        常见场景：上次连接或测试关闭后 Windows 串口资源未完全释放、
        或者连接线程与 test_connection 短时间内先后打开同一端口。
        这种情况下首次 open 失败、间隔 1 秒后再试往往就能成功。
        """
        import serial
        last_err: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                ser = serial.Serial(
                    port=port, baudrate=baudrate,
                    bytesize=bytesize, parity=parity, stopbits=stopbits,
                    timeout=timeout,
                )
                if attempt > 0:
                    logger.info("[ExtDev] %s 第 %d 次重试打开成功", port, attempt + 1)
                return ser
            except PermissionError as e:
                last_err = e
                logger.warning(
                    "[ExtDev] %s 打开被拒绝(PermissionError) 第 %d/%d 次尝试: %s",
                    port, attempt + 1, max_retries, e
                )
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
            except Exception as e:
                last_err = e
                logger.warning(
                    "[ExtDev] %s 打开失败 第 %d/%d 次尝试: %s",
                    port, attempt + 1, max_retries, e
                )
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        assert last_err is not None
        raise last_err

    def _serial_loop(self, conn: DeviceConnection):
        try:
            import serial  # noqa: F401  # 仅探测可用性，实际通过 _open_serial_with_retry 调用
        except ImportError:
            logger.error("[ExtDev] %s pyserial 未安装", conn.name)
            conn.status = "error"
            conn.last_error = "pyserial 未安装"
            return

        cfg = conn.protocol_config
        conn.status = "connecting"
        try:
            ser = self._open_serial_with_retry(
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

    def _modbus_ascii_lrc(data: bytes) -> int:
        """Modbus ASCII LRC 校验"""
        return (-sum(data)) & 0xFF

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

    def _parse_modbus_ascii_response(frame: str, expected_slave: int = 1) -> Optional[list]:
        """解析 Modbus ASCII 响应帧，返回寄存器值列表

        容错说明：
        - 支持单帧 ":ADDR FUNC CNT DATA LRC \r\n"
        - 支持多帧粘包（缓冲区残留 / 轮询+测试并发）：按 ":" 拆，遍历所有候选片段，
          取第一个 LRC 通过、slave/func 匹配的片段
        - 忽略片段中的 \r\n 以及末尾非 hex 噪声
        """
        if not frame:
            return None
        frame = frame.strip()
        if ":" not in frame:
            return None

        candidates = []
        for seg in frame.split(":"):
            seg = seg.split("\r")[0].split("\n")[0].strip()
            if not seg:
                continue
            if len(seg) < 8 or len(seg) % 2 != 0:
                continue
            if not all(c in "0123456789abcdefABCDEF" for c in seg):
                continue
            candidates.append(seg)

        for hex_str in candidates:
            try:
                raw = bytes.fromhex(hex_str)
            except ValueError:
                continue
            if len(raw) < 4:
                continue
            payload = raw[:-1]
            lrc_recv = raw[-1]
            lrc_calc = (-sum(payload)) & 0xFF
            if lrc_recv != lrc_calc:
                continue
            slave = payload[0]
            func = payload[1]
            if slave != expected_slave or func != 0x03:
                continue
            byte_count = payload[2]
            data = payload[3:3 + byte_count]
            registers = []
            for i in range(0, len(data), 2):
                if i + 1 < len(data):
                    registers.append((data[i] << 8) | data[i + 1])
            return registers
        return None

    def _serial_modbus_ascii_loop(self, conn: DeviceConnection):
        """串口 Modbus ASCII 主从模式 — 定时发读取请求，解析响应"""
        try:
            import serial  # noqa: F401  # 仅探测可用性，实际通过 _open_serial_with_retry 调用
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
            ser = self._open_serial_with_retry(
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

    def _serial_continuous_loop(self, conn: DeviceConnection):
        """串口连续接收模式 — 设备主动推送数据（需设备端配置为连续发送模式）"""
        try:
            import serial  # noqa: F401  # 仅探测可用性，实际通过 _open_serial_with_retry 调用
        except ImportError:
            conn.status = "error"
            conn.last_error = "pyserial 未安装"
            logger.error("[ExtDev] %s pyserial 未安装", conn.name)
            return

        cfg = conn.protocol_config
        conn.status = "connecting"
        try:
            ser = self._open_serial_with_retry(
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
