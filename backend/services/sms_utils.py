"""短信号码、编码与日志脱敏工具。"""

from __future__ import annotations

import re
from collections.abc import Iterable


class SmsValidationError(ValueError):
    """短信配置或内容不满足发送约束。"""


def normalize_phone(phone: str) -> str:
    """清理常见分隔符并校验国际号码格式。"""

    value = re.sub(r"[\s()\-]", "", phone or "")
    if not re.fullmatch(r"\+?\d{7,20}", value):
        raise SmsValidationError(
            "手机号格式无效；请输入 7~20 位数字，可使用 +86 等国际区号"
        )
    return value


def parse_recipients(value: str | Iterable[str]) -> tuple[str, ...]:
    """解析逗号、分号、换行分隔的号码并按输入顺序去重。"""

    if isinstance(value, str):
        raw_items = re.split(r"[,，;；\r\n]+", value)
    else:
        raw_items = list(value)

    recipients: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not str(item).strip():
            continue
        phone = normalize_phone(str(item).strip())
        if phone not in seen:
            recipients.append(phone)
            seen.add(phone)

    if not recipients:
        raise SmsValidationError("至少填写一个接收手机号")
    return tuple(recipients)


def mask_phone(phone: str) -> str:
    """对日志中的手机号脱敏，禁止输出完整号码。"""

    prefix = "+" if phone.startswith("+") else ""
    digits = phone[1:] if prefix else phone
    if len(digits) <= 7:
        return f"{prefix}{digits[:2]}***{digits[-2:]}"
    return f"{prefix}{digits[:3]}****{digits[-4:]}"


def choose_encoding(message: str, requested: str = "auto") -> str:
    """选择 GSM 或 UCS2；自动模式遇到非 ASCII 文本使用 UCS2。"""

    mode = requested.lower().strip()
    if mode not in {"auto", "gsm", "ucs2"}:
        raise SmsValidationError(f"不支持的短信编码：{requested}")
    if mode == "auto":
        return "gsm" if message.isascii() else "ucs2"
    return mode


def validate_message(message: str, encoding: str) -> None:
    """校验单条短信长度，避免模块静默截断或不确定分片。"""

    if not message or not message.strip():
        raise SmsValidationError("短信内容不能为空")
    if "\x1a" in message or "\x1b" in message:
        raise SmsValidationError("短信内容不能包含 Ctrl+Z 或 ESC 控制字符")

    if encoding == "gsm":
        try:
            message.encode("ascii")
        except UnicodeEncodeError as exc:
            raise SmsValidationError("GSM 模式只接受 ASCII；中文请选自动或 UCS2") from exc
        if len(message) > 160:
            raise SmsValidationError("GSM 单条短信最多 160 个 ASCII 字符")
        return

    code_units = len(message.encode("utf-16-be")) // 2
    if code_units > 70:
        raise SmsValidationError(
            f"UCS2 单条短信最多 70 个字符单元，当前为 {code_units}"
        )


def encode_ucs2(value: str) -> str:
    """按 3GPP Text Mode 常用格式编码为大写 UTF-16BE 十六进制。"""

    return value.encode("utf-16-be").hex().upper()
