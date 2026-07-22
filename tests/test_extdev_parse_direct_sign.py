"""直接取值解析的符号位回归 (2026-07 萍乡百斯特现场缺陷)。

安衡台秤报文符号位与数字之间带空格 (如 "ST,TR,- 4.692kg")。旧实现的
正则吃不进这个空格, 负数一律被解析成正数 —— 去皮后拿走工件秤面为负,
负号一丢, 流水线称重的"离秤识别"永远不触发, 结算把上一件皮重串进下一件。
本文件钉死: 带空格符号 / 紧贴符号 / 无符号 / 正号 都必须解析出正确的带符号数值。
"""
import types

import pytest

from backend.services.external_device_pipeline import ExternalDevicePipelineMixin


def _parse(raw: str):
    stub = types.SimpleNamespace()
    return ExternalDevicePipelineMixin._parse_direct(stub, raw, "weight")


@pytest.mark.parametrize("raw, expected", [
    # 安衡真实报文: 符号与数字之间一个空格 (现场抓包原样)
    ("ST,TR,- 4.692kg", -4.692),
    ("US,TR,- 4.688kg", -4.688),
    ("ST,TR,+ 1.274kg", 1.274),
    ("US,TR,+ 1.272kg", 1.272),
    # 符号紧贴数字 (其它品牌常见)
    ("ST,GS,-0.512kg", -0.512),
    ("ST,GS,+2.000kg", 2.000),
    # 无符号裸数字
    ("1.234", 1.234),
    ("ST,NT, 0.000kg", 0.000),
    # 小数点开头
    ("w= -.25", -0.25),
])
def test_direct_parse_keeps_sign(raw, expected):
    parsed = _parse(raw)
    assert parsed.get("weight") == pytest.approx(expected), \
        f"报文 {raw!r} 应解析为 {expected}, 实得 {parsed.get('weight')}"


def test_direct_parse_non_weight_role_untouched():
    stub = types.SimpleNamespace()
    parsed = ExternalDevicePipelineMixin._parse_direct(stub, "ABC123", "scanner")
    assert parsed == {"value": "ABC123", "raw": "ABC123"}


def test_direct_parse_no_number_falls_back():
    parsed = _parse("OVERLOAD")
    assert "weight" not in parsed
    assert parsed["value"] == "OVERLOAD"
