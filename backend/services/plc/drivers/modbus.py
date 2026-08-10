"""
Modbus 驱动 (pymodbus) — TCP + RTU 两个变体。覆盖台达/汇川/信捷等国产 PLC
以及一切带 Modbus 从站能力的设备。

conn_params:
  modbus_tcp: {"ip","port":502,"unit_id":1,"timeout":3,"poll_interval_ms":100}
  modbus_rtu: {"serial_port":"COM3","baudrate":9600,"bytesize":8,"parity":"N",
               "stopbits":1,"unit_id":1,"timeout":3,"poll_interval_ms":200}

地址方言 (表前缀:地址, 地址为 0 基):
  hr:100  保持寄存器 (读写)      ir:30   输入寄存器 (只读)
  co:5    线圈 (读写 bool)       di:2    离散输入 (只读 bool)
  兼容纯数字 "100" = hr:100
多寄存器类型 (int32/float32/float64/字符串) 从起始地址连续占用
ceil(size/2) 个寄存器, 32 位字序用点位 byte_order (word_swap=CDAB 最常见)。
"""
import inspect
import math
import re
from typing import Any, Dict, List, Tuple

from backend.services.plc.drivers.base import BasePLCDriver
from backend.services.plc import point_codec

_ADDR_RE = re.compile(r"^(?:(hr|ir|co|di):)?(\d+)$", re.IGNORECASE)
_MERGE_GAP = 8       # 寄存器空洞小于 8 个就并单次读
_CHUNK_CAP = 120     # 单次读寄存器数上限 (Modbus 协议上限 125)


def parse_modbus_address(addr: str) -> Tuple[str, int]:
    m = _ADDR_RE.match(str(addr or "").strip())
    if not m:
        raise ValueError(f"非法 Modbus 地址: {addr!r} (期望 hr:N / ir:N / co:N / di:N)")
    return (m.group(1) or "hr").lower(), int(m.group(2))


def _unit_kw(fn, unit_id: int) -> dict:
    """pymodbus 3.x 从站参数名跨版本漂移 (unit→slave→device_id), 按签名适配。"""
    params = inspect.signature(fn).parameters
    for name in ("device_id", "slave", "unit"):
        if name in params:
            return {name: unit_id}
    return {}


class _ModbusDriverBase(BasePLCDriver):

    def __init__(self, conn_params: dict, points: List[dict]):
        super().__init__(conn_params, points)
        self._client = None
        self._unit = int(self.conn_params.get("unit_id") or 1)
        self._addr: Dict[str, Tuple[str, int, int]] = {}   # key → (table, addr, reg数)
        for p in (points or []):
            table, a = parse_modbus_address(p.get("addr"))
            ptype = p.get("type") or "bool"
            if table in ("co", "di") and ptype != "bool":
                raise ValueError(f"点位 {p['key']}: 线圈/离散输入只支持 bool")
            regs = 1 if table in ("co", "di") \
                else max(1, math.ceil(point_codec.point_byte_size(p) / 2))
            self._addr[p["key"]] = (table, a, regs)
        self._read_plan = self._build_read_plan()

    def _build_read_plan(self) -> List[Tuple[str, int, int, List[dict]]]:
        by_table: Dict[str, List[dict]] = {}
        for p in self.read_points:
            by_table.setdefault(self._addr[p["key"]][0], []).append(p)
        plan = []
        for table, plist in by_table.items():
            plist.sort(key=lambda p: self._addr[p["key"]][1])
            seg_pts, seg_start, seg_end = [], None, None
            for p in plist:
                _, a, n = self._addr[p["key"]]
                end = a + n
                if seg_start is None:
                    seg_pts, seg_start, seg_end = [p], a, end
                elif a - seg_end <= _MERGE_GAP and end - seg_start <= _CHUNK_CAP:
                    seg_pts.append(p)
                    seg_end = max(seg_end, end)
                else:
                    plan.append((table, seg_start, seg_end - seg_start, seg_pts))
                    seg_pts, seg_start, seg_end = [p], a, end
            if seg_pts:
                plan.append((table, seg_start, seg_end - seg_start, seg_pts))
        return plan

    def _make_client(self):
        raise NotImplementedError

    def connect(self) -> None:
        self.close()
        client = self._make_client()
        if not client.connect():
            raise ConnectionError(f"Modbus 连接失败: {self.conn_params}")
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def _read_table(self, table: str, address: int, count: int):
        c = self._client
        fn = {"hr": c.read_holding_registers, "ir": c.read_input_registers,
              "co": c.read_coils, "di": c.read_discrete_inputs}[table]
        rr = fn(address, count=count, **_unit_kw(fn, self._unit))
        if rr is None or rr.isError():
            raise ConnectionError(f"Modbus 读失败 {table}:{address}x{count}: {rr}")
        return rr.bits[:count] if table in ("co", "di") else rr.registers

    def read_all(self) -> Dict[str, Any]:
        if self._client is None:
            raise ConnectionError("Modbus 未连接")
        values: Dict[str, Any] = {}
        for table, start, count, plist in self._read_plan:
            data = self._read_table(table, start, count)
            for p in plist:
                _, a, n = self._addr[p["key"]]
                if table in ("co", "di"):
                    values[p["key"]] = bool(data[a - start])
                    continue
                regs = data[a - start: a - start + n]
                raw = b"".join(int(r).to_bytes(2, "big") for r in regs)
                raw = raw[:point_codec.point_byte_size(p)]
                if (p.get("type") or "bool") == "bool":
                    # 寄存器承载 bool: 取 bit 位 (默认 bit0, 即非零判定按位)
                    values[p["key"]] = bool(int(regs[0]) >> int(p.get("bit") or 0) & 1)
                else:
                    values[p["key"]] = point_codec.decode(raw, p)
        return values

    def write(self, key: str, value: Any) -> None:
        if self._client is None:
            raise ConnectionError("Modbus 未连接")
        p = self.points[key]
        table, a, n = self._addr[key]
        c = self._client
        if table == "co":
            fn = c.write_coil
            rr = fn(a, bool(value), **_unit_kw(fn, self._unit))
        elif table == "hr":
            if (p.get("type") or "bool") == "bool":
                # 保持寄存器承载 bool: 读改写对应 bit
                cur = self._read_table("hr", a, 1)[0]
                bit = int(p.get("bit") or 0)
                new = (cur | (1 << bit)) if value else (cur & ~(1 << bit))
                fn = c.write_register
                rr = fn(a, new & 0xFFFF, **_unit_kw(fn, self._unit))
            else:
                raw = point_codec.encode(value, p)
                if len(raw) % 2:
                    raw += b"\x00"
                regs = [int.from_bytes(raw[i:i + 2], "big")
                        for i in range(0, len(raw), 2)]
                fn = c.write_registers
                rr = fn(a, values=regs, **_unit_kw(fn, self._unit))
        else:
            raise ValueError(f"点位 {key}: {table} 表只读, 不能写")
        if rr is None or rr.isError():
            raise ConnectionError(f"Modbus 写失败 {table}:{a}: {rr}")


class ModbusTcpDriver(_ModbusDriverBase):
    name = "modbus_tcp"

    def _make_client(self):
        from pymodbus.client import ModbusTcpClient
        return ModbusTcpClient(
            str(self.conn_params.get("ip") or ""),
            port=int(self.conn_params.get("port") or 502),
            timeout=float(self.conn_params.get("timeout") or 3),
        )


class ModbusRtuDriver(_ModbusDriverBase):
    name = "modbus_rtu"

    def _make_client(self):
        from pymodbus.client import ModbusSerialClient
        return ModbusSerialClient(
            port=str(self.conn_params.get("serial_port") or ""),
            baudrate=int(self.conn_params.get("baudrate") or 9600),
            bytesize=int(self.conn_params.get("bytesize") or 8),
            parity=str(self.conn_params.get("parity") or "N"),
            stopbits=int(self.conn_params.get("stopbits") or 1),
            timeout=float(self.conn_params.get("timeout") or 3),
        )
