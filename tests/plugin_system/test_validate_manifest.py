"""manifest 校验：合法/非法各种边界。"""
import json

import pytest

from _plugin_common import (
    CUSTOMER_CODE_REGEX,
    PLUGIN_VERSION_REGEX,
    ManifestValidationError,
    validate_manifest,
)


def _good_tier1():
    return {
        "manifest_version": 1,
        "name": "Sample",
        "customer_code": "internal-test",
        "plugin_version": "1.0.0",
        "description": "ok",
        "author": "tianjun-ai-master",
        "tier": 1,
        "capabilities": ["theme.css"],
        "main_version_min": "3.7.0",
        "created_at": "2026-05-09T00:00:00+00:00",
        "signed_at": "1970-01-01T00:00:00+00:00",
        "signed_by": "unsigned-dev-build",
        "files_digest": "sha256:" + "0" * 64,
    }


def test_valid_tier1_passes():
    validate_manifest(_good_tier1())


def test_missing_required_field_fails():
    m = _good_tier1()
    del m["customer_code"]
    with pytest.raises(ManifestValidationError):
        validate_manifest(m)


def test_invalid_customer_code_fails():
    m = _good_tier1()
    m["customer_code"] = "ACME"  # 大写不允许
    with pytest.raises(ManifestValidationError):
        validate_manifest(m)


def test_customer_code_starts_with_letter():
    assert not CUSTOMER_CODE_REGEX.match("2acme")
    assert CUSTOMER_CODE_REGEX.match("acme2")


def test_customer_code_length_limits():
    assert not CUSTOMER_CODE_REGEX.match("ab")  # 太短
    assert CUSTOMER_CODE_REGEX.match("abc")  # 3 = 最短
    assert CUSTOMER_CODE_REGEX.match("a" + "b" * 19)  # 20 = 最长
    assert not CUSTOMER_CODE_REGEX.match("a" + "b" * 20)  # 21 = 太长


def test_invalid_plugin_version_fails():
    m = _good_tier1()
    m["plugin_version"] = "v1.0"  # 不合 SemVer
    with pytest.raises(ManifestValidationError):
        validate_manifest(m)


def test_plugin_version_with_prerelease():
    m = _good_tier1()
    m["plugin_version"] = "1.0.0-rc1"
    validate_manifest(m)
    assert PLUGIN_VERSION_REGEX.match("1.0.0-rc1")


def test_files_digest_format():
    m = _good_tier1()
    m["files_digest"] = "md5:abcd"  # 错的算法
    with pytest.raises(ManifestValidationError):
        validate_manifest(m)


def test_unknown_top_level_key_fails():
    m = _good_tier1()
    m["my_extra"] = "x"
    with pytest.raises(ManifestValidationError):
        validate_manifest(m)


def test_invalid_tier():
    m = _good_tier1()
    m["tier"] = 4  # 只允许 1/2/3
    with pytest.raises(ManifestValidationError):
        validate_manifest(m)


def test_pinia_store_id_must_have_plugin_prefix():
    m = _good_tier1()
    m["tier"] = 2
    m["frontend"] = {
        "stores": [
            {"id": "without-prefix", "module": "frontend/store.js"}
        ]
    }
    with pytest.raises(ManifestValidationError):
        validate_manifest(m)


def test_table_name_must_have_p_prefix():
    m = _good_tier1()
    m["tier"] = 3
    m["backend"] = {
        "entry": "backend/__init__.py",
        "tables": [
            {"name": "no_prefix", "module": "backend/models.py", "class_name": "PluginAcmeWidget"}
        ],
    }
    with pytest.raises(ManifestValidationError):
        validate_manifest(m)


def test_adapter_name_must_have_plugin_prefix():
    m = _good_tier1()
    m["tier"] = 3
    m["backend"] = {
        "entry": "backend/__init__.py",
        "adapters": [
            {"name": "myadapter", "class_name": "X", "module": "y"}
        ],
    }
    with pytest.raises(ManifestValidationError):
        validate_manifest(m)


def test_hook_type_enum():
    m = _good_tier1()
    m["tier"] = 3
    m["backend"] = {
        "entry": "backend/__init__.py",
        "hooks": [
            {"type": "unknown_event", "module": "x", "function": "y"}
        ],
    }
    with pytest.raises(ManifestValidationError):
        validate_manifest(m)
