"""
欧姆龙 FINS/UDP 驱动 (内置实现, 零第三方依赖) — CJ/CS/CP/NJ 系列。

FINS 报文简单 (10 字节头 + 命令), 没有维护良好的 pip 库, 直接内置实现:
- 0101 内存区读 / 0102 内存区写, 字单位
- 支持内存区: DM(D) / CIO / W / H (字访问)

conn_params:
  {"ip": "192.168.1.30", "port": 9600,
   "dest_node": null,   # DA1, 默认取 PLC IP 末段
   "src_node": 1,       # SA1, 本机 FINS 节点号 (与 PLC 同网段时取本机 IP 末段)
   "timeout": 3, "poll_interval_ms": 100}

地址方言: D100 / DM100 / CIO50 / W10 / H5 (bool 用字点位 + bit 参数)。
字→字节按"每字大端"拼接, 32 位低字在前配 byte_order="word_swap"。
"""
import math
import re
import socket
from typing import Any, Dict, List, Tuple

from backend.services.plc.drivers.base import BasePLCDriver
from backend.services.plc import point_codec

# 内存区码 (字访问)
_AREA_CODES = {"D": 0x82, "DM": 0x82, "CIO": 0xB0, "W": 0xB1, "H": 0xB2}
_ADDR_RE = re.compile(r"^(D|DM|CIO|W|H)(\d+)$", re.IGNORECASE)


def parse_fins_address(addr: str) -> Tuple[int, int]:
    """'DM100' → (0x82, 100)。"""
    m = _ADDR_RE.match(str(addr or "").strip().upper())
    if not m:
        raise ValueError(f"非法 FINS 地址: {addr!r} (期望 D100/DM100/CIO50/W10/H5)")
    return _AREA_CODES[m.group(1)], int(m.group(2))


class FinsDriver(BasePLCDriver):
    name = "fins"

    def __init__(self, conn_params: dict, points: List[dict]):
        super().__init__(conn_params, points)
        self._sock = None
        self._sid = 0
        self._meta: Dict[str, Tuple[int, int, int]] = {}    # key → (区码, 字地址, 字数)
        for p in (points or []):
            area, waddr = parse_fins_address(p.get("addr"))
            words = max(1, math.ceil(point_codec.point_byte_size(p) / 2))
            self._meta[p["key"]] = (area, waddr, words)

    # ---- FINS/UDP 报文 ----

    def _header(self) -> bytes:
        self._sid = (self._sid + 1) % 256
        ip = str(self.conn_params.get("ip") or "")
        dest_node = int(self.conn_params.get("dest_node")
                        or (ip.rsplit(".", 1)[-1] if "." in ip else 0))
        src_node = int(self.conn_params.get("src_node") or 1)
        return bytes([
            0x80, 0x00, 0x02,          # ICF/RSV/GCT
            0x00, dest_node, 0x00,     # DNA/DA1/DA2
            0x00, src_node, 0x00,      # SNA/SA1/SA2
            self._sid,
        ])

    def _exchange(self, command: bytes) -> bytes:
        if self._sock is None:
            raise ConnectionError("FINS 未连接")
        req = self._header() + command
        self._sock.send(req)
        resp = self._sock.recv(4096)
        if len(resp) < 14:
            raise ConnectionError(f"FINS 响应过短: {len(resp)}B")
        end_code = (resp[12], resp[13])
        if end_code != (0, 0):
            raise ConnectionError(
                f"FINS 错误码: {end_code[0]:02X}{end_code[1]:02X}")
        return resp[14:]

    def connect(self) -> None:
        self.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(float(self.conn_params.get("timeout") or 3))
        sock.connect((str(self.conn_params.get("ip") or ""),
                      int(self.conn_params.get("port") or 9600)))
        self._sock = sock
        # 连通探测: 读 1 个字 (任选第一个可读点位, 没有则跳过)
        if self.read_points:
            area, waddr, _ = self._meta[self.read_points[0]["key"]]
            self._exchange(bytes([0x01, 0x01, area,
                                  (waddr >> 8) & 0xFF, waddr & 0xFF, 0x00,
                                  0x00, 0x01]))

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _read_words(self, area: int, waddr: int, count: int) -> bytes:
        data = self._exchange(bytes([
            0x01, 0x01, area,
            (waddr >> 8) & 0xFF, waddr & 0xFF, 0x00,
            (count >> 8) & 0xFF, count & 0xFF]))
        if len(data) < count * 2:
            raise ConnectionError(f"FINS 读返回不足: {len(data)}B < {count * 2}B")
        return data[:count * 2]

    def _write_words(self, area: int, waddr: int, raw: bytes) -> None:
        count = len(raw) // 2
        self._exchange(bytes([
            0x01, 0x02, area,
            (waddr >> 8) & 0xFF, waddr & 0xFF, 0x00,
            (count >> 8) & 0xFF, count & 0xFF]) + raw)

    # ---- 契约实现 ----

    def read_all(self) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for p in self.read_points:
            area, waddr, words = self._meta[p["key"]]
            raw = self._read_words(area, waddr, words)
            raw = raw[:point_codec.point_byte_size(p)]
            if (p.get("type") or "bool") == "bool":
                word = int.from_bytes(raw[:2], "big")
                values[p["key"]] = bool(word >> int(p.get("bit") or 0) & 1)
            else:
                values[p["key"]] = point_codec.decode(raw, p)
        return values

    def write(self, key: str, value: Any) -> None:
        p = self.points[key]
        area, waddr, _ = self._meta[key]
        if (p.get("type") or "bool") == "bool":
            cur = int.from_bytes(self._read_words(area, waddr, 1), "big")
            bit = int(p.get("bit") or 0)
            new = (cur | (1 << bit)) if value else (cur & ~(1 << bit))
            self._write_words(area, waddr, (new & 0xFFFF).to_bytes(2, "big"))
            return
        raw = point_codec.encode(value, p)
        if len(raw) % 2:
            raw += b"\x00"
        self._write_words(area, waddr, raw)
