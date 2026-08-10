"""
点位编解码 (纯函数, 无 IO) — RFC 13 维度二的实现。

driver 负责从 PLC 拿到原始字节 (bytes), 本模块负责 字节 ↔ Python 值:
- 数据类型: bool / int16 / uint16 / int32 / uint32 / float32 / float64 /
            byte / word / dword / bcd / string_s7 / string_fixed / string_cstr
- 字节序:   big(ABCD, S7 默认) / little(DCBA) / word_swap(CDAB, Modbus 32 位常见)
            / byte_swap(BADC)
- 字符串:   encoding(ascii/gbk/utf8/utf16) + strip(去 \\0/空白)
- 数值修饰: scale / offset  (decode: v*scale+offset; encode 取逆)

Modbus 寄存器 (u16 列表) 由 driver 先按大端拼成 bytes 再进本模块,
字节序修饰在本模块统一处理, 各 driver 不重复实现。
"""
import struct
from typing import Any, Optional

# 各类型占用字节数 (字符串/位类型另算)
TYPE_SIZES = {
    "bool": 1, "byte": 1,
    "int16": 2, "uint16": 2, "word": 2,
    "int32": 4, "uint32": 4, "dword": 4, "float32": 4,
    "float64": 8,
}

_STRUCT_FMT = {
    "int16": "h", "uint16": "H", "word": "H",
    "int32": "i", "uint32": "I", "dword": "I",
    "float32": "f", "float64": "d",
}

STRING_TYPES = ("string_s7", "string_fixed", "string_cstr")


def point_byte_size(point: dict) -> int:
    """点位在 PLC 内存中占用的字节数 (用于批量读区间合并)。"""
    ptype = point.get("type") or "bool"
    if ptype == "string_s7":
        # S7 STRING: 2 字节头(max/cur) + max 字符
        return 2 + int(point.get("length") or 254)
    if ptype in ("string_fixed", "string_cstr"):
        return int(point.get("length") or 32)
    if ptype == "bcd":
        return int(point.get("length") or 2)
    return TYPE_SIZES.get(ptype, 2)


def _reorder(raw: bytes, byte_order: str) -> bytes:
    """把 PLC 原始字节重排成大端语义 (decode 前调用; encode 后逆用同函数, 自反)。"""
    order = (byte_order or "big").lower()
    if order == "big" or len(raw) <= 1:
        return raw
    if order == "little":
        return raw[::-1]
    if order == "word_swap":
        # CDAB: 16 位字倒序, 字内字节序不变
        words = [raw[i:i + 2] for i in range(0, len(raw), 2)]
        return b"".join(reversed(words))
    if order == "byte_swap":
        # BADC: 每个 16 位字内部字节互换
        out = bytearray(raw)
        for i in range(0, len(out) - 1, 2):
            out[i], out[i + 1] = out[i + 1], out[i]
        return bytes(out)
    raise ValueError(f"未知字节序: {byte_order}")


def _apply_scale(value, point: dict):
    scale = point.get("scale")
    offset = point.get("offset")
    if scale is None and offset is None:
        return value
    return value * float(scale if scale is not None else 1.0) \
        + float(offset if offset is not None else 0.0)


def _unapply_scale(value, point: dict):
    scale = point.get("scale")
    offset = point.get("offset")
    if scale is None and offset is None:
        return value
    return (float(value) - float(offset if offset is not None else 0.0)) \
        / float(scale if scale is not None else 1.0)


def decode(raw: bytes, point: dict) -> Any:
    """原始字节 → Python 值。raw 必须恰好是 point_byte_size(point) 字节。"""
    ptype = point.get("type") or "bool"

    if ptype == "bool":
        bit = int(point.get("bit") or 0)
        return bool(raw[0] & (1 << bit))

    if ptype == "byte":
        return raw[0]

    if ptype in _STRUCT_FMT:
        data = _reorder(raw, point.get("byte_order") or "big")
        value = struct.unpack(">" + _STRUCT_FMT[ptype], data)[0]
        return _apply_scale(value, point)

    if ptype == "bcd":
        data = _reorder(raw, point.get("byte_order") or "big")
        value = 0
        for b in data:
            hi, lo = (b >> 4) & 0xF, b & 0xF
            if hi > 9 or lo > 9:
                raise ValueError(f"非法 BCD 字节: 0x{b:02X}")
            value = value * 100 + hi * 10 + lo
        return _apply_scale(value, point)

    if ptype in STRING_TYPES:
        encoding = point.get("encoding") or "ascii"
        if ptype == "string_s7":
            cur_len = min(raw[1], len(raw) - 2)
            payload = raw[2:2 + cur_len]
        elif ptype == "string_cstr":
            nul = raw.find(b"\x00")
            payload = raw[:nul] if nul >= 0 else raw
        else:  # string_fixed
            payload = raw
        text = payload.decode(encoding, errors="replace")
        if point.get("strip", True):
            text = text.strip("\x00").strip()
        return text

    raise ValueError(f"未知点位类型: {ptype}")


def encode(value: Any, point: dict) -> bytes:
    """Python 值 → 原始字节 (bool 除外, bool 由 driver 做读改写/单 bit 写)。"""
    ptype = point.get("type") or "bool"

    if ptype == "bool":
        return b"\x01" if value else b"\x00"

    if ptype == "byte":
        return bytes([int(value) & 0xFF])

    if ptype in _STRUCT_FMT:
        v = _unapply_scale(value, point)
        if ptype not in ("float32", "float64"):
            v = int(round(float(v)))
        data = struct.pack(">" + _STRUCT_FMT[ptype], v)
        return _reorder(data, point.get("byte_order") or "big")

    if ptype == "bcd":
        v = int(round(float(_unapply_scale(value, point))))
        length = int(point.get("length") or 2)
        out = bytearray(length)
        for i in range(length - 1, -1, -1):
            out[i] = ((v // 10 % 10) << 4) | (v % 10)
            v //= 100
        return _reorder(bytes(out), point.get("byte_order") or "big")

    if ptype in STRING_TYPES:
        encoding = point.get("encoding") or "ascii"
        payload = str(value).encode(encoding, errors="replace")
        if ptype == "string_s7":
            max_len = int(point.get("length") or 254)
            payload = payload[:max_len]
            return bytes([max_len, len(payload)]) + payload \
                + b"\x00" * (max_len - len(payload))
        length = int(point.get("length") or 32)
        payload = payload[:length]
        pad = b"\x00" if ptype == "string_cstr" else b" "
        return payload + pad * (length - len(payload))

    raise ValueError(f"未知点位类型: {ptype}")


def coerce_value(value: Any, point: dict) -> Any:
    """把 API/规则里来的宽松值 (字符串数字/0/1) 归一成点位类型的 Python 值。"""
    ptype = point.get("type") or "bool"
    if ptype == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "yes")
        return bool(value)
    if ptype in STRING_TYPES:
        return str(value)
    if ptype in ("float32", "float64"):
        return float(value)
    return int(round(float(value)))


def validate_point(point: dict) -> Optional[str]:
    """配置校验: 返回错误文案, 合法返回 None。driver 另行校验地址方言。"""
    if not point.get("key"):
        return "点位缺少 key"
    ptype = point.get("type") or "bool"
    if ptype not in TYPE_SIZES and ptype not in STRING_TYPES and ptype != "bcd":
        return f"点位 {point.get('key')}: 未知类型 {ptype}"
    if ptype in ("string_fixed", "string_cstr") and not point.get("length"):
        return f"点位 {point.get('key')}: {ptype} 必须指定 length"
    direction = point.get("dir") or "read"
    if direction not in ("read", "write", "read_write"):
        return f"点位 {point.get('key')}: 未知方向 {direction}"
    return None
