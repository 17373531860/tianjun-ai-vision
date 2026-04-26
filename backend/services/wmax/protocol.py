"""
WMax IDManager 二进制帧协议

帧格式 (大端序):
  [0x5A][0x5A][Flag:4B][SN:10B?][CmdType:1B][CmdIdx:1B]
  [DataLen:4B?][HeadChk:1B][Payload:NB?][DataChk:2B?][0xA5]

Flag 位定义:
  bit31  HasSN       0x80000000   帧含 10 字节序列号
  bit30  HasData     0x40000000   帧含数据载荷
  bit27  IsProtobuf  0x08000000   数据为 Protobuf
  bit26  IsResponse  0x04000000   响应帧
  bit[4:0] DevClass               设备类型
"""
from __future__ import annotations

import struct
import logging
from enum import IntEnum
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

FRAME_HEADER = b"\x5A\x5A"
FRAME_TAIL = 0xA5
MIN_FRAME_LEN = 10

# ── Flag 位 ──────────────────────────────────────────────
FLAG_HAS_SN = 0x80000000
FLAG_HAS_DATA = 0x40000000
FLAG_IS_PROTOBUF = 0x08000000
FLAG_IS_RESPONSE = 0x04000000
FLAG_DEV_CLASS_MASK = 0x1F

DEFAULT_FLAG = FLAG_IS_PROTOBUF | 0x01  # IsProtobuf + FixedMount


class CmdType(IntEnum):
    """命令码，从 C# CommandType_E 完整映射"""
    Unknown = 0
    QuickFind = 1
    FindDevice = 2
    HandShake = 3
    SetBankOpt = 4
    SetEthernetOpt = 5
    SetHardwareOpt = 6
    TurnOnOffVideo = 7
    TakePicture = 8
    SetInputOpt = 9
    SetOutputOpt = 10
    SetCodesOpt = 11
    SetTriggerOpt = 12
    SendFile = 13
    SendImagePtcol = 14
    SetConfigOpt = 15
    GetConfigOpt = 16
    AutoFocus = 17
    StartTune = 18
    CancelTune = 19
    SendTermCmd = 20
    CtrlReboot = 21
    CtrlReset = 22
    SetReadingOpt = 23
    GetFileList = 24
    ReadFile = 25
    DeleteFile = 26
    CtrlRRTest = 27
    CtrlTactTest = 28
    CtrlDoFTest = 29
    Trigger = 30
    TurnOnOffTrggerImage = 31
    DeviceRunMode = 32
    DeviceFeature = 33
    SendImageNew = 49
    ForceIp = 50
    FocusIllum = 51
    FindGroup = 52
    SaveConfigAsCustomer = 53
    RestoreToCustomerConfig = 54
    SaveConfig = 55
    IndicateDev = 56
    Verify = 57
    RptCode = 200
    RptUpdateResult = 201
    RptTuneResult = 202
    RptReadRateTest = 203
    RptTactTest = 204
    RptDoFTest = 205
    RptFocusResult = 206
    PCMD_RAW = 254


_CMD_NAMES = {v: k for k, v in CmdType.__members__.items()}


@dataclass
class Command:
    """一条 WMax 协议帧"""
    cmd_type: int = 0
    cmd_index: int = 0
    flag: int = DEFAULT_FLAG
    serial_number: str = ""
    data_part: Optional[bytes] = None

    @property
    def is_response(self) -> bool:
        return bool(self.flag & FLAG_IS_RESPONSE)

    @is_response.setter
    def is_response(self, val: bool):
        if val:
            self.flag |= FLAG_IS_RESPONSE
        else:
            self.flag &= ~FLAG_IS_RESPONSE

    @property
    def is_protobuf(self) -> bool:
        return bool(self.flag & FLAG_IS_PROTOBUF)

    def __repr__(self) -> str:
        name = _CMD_NAMES.get(self.cmd_type, f"Cmd({self.cmd_type})")
        data_len = len(self.data_part) if self.data_part else 0
        resp = "Resp" if self.is_response else "Req"
        return f"<Command {name} idx={self.cmd_index} {resp} data={data_len}B>"


def _checksum(data: bytes, start: int = 0, count: int = 0) -> int:
    end = len(data) if count <= 0 else start + count
    return sum(data[start:end])


def pack(cmd: Command, data_len_size: int = 4) -> bytes:
    """将 Command 打包为二进制帧

    data_len_size: DataLen 字段宽度，标准为 4 字节，部分设备变体为 3 字节
    """
    data_len = len(cmd.data_part) if cmd.data_part else 0
    sn_bytes = cmd.serial_number.encode("utf-8") if cmd.serial_number else b""
    sn_len = len(sn_bytes)

    flag = cmd.flag
    if data_len > 0:
        flag |= FLAG_HAS_DATA
    if sn_len > 0:
        flag |= FLAG_HAS_SN

    dl_field = data_len_size if data_len > 0 else 0
    total = (2 + 4 + sn_len + 1 + 1
             + dl_field + 1
             + data_len
             + (2 if data_len > 0 else 0)
             + 1)

    buf = bytearray(total)
    i = 0

    buf[i] = 0x5A; i += 1
    buf[i] = 0x5A; i += 1

    struct.pack_into(">I", buf, i, flag & 0xFFFFFFFF); i += 4

    if sn_len == 10:
        buf[i:i + 10] = sn_bytes; i += 10

    buf[i] = cmd.cmd_type & 0xFF; i += 1
    buf[i] = cmd.cmd_index & 0xFF; i += 1

    if data_len > 0:
        if data_len_size == 3:
            buf[i] = (data_len >> 16) & 0xFF; i += 1
            buf[i] = (data_len >> 8) & 0xFF; i += 1
            buf[i] = data_len & 0xFF; i += 1
        else:
            struct.pack_into(">I", buf, i, data_len); i += 4

    head_chk = _checksum(buf, 2, i - 2) & 0xFF
    buf[i] = head_chk; i += 1

    if data_len > 0:
        buf[i:i + data_len] = cmd.data_part; i += data_len
        data_chk = _checksum(cmd.data_part) & 0xFFFF
        buf[i] = (data_chk >> 8) & 0xFF; i += 1
        buf[i] = data_chk & 0xFF; i += 1

    buf[i] = FRAME_TAIL; i += 1

    result = bytes(buf[:i])
    name = _CMD_NAMES.get(cmd.cmd_type, f"Cmd({cmd.cmd_type})")
    logger.debug("[Protocol] pack %s idx=%d data=%dB dl_size=%d → frame=%dB",
                 name, cmd.cmd_index, data_len, data_len_size, len(result))
    return result


@dataclass
class ParseResult:
    """解析状态"""
    received_head: bool = False
    processed_len: int = 0
    needed_payload_len: int = 0
    detected_data_len_size: int = 4


def parse_modified(data: bytes, result: ParseResult) -> list[Command]:
    """从缓冲区解析帧（Modified 模式，生产环境默认）"""
    commands: list[Command] = []
    result.processed_len = 0
    remaining = len(data)
    skip_count = 0

    while remaining >= MIN_FRAME_LEN:
        idx = result.processed_len
        result.received_head = False
        result.needed_payload_len = 0

        if data[idx] != 0x5A or data[idx + 1] != 0x5A:
            result.processed_len += 1
            remaining -= 1
            skip_count += 1
            continue

        if skip_count > 0:
            logger.debug("[Protocol] 跳过 %d 字节垃圾数据寻找帧头", skip_count)
            skip_count = 0

        idx += 2
        flag = struct.unpack_from(">I", data, idx)[0]
        idx += 4

        sn = ""
        if flag & FLAG_HAS_SN:
            if remaining < MIN_FRAME_LEN + 10:
                break
            sn = data[idx:idx + 10].decode("utf-8", errors="replace")
            idx += 10

        cmd_type = data[idx]; idx += 1
        cmd_index = data[idx]; idx += 1

        data_len = 0
        data_len_size = 0
        if flag & FLAG_HAS_DATA:
            if remaining < (idx - result.processed_len) + 5:
                break
            data_len = struct.unpack_from(">I", data, idx)[0]
            data_len_size = 4
            idx += 4

        expected_chk = _checksum(data, result.processed_len + 2,
                                 idx - result.processed_len - 2) & 0xFF
        actual_chk = data[idx]

        if expected_chk != actual_chk and data_len_size == 4:
            chk_pos = idx - 1
            dl_start = chk_pos - 3
            alt_data_len = (data[dl_start] << 16) | (data[dl_start + 1] << 8) | data[dl_start + 2]
            head_bytes = chk_pos - result.processed_len - 2
            alt_chk = _checksum(data, result.processed_len + 2, head_bytes) & 0xFF
            if alt_chk == data[chk_pos]:
                data_len = alt_data_len
                data_len_size = 3
                idx = chk_pos
                actual_chk = data[idx]
                expected_chk = alt_chk
                result.detected_data_len_size = 3
                logger.debug("[Protocol] 使用 3 字节 DataLen=%d (设备变体)", data_len)

        idx += 1

        if expected_chk != actual_chk:
            name = _CMD_NAMES.get(cmd_type, f"Cmd({cmd_type})")
            logger.debug("[Protocol] 头部校验失败: cmd=%s idx=%d expected=0x%02X actual=0x%02X",
                         name, cmd_index, expected_chk, actual_chk)
            result.processed_len += 1
            remaining -= 1
            continue

        result.received_head = True
        frame_len = idx - result.processed_len + 1  # +1 for tail

        payload = None
        if data_len > 0:
            frame_len += data_len + 2  # +2 for data checksum
            if remaining < frame_len:
                result.needed_payload_len = frame_len
                logger.debug("[Protocol] 等待更多数据: 需要 %d 字节，当前 %d 字节",
                             frame_len, remaining)
                break
            payload = bytes(data[idx:idx + data_len])
            idx += data_len
            idx += 2  # skip data checksum

        if data[idx] != FRAME_TAIL:
            name = _CMD_NAMES.get(cmd_type, f"Cmd({cmd_type})")
            logger.debug("[Protocol] 帧尾错误: cmd=%s idx=%d expected=0xA5 actual=0x%02X",
                         name, cmd_index, data[idx])
            result.processed_len += 1
            remaining -= 1
            continue

        idx += 1
        cmd = Command(
            cmd_type=cmd_type,
            cmd_index=cmd_index,
            flag=flag,
            serial_number=sn,
            data_part=payload,
        )
        commands.append(cmd)
        result.processed_len += frame_len
        remaining -= frame_len

    if skip_count > 0:
        logger.debug("[Protocol] 缓冲区末尾跳过 %d 字节", skip_count)

    if commands:
        logger.debug("[Protocol] 本轮解析 %d 帧，消耗 %d 字节，剩余 %d 字节",
                     len(commands), result.processed_len, remaining)

    return commands


class DataReceiver:
    """TCP 流式接收缓冲区，处理粘包/分包"""

    def __init__(self):
        self._buf = bytearray()
        self._result = ParseResult()
        self._total_fed = 0
        self._total_parsed = 0
        self.detected_data_len_size: int = 4

    def feed(self, data: bytes) -> list[Command]:
        self._buf.extend(data)
        self._total_fed += len(data)

        if (self._result.received_head
                and self._result.needed_payload_len > 0
                and len(self._buf) < self._result.needed_payload_len):
            logger.debug("[Protocol] 缓冲区 %d/%d 字节，等待载荷完整",
                         len(self._buf), self._result.needed_payload_len)
            return []

        self._result = ParseResult()
        cmds = parse_modified(bytes(self._buf), self._result)

        if self._result.detected_data_len_size != 4:
            self.detected_data_len_size = self._result.detected_data_len_size

        if self._result.processed_len > 0:
            del self._buf[:self._result.processed_len]

        self._total_parsed += len(cmds)

        if len(self._buf) > 524288:
            logger.warning("[Protocol] 接收缓冲区异常: %d 字节未消耗，清空",
                           len(self._buf))
            self._buf.clear()

        return cmds


# ── 图像数据解析 ─────────────────────────────────────────

@dataclass
class ZImage:
    bank_id: int = 0
    image_type: int = 0
    timestamp: int = 0
    image_format: int = 0
    width: int = 0
    height: int = 0
    image_data: bytes = b""


def parse_new_image(data: bytes) -> Optional[ZImage]:
    """解析 CmdType=49 (SendImageNew) 的图像载荷"""
    if not data or len(data) < 19:
        logger.warning("[Protocol] 新图像数据太短: %d 字节", len(data) if data else 0)
        return None
    img = ZImage()
    img.bank_id = data[0]
    img.image_type = data[1]
    img.timestamp = struct.unpack_from(">Q", data, 2)[0]
    img.image_format = data[10]
    img.width = struct.unpack_from(">H", data, 11)[0]
    img.height = struct.unpack_from(">H", data, 13)[0]
    data_len = struct.unpack_from(">I", data, 15)[0]
    if len(data) < 19 + data_len:
        logger.warning("[Protocol] 新图像数据不完整: 需要 %d 字节，实际 %d 字节",
                       19 + data_len, len(data))
        return None
    img.image_data = data[19:19 + data_len]
    logger.debug("[Protocol] 解析新图像: %dx%d fmt=%d data=%dB",
                 img.width, img.height, img.image_format, data_len)
    return img


def parse_old_image(data: bytes) -> Optional[ZImage]:
    """解析 CmdType=14 (SendImagePtcol) 的图像载荷"""
    if not data or len(data) < 15:
        logger.warning("[Protocol] 旧图像数据太短: %d 字节", len(data) if data else 0)
        return None
    img = ZImage()
    img.bank_id = data[0]
    img.image_type = data[1]
    img.timestamp = struct.unpack_from(">I", data, 2)[0]
    img.image_format = data[6]
    img.width = struct.unpack_from(">H", data, 7)[0]
    img.height = struct.unpack_from(">H", data, 9)[0]
    data_len = struct.unpack_from(">I", data, 11)[0]
    if len(data) < 15 + data_len:
        logger.warning("[Protocol] 旧图像数据不完整: 需要 %d 字节，实际 %d 字节",
                       15 + data_len, len(data))
        return None
    img.image_data = data[15:15 + data_len]
    logger.debug("[Protocol] 解析旧图像: %dx%d fmt=%d data=%dB",
                 img.width, img.height, img.image_format, data_len)
    return img
