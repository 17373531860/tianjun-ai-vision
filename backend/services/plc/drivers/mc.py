"""
三菱 MC 协议驱动 (pymcprotocol, 3E 帧) — FX5U/Q/L/iQ-R 系列。

conn_params:
  {"ip": "192.168.1.20", "port": 5007, "plc_type": "Q",   # Q/L/QnA/iQ-L/iQ-R
   "poll_interval_ms": 100}

地址方言 = 三菱软元件名:
  位软元件:  X0 / Y10 / M100 / L0 / B1A (bool 点位)
  字软元件:  D100 / W1A / R50 / ZR100 (数值/字符串点位)
多字类型 (int32/float/字符串) 从起始软元件连续占用 ceil(size/2) 个字。
字→字节按"每字大端"拼接 (与 Modbus 驱动一致), 32 位低字在前的设备配
byte_order="word_swap", D 寄存器 ASCII 低字节在前配 byte_order="byte_swap"。
"""
import math
import re
from typing import Any, Dict, List, Tuple

from backend.services.plc.drivers.base import BasePLCDriver
from backend.services.plc import point_codec

_BIT_DEVICES = ("X", "Y", "M", "L", "B", "S", "F", "SM")
_WORD_DEVICES = ("D", "W", "R", "ZR", "SD")

_ADDR_RE = re.compile(r"^([A-Z]{1,2})([0-9A-F]+)$", re.IGNORECASE)


def parse_mc_address(addr: str) -> Tuple[str, str, bool]:
    """'D100' → ('D', 'D100', False=字软元件); 'M10' → ('M', 'M10', True=位)。"""
    a = str(addr or "").strip().upper()
    m = _ADDR_RE.match(a)
    if not m:
        raise ValueError(f"非法 MC 软元件地址: {addr!r} (期望 D100 / M10 / X0 ...)")
    dev = m.group(1)
    if dev in _BIT_DEVICES:
        return dev, a, True
    if dev in _WORD_DEVICES:
        return dev, a, False
    raise ValueError(f"不支持的软元件: {dev} (位: {_BIT_DEVICES} / 字: {_WORD_DEVICES})")


class MCDriver(BasePLCDriver):
    name = "mc"

    def __init__(self, conn_params: dict, points: List[dict]):
        super().__init__(conn_params, points)
        self._client = None
        self._meta: Dict[str, Tuple[str, bool, int]] = {}   # key → (地址, 是否位, 字数)
        for p in (points or []):
            _, canon, is_bit = parse_mc_address(p.get("addr"))
            ptype = p.get("type") or "bool"
            if is_bit and ptype != "bool":
                raise ValueError(f"点位 {p['key']}: 位软元件只支持 bool")
            words = 0 if is_bit else max(1, math.ceil(point_codec.point_byte_size(p) / 2))
            self._meta[p["key"]] = (canon, is_bit, words)

    def connect(self) -> None:
        import pymcprotocol
        self.close()
        client = pymcprotocol.Type3E(
            plctype=str(self.conn_params.get("plc_type") or "Q"))
        client.setaccessopt(commtype="binary")
        client.connect(str(self.conn_params.get("ip") or ""),
                       int(self.conn_params.get("port") or 5007))
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def read_all(self) -> Dict[str, Any]:
        if self._client is None:
            raise ConnectionError("MC 未连接")
        values: Dict[str, Any] = {}
        for p in self.read_points:
            addr, is_bit, words = self._meta[p["key"]]
            if is_bit:
                bits = self._client.batchread_bitunits(headdevice=addr, readsize=1)
                values[p["key"]] = bool(bits[0])
                continue
            regs = self._client.batchread_wordunits(headdevice=addr, readsize=words)
            raw = b"".join((int(r) & 0xFFFF).to_bytes(2, "big") for r in regs)
            raw = raw[:point_codec.point_byte_size(p)]
            if (p.get("type") or "bool") == "bool":
                values[p["key"]] = bool(int(regs[0]) >> int(p.get("bit") or 0) & 1)
            else:
                values[p["key"]] = point_codec.decode(raw, p)
        return values

    def write(self, key: str, value: Any) -> None:
        if self._client is None:
            raise ConnectionError("MC 未连接")
        p = self.points[key]
        addr, is_bit, words = self._meta[key]
        if is_bit:
            self._client.batchwrite_bitunits(headdevice=addr,
                                             values=[1 if value else 0])
            return
        if (p.get("type") or "bool") == "bool":
            cur = self._client.batchread_wordunits(headdevice=addr, readsize=1)[0]
            bit = int(p.get("bit") or 0)
            new = (cur | (1 << bit)) if value else (cur & ~(1 << bit))
            self._client.batchwrite_wordunits(headdevice=addr,
                                              values=[new & 0xFFFF])
            return
        raw = point_codec.encode(value, p)
        if len(raw) % 2:
            raw += b"\x00"
        regs = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw), 2)]
        self._client.batchwrite_wordunits(headdevice=addr, values=regs)
