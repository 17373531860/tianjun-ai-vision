"""
Modbus RTU / TCP 适配器

通过 RS485 串口或 TCP 网络将检测结果写入 Modbus 寄存器。

config 示例 (RTU):
{
  "transport": "rtu",
  "port": "/dev/ttyUSB0",
  "baudrate": 9600,
  "slave_id": 1,
  "parity": "N",
  "data_bits": 8,
  "stop_bits": 1,
  "byte_order": "big",
  "timeout": 3,
  "ok_value": 1,
  "ng_value": 2,
  "registers": [
    {"address": 40001, "source": "result_code", "data_type": "uint16"},
    {"address": 40002, "source": "total_count", "data_type": "int32"},
    {"address": 40004, "source": "const", "const_value": 100, "data_type": "uint16"}
  ]
}

config 示例 (TCP):
{
  "transport": "tcp",
  "host": "192.168.1.100",
  "tcp_port": 502,
  "slave_id": 1,
  "byte_order": "big",
  "timeout": 3,
  "registers": [...]
}
"""
import time
import threading
from typing import Any

from backend.services.mes_adapters.base import BaseAdapter

_serial_lock = threading.Lock()


def _slave_kwarg() -> str:
    """pymodbus 3.13+ 改用 device_id，旧版用 slave"""
    try:
        import inspect
        from pymodbus.client import ModbusSerialClient
        sig = inspect.signature(ModbusSerialClient.write_register)
        if "device_id" in sig.parameters:
            return "device_id"
    except Exception:
        pass
    return "slave"


def _modbus_addr(register_addr: int) -> int:
    """40001-49999 → 0-based Holding Register 地址
       00001-09999 → 0-based Coil 地址
       30001-39999 → 0-based Input Register 地址
    """
    if 40001 <= register_addr <= 49999:
        return register_addr - 40001
    if 30001 <= register_addr <= 39999:
        return register_addr - 30001
    if 1 <= register_addr <= 9999:
        return register_addr - 1
    return register_addr


def _resolve_source(source: str, context: dict, reg: dict = None,
                    ok_value: int = 1, ng_value: int = 2) -> int:
    """从上下文解析数据源到整数值"""
    if source == "result_code":
        is_good = context.get("cycle", {}).get("is_good", True)
        return ok_value if is_good else ng_value

    if source == "const":
        return int(reg.get("const_value", 0)) if reg else 0

    if source == "ok_count":
        return context.get("session", {}).get("good_cycles", 0) or \
               context.get("counters", {}).get("ok", 0)

    if source == "ng_count":
        return context.get("session", {}).get("ng_cycles", 0) or \
               context.get("counters", {}).get("ng", 0)

    if source == "total_count":
        return context.get("session", {}).get("total_cycles", 0) or \
               context.get("counters", {}).get("total", 0)

    if source == "cycle_id":
        return context.get("cycle", {}).get("id", 0)

    if source == "duration_ms":
        dur = context.get("cycle", {}).get("duration") or 0
        return int(dur * 1000)

    if source == "inspection_count":
        return context.get("workpiece", {}).get("inspection_count", 0)

    # 支持 dotted path: cycle.id, workpiece.inspection_count 等
    parts = source.split(".")
    cur = context
    for p in parts:
        if isinstance(cur, dict):
            cur = cur.get(p, 0)
        else:
            return 0
    try:
        return int(cur)
    except (TypeError, ValueError):
        return 0


def _split_int32(value: int, byte_order: str = "big") -> list:
    """将 32 位整数拆为两个 16 位寄存器值"""
    value = value & 0xFFFFFFFF
    hi = (value >> 16) & 0xFFFF
    lo = value & 0xFFFF
    if byte_order == "big":
        return [hi, lo]
    return [lo, hi]


class ModbusRTUAdapter(BaseAdapter):

    def build_payload(self, context: dict, config: dict) -> Any:
        """构建寄存器写入列表 (不使用 JSON 模板)"""
        registers = config.get("registers", [])
        ok_val = config.get("ok_value", 1)
        ng_val = config.get("ng_value", 2)
        writes = []
        for reg in registers:
            addr = _modbus_addr(reg.get("address", 0))
            source = reg.get("source", "result_code")
            data_type = reg.get("data_type", "uint16")
            value = _resolve_source(source, context, reg,
                                    ok_value=ok_val, ng_value=ng_val)

            if data_type in ("int32", "uint32"):
                values = _split_int32(value, config.get("byte_order", "big"))
                writes.append({
                    "address": addr,
                    "values": values,
                    "source": source,
                    "raw_value": value,
                    "type": "multi",
                })
            else:
                writes.append({
                    "address": addr,
                    "values": [value & 0xFFFF],
                    "source": source,
                    "raw_value": value,
                    "type": "single",
                })
        return writes

    def _create_client(self, config: dict):
        """根据 transport 类型创建 pymodbus 客户端"""
        transport = config.get("transport", "rtu")
        timeout_sec = config.get("timeout", 3)

        if transport == "tcp":
            from pymodbus.client import ModbusTcpClient
            return ModbusTcpClient(
                host=config.get("host", "127.0.0.1"),
                port=config.get("tcp_port", 502),
                timeout=timeout_sec,
            )
        else:
            from pymodbus.client import ModbusSerialClient
            return ModbusSerialClient(
                port=config.get("port", ""),
                baudrate=config.get("baudrate", 9600),
                parity=config.get("parity", "N"),
                stopbits=config.get("stop_bits", 1),
                bytesize=config.get("data_bits", 8),
                timeout=timeout_sec,
            )

    def send(self, payload: Any, config: dict) -> dict:
        transport = config.get("transport", "rtu")
        slave_id = config.get("slave_id", 1)

        if transport == "rtu" and not config.get("port"):
            return {
                "status_code": 0, "body": None, "success": False,
                "error": "未配置串口路径 (port)", "duration_ms": 0,
            }
        if transport == "tcp" and not config.get("host"):
            return {
                "status_code": 0, "body": None, "success": False,
                "error": "未配置 Modbus TCP 主机地址 (host)", "duration_ms": 0,
            }

        start = time.time()
        try:
            lock = _serial_lock if transport == "rtu" else threading.Lock()
            with lock:
                client = self._create_client(config)
                target = config.get("port") if transport == "rtu" else config.get("host")
                if not client.connect():
                    elapsed = int((time.time() - start) * 1000)
                    return {
                        "status_code": 0, "body": None, "success": False,
                        "error": f"连接失败: {target}",
                        "duration_ms": elapsed,
                    }

                results = []
                slave_kw = _slave_kwarg()
                try:
                    for write in payload:
                        addr = write["address"]
                        values = write["values"]
                        if write["type"] == "single":
                            resp = client.write_register(
                                address=addr, value=values[0], **{slave_kw: slave_id}
                            )
                        else:
                            resp = client.write_registers(
                                address=addr, values=values, **{slave_kw: slave_id}
                            )

                        ok = not resp.isError() if hasattr(resp, 'isError') else True
                        results.append({
                            "address": addr + 40001,
                            "source": write["source"],
                            "value": write["raw_value"],
                            "registers": values,
                            "ok": ok,
                            "error": str(resp) if not ok else None,
                        })
                finally:
                    client.close()

            elapsed = int((time.time() - start) * 1000)
            all_ok = all(r["ok"] for r in results)
            errors = [r["error"] for r in results if r.get("error")]

            return {
                "status_code": 200 if all_ok else 500,
                "body": {"writes": results},
                "success": all_ok,
                "error": "; ".join(errors) if errors else None,
                "duration_ms": elapsed,
            }

        except ImportError:
            return {
                "status_code": 0, "body": None, "success": False,
                "error": "缺少 pymodbus 依赖，请运行: pip install pymodbus",
                "duration_ms": int((time.time() - start) * 1000),
            }
        except Exception as e:
            return {
                "status_code": 0, "body": None, "success": False,
                "error": f"Modbus 通讯异常: {e}",
                "duration_ms": int((time.time() - start) * 1000),
            }

    def check_response(self, response: dict, config: dict) -> bool:
        return response.get("success", False)
