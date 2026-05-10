from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from backend.core.config import DATA_DIR


_SCRIPT_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "scripts" / "plugin"
if str(_SCRIPT_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_PLUGIN_DIR))

from _plugin_common import (  # noqa: E402
    calc_customer_hmac,
    calc_files_digest,
    calc_pubkey_fingerprint,
    canonical_json,
    parse_signature_blob,
    rsa_verify_pss,
    validate_manifest,
)


class PluginVerifyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class VerifiedPlugin:
    manifest: Dict[str, Any]
    extracted_dir: Path
    signature_fingerprint: str
    customer_hmac_hex: str
    package_path: Path


def plugins_root() -> Path:
    root = Path(DATA_DIR) / "plugins"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_plugin_secret() -> Optional[bytes]:
    """读取插件 HMAC secret。

    支持:
    - PLUGIN_SECRET: base64 或 hex
    - PLUGIN_SECRET_FILE: gen-master-keypair.py 生成的文本文件
    - TIANJUN_TEST_MODE=1 时默认 bytes(range(32))，仅用于测试
    """
    raw = os.environ.get("PLUGIN_SECRET", "").strip()
    if raw:
        try:
            secret = base64.b64decode(raw)
            if len(secret) == 32:
                return secret
        except Exception:
            pass
        try:
            secret = bytes.fromhex(raw)
            if len(secret) == 32:
                return secret
        except Exception:
            pass
        raise PluginVerifyError("PLUGIN_SECRET_INVALID", "PLUGIN_SECRET 必须是 32 byte 的 base64 或 hex")

    secret_file = os.environ.get("PLUGIN_SECRET_FILE", "").strip()
    if secret_file:
        text = Path(secret_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                secret = base64.b64decode(line)
                if len(secret) != 32:
                    raise PluginVerifyError("PLUGIN_SECRET_INVALID", "PLUGIN_SECRET_FILE 内容不是 32 byte")
                return secret

    if os.environ.get("TIANJUN_TEST_MODE") == "1":
        return bytes(range(32))
    return None


def get_public_key_candidates() -> list[bytes]:
    candidates: list[bytes] = []
    pem = os.environ.get("PLUGIN_PUBLIC_KEY_PEM", "").strip()
    if pem:
        candidates.append(pem.encode("utf-8"))

    pem_path = os.environ.get("PLUGIN_PUBLIC_KEY_PATH", "").strip()
    if pem_path:
        candidates.append(Path(pem_path).read_bytes())

    try:
        from backend.plugin_system.plugin_public_keys import PLUGIN_PUBLIC_KEYS

        for entry in PLUGIN_PUBLIC_KEYS:
            value = entry.get("pem")
            if isinstance(value, str):
                candidates.append(value.encode("utf-8"))
            elif isinstance(value, bytes):
                candidates.append(value)
    except Exception:
        pass

    return candidates


def read_license_payload(db) -> Dict[str, Any]:
    try:
        from backend.models.models import SystemConfig

        row = db.query(SystemConfig).filter(SystemConfig.key == "license.cache").first()
        if not row or not row.value:
            return {}
        return json.loads(row.value)
    except Exception:
        return {}


def check_license_customer(manifest: Dict[str, Any], license_payload: Dict[str, Any]) -> None:
    """校验 license 客户码。

    测试模式无缓存时放行；生产若缓存存在则必须匹配。
    """
    if not license_payload:
        if os.environ.get("TIANJUN_TEST_MODE") == "1":
            return
        return

    customer = (
        license_payload.get("customerName")
        or license_payload.get("customer")
        or license_payload.get("customer_name")
    )
    if customer and customer != manifest["customer_code"]:
        raise PluginVerifyError("PLUGIN_CUSTOMER_MISMATCH", "插件客户码与当前授权不一致")


def safe_extract_zip(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos)
        if total > 500 * 1024 * 1024:
            raise PluginVerifyError("PLUGIN_PACKAGE_TOO_LARGE", "插件包解压后超过 500MB")
        for info in infos:
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                raise PluginVerifyError("PLUGIN_ZIP_SLIP", "插件包包含非法路径")
        zf.extractall(dest)


def verify_extracted_plugin(
    extracted_dir: Path,
    package_path: Path,
    db=None,
) -> VerifiedPlugin:
    manifest_path = extracted_dir / "plugin.json"
    sig_path = extracted_dir / "signature.bin"
    if not manifest_path.exists():
        raise PluginVerifyError("PLUGIN_MANIFEST_NOT_FOUND", "插件包缺少 plugin.json")
    if not sig_path.exists():
        raise PluginVerifyError("PLUGIN_SIGNATURE_NOT_FOUND", "插件包缺少 signature.bin")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest)
    except PluginVerifyError:
        raise
    except Exception as exc:
        raise PluginVerifyError("PLUGIN_MANIFEST_SCHEMA_FAIL", f"插件清单格式错误: {exc}") from exc

    actual_digest = calc_files_digest(extracted_dir)
    if actual_digest != manifest.get("files_digest"):
        raise PluginVerifyError("PLUGIN_FILES_DIGEST_FAIL", "插件文件摘要不一致")

    try:
        sig = parse_signature_blob(sig_path.read_bytes())
    except Exception as exc:
        raise PluginVerifyError("PLUGIN_SIGNATURE_FAIL", f"签名文件格式错误: {exc}") from exc

    signed = False
    for pub_pem in get_public_key_candidates():
        try:
            if calc_pubkey_fingerprint(pub_pem) != sig.public_key_fingerprint:
                continue
            if rsa_verify_pss(canonical_json(manifest), sig.rsa_signature, pub_pem):
                signed = True
                break
        except Exception:
            continue
    if not signed:
        raise PluginVerifyError("PLUGIN_SIGNATURE_FAIL", "插件签名校验失败")

    secret = get_plugin_secret()
    if secret is None:
        raise PluginVerifyError("PLUGIN_SECRET_MISSING", "未配置插件客户绑定密钥")
    expected = calc_customer_hmac(manifest["customer_code"], manifest["files_digest"], secret)
    if expected != sig.customer_hmac:
        raise PluginVerifyError("PLUGIN_CUSTOMER_HMAC_FAIL", "插件客户绑定校验失败")

    if db is not None:
        check_license_customer(manifest, read_license_payload(db))

    return VerifiedPlugin(
        manifest=manifest,
        extracted_dir=extracted_dir,
        signature_fingerprint=sig.public_key_fingerprint.hex(),
        customer_hmac_hex=sig.customer_hmac.hex(),
        package_path=package_path,
    )


def verify_package_to_temp(zip_path: Path, db=None) -> VerifiedPlugin:
    if not zipfile.is_zipfile(zip_path):
        raise PluginVerifyError("PLUGIN_PACKAGE_INVALID", "上传文件不是合法 ZIP 插件包")
    tmp_dir = Path(tempfile.mkdtemp(prefix="tjv-plugin-verify-"))
    try:
        safe_extract_zip(zip_path, tmp_dir)
        return verify_extracted_plugin(tmp_dir, zip_path, db=db)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def copy_verified_to_install_dir(verified: VerifiedPlugin) -> Path:
    cc = verified.manifest["customer_code"]
    install_dir = plugins_root() / cc
    staging = plugins_root() / f".{cc}.staging"
    backup = plugins_root() / f".{cc}.backup"

    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(verified.extracted_dir, staging)

    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    if install_dir.exists():
        install_dir.rename(backup)
    staging.rename(install_dir)
    shutil.rmtree(backup, ignore_errors=True)
    return install_dir
