"""
可配置的条码内容解析器

支持三种模式:
- direct:    条码内容直接作为序列号
- separator: 按分隔符拆分, 通过 field_mapping 指定字段
- regex:     自定义正则提取命名组
"""
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParseResult:
    serial_no: str
    order_no: Optional[str] = None
    batch_no: Optional[str] = None
    extra: dict = field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None


class BarcodeParser:

    def parse(self, raw_data: str, config: dict = None) -> ParseResult:
        raw_data = raw_data.strip()
        if not raw_data:
            return ParseResult(serial_no="", success=False, error="空数据")

        if not config:
            return self._parse_direct(raw_data)

        mode = config.get("parse_mode", "direct")
        if mode == "separator":
            return self._parse_separator(
                raw_data,
                config.get("separator", "-"),
                config.get("field_mapping", {}),
            )
        elif mode == "regex":
            return self._parse_regex(
                raw_data,
                config.get("regex_pattern", ""),
                config.get("field_mapping", {}),
            )
        else:
            return self._parse_direct(raw_data)

    def _parse_direct(self, raw: str) -> ParseResult:
        return ParseResult(serial_no=raw)

    def _parse_separator(self, raw: str, sep: str,
                         mapping: dict) -> ParseResult:
        parts = raw.split(sep)
        result = ParseResult(serial_no=raw)

        for idx_str, field_name in mapping.items():
            idx = int(idx_str)
            if idx < len(parts):
                value = parts[idx].strip()
                if field_name == "serial_no":
                    result.serial_no = value
                elif field_name == "order_no":
                    result.order_no = value
                elif field_name == "batch_no":
                    result.batch_no = value
                else:
                    result.extra[field_name] = value

        return result

    def _parse_regex(self, raw: str, pattern: str,
                     mapping: dict) -> ParseResult:
        if not pattern:
            return self._parse_direct(raw)

        try:
            m = re.match(pattern, raw)
        except re.error as e:
            return ParseResult(serial_no=raw, success=False,
                               error=f"正则错误: {e}")

        if not m:
            return ParseResult(serial_no=raw, success=False,
                               error=f"不匹配模式: {pattern}")

        groups = m.groupdict()
        result = ParseResult(
            serial_no=groups.get("serial_no", groups.get("serial", raw)),
            order_no=groups.get("order_no", groups.get("order")),
            batch_no=groups.get("batch_no", groups.get("batch")),
        )
        for k, v in groups.items():
            if k not in ("serial_no", "serial", "order_no", "order",
                         "batch_no", "batch"):
                result.extra[k] = v

        return result
