"""canonical_json — 必须确定性、跨平台一致。"""
from _plugin_common import canonical_json


def test_keys_sorted():
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b == b'{"a":2,"b":1}'


def test_no_whitespace():
    out = canonical_json({"x": 1, "y": [1, 2]})
    assert b" " not in out
    assert b"\n" not in out


def test_chinese_kept():
    out = canonical_json({"name": "天骏 AI", "key": "中文"})
    assert "天骏 AI".encode("utf-8") in out
    assert "中文".encode("utf-8") in out


def test_no_trailing_newline():
    out = canonical_json({"a": 1})
    assert not out.endswith(b"\n")


def test_nested_keys_sorted():
    out = canonical_json({"outer": {"z": 1, "a": 2}})
    assert out == b'{"outer":{"a":2,"z":1}}'


def test_unicode_escaping_consistent():
    """ensure_ascii=False 必须保持中文可见（不 \\u 转义）"""
    out = canonical_json({"k": "中"})
    assert b"\\u" not in out
