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

def _is_expected_connection_error(exc: Exception) -> bool:
    """设备未连接、端口未开、网络不可达等预期离线错误。"""
    return isinstance(exc, (
        ConnectionRefusedError,
        ConnectionResetError,
        ConnectionAbortedError,
        TimeoutError,
        socket.timeout,
        OSError,
    ))


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
                elif conn.protocol == "serial_command":
                    self._serial_command_loop(conn)
                elif conn.protocol == "http_poll":
                    self._http_poll_loop(conn)
                elif conn.protocol == "mock_weight":
                    self._mock_weight_loop(conn)
                elif conn.protocol == "modbus_pulse":
                    # 只写不读: 常驻等「完成脉冲」请求, 实现见 external_device_pulse.py
                    self._modbus_pulse_loop(conn)
                else:
                    logger.error("[ExtDev] %s 未知协议: %s", conn.name, conn.protocol)
                    break
            except Exception as e:
                conn.last_error = str(e)
                conn.status = "error"
                if _is_expected_connection_error(e):
                    logger.warning("[ExtDev] %s 连接/通讯失败，将继续重试: %s", conn.name, e)
                else:
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

    @staticmethod
    def _open_serial_with_retry(port: str, baudrate: int, *,
                                 bytesize: int = 8, parity: str = "N",
                                 stopbits: int = 1, timeout: float = 2.0,
                                 max_retries: int = 3, retry_delay: float = 1.0):
        """打开串口，失败自动重试。解决 Windows 下 PermissionError(拒绝访问)。

        常见场景：上次连接或测试关闭后 Windows 串口资源未完全释放、
        或者连接线程与 test_connection 短时间内先后打开同一端口。
        这种情况下首次 open 失败、间隔 1 秒后再试往往就能成功。

        v2.7.17: 漏 @staticmethod 导致 self.xxx(port=...) 调用时 self 落到 port
        位置参数, 又有 kwargs port=... → "got multiple values for argument 'port'".
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
        # v2.7.17: 之前误用宿主 service 类名 (本模块没 import 它), 触发 NameError.
        # 用 mixin 自身静态方法即可.
        lrc = ExternalDeviceProtocolsMixin._modbus_ascii_lrc(pdu)
        hex_str = pdu.hex().upper() + f"{lrc:02X}"
        return f":{hex_str}\r\n".encode("ascii")

    @staticmethod
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

        # v2.7.17: 把 open + 主循环包成"外层重连循环".
        # 之前 open 在 while 外, 内层 except 只 log 不重连, 导致 USB 拔插后
        # 句柄失效就 EIO 死刷, 重新拔插也救不回来 (新设备号变 ttyUSB1 了).
        # 现在: 任何串口异常 → 关 ser → 退内层 → 外层重新走 _open_serial_with_retry.
        # 重连失败 backoff 5 秒再试.
        import errno as _errno
        reconnect_backoff = 5.0
        ser = None
        first_open = True

        try:
            while not conn._stop_event.is_set():
                # ---- (re)connect ----
                if ser is None:
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
                        if first_open:
                            # 首次失败保持原行为 raise, 让上层服务知道启动失败
                            raise
                        logger.warning(
                            "[ExtDev] %s 串口重连失败, %.1fs 后再试: %s",
                            conn.name, reconnect_backoff, e,
                        )
                        if conn._stop_event.wait(timeout=reconnect_backoff):
                            return
                        continue

                    conn.status = "connected"
                    conn.last_error = ""
                    if first_open:
                        logger.info(
                            "[ExtDev] %s Modbus ASCII 主从模式启动 (slave=%d, reg=%d, count=%d)",
                            conn.name, slave_id, modbus_register, count,
                        )
                    else:
                        logger.info("[ExtDev] %s 串口已重连: %s", conn.name, conn.serial_port)
                    first_open = False

                # ---- 一轮 read/write ----
                request = self._build_modbus_ascii_read(slave_id, modbus_register, count)
                fatal = False
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

                except OSError as e:
                    # EIO / ENODEV / ENXIO 都意味着设备掉线, 必须重连
                    if getattr(e, 'errno', None) in (
                        _errno.EIO, _errno.ENODEV, _errno.ENXIO,
                        _errno.EBADF, _errno.EACCES,
                    ):
                        logger.error(
                            "[ExtDev] %s 串口掉线 (errno=%s, %s), 关闭后重连...",
                            conn.name, e.errno, e,
                        )
                        fatal = True
                    else:
                        logger.error("[ExtDev] %s Modbus ASCII 通信错误: %s", conn.name, e)
                except Exception as e:
                    # serial.SerialException 也归到致命这边
                    msg = str(e)
                    if 'device' in msg.lower() and ('disconnect' in msg.lower() or 'remove' in msg.lower()):
                        fatal = True
                    logger.error("[ExtDev] %s Modbus ASCII 通信错误: %s", conn.name, e)

                if fatal:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                    conn.status = "error"
                    conn.last_error = "串口掉线, 等待重连"
                    if conn._stop_event.wait(timeout=reconnect_backoff):
                        return
                    continue

                conn._stop_event.wait(timeout=poll_interval)
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass

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
                    # v3.41.1b: 有多少收多少 —— 旧写法 ser.read(1024) 要攒满
                    # 1024 字节或等满 2s 超时才返回, 连续输出的秤 (~10 帧/s,
                    # 每帧十几字节) 数据被攒成 2 秒一批, 界面/状态机全部滞后。
                    # 空闲时仍按"等 1 字节到达或超时"阻塞, 不空转。
                    try:
                        n = ser.in_waiting
                    except Exception:
                        n = 0
                    data = ser.read(max(1, n))
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

    @staticmethod
    def _decode_escape(s: str) -> str:
        r"""把前端传来的字面 '\r\n' 还原成真正的 CR LF；真实 CRLF 原样返回。"""
        try:
            return (s or "").encode().decode("unicode_escape")
        except Exception:
            return s or ""

    @staticmethod
    def _write_serial_command(ser, command: str, suffix: str):
        """发送一条 ASCII 指令(发前清输入缓冲, 避免上一帧残留串读)。"""
        ser.reset_input_buffer()
        ser.write((str(command) + (suffix or "")).encode("ascii", errors="ignore"))

    @staticmethod
    def _read_serial_frame(ser, delimiter: bytes, timeout: float):
        """读一帧(到分隔符为止), 超时返回已读到的内容或 None。

        v3.41.1b: 改"有多少收多少、见帧尾立即交货"。旧实现 ser.read(256)
        要攒满 256 字节或等满串口超时(1s)才返回, 而秤一帧只有十几字节,
        每次查询都白等整个超时 → 采样被硬锁在 ~1Hz, 去皮/稳定判定全被
        拖慢 2~3s (2026-07 萍乡百斯特现场"延迟高"根因)。
        无分隔符的帧以 ~60ms 静默作帧尾兜底。
        """
        buf = b""
        deadline = time.time() + timeout
        quiet = 0
        while time.time() < deadline:
            try:
                n = ser.in_waiting
            except Exception:
                n = 0
            chunk = ser.read(n) if n else b""
            if chunk:
                buf += chunk
                quiet = 0
                if delimiter in buf:
                    line, _ = buf.split(delimiter, 1)
                    return line
            else:
                if buf:
                    quiet += 1
                    if quiet >= 3:   # 已有数据且 ~60ms 无新字节 → 当帧尾
                        break
                time.sleep(0.02)
        return buf or None

    def _mock_weight_loop(self, conn: DeviceConnection):
        r"""模拟称重协议 —— 不连真串口, 按脚本生成模拟重量数据流。

        专用于没有真实称重器/真实数据时, 验证称重读数/稳定判定/去皮置零整条链路。
        复用 _on_raw_data 上报, 下游解析(parse_mode/parse_config)与真秤完全一致。

        protocol_config 配置:
          mock_script: [{"weight": 毛重kg, "hold": 持续sec}, ...] 时间轴按序播放;
                       缺省给一段投料全过程(空盘→放盆皮重→投料爬升→到量稳定)。
          loop:        bool, 播完是否循环(模拟下一件), 默认 True。
          poll_interval: float, 出数间隔秒, 默认 0.5。
          mock_format: str, 输出格式模板, 占位符 {value}/{net}=去皮后净重, {gross}=毛重;
                       缺省 "{value}"。可改成模仿真秤的帧(如 "ST,NT,{value}kg")配合解析配置。
          decimals:    int, 重量小数位, 默认 3。
        响应 _command_queue 控制指令(与真秤一致, 供软件去皮/置零联动测试):
          'T' 去皮 / 'Z' 置零: 把当前毛重设为皮重基准, 之后显示净重(归零)。
        """
        cfg = conn.protocol_config or {}
        script = cfg.get("mock_script") or [
            {"weight": 0.0, "hold": 2.0},    # 空盘
            {"weight": 0.20, "hold": 3.0},   # 放空盆(皮重) → 可触发自动去皮
            {"weight": 0.35, "hold": 1.5},   # 投料爬升
            {"weight": 0.50, "hold": 4.0},   # 到量并稳定
        ]
        loop_play = cfg.get("loop", True)
        poll_interval = float(cfg.get("poll_interval", 0.5))
        fmt = cfg.get("mock_format", "{value}")
        decimals = int(cfg.get("decimals", 3))

        conn.status = "connected"
        conn.last_error = ""
        logger.info(f"[{conn.name}] 模拟称重启动 (脚本{len(script)}段, 间隔{poll_interval}s, 循环={loop_play})")

        tare_offset = 0.0  # 去皮/置零基准(毛重)

        def emit(gross: float):
            nonlocal tare_offset
            # 先消费待发控制指令(去皮/置零), 与真秤被上位机控制一致
            while conn._command_queue:
                ctrl = str(conn._command_queue.popleft()).strip().upper()
                if ctrl in ("T", "Z"):
                    tare_offset = gross
                    logger.info(f"[{conn.name}] 模拟称重收到控制指令 {ctrl} → 去皮基准={tare_offset:.{decimals}f}")
            net = gross - tare_offset
            value = f"{net:+.{decimals}f}"
            try:
                raw = fmt.format(value=value, net=value, gross=f"{gross:+.{decimals}f}")
            except Exception:
                raw = value
            self._on_raw_data(conn, raw)

        while not conn._stop_event.is_set():
            for seg in script:
                if conn._stop_event.is_set():
                    break
                try:
                    gross = float(seg.get("weight", 0.0))
                    hold = float(seg.get("hold", 1.0))
                except Exception:
                    gross, hold = 0.0, 1.0
                # 按墙钟计时: 之前按"睡眠次数×间隔"累计, 不含 emit 处理耗时,
                # 长脚本会越播越慢 (实测 4 分钟漂 ~20s), 与外部时间轴(如视频源)
                # 同步的仿真场景会整体错位 —— 真秤无此问题, 仅模拟协议受影响。
                seg_start = time.time()
                while time.time() - seg_start < hold and not conn._stop_event.is_set():
                    emit(gross)
                    conn._stop_event.wait(timeout=poll_interval)
            if not loop_play:
                break
            tare_offset = 0.0  # 一件完成, 复位去皮基准, 准备下一件

    def _serial_command_loop(self, conn: DeviceConnection):
        r"""串口「指令应答」主从模式 —— 上位机发 ASCII 指令、仪表回一帧。

        适配安衡等台秤的应答协议：
        - 查询指令(默认 'R') → 仪表回一帧重量(如 'ST,NT,+0.071kg\r\n')
        - 去皮(默认 'T') / 置零(默认 'Z') 指令由 conn._command_queue 排入，
          每轮轮询优先发送，仪表回 CR LF 应答(丢弃，不当重量数据)。

        与 serial_modbus_ascii 的区别：发的是厂商自定义 ASCII 字符，不是 Modbus 帧。
        重连结构沿用 modbus_ascii loop：任何串口致命异常 → 关闭 → backoff 重连。
        """
        try:
            import serial  # noqa: F401  # 仅探测可用性，实际通过 _open_serial_with_retry 调用
        except ImportError:
            conn.status = "error"
            conn.last_error = "pyserial 未安装"
            logger.error("[ExtDev] %s pyserial 未安装", conn.name)
            return

        cfg = conn.protocol_config
        query_cmd = cfg.get("query_command", "R")
        cmd_suffix = self._decode_escape(cfg.get("command_suffix", "\r\n"))
        poll_interval = cfg.get("poll_interval", 1.0)
        delimiter = self._decode_escape(cfg.get("delimiter", "\r\n")).encode()
        resp_timeout = float(cfg.get("response_timeout", 2.0))

        import errno as _errno
        reconnect_backoff = 5.0
        ser = None
        first_open = True

        try:
            while not conn._stop_event.is_set():
                # ---- (re)connect ----
                if ser is None:
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
                        if first_open:
                            raise
                        logger.warning("[ExtDev] %s 串口重连失败, %.1fs 后再试: %s",
                                       conn.name, reconnect_backoff, e)
                        if conn._stop_event.wait(timeout=reconnect_backoff):
                            return
                        continue

                    conn.status = "connected"
                    conn.last_error = ""
                    if first_open:
                        logger.info("[ExtDev] %s 指令应答模式启动 (查询='%s', 间隔=%.2fs)",
                                    conn.name, query_cmd, poll_interval)
                    else:
                        logger.info("[ExtDev] %s 串口已重连: %s", conn.name, conn.serial_port)
                    first_open = False

                # ---- 一轮: 先发控制指令(去皮/置零), 再发查询指令读重量 ----
                fatal = False
                try:
                    while conn._command_queue:
                        ctrl = conn._command_queue.popleft()
                        self._write_serial_command(ser, ctrl, cmd_suffix)
                        time.sleep(0.1)
                        # 只清掉已到的应答回显, 不作为重量数据。
                        # v3.41.1b: 旧写法 ser.read(256) 会为攒满 256 字节干等
                        # 整个串口超时(1s), 每条控制指令白挂 1 秒。
                        try:
                            n = ser.in_waiting
                        except Exception:
                            n = 0
                        if n:
                            ser.read(n)
                        logger.info("[ExtDev] %s 已发送控制指令: %s", conn.name, ctrl)

                    self._write_serial_command(ser, query_cmd, cmd_suffix)
                    response = self._read_serial_frame(ser, delimiter, resp_timeout)
                    if response:
                        text = response.decode("utf-8", errors="ignore").strip()
                        if text:
                            self._on_raw_data(conn, text)
                    else:
                        logger.debug("[ExtDev] %s 指令 '%s' 无响应", conn.name, query_cmd)

                except OSError as e:
                    if getattr(e, 'errno', None) in (
                        _errno.EIO, _errno.ENODEV, _errno.ENXIO,
                        _errno.EBADF, _errno.EACCES,
                    ):
                        logger.error("[ExtDev] %s 串口掉线 (errno=%s), 关闭后重连...",
                                     conn.name, e.errno)
                        fatal = True
                    else:
                        logger.error("[ExtDev] %s 指令应答通信错误: %s", conn.name, e)
                except Exception as e:
                    msg = str(e)
                    if 'device' in msg.lower() and ('disconnect' in msg.lower() or 'remove' in msg.lower()):
                        fatal = True
                    logger.error("[ExtDev] %s 指令应答通信错误: %s", conn.name, e)

                if fatal:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                    conn.status = "error"
                    conn.last_error = "串口掉线, 等待重连"
                    if conn._stop_event.wait(timeout=reconnect_backoff):
                        return
                    continue

                # v3.41.1b: 轮询间隔切成小片等待, 控制指令(去皮/置零)一入队
                # 立即唤醒发出 —— 旧写法整段睡满 poll_interval, 按钮点下去
                # 最长要等一个完整轮询周期才发, 是现场"点去皮 2~3 秒才见数"
                # 的组成环节之一。
                wait_deadline = time.time() + poll_interval
                while (time.time() < wait_deadline
                       and not conn._command_queue
                       and not conn._stop_event.wait(timeout=0.05)):
                    pass
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass

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
