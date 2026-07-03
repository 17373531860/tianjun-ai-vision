"""
通用外部设备接入服务

支持协议:
- tcp: TCP 文本流监听（称重器、传感器等主动推送数据）
- modbus_tcp: Modbus TCP 轮询寄存器
- serial: 串口监听（RS232/RS485/USB转串口）
- serial_modbus_ascii: 串口 Modbus ASCII 主从模式（定时发请求读取寄存器）
- serial_continuous: 串口连续接收模式（设备主动推送数据，被动接收）
- serial_command: 串口指令应答模式（发 ASCII 指令 R 读数 / T 去皮 / Z 置零，适配安衡等台秤）
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
from datetime import datetime
from typing import Optional

from backend.db.database import SessionLocal
from backend.models.mes_models import ExternalDevice, ExternalDeviceLog
from backend.services.external_device_models import DeviceConnection
from backend.services.external_device_protocols import ExternalDeviceProtocolsMixin
from backend.services.external_device_pipeline import ExternalDevicePipelineMixin
from backend.services.external_device_test import ExternalDeviceTestMixin

logger = logging.getLogger(__name__)


class ExternalDeviceService(
    ExternalDeviceProtocolsMixin,
    ExternalDevicePipelineMixin,
    ExternalDeviceTestMixin,
):
    """外设服务核心 (设备管理/启停/状态查询)。
    协议循环/数据pipeline/连通性测试 已拆到 mixin。
    """
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

    def cleanup_old_logs(self, keep_bound: bool = True,
                         older_than_seconds: int = 3600) -> int:
        """定时清理外设日志。

        v2.7.9: 称重器/传感器在稳定阶段会写 stabilizing 过程日志，长期跑会堆积。
        只清理"无 box_serial 绑定"的过程日志，有扫码绑定的业务日志保留。

        Args:
            keep_bound: True=只删 box_serial 为 NULL 的记录（默认）；
                        False=按 older_than_seconds 无条件清理
            older_than_seconds: 删除 N 秒之前的记录，默认 1 小时

        Returns:
            被删除的行数
        """
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError
        from datetime import datetime, timedelta
        for attempt in range(3):
            db = SessionLocal()
            try:
                if keep_bound:
                    sql = text(
                        "DELETE FROM external_device_logs "
                        "WHERE box_serial IS NULL "
                        "AND created_at < :cutoff"
                    )
                else:
                    sql = text(
                        "DELETE FROM external_device_logs "
                        "WHERE created_at < :cutoff"
                    )
                cutoff = datetime.utcnow() - timedelta(seconds=int(older_than_seconds))
                res = db.execute(sql, {"cutoff": cutoff})
                deleted = res.rowcount or 0
                db.commit()
                if deleted > 0:
                    logger.info("[ExtDev] 定时清理日志: 删除 %d 条 (keep_bound=%s, cutoff=%s)",
                                deleted, keep_bound, cutoff)
                return deleted
            except OperationalError as e:
                db.rollback()
                msg = str(e).lower()
                if ("locked" in msg or "busy" in msg) and attempt < 2:
                    import time as _t
                    _t.sleep(0.3)
                    continue
                logger.warning("[ExtDev] 清理日志失败: %s", e)
                return 0
            except Exception as e:
                db.rollback()
                logger.warning("[ExtDev] 清理日志异常: %s", e)
                return 0
            finally:
                db.close()
        return 0

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
        """外部设置条码（当设备本身不带扫码器时，由扫码器回调注入）。

        两种配对模式（按 `conn.pairing_mode`）:

        - ``stable`` (默认, v2.7.10 老逻辑):
            条码先存入 ``_barcode_buffer``。若目标设备是已稳定的称重器，
            立即用缓存的稳定值补发一次 dispatch，否则节流逻辑会把
            "先上称再扫码"场景永远卡住。
        - ``instant`` (v3.1.1 新):
            扫码瞬间 → 立即用 ``last_parsed`` 里"最近一次称重器读数"
            (不论是否稳定) 派发一次, **派发后立即把 buffer 里这个 device_id 的
            条码清掉**, 防止下一帧称重数据再误用同一条码。
            适用于"工件不会回零、不允许丢数据"的连续上料流水线。
            如果秤还没出过任何读数 (`last_parsed is None`), 则把条码留在
            buffer, 等下一帧 ``_on_raw_data`` 到达时立即派发 (兜底)。
        """
        self._barcode_buffer[device_id] = barcode

        conn = self._connections.get(device_id)
        if conn is None:
            return
        if conn.device_role != "weight":
            return

        # v3.1.1 instant 模式: 扫码即派发, 派发后立即清 buffer
        if (conn.pairing_mode or "stable").lower() == "instant":
            self._instant_dispatch_on_scan(conn, barcode)
            return

        # 老 stable 模式: 已稳定才补发
        if conn._stable_state != "stable":
            return
        if conn._last_reported_value is None:
            return

        parsed = dict(conn.last_parsed or {})
        parsed["weight"] = conn._last_reported_value
        parsed["_raw_value"] = conn._last_reported_value

        is_valid, error = self._validate(conn, parsed)
        raw_repr = str(conn.last_data or conn._last_reported_value)

        try:
            self._log_data(conn, raw_repr, parsed, is_valid,
                           error or "barcode_late_bind", barcode)
            self._dispatch(conn, parsed, barcode)
            logger.info("[ExtDev] %s 扫码迟到 → 用稳定值补发: weight=%.4f, barcode=%s",
                        conn.name, conn._last_reported_value, barcode)
        except Exception as e:
            logger.error("[ExtDev] %s 扫码迟到补发失败: %s", conn.name, e)

    def _instant_dispatch_on_scan(self, conn: DeviceConnection, barcode: str):
        """instant 模式下, 扫码事件触发的"瞬时绑定"派发。

        策略: 用 conn.last_parsed (最近一次任意读数, 不论 stable / unstable / idle)
              立即派发一次, 然后清 _barcode_buffer 里这个设备的条码。
              如果秤还没出过任何读数, 则保留 buffer, 让下一帧 _on_raw_data 兜底。
        """
        if conn.last_parsed is None:
            logger.info("[ExtDev] %s instant 暂存条码 %s, 等首帧称重数据兜底派发",
                        conn.name, barcode)
            return

        parsed = dict(conn.last_parsed)
        weight = parsed.get("weight")
        if weight is None:
            weight = parsed.get("_raw_value")
        if weight is None:
            logger.warning("[ExtDev] %s instant last_parsed 无 weight 字段, 跳过: %s",
                           conn.name, parsed)
            return
        try:
            parsed["weight"] = float(weight)
            parsed["_raw_value"] = float(weight)
        except (TypeError, ValueError):
            logger.warning("[ExtDev] %s instant 重量字段非数字: %s", conn.name, weight)
            return

        is_valid, error = self._validate(conn, parsed)
        raw_repr = str(conn.last_data or parsed["weight"])

        try:
            self._log_data(conn, raw_repr, parsed, is_valid,
                           error or "instant_pair", barcode)
            self._dispatch(conn, parsed, barcode)
            self._barcode_buffer.pop(conn.device_id, None)
            logger.info("[ExtDev] %s instant 扫码即绑: weight=%.4f, barcode=%s "
                        "(stable=%s)", conn.name, parsed["weight"], barcode,
                        conn._stable_state)
        except Exception as e:
            logger.error("[ExtDev] %s instant 派发失败: %s", conn.name, e)

    def send_command(self, device_id: int, command: str) -> dict:
        """向「串口指令应答」设备主动下发一条控制指令(去皮 T / 置零 Z 等)。

        指令排入设备线程的命令队列, 由线程在下一轮轮询时发送, 避免多线程
        并发写同一串口。serial_command(真秤) 与 mock_weight(模拟测试) 均支持。
        """
        conn = self._connections.get(device_id)
        if conn is None:
            return {"success": False, "message": "设备未连接或未启用"}
        if conn.protocol not in ("serial_command", "mock_weight"):
            return {"success": False, "message": "仅「串口指令应答」/「模拟称重」设备支持主动发指令"}
        command = (command or "").strip()
        if not command:
            return {"success": False, "message": "指令不能为空"}
        conn._command_queue.append(command)
        return {"success": True, "message": f"指令 '{command}' 已排入发送队列",
                "device_id": device_id}

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
        elif protocol == "serial_command":
            return self._test_serial_command(serial_port, serial_baud,
                                             protocol_config or {}, timeout)
        elif protocol == "http_poll":
            return self._test_http(protocol_config or {}, timeout)
        return {"success": False, "message": f"未知协议: {protocol}"}

    def simulate_raw_data(self, raw: str, device_id: int = None, barcode: str = None,
                          full_chain: bool = False, repeat_count: int = 1,
                          channel_id: int = 0) -> dict:
        """调试入口：不依赖真实外设，模拟一条称重器/传感器原始数据。"""
        conn = self._connections.get(device_id) if device_id is not None else None
        if conn is None:
            conn = DeviceConnection(
                device_id=device_id,
                name="AUTO_QA_虚拟外部设备",
                device_role="weight",
                protocol="virtual",
                ip=None,
                port=None,
                serial_port=None,
                serial_baud=9600,
                protocol_config={},
                parse_mode="direct",
                parse_config={},
                station_id=None,
                channel_id=channel_id,
                data_target="extra_fields",
                validation_rules={},
                enabled=True,
                stable_enabled=False,
                stable_delta=0.05,
                stable_count=2,
                zero_threshold=0.05,
            )

        if barcode:
            self._barcode_buffer[conn.device_id] = barcode

        repeat = max(1, min(int(repeat_count or 1), 20))
        if full_chain:
            for _ in range(repeat):
                self._on_raw_data(conn, raw)
            parsed = conn.last_parsed or self._parse_data(conn, raw) or {}
            return {
                "success": True,
                "message": "模拟数据已按真实链路注入",
                "device_id": conn.device_id,
                "device_name": conn.name,
                "raw_data": raw,
                "parsed": parsed,
                "barcode": barcode,
                "full_chain": True,
            }

        conn.last_data = raw
        conn.last_data_time = time.time()
        parsed = self._parse_data(conn, raw)
        if parsed is None:
            self._log_data(conn, raw, None, False, "模拟数据解析失败", barcode)
            return {
                "success": False,
                "message": "模拟数据解析失败",
                "device_id": conn.device_id,
                "raw_data": raw,
                "full_chain": False,
            }

        if barcode:
            parsed["barcode"] = barcode
        conn.last_parsed = parsed
        is_valid, error = self._validate(conn, parsed)
        self._log_data(conn, raw, parsed, is_valid, error or "AUTO_QA_SIMULATED", barcode)
        return {
            "success": is_valid,
            "message": "模拟数据已写入日志（未触发真实业务链路）",
            "device_id": conn.device_id,
            "device_name": conn.name,
            "raw_data": raw,
            "parsed": parsed,
            "barcode": barcode,
            "full_chain": False,
            "error": error,
        }

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
            pairing_group=(getattr(dev, "pairing_group", None) or None),
            stable_enabled=bool(getattr(dev, "stable_enabled", True)),
            stable_delta=float(getattr(dev, "stable_delta", 0.05) or 0.05),
            stable_count=max(2, int(getattr(dev, "stable_count", 5) or 5)),
            zero_threshold=float(getattr(dev, "zero_threshold", 0.05) or 0.05),
            weight_no_barcode_alarm_enabled=bool(getattr(dev, "weight_no_barcode_alarm_enabled", False)),
            weight_no_barcode_alarm_delay_sec=max(1, int(getattr(dev, "weight_no_barcode_alarm_delay_sec", 10) or 10)),
            pairing_mode=str(getattr(dev, "pairing_mode", "stable") or "stable").lower(),
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



_service_instance: Optional[ExternalDeviceService] = None


def get_external_device_service() -> ExternalDeviceService:
    global _service_instance
    if _service_instance is None:
        _service_instance = ExternalDeviceService()
    return _service_instance
