from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from backend.core.config import DATA_DIR

# 与 PluginManager / plugins API 同源 logger, 验签全过程一处可查.
log = logging.getLogger("tianjun.plugin")
from backend.plugin_system._plugin_common import (
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

    # 环境变量指了密钥文件但文件不存在时, 跳过它回退到内置统一密钥
    # (本地 start_backend.sh 无条件 export dev_keys/PLUGIN_SECRET.txt, 但该文件
    #  被 gitignore 常常不存在; 不容错会在读文件时抛 FileNotFoundError 导致安装崩).
    secret_file = os.environ.get("PLUGIN_SECRET_FILE", "").strip()
    if secret_file and Path(secret_file).is_file():
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

    # 内置全网统一密钥兜底 (design 02 §1.3/§5.2):
    # env 都未配置时回退到主程序内置密钥, 让正式安装包开箱即用,
    # 不再因缺少 PLUGIN_SECRET 配置而报 PLUGIN_SECRET_MISSING.
    try:
        from backend.core.plugin_secret import PLUGIN_SECRET
        if isinstance(PLUGIN_SECRET, bytes) and len(PLUGIN_SECRET) == 32:
            return PLUGIN_SECRET
    except Exception:
        pass
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
    """逐步验签. v3.15.4 起每一步都打详尽日志 (期望值 vs 实际值 / 来源 / 候选数),
    让任何 PLUGIN_*_FAIL 在后端日志里都能一眼看出根因, 不再靠现场转发补丁猜.
    """
    log.info("[Verify] ===== 开始验证插件包: %s =====", extracted_dir)

    manifest_path = extracted_dir / "plugin.json"
    sig_path = extracted_dir / "signature.bin"
    if not manifest_path.exists():
        log.error("[Verify] 缺 plugin.json @ %s", manifest_path)
        raise PluginVerifyError("PLUGIN_MANIFEST_NOT_FOUND", "插件包缺少 plugin.json")
    if not sig_path.exists():
        log.error("[Verify] 缺 signature.bin @ %s", sig_path)
        raise PluginVerifyError("PLUGIN_SIGNATURE_NOT_FOUND", "插件包缺少 signature.bin")

    # ---- 1. manifest 解析 + schema ----
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest)
    except PluginVerifyError:
        raise
    except Exception as exc:
        log.error("[Verify] manifest schema 校验失败: %s", exc)
        raise PluginVerifyError("PLUGIN_MANIFEST_SCHEMA_FAIL", f"插件清单格式错误: {exc}") from exc
    log.info(
        "[Verify] 1/5 manifest OK: customer=%s name=%s version=%s tier=%s",
        manifest.get("customer_code"), manifest.get("name"),
        manifest.get("plugin_version"), manifest.get("tier"),
    )

    # ---- 2. 文件摘要 ----
    actual_digest = calc_files_digest(extracted_dir)
    expected_digest = manifest.get("files_digest")
    if actual_digest != expected_digest:
        log.error(
            "[Verify] 2/5 摘要不一致!\n  期望(manifest): %s\n  实际(计算): %s",
            expected_digest, actual_digest,
        )
        raise PluginVerifyError("PLUGIN_FILES_DIGEST_FAIL", "插件文件摘要不一致")
    log.info("[Verify] 2/5 文件摘要一致: %s", actual_digest)

    # ---- 3. 签名 blob 解析 ----
    try:
        sig = parse_signature_blob(sig_path.read_bytes())
    except Exception as exc:
        log.error("[Verify] signature.bin 解析失败: %s", exc)
        raise PluginVerifyError("PLUGIN_SIGNATURE_FAIL", f"签名文件格式错误: {exc}") from exc

    # ---- 4. RSA-PSS 验签 (遍历公钥候选, 指纹先匹配再验签) ----
    candidates = get_public_key_candidates()
    log.info(
        "[Verify] 3/5 验签开始: 公钥候选=%d 个, 签名指纹=%s",
        len(candidates), sig.public_key_fingerprint.hex(),
    )
    if not candidates:
        log.error("[Verify] 无任何内置/环境公钥候选 — 检查 plugin_public_keys.py / cryptography 是否装上")
    signed = False
    fp_matched = False
    for idx, pub_pem in enumerate(candidates):
        try:
            cand_fp = calc_pubkey_fingerprint(pub_pem)
            if cand_fp != sig.public_key_fingerprint:
                log.info("[Verify]   候选#%d 指纹不匹配 (%s), 跳过", idx, cand_fp.hex())
                continue
            fp_matched = True
            if rsa_verify_pss(canonical_json(manifest), sig.rsa_signature, pub_pem):
                signed = True
                log.info("[Verify]   候选#%d 指纹匹配且验签通过", idx)
                break
            log.warning("[Verify]   候选#%d 指纹匹配但 RSA 验签未过", idx)
        except Exception as exc:
            log.warning("[Verify]   候选#%d 验签异常 (常见: 缺 cryptography): %s", idx, exc)
            continue
    if not signed:
        log.error(
            "[Verify] 签名校验失败: 指纹是否命中候选=%s (命中也没过=验签失败/签名被篡改; "
            "没命中=公钥不在白名单或包重签)", fp_matched,
        )
        raise PluginVerifyError("PLUGIN_SIGNATURE_FAIL", "插件签名校验失败")
    log.info("[Verify] 3/5 RSA-PSS 验签通过")

    # ---- 5. 客户绑定 HMAC ----
    secret = get_plugin_secret()
    if secret is None:
        log.error("[Verify] 取不到客户绑定密钥 (env 未配且内置 plugin_secret 缺失)")
        raise PluginVerifyError("PLUGIN_SECRET_MISSING", "未配置插件客户绑定密钥")
    expected = calc_customer_hmac(manifest["customer_code"], manifest["files_digest"], secret)
    if expected != sig.customer_hmac:
        log.error(
            "[Verify] 4/5 客户绑定 HMAC 不一致 (密钥与签包时不同, 或 customer_code 被改)\n"
            "  期望: %s\n  实际: %s", expected.hex(), sig.customer_hmac.hex(),
        )
        raise PluginVerifyError("PLUGIN_CUSTOMER_HMAC_FAIL", "插件客户绑定校验失败")
    log.info("[Verify] 4/5 客户绑定 HMAC 通过")

    # ---- 6. license 客户码 ----
    if db is not None:
        check_license_customer(manifest, read_license_payload(db))
    log.info("[Verify] 5/5 license 客户码校验通过 ===== 验证全部通过 =====")

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
