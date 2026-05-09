"""插件系统测试的 fixtures。

不依赖父级 backend 启动相关 fixture，但允许 import scripts/plugin/_plugin_common。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_PLUGIN = _REPO_ROOT / "scripts" / "plugin"

# 把 scripts/plugin 加 sys.path，让所有 test 都能 import _plugin_common
if str(_SCRIPTS_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_PLUGIN))


@pytest.fixture
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture
def scripts_plugin() -> Path:
    return _SCRIPTS_PLUGIN


@pytest.fixture
def schema_path() -> Path:
    return _REPO_ROOT / "docs" / "plugin-system" / "plugin.schema.json"


@pytest.fixture
def sample_plugin_dir(tmp_path: Path) -> Path:
    """生成一个最小合法的 Tier 1 主题包目录。"""
    p = tmp_path / "sample-plugin"
    p.mkdir()

    (p / "frontend").mkdir()
    (p / "frontend" / "theme.css").write_text(
        ":root { --tj-primary: #ff6b35; }\n", encoding="utf-8"
    )
    (p / "frontend" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"></svg>\n',
        encoding="utf-8",
    )

    import json
    manifest = {
        "manifest_version": 1,
        "name": "Sample Theme",
        "customer_code": "internal-test",
        "plugin_version": "1.0.0",
        "description": "测试用 Tier 1 主题包",
        "author": "tianjun-ai-master",
        "tier": 1,
        "capabilities": ["theme.css", "theme.logo"],
        "main_version_min": "3.7.0",
        "created_at": "2026-05-09T00:00:00+00:00",
        "signed_at": "",
        "signed_by": "",
        "files_digest": "sha256:" + "0" * 64,
        "frontend": {
            "theme": {
                "css": "frontend/theme.css",
                "logo": "frontend/logo.svg",
            }
        },
    }
    (p / "plugin.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def rsa_keypair():
    """生成一个 RSA-2048 临时密钥对（速度比 4096 快 8 倍）。

    单元测试用 2048; 真实生产用 4096（design/02 §3.2）。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pri = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pri_pem = pri.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = pri.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pri_pem, pub_pem


@pytest.fixture
def plugin_secret() -> bytes:
    """32-byte 测试 secret (固定值, 测试可复现)。"""
    return bytes(range(32))
