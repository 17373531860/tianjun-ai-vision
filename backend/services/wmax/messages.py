"""
WMax Protobuf 消息的轻量编解码

不依赖 protoc 编译的 _pb2 文件，直接用 protobuf wire format 手动编解码。
这样避免了从反编译 .NET 代码中逆向完整 .proto 的复杂度。

Wire format 参考: https://protobuf.dev/programming-guides/encoding/
- Varint:       wire type 0
- Fixed64:      wire type 1
- Length-delim:  wire type 2
- Fixed32:      wire type 5

字段号来源: 反编译 Ntj.Reader.* 中 *FieldNumber 常量 + InternalWriteTo WriteRawTag 验证
"""
from __future__ import annotations

import logging
import struct
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Protobuf wire format helpers ─────────────────────────

def _encode_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    parts = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value & 0x7F)
    return bytes(parts)


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _encode_tag(field_number: int, wire_type: int) -> bytes:
    return _encode_varint((field_number << 3) | wire_type)


def _decode_tag(data: bytes, pos: int) -> tuple[int, int, int]:
    val, pos = _decode_varint(data, pos)
    return val >> 3, val & 0x07, pos


# ── 编码辅助 ─────────────────────────────────────────────

def encode_field_varint(fn: int, val: int) -> bytes:
    return _encode_tag(fn, 0) + _encode_varint(val)


def encode_field_bytes(fn: int, val: bytes) -> bytes:
    return _encode_tag(fn, 2) + _encode_varint(len(val)) + val


def encode_field_string(fn: int, val: str) -> bytes:
    b = val.encode("utf-8")
    return encode_field_bytes(fn, b)


def encode_field_bool(fn: int, val: bool) -> bytes:
    return encode_field_varint(fn, 1 if val else 0)


def encode_field_message(fn: int, msg: bytes) -> bytes:
    return encode_field_bytes(fn, msg)


def encode_wrapper_int32(fn: int, val: int) -> bytes:
    """google.protobuf.Int32Value wrapper: field 1 = varint"""
    inner = encode_field_varint(1, val)
    return encode_field_message(fn, inner)


def encode_wrapper_double(fn: int, val: float) -> bytes:
    """google.protobuf.DoubleValue wrapper: field 1 = fixed64"""
    inner = _encode_tag(1, 1) + struct.pack("<d", val)
    return encode_field_message(fn, inner)


def encode_wrapper_bool(fn: int, val: bool) -> bytes:
    """google.protobuf.BoolValue wrapper: field 1 = varint"""
    inner = encode_field_varint(1, 1 if val else 0)
    return encode_field_message(fn, inner)


def encode_wrapper_string(fn: int, val: str) -> bytes:
    """google.protobuf.StringValue wrapper: field 1 = string"""
    inner = encode_field_string(1, val)
    return encode_field_message(fn, inner)


def encode_wrapper_bytes(fn: int, val: bytes) -> bytes:
    """google.protobuf.BytesValue wrapper: field 1 = bytes"""
    inner = encode_field_bytes(1, val)
    return encode_field_message(fn, inner)


# ── 通用解码 ─────────────────────────────────────────────

def _parse_raw_fields(data: bytes) -> list[tuple[int, int, bytes]]:
    """解析 protobuf 为 (field_number, wire_type, raw_value_bytes) 列表。

    保留原始顺序和所有字段，用于 patch_message。
    """
    entries = []
    pos = 0
    while pos < len(data):
        try:
            fn, wt, tag_end = _decode_tag(data, pos)
        except (IndexError, ValueError):
            break
        if wt == 0:
            _, val_end = _decode_varint(data, tag_end)
            entries.append((fn, wt, data[pos:val_end]))
            pos = val_end
        elif wt == 1:
            entries.append((fn, wt, data[pos:tag_end + 8]))
            pos = tag_end + 8
        elif wt == 2:
            length, len_end = _decode_varint(data, tag_end)
            entries.append((fn, wt, data[pos:len_end + length]))
            pos = len_end + length
        elif wt == 5:
            entries.append((fn, wt, data[pos:tag_end + 4]))
            pos = tag_end + 4
        else:
            break
    return entries


def patch_message(original: bytes, patches: dict[int, bytes]) -> bytes:
    """在 protobuf message 中替换指定字段号的内容，保留其他所有字段不变。

    patches: { field_number: new_encoded_field_bytes (已包含 tag + length + value) }
    如果 new_encoded_field_bytes 为 None，则删除该字段。
    """
    raw_fields = _parse_raw_fields(original)
    patched_fields = set()
    result = bytearray()
    for fn, wt, raw_bytes in raw_fields:
        if fn in patches:
            if fn not in patched_fields:
                new_val = patches[fn]
                if new_val is not None:
                    result.extend(new_val)
                patched_fields.add(fn)
        else:
            result.extend(raw_bytes)
    for fn, new_val in patches.items():
        if fn not in patched_fields and new_val is not None:
            result.extend(new_val)
    return bytes(result)


def decode_message(data: bytes) -> dict[int, list[Any]]:
    fields: dict[int, list] = {}
    pos = 0
    while pos < len(data):
        try:
            fn, wt, pos = _decode_tag(data, pos)
        except (IndexError, ValueError) as e:
            logger.debug("[Msg] decode_message: tag 解析在 pos=%d 中断: %s", pos, e)
            break
        if wt == 0:
            val, pos = _decode_varint(data, pos)
        elif wt == 1:
            val = data[pos:pos + 8]
            pos += 8
        elif wt == 2:
            length, pos = _decode_varint(data, pos)
            val = data[pos:pos + length]
            pos += length
        elif wt == 5:
            val = data[pos:pos + 4]
            pos += 4
        else:
            logger.warning("[Msg] decode_message: 未知 wire_type=%d fn=%d pos=%d", wt, fn, pos)
            break
        fields.setdefault(fn, []).append(val)
    return fields


def get_varint(fields: dict, fn: int, default: int = 0) -> int:
    vals = fields.get(fn)
    if vals and isinstance(vals[0], int):
        return vals[0]
    return default


def get_bytes(fields: dict, fn: int, default: bytes = b"") -> bytes:
    vals = fields.get(fn)
    if vals and isinstance(vals[0], bytes):
        return vals[0]
    return default


def get_string(fields: dict, fn: int, default: str = "") -> str:
    val = get_bytes(fields, fn)
    return val.decode("utf-8", errors="replace") if val else default


def get_all_bytes(fields: dict, fn: int) -> list[bytes]:
    """获取 repeated length-delimited 字段的全部值"""
    vals = fields.get(fn, [])
    return [v for v in vals if isinstance(v, bytes)]


def get_wrapper_int(fields: dict, fn: int) -> Optional[int]:
    raw = get_bytes(fields, fn)
    if not raw:
        return None
    inner = decode_message(raw)
    return get_varint(inner, 1)


def get_wrapper_double(fields: dict, fn: int) -> Optional[float]:
    raw = get_bytes(fields, fn)
    if not raw:
        return None
    inner = decode_message(raw)
    raw_bytes = get_bytes(inner, 1)
    if raw_bytes and len(raw_bytes) == 8:
        return struct.unpack("<d", raw_bytes)[0]
    return None


def get_wrapper_bool(fields: dict, fn: int) -> Optional[bool]:
    raw = get_bytes(fields, fn)
    if not raw:
        return None
    inner = decode_message(raw)
    return bool(get_varint(inner, 1))


def get_wrapper_string(fields: dict, fn: int) -> Optional[str]:
    raw = get_bytes(fields, fn)
    if not raw:
        return None
    inner = decode_message(raw)
    return get_string(inner, 1)


def get_wrapper_bytes_val(fields: dict, fn: int) -> Optional[bytes]:
    """google.protobuf.BytesValue wrapper"""
    raw = get_bytes(fields, fn)
    if not raw:
        return None
    inner = decode_message(raw)
    return get_bytes(inner, 1) or None


def get_sub_message(fields: dict, fn: int) -> Optional[dict]:
    raw = get_bytes(fields, fn)
    if not raw:
        return None
    return decode_message(raw)


# ═══════════════════════════════════════════════════════════
# 常用消息编码
# ═══════════════════════════════════════════════════════════

def encode_get_config_opt(config_id: int = -1, inc_global: bool = True) -> bytes:
    """GetConfigOpt { config_id=1(Int32Value), inc_global_opt=2(BoolValue) }

    注: IDManager 抓包 (1111.pcapng) 显示 config_id<0 时省略 field 1,
        只发 field 2 (BoolValue true) = 4B `12 02 08 01`, 设备立即回 28K 配置.
        之前带 field1 varint=-1 会编码 10 字节大负数, 设备不认.
    """
    msg = b""
    if config_id >= 0:
        msg += encode_wrapper_int32(1, config_id)
    msg += encode_wrapper_bool(2, inc_global)
    return msg


def encode_turn_on_off_video(on: bool, bank_id: int = 1) -> bytes:
    """TurnOnOffVideo: {bankId: int32(field1), on: bool(field2)} — 普通 varint 字段"""
    buf = b""
    buf += encode_field_varint(1, bank_id)
    buf += encode_field_varint(2, 1 if on else 0)
    return buf


def encode_auto_focus(start: bool) -> bytes:
    return encode_wrapper_bool(1, start)


def encode_run_mode(mode: int = 0, bank_id: int = 0,
                    start: bool = True, image: bool = True) -> bytes:
    """RunMode { mode=1(Int32Value), start=2(BoolValue), image=3(BoolValue), bankId=4(Int32Value) }"""
    msg = b""
    msg += encode_wrapper_int32(1, mode)
    msg += encode_wrapper_bool(2, start)
    msg += encode_wrapper_bool(3, image)
    msg += encode_wrapper_int32(4, bank_id)
    return msg


def encode_send_term_cmd(cmd_str: str) -> bytes:
    return encode_field_bytes(1, cmd_str.encode("ascii"))


def encode_trigger(on: bool) -> bytes:
    return encode_wrapper_bool(1, on)


def encode_dev_reset(reset_bank: bool = True, reset_ip: bool = False,
                     reset_other: bool = True) -> bytes:
    msg = b""
    msg += encode_wrapper_bool(1, reset_bank)
    msg += encode_wrapper_bool(2, reset_ip)
    msg += encode_wrapper_bool(3, reset_other)
    return msg


def encode_start_tune() -> bytes:
    return b""


def encode_cancel_tune() -> bytes:
    return b""


def encode_force_ip(ip: str, mask: str, gateway: str,
                    mac: str, sn: str) -> bytes:
    msg = b""
    msg += encode_field_string(1, ip)
    msg += encode_field_string(2, mask)
    msg += encode_field_string(3, gateway)
    msg += encode_field_string(4, mac)
    msg += encode_field_string(5, sn)
    return msg


def encode_find_grouping(group_name: str) -> bytes:
    return encode_field_string(1, group_name)


# ═══════════════════════════════════════════════════════════
# 响应消息解码
# ═══════════════════════════════════════════════════════════

def decode_resp_base(data: bytes) -> dict:
    fields = decode_message(data)
    return {"ret_code": get_varint(fields, 1)}


def decode_device_info(data: bytes) -> dict:
    fields = decode_message(data)
    return {
        "sn": get_wrapper_string(fields, 1),
        "dev_name": get_wrapper_string(fields, 2),
        "dev_type": get_varint(fields, 3),
        "hardware_version": get_wrapper_string(fields, 4),
        "app_version": get_wrapper_string(fields, 5),
        "kernel_version": get_wrapper_string(fields, 6),
        "boot_version": get_wrapper_string(fields, 7),
        "algorithm_version": get_wrapper_string(fields, 8),
    }


def decode_ethernet_opt(data: bytes) -> dict:
    fields = decode_message(data)
    return {
        "ip_address": get_wrapper_string(fields, 1),
        "submask": get_wrapper_string(fields, 2),
        "gateway": get_wrapper_string(fields, 3),
        "mac_address": get_wrapper_string(fields, 4),
        "enable_dhcp": get_wrapper_bool(fields, 5),
    }


def decode_find_device_resp(data: bytes) -> dict:
    fields = decode_message(data)
    result = {}
    dev_info_raw = get_bytes(fields, 1)
    if dev_info_raw:
        result["dev_info"] = decode_device_info(dev_info_raw)
    net_raw = get_bytes(fields, 2)
    if net_raw:
        net = decode_ethernet_opt(net_raw)
        for k in ("ip_address", "submask", "gateway"):
            if net.get(k):
                net[k] = net[k].replace("-", ".")
        result["network_opt"] = net
    return result


# ── SensorOpt (BankChannelOpt field 5) ───────────────────

def decode_sensor_opt(data: bytes) -> dict:
    """解码 SensorOpt — 基于 bank_params.pcapng 逆向的实际结构

    SensorOpt { 1: WrapperInt exposure_us, 2: WrapperInt gain, 3: int32 ?, 4: WrapperInt ? }
    """
    fields = decode_message(data)
    return {
        "exposure_us": get_wrapper_int(fields, 1),
        "gain": get_wrapper_int(fields, 2),
    }


def encode_sensor_opt(params: dict) -> bytes:
    """编码 SensorOpt — 基于 bank_params.pcapng 逆向

    只编码 exposure_us(field 1) 和 gain(field 2)，保留 field 3/4 原始常量。
    """
    msg = b""
    encoded_fields = []
    if "exposure_us" in params and params["exposure_us"] is not None:
        msg += encode_wrapper_int32(1, int(params["exposure_us"]))
        encoded_fields.append(f"exposure_us={params['exposure_us']}")
    if "gain" in params and params["gain"] is not None:
        msg += encode_wrapper_int32(2, int(params["gain"]))
        encoded_fields.append(f"gain={params['gain']}")
    msg += encode_field_varint(3, 1)
    msg += encode_wrapper_int32(4, 1)
    logger.debug("[Msg] encode_sensor_opt: %s → %dB", ", ".join(encoded_fields) or "(空)", len(msg))
    return msg


# ── LightOpt (BankChannelOpt field 4) ────────────────────

def encode_light_opt(params: dict) -> bytes:
    msg = b""
    if "internal_light" in params:
        msg += encode_wrapper_int32(1, params["internal_light"])
    if "external_light" in params:
        msg += encode_wrapper_int32(2, params["external_light"])
    return msg


def decode_light_opt(data: bytes) -> dict:
    fields = decode_message(data)
    return {
        "internal_light": get_wrapper_int(fields, 1),
        "external_light": get_wrapper_int(fields, 2),
    }


# ── CommonOpt (BankChannelOpt field 2) ───────────────────
# CommonOpt { name=1(Str), enable=2(Bool), tryCount=3(Int32),
#   decodeTimeout=4(Int32), shutterDelay=5(Int32),
#   bInverseRead=7(Bool), bReverseRead=8(Bool),
#   baseTiltAngle=9(Int32), tiltAngleRange=10(Int32),
#   decoderType=12(enum), barcodePolarity=13(enum), barcodeMirror=14(enum) }

def decode_common_opt(data: bytes) -> dict:
    fields = decode_message(data)
    return {
        "name": get_wrapper_string(fields, 1),
        "enable": get_wrapper_bool(fields, 2),
        "try_count": get_wrapper_int(fields, 3),
        "decode_timeout": get_wrapper_int(fields, 4),
        "shutter_delay": get_wrapper_int(fields, 5),
        "inverse_read": get_wrapper_bool(fields, 7),
        "reverse_read": get_wrapper_bool(fields, 8),
        "base_tilt_angle": get_wrapper_int(fields, 9),
        "tilt_angle_range": get_wrapper_int(fields, 10),
        "decoder_type": get_varint(fields, 12),
        "barcode_polarity": get_varint(fields, 13),
        "barcode_mirror": get_varint(fields, 14),
    }


def encode_common_opt(params: dict) -> bytes:
    msg = b""
    if "name" in params and params["name"] is not None:
        msg += encode_wrapper_string(1, params["name"])
    if "enable" in params and params["enable"] is not None:
        msg += encode_wrapper_bool(2, params["enable"])
    if "try_count" in params and params["try_count"] is not None:
        msg += encode_wrapper_int32(3, int(params["try_count"]))
    if "decode_timeout" in params and params["decode_timeout"] is not None:
        msg += encode_wrapper_int32(4, int(params["decode_timeout"]))
    if "shutter_delay" in params and params["shutter_delay"] is not None:
        msg += encode_wrapper_int32(5, int(params["shutter_delay"]))
    if "inverse_read" in params and params["inverse_read"] is not None:
        msg += encode_wrapper_bool(7, params["inverse_read"])
    if "reverse_read" in params and params["reverse_read"] is not None:
        msg += encode_wrapper_bool(8, params["reverse_read"])
    if "base_tilt_angle" in params and params["base_tilt_angle"] is not None:
        msg += encode_wrapper_int32(9, int(params["base_tilt_angle"]))
    if "tilt_angle_range" in params and params["tilt_angle_range"] is not None:
        msg += encode_wrapper_int32(10, int(params["tilt_angle_range"]))
    if "decoder_type" in params and params["decoder_type"] is not None:
        msg += encode_field_varint(12, int(params["decoder_type"]))
    if "barcode_polarity" in params and params["barcode_polarity"] is not None:
        msg += encode_field_varint(13, int(params["barcode_polarity"]))
    if "barcode_mirror" in params and params["barcode_mirror"] is not None:
        msg += encode_field_varint(14, int(params["barcode_mirror"]))
    return msg


# ── CodeOpt (BankChannelOpt field 3) ─────────────────────
# CodeOpt { codes=1(repeated CodeItemOpt), outputLenLimit=2(Bool),
#   outputLen=3(Int32), codeMode=4(enum), startOffset=5(Int32), redundant=6(Int32) }
# CodeItemOpt { codeTypeE=1(enum), enable=2(Bool), minLen=3(Int32), maxLen=4(Int32) }

BARCODE_TYPES = {
    0: "Unknown", 1: "QR", 2: "DataMatrix", 3: "PDF417",
    4: "Code128", 5: "Code39", 6: "Code93", 7: "Codabar",
    8: "ITF", 9: "Industrial2of5", 10: "UPC_A", 11: "UPC_E",
    12: "EAN13", 13: "EAN8", 14: "GS1Databar",
    15: "GS1DatabarExpanded", 16: "Code11",
    17: "DotCode", 18: "Postal",
}


def decode_code_item_opt(data: bytes) -> dict:
    fields = decode_message(data)
    code_type = get_varint(fields, 1)
    return {
        "code_type": code_type,
        "code_type_name": BARCODE_TYPES.get(code_type, f"Type_{code_type}"),
        "enable": get_wrapper_bool(fields, 2),
        "min_len": get_wrapper_int(fields, 3),
        "max_len": get_wrapper_int(fields, 4),
    }


def decode_code_opt(data: bytes) -> dict:
    fields = decode_message(data)
    codes = []
    for raw in get_all_bytes(fields, 1):
        codes.append(decode_code_item_opt(raw))
    return {
        "codes": codes,
        "output_len_limit": get_wrapper_bool(fields, 2),
        "output_len": get_wrapper_int(fields, 3),
        "code_mode": get_varint(fields, 4),
        "start_offset": get_wrapper_int(fields, 5),
        "redundant": get_wrapper_int(fields, 6),
    }


def encode_code_item_opt(item: dict) -> bytes:
    msg = b""
    if "code_type" in item:
        msg += encode_field_varint(1, int(item["code_type"]))
    if "enable" in item and item["enable"] is not None:
        msg += encode_wrapper_bool(2, item["enable"])
    if "min_len" in item and item["min_len"] is not None:
        msg += encode_wrapper_int32(3, int(item["min_len"]))
    if "max_len" in item and item["max_len"] is not None:
        msg += encode_wrapper_int32(4, int(item["max_len"]))
    return msg


def encode_code_opt(params: dict) -> bytes:
    msg = b""
    for item in params.get("codes", []):
        msg += encode_field_message(1, encode_code_item_opt(item))
    if "output_len_limit" in params and params["output_len_limit"] is not None:
        msg += encode_wrapper_bool(2, params["output_len_limit"])
    if "output_len" in params and params["output_len"] is not None:
        msg += encode_wrapper_int32(3, int(params["output_len"]))
    if "code_mode" in params and params["code_mode"] is not None:
        msg += encode_field_varint(4, int(params["code_mode"]))
    if "start_offset" in params and params["start_offset"] is not None:
        msg += encode_wrapper_int32(5, int(params["start_offset"]))
    if "redundant" in params and params["redundant"] is not None:
        msg += encode_wrapper_int32(6, int(params["redundant"]))
    return msg


# ── ReadingOpt (NormalConfigOpt field 8) ──────────────────
# ReadingOpt { enableSmartMode=1(Bool), imgCaptureRect=2(Rect),
#   polarization=3(enum), rois=4(repeated ROI), focusValue=5(Int32),
#   illumValue=6(Int32), enableDetectRoi=7(Bool), detectroi=8(ROI) }
# Rect { x=1(Int32), y=2(Int32), w=3(Int32), h=4(Int32) }
# ROI { id=1(Int32Value), rect=2(Rect) }

def decode_rect(data: bytes) -> dict:
    fields = decode_message(data)
    return {
        "x": get_wrapper_int(fields, 1) or 0,
        "y": get_wrapper_int(fields, 2) or 0,
        "w": get_wrapper_int(fields, 3) or 0,
        "h": get_wrapper_int(fields, 4) or 0,
    }


def encode_rect(params: dict) -> bytes:
    msg = b""
    for key, fn in [("x", 1), ("y", 2), ("w", 3), ("h", 4)]:
        if key in params and params[key] is not None:
            msg += encode_wrapper_int32(fn, int(params[key]))
    return msg


def decode_roi(data: bytes) -> dict:
    fields = decode_message(data)
    result = {"id": get_wrapper_int(fields, 1)}
    rect_raw = get_bytes(fields, 2)
    if rect_raw:
        result["rect"] = decode_rect(rect_raw)
    return result


def encode_roi(params: dict) -> bytes:
    msg = b""
    if "id" in params and params["id"] is not None:
        msg += encode_wrapper_int32(1, int(params["id"]))
    if "rect" in params and params["rect"]:
        msg += encode_field_message(2, encode_rect(params["rect"]))
    return msg


def decode_reading_opt(data: bytes) -> dict:
    fields = decode_message(data)
    result = {
        "enable_smart_mode": get_wrapper_bool(fields, 1),
        "polarization": get_varint(fields, 3),
        "focus_value": get_wrapper_int(fields, 5),
        "illum_value": get_wrapper_int(fields, 6),
        "enable_detect_roi": get_wrapper_bool(fields, 7),
    }
    capture_raw = get_bytes(fields, 2)
    if capture_raw:
        result["img_capture_rect"] = decode_rect(capture_raw)
    rois = []
    for raw in get_all_bytes(fields, 4):
        rois.append(decode_roi(raw))
    result["rois"] = rois
    detect_raw = get_bytes(fields, 8)
    if detect_raw:
        result["detect_roi"] = decode_roi(detect_raw)
    return result


def encode_reading_opt(params: dict) -> bytes:
    msg = b""
    if "enable_smart_mode" in params and params["enable_smart_mode"] is not None:
        msg += encode_wrapper_bool(1, params["enable_smart_mode"])
    if "img_capture_rect" in params and params["img_capture_rect"]:
        msg += encode_field_message(2, encode_rect(params["img_capture_rect"]))
    if "polarization" in params and params["polarization"] is not None:
        msg += encode_field_varint(3, int(params["polarization"]))
    for roi in params.get("rois", []):
        msg += encode_field_message(4, encode_roi(roi))
    if "focus_value" in params and params["focus_value"] is not None:
        msg += encode_wrapper_int32(5, int(params["focus_value"]))
    if "illum_value" in params and params["illum_value"] is not None:
        msg += encode_wrapper_int32(6, int(params["illum_value"]))
    if "enable_detect_roi" in params and params["enable_detect_roi"] is not None:
        msg += encode_wrapper_bool(7, params["enable_detect_roi"])
    if "detect_roi" in params and params["detect_roi"]:
        msg += encode_field_message(8, encode_roi(params["detect_roi"]))
    return msg


# ── DataEditOpt (NormalConfigOpt field 15) ────────────────
# SetDataEditOpt { dataFilter=1, dataEdit=2, script=3(BytesValue),
#   dataCompare=4, multiData=5(MulitDataOpt), prefix=6(BytesValue),
#   suffix=7(BytesValue), noBarcodeFlags=8(Int32Value),
#   noBarcodeStr=9(BytesValue), barcodeNumber=10(Int32Value) }
# MulitDataOpt { bEnable=1(Bool), delimiterFlags=2(Int32),
#   delimiterStr=3(BytesValue), incompleteFlags=4(Int32),
#   incompleteStr=5(BytesValue) }

def _bytes_to_str(b: Optional[bytes]) -> Optional[str]:
    if b is None:
        return None
    return b.decode("utf-8", errors="replace")


def decode_multi_data_opt(data: bytes) -> dict:
    fields = decode_message(data)
    return {
        "enable": get_wrapper_bool(fields, 1),
        "delimiter_flags": get_wrapper_int(fields, 2),
        "delimiter_str": _bytes_to_str(get_wrapper_bytes_val(fields, 3)),
        "incomplete_flags": get_wrapper_int(fields, 4),
        "incomplete_str": _bytes_to_str(get_wrapper_bytes_val(fields, 5)),
    }


def encode_multi_data_opt(params: dict) -> bytes:
    msg = b""
    if "enable" in params and params["enable"] is not None:
        msg += encode_wrapper_bool(1, params["enable"])
    if "delimiter_flags" in params and params["delimiter_flags"] is not None:
        msg += encode_wrapper_int32(2, int(params["delimiter_flags"]))
    if "delimiter_str" in params and params["delimiter_str"] is not None:
        msg += encode_wrapper_bytes(3, params["delimiter_str"].encode("utf-8"))
    if "incomplete_flags" in params and params["incomplete_flags"] is not None:
        msg += encode_wrapper_int32(4, int(params["incomplete_flags"]))
    if "incomplete_str" in params and params["incomplete_str"] is not None:
        msg += encode_wrapper_bytes(5, params["incomplete_str"].encode("utf-8"))
    return msg


def decode_data_edit_opt(data: bytes) -> dict:
    fields = decode_message(data)
    result = {
        "prefix": _bytes_to_str(get_wrapper_bytes_val(fields, 6)),
        "suffix": _bytes_to_str(get_wrapper_bytes_val(fields, 7)),
        "no_barcode_flags": get_wrapper_int(fields, 8),
        "no_barcode_str": _bytes_to_str(get_wrapper_bytes_val(fields, 9)),
        "barcode_number": get_wrapper_int(fields, 10),
    }
    multi_raw = get_bytes(fields, 5)
    if multi_raw:
        result["multi_data"] = decode_multi_data_opt(multi_raw)
    return result


def encode_data_edit_opt(params: dict) -> bytes:
    msg = b""
    if "prefix" in params and params["prefix"] is not None:
        msg += encode_wrapper_bytes(6, params["prefix"].encode("utf-8"))
    if "suffix" in params and params["suffix"] is not None:
        msg += encode_wrapper_bytes(7, params["suffix"].encode("utf-8"))
    if "no_barcode_flags" in params and params["no_barcode_flags"] is not None:
        msg += encode_wrapper_int32(8, int(params["no_barcode_flags"]))
    if "no_barcode_str" in params and params["no_barcode_str"] is not None:
        msg += encode_wrapper_bytes(9, params["no_barcode_str"].encode("utf-8"))
    if "barcode_number" in params and params["barcode_number"] is not None:
        msg += encode_wrapper_int32(10, int(params["barcode_number"]))
    if "multi_data" in params and params["multi_data"]:
        msg += encode_field_message(5, encode_multi_data_opt(params["multi_data"]))
    return msg


def decode_data_output_format(data: bytes) -> dict:
    """解码 DataOutputFormat (NormalConfig field 10) — 基于 data_format.pcapng 逆向

    结构: { 1: bytes, 2: inner }, inner = { 1: empty, 2: {1: separator}, 3: {1: internal_sep}, 4: append_flags, ... }
    """
    fields = decode_message(data)
    inner_raw = get_bytes(fields, 2)
    if not inner_raw:
        return {"separator": None, "internal_separator": None}
    inner = decode_message(inner_raw)
    sep_raw = get_bytes(inner, 2)
    separator = None
    if sep_raw:
        sep_inner = decode_message(sep_raw)
        separator = get_string(sep_inner, 1) or None
    isep_raw = get_bytes(inner, 3)
    internal_separator = None
    if isep_raw:
        isep_inner = decode_message(isep_raw)
        internal_separator = get_string(isep_inner, 1) or None
    append_raw = get_bytes(inner, 4)
    append_flags_hex = append_raw.hex() if append_raw else None
    logger.debug("[Msg] decode_data_output_format: sep=%r isep=%r append=%s",
                 separator, internal_separator, append_flags_hex)
    return {
        "separator": separator,
        "internal_separator": internal_separator,
        "append_flags_hex": append_flags_hex,
    }


def encode_data_output_format(params: dict) -> bytes:
    """编码 DataOutputFormat (NormalConfig field 10) — 基于 data_format.pcapng 逆向"""
    inner = b""
    inner += encode_field_bytes(1, b"")
    sep = params.get("separator", ":")
    inner += encode_field_message(2, encode_field_message(1, sep.encode("utf-8") if sep else b""))
    isep = params.get("internal_separator", ",")
    inner += encode_field_message(3, encode_field_message(1, isep.encode("utf-8") if isep else b""))
    result = encode_field_bytes(1, b"")
    result += encode_field_message(2, inner)
    logger.debug("[Msg] encode_data_output_format: sep=%r isep=%r → %dB",
                 sep, isep, len(result))
    return result


# ── InputOpt / OutputOpt (逆向自 Wireshark 抓包) ─────────
#
# OutputOpt 实际结构 (field 5 of NormalConfigOpt):
#   f1: {f1: 1}  // 类型常量
#   f2: {  // OutputSettings
#     f1 (repeated): [  // 输出端子配置
#       {f2: {f1: signal_mask}},  // pin 0 (可配置)
#       {f1: 1, f2: {f1: 1}},    // pin 1 (固定)
#       {f1: 2, f2: {f1: 1}},    // pin 2 (固定)
#     ]
#     f2: {f1: duration_ms}  // 持续时间
#     f3: ""  f4: ""
#   }
#
# signal_mask 位掩码:
#   0/空 = 无信号, 1 = OK, 4 = 错误, 5 = OK+错误, 1024 = 触发器忙
# OK/错误 与 触发器忙 互斥

OUTPUT_SIGNAL_NONE = 0
OUTPUT_SIGNAL_OK = 1
OUTPUT_SIGNAL_ERROR = 4
OUTPUT_SIGNAL_OK_ERROR = 5
OUTPUT_SIGNAL_TRIGGER_BUSY = 1024

INDICATOR_MODE_MANUAL = 1
INDICATOR_MODE_AUTO_SCAN = 3


def decode_input_opt(data: bytes) -> dict:
    fields = decode_message(data)
    return {
        "polarity": get_varint(fields, 2),
        "debounce_time": get_wrapper_int(fields, 3),
    }


def decode_output_opt(data: bytes) -> dict:
    """解码 OutputSettings — 基于 Wireshark 逆向的实际 protobuf 结构

    data = OutputSettings (OutputOpt.field2 的内容)
    OutputSettings { repeated f1: channel_entry, f2: {f1: duration_ms}, f3: empty, f4: empty }
    channel_entry { f1: pin_id(varint), f2: {f1: signal_mask(varint)} }
    """
    fields = decode_message(data)

    outputs = []
    for entry_raw in get_all_bytes(fields, 1):
        entry = decode_message(entry_raw)
        pin_id = get_varint(entry, 1, 0)
        signal_raw = get_bytes(entry, 2)
        signal_mask = 0
        if signal_raw:
            sig = decode_message(signal_raw)
            signal_mask = get_varint(sig, 1, 0)
        outputs.append({"pin_id": pin_id, "signal_mask": signal_mask})

    dur_raw = get_bytes(fields, 2)
    duration_ms = 150
    if dur_raw:
        dur_inner = decode_message(dur_raw)
        duration_ms = get_varint(dur_inner, 1, 150)

    primary_signal = outputs[0]["signal_mask"] if outputs else 0

    return {
        "signal_mask": primary_signal,
        "duration_ms": duration_ms,
        "ok_enabled": bool(primary_signal & OUTPUT_SIGNAL_OK),
        "error_enabled": bool(primary_signal & OUTPUT_SIGNAL_ERROR),
        "trigger_busy_enabled": primary_signal == OUTPUT_SIGNAL_TRIGGER_BUSY,
        "outputs": outputs,
    }


def encode_input_opt_wrapper(params: dict, config_id: int = -1) -> bytes:
    """SetInputOpt { configId=1(Int32Value), inputOpt=2(InputOpt) }"""
    inner = b""
    if "polarity" in params and params["polarity"] is not None:
        inner += encode_field_varint(2, int(params["polarity"]))
    if "debounce_time" in params and params["debounce_time"] is not None:
        inner += encode_wrapper_int32(3, int(params["debounce_time"]))
    result = b""
    if config_id >= 0:
        result += encode_wrapper_int32(1, config_id)
    if inner:
        result += encode_field_message(2, inner)
    return result


def encode_output_opt_wrapper(params: dict, config_id: int = -1) -> bytes:
    """编码 OutputOpt — 基于 Wireshark 逆向的实际 protobuf 结构

    params: signal_mask (int), duration_ms (int)
    """
    signal_mask = int(params.get("signal_mask", 0))
    duration_ms = int(params.get("duration_ms", 150))

    if signal_mask == 0:
        pin0_inner = encode_field_bytes(2, b"")
    else:
        pin0_inner = encode_field_message(2, encode_field_varint(1, signal_mask))

    pin1_inner = (encode_field_varint(1, 1) +
                  encode_field_message(2, encode_field_varint(1, 1)))
    pin2_inner = (encode_field_varint(1, 2) +
                  encode_field_message(2, encode_field_varint(1, 1)))

    settings = b""
    settings += encode_field_message(1, pin0_inner)
    settings += encode_field_message(1, pin1_inner)
    settings += encode_field_message(1, pin2_inner)
    settings += encode_field_message(2, encode_field_varint(1, duration_ms))
    settings += encode_field_bytes(3, b"")
    settings += encode_field_bytes(4, b"")

    result = encode_field_message(1, encode_field_varint(1, 1))
    result += encode_field_message(2, settings)
    logger.debug("[Msg] encode_output_opt: signal=0x%X dur=%dms → %dB",
                 signal_mask, duration_ms, len(result))
    return result


# ── IndicatorOpt (NormalConfigOpt field 9) ────────────────
# 实际结构 (逆向自 Wireshark):
#   f2: {
#     f1: mode     // 1=手动亮灯, 3=仅扫描时自动亮灯
#     f2: 1        // 常量
#     f3-f10: ...  // 其他子字段(保持不变)
#   }

def decode_indicator_opt(data: bytes) -> dict:
    """解码 IndicatorOpt"""
    fields = decode_message(data)
    f2_raw = get_bytes(fields, 2)
    if not f2_raw:
        return {"mode": INDICATOR_MODE_AUTO_SCAN, "mode_name": "auto_on_scan"}

    f2_fields = decode_message(f2_raw)
    mode = get_varint(f2_fields, 1, INDICATOR_MODE_AUTO_SCAN)

    return {
        "mode": mode,
        "mode_name": {
            INDICATOR_MODE_MANUAL: "manual",
            INDICATOR_MODE_AUTO_SCAN: "auto_on_scan",
        }.get(mode, f"unknown({mode})"),
    }


def encode_indicator_opt(mode: int = INDICATOR_MODE_AUTO_SCAN) -> bytes:
    """编码 IndicatorOpt

    mode: 1=手动亮灯, 3=仅扫描时自动亮灯
    使用从设备逆向的常量值来填充其他子字段。
    """
    settings = b""
    settings += encode_field_varint(1, mode)
    settings += encode_field_varint(2, 1)
    settings += encode_field_message(3, encode_field_varint(1, 1))
    settings += encode_field_message(4, encode_field_varint(1, 494))
    settings += encode_field_message(5, encode_field_varint(1, 1))
    settings += encode_field_bytes(6, b"")
    settings += encode_field_bytes(7, b"")
    settings += encode_field_message(8, encode_field_varint(1, 1))
    f9_inner = b""
    for fn in range(1, 9):
        f9_inner += encode_field_bytes(fn, b"")
    settings += encode_field_message(9, f9_inner)
    settings += encode_field_varint(10, 1)

    result = encode_field_message(2, settings)
    logger.debug("[Msg] encode_indicator_opt: mode=%d → %dB", mode, len(result))
    return result


# ── RptCode ───────────────────────────────────────────────

def decode_rpt_code(data: bytes) -> dict:
    """解码 RptCode 帧 (逆向自实际设备 protobuf)

    实际结构有两种变体:
    完整帧: {1=ts_msg, 2={1=count, 2=scores, 3={1=code_str}, 4=result, 5=mode, 7=timing}}
    简短帧: {1=ts_msg, 3={1=code_str}, 4=result}
    """
    fields = decode_message(data)

    ts_raw = get_bytes(fields, 1)
    timestamp = None
    if ts_raw:
        ts_fields = decode_message(ts_raw)
        timestamp = get_varint(ts_fields, 1)

    code_str = None
    decode_time = None
    total_time = None
    result_code = None
    attempt_count = None

    detail_raw = get_bytes(fields, 2)
    if detail_raw:
        detail = decode_message(detail_raw)
        attempt_count = get_varint(detail, 1)
        code_msg = get_bytes(detail, 3)
        if code_msg:
            code_inner = decode_message(code_msg)
            code_str = get_string(code_inner, 1)
        result_msg = get_bytes(detail, 4)
        if result_msg:
            result_code = get_varint(decode_message(result_msg), 1)
        timing_raw = get_bytes(detail, 7)
        if timing_raw:
            timing = decode_message(timing_raw)
            decode_time = get_varint(timing, 1)
            total_time = get_varint(timing, 2)

    if not code_str:
        code_msg_top = get_bytes(fields, 3)
        if code_msg_top:
            code_inner = decode_message(code_msg_top)
            code_str = get_string(code_inner, 1)

    if result_code is None:
        result_msg_top = get_bytes(fields, 4)
        if result_msg_top:
            result_code = get_varint(decode_message(result_msg_top), 1)

    codes = []
    if code_str:
        codes.append({
            "data": code_str,
            "code_type": result_code,
            "score": attempt_count,
        })

    result = {
        "codes": codes,
        "img_timestamp": timestamp,
        "decode_time": decode_time,
        "total_time": total_time,
    }
    if codes:
        logger.debug("[Msg] decode_rpt_code: %d 字节 → code=%s result=%s",
                     len(data), code_str, result_code)
    return result


# ── Handshake ─────────────────────────────────────────────

def decode_handshake_resp(data: bytes) -> dict:
    fields = decode_message(data)
    resp_raw = get_bytes(fields, 1)
    result = {}
    if resp_raw:
        result["response"] = decode_resp_base(resp_raw)
    result["protocol_version"] = get_wrapper_int(fields, 2)
    logger.debug("[Msg] decode_handshake_resp: ret_code=%s proto_ver=%s",
                 result.get("response", {}).get("ret_code"),
                 result.get("protocol_version"))
    return result


# ── RptReadRateTest (CmdType=203) ─────────────────────────

def decode_rpt_read_rate(data: bytes) -> dict:
    fields = decode_message(data)
    return {
        "total_count": get_wrapper_int(fields, 1),
        "success_count": get_wrapper_int(fields, 2),
        "fail_count": get_wrapper_int(fields, 3),
        "rate": get_wrapper_double(fields, 4),
        "is_running": get_wrapper_bool(fields, 5),
    }


# ═══════════════════════════════════════════════════════════
# 配置树 组装与解析
# ═══════════════════════════════════════════════════════════
# 完整配置树（已修正字段号，来自反编译验证）:
#
# SetConfigOpt/GetConfigOptResp {
#   normal_config_opt = 1 (NormalConfigOpt)
#   global_opt = 2
#   startup_cfg_id = 3 (Int32Value)
#   config_id = 4 (Int32Value)
# }
#
# NormalConfigOpt {
#   id=1, name=2, bankOpt=3(SetBankOpt), inputOpt=4(SetInputOpt),
#   outputOpt=5(SetOutputOpt), hardwareOpt=6, triggerOpt=7,
#   readingOpt=8(ReadingOpt), miscOpt=9, formatOpt=10,
#   imageOpt=11, verifyOpt=12, autoTuneOpt=13,
#   inputOutputOpt=14(InputOutputOpt), dataEdit=15(SetDataEditOpt),
#   runMode=16(RunMode)
# }
#
# SetBankOpt { banks=1(repeated BankChannelOpt) }
#
# BankChannelOpt {
#   id=1(Int32Value), commonOpt=2(CommonOpt), codeOpt=3(CodeOpt),
#   lightOpt=4(LightOpt), sensorOpt=5(SensorOpt),
#   filters=6(repeated FilterOpt)
# }


def encode_bank_channel_opt(sensor: bytes = b"", light: bytes = b"",
                             common: bytes = b"", code: bytes = b"") -> bytes:
    """BankChannelOpt { id=1, commonOpt=2, codeOpt=3, lightOpt=4, sensorOpt=5, filters=6 }"""
    msg = b""
    if common:
        msg += encode_field_message(2, common)
    if code:
        msg += encode_field_message(3, code)
    if light:
        msg += encode_field_message(4, light)
    if sensor:
        msg += encode_field_message(5, sensor)
    return msg


def encode_bank_opt(banks: list[bytes]) -> bytes:
    """SetBankOpt { banks=1(repeated BankChannelOpt) }"""
    msg = b""
    for bank in banks:
        msg += encode_field_message(1, bank)
    return msg


def encode_normal_config_opt(bank_opt: bytes = b"",
                              input_opt: bytes = b"",
                              output_opt: bytes = b"",
                              reading_opt: bytes = b"",
                              indicator_opt: bytes = b"",
                              data_output_format: bytes = b"") -> bytes:
    """NormalConfigOpt { bankOpt=3, inputOpt=4, outputOpt=5,
       readingOpt=8, indicatorOpt=9, dataOutputFormat=10 }"""
    result = b""
    if bank_opt:
        result += encode_field_message(3, bank_opt)
    if input_opt:
        result += encode_field_message(4, input_opt)
    if output_opt:
        result += encode_field_message(5, output_opt)
    if reading_opt:
        result += encode_field_message(8, reading_opt)
    if indicator_opt:
        result += encode_field_message(9, indicator_opt)
    if data_output_format:
        result += encode_field_message(10, data_output_format)
    return result


def encode_set_config_opt(normal: bytes = b"", global_opt: bytes = b"",
                          startup_cfg_id: int = -1, config_id: int = -1) -> bytes:
    """SetConfigOpt { normal_config_opt=1, global_opt=2, startup_cfg_id=3, config_id=4 }"""
    msg = b""
    if normal:
        msg += encode_field_message(1, normal)
    if global_opt:
        msg += encode_field_message(2, global_opt)
    if startup_cfg_id >= 0:
        msg += encode_wrapper_int32(3, startup_cfg_id)
    if config_id >= 0:
        msg += encode_wrapper_int32(4, config_id)
    logger.debug("[Msg] encode_set_config_opt: normal=%dB global=%dB startup=%d cfg_id=%d → %dB",
                 len(normal), len(global_opt), startup_cfg_id, config_id, len(msg))
    return msg


def decode_config_opt_resp(data: bytes) -> dict:
    """解析 GetConfigOptResp / SetConfigOptResp 的完整配置树"""
    logger.debug("[Msg] decode_config_opt_resp: 输入 %d 字节", len(data))
    fields = decode_message(data)
    result: dict = {"raw_fields": list(fields.keys())}
    logger.debug("[Msg] 顶层字段: %s", list(fields.keys()))

    resp_base = get_bytes(fields, 1)
    if resp_base:
        base_fields = decode_message(resp_base)
        result["ret_code"] = get_wrapper_int(base_fields, 1)
        logger.debug("[Msg] resp_base: ret_code=%s", result["ret_code"])

    normal_raw = get_bytes(fields, 2) or get_bytes(fields, 1)
    if not normal_raw:
        logger.warning("[Msg] decode_config_opt_resp: 无 normal_config_opt (field 1/2)")
        return result

    normal_fields = decode_message(normal_raw)
    result["config_id"] = get_wrapper_int(normal_fields, 1)
    result["config_name"] = get_wrapper_string(normal_fields, 2)
    logger.debug("[Msg] NormalConfig: id=%s name=%s fields=%s",
                 result["config_id"], result["config_name"], list(normal_fields.keys()))

    # field 3: SetBankOpt → repeated BankChannelOpt
    bank_opt_raw = get_bytes(normal_fields, 3)
    if bank_opt_raw:
        bank_fields = decode_message(bank_opt_raw)
        banks_list = get_all_bytes(bank_fields, 2) or get_all_bytes(bank_fields, 1)
        logger.debug("[Msg] BankOpt: %d 个 bank", len(banks_list))
        for i, bank_raw in enumerate(banks_list):
            single_bank = decode_message(bank_raw)
            logger.debug("[Msg] Bank[%d] fields: %s", i, list(single_bank.keys()))

            common_raw = get_bytes(single_bank, 2)
            if common_raw:
                result["common_opt"] = decode_common_opt(common_raw)
                logger.debug("[Msg]   common_opt: %dB → %s",
                             len(common_raw), {k: v for k, v in result["common_opt"].items() if v is not None})

            code_raw = get_bytes(single_bank, 3)
            if code_raw:
                result["code_opt"] = decode_code_opt(code_raw)
                n_codes = len(result["code_opt"].get("codes", []))
                enabled = sum(1 for c in result["code_opt"].get("codes", []) if c.get("enable"))
                logger.debug("[Msg]   code_opt: %dB → %d 种码制 (%d 启用)",
                             len(code_raw), n_codes, enabled)

            light_raw = get_bytes(single_bank, 4)
            if light_raw:
                result["light_opt"] = decode_light_opt(light_raw)
                logger.debug("[Msg]   light_opt: %dB → %s", len(light_raw), result["light_opt"])

            sensor_raw = get_bytes(single_bank, 5)
            if sensor_raw:
                result["sensor_opt"] = decode_sensor_opt(sensor_raw)
                logger.debug("[Msg]   sensor_opt: %dB → exposure=%s gain=%s",
                             len(sensor_raw),
                             result["sensor_opt"].get("exposure"),
                             result["sensor_opt"].get("gain"))
            break
    else:
        logger.debug("[Msg] 无 BankOpt (field 3)")

    # field 4: SetInputOpt
    input_raw = get_bytes(normal_fields, 4)
    if input_raw:
        inp_fields = decode_message(input_raw)
        inp_inner = get_bytes(inp_fields, 2)
        if inp_inner:
            result["input_opt"] = decode_input_opt(inp_inner)
            logger.debug("[Msg] input_opt: %s", result["input_opt"])
        else:
            logger.debug("[Msg] InputOpt 外层存在但无 inner (field 2)")
    else:
        logger.debug("[Msg] 无 InputOpt (field 4)")

    # field 5: SetOutputOpt
    output_raw = get_bytes(normal_fields, 5)
    if output_raw:
        out_fields = decode_message(output_raw)
        out_inner = get_bytes(out_fields, 2)
        if out_inner:
            result["output_opt"] = decode_output_opt(out_inner)
            logger.debug("[Msg] output_opt: %s", result["output_opt"])
        else:
            logger.debug("[Msg] OutputOpt 外层存在但无 inner (field 2)")
    else:
        logger.debug("[Msg] 无 OutputOpt (field 5)")

    # field 8: ReadingOpt
    reading_raw = get_bytes(normal_fields, 8)
    if reading_raw:
        result["reading_opt"] = decode_reading_opt(reading_raw)
        logger.debug("[Msg] reading_opt: %dB → roi=%s smart=%s",
                     len(reading_raw),
                     result["reading_opt"].get("enable_detect_roi"),
                     result["reading_opt"].get("enable_smart_mode"))
    else:
        logger.debug("[Msg] 无 ReadingOpt (field 8)")

    # field 9: IndicatorOpt
    indicator_raw = get_bytes(normal_fields, 9)
    if indicator_raw:
        result["indicator_opt"] = decode_indicator_opt(indicator_raw)
        logger.debug("[Msg] indicator_opt: %dB → mode=%s",
                     len(indicator_raw),
                     result["indicator_opt"].get("mode_name"))
    else:
        logger.debug("[Msg] 无 IndicatorOpt (field 9)")

    # field 10: DataOutputFormat (分隔符/数据附加)
    data_output_raw = get_bytes(normal_fields, 10)
    if data_output_raw:
        result["data_output_format"] = decode_data_output_format(data_output_raw)
        logger.debug("[Msg] data_output_format: %dB → sep=%s isep=%s",
                     len(data_output_raw),
                     result["data_output_format"].get("separator"),
                     result["data_output_format"].get("internal_separator"))
    else:
        logger.debug("[Msg] 无 DataOutputFormat (field 10)")

    sections = [k for k in result if k not in ("raw_fields",) and result[k] is not None]
    logger.info("[Msg] decode_config_opt_resp 完成: %s", ", ".join(sections))
    return result
