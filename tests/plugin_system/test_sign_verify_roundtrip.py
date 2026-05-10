"""端到端 roundtrip：pack-like → sign-like → verify。

不依赖真实的 4096 私钥, 也不依赖 PLUGIN_SECRET 文件 — 用 fixture 提供。
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from _plugin_common import (
    SIG_MAGIC,
    SIG_VERSION,
    SignatureBlob,
    calc_customer_hmac,
    calc_files_digest,
    calc_pubkey_fingerprint,
    canonical_json,
    parse_signature_blob,
    rsa_sign_pss,
    rsa_verify_pss,
    verify_customer_hmac,
    write_signature_blob,
)


def _build_unsigned_zip(plugin_dir: Path, out_zip: Path) -> str:
    """模拟 pack-plugin.py 的核心步骤: 计算 digest, canonicalize, 打 ZIP."""
    manifest_path = plugin_dir / "plugin.json"
    with manifest_path.open(encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["files_digest"] = calc_files_digest(plugin_dir)
    manifest_path.write_bytes(canonical_json(manifest))

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(plugin_dir.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(plugin_dir).as_posix())

    return manifest["files_digest"]


def _sign_zip(
    in_zip: Path,
    out_zip: Path,
    pri_pem: bytes,
    pub_pem: bytes,
    plugin_secret: bytes,
    tmp_path: Path,
) -> None:
    """模拟 sign-plugin.py: 重打 ZIP 加 signature.bin."""
    extracted = tmp_path / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(in_zip) as zf:
        zf.extractall(extracted)

    with (extracted / "plugin.json").open(encoding="utf-8") as f:
        manifest = json.load(f)

    manifest["signed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["signed_by"] = "test-master"
    canon = canonical_json(manifest)
    (extracted / "plugin.json").write_bytes(canon)

    rsa_sig = rsa_sign_pss(canon, pri_pem, password=None)
    fp = calc_pubkey_fingerprint(pub_pem)
    digest = manifest["files_digest"]
    customer_hmac = calc_customer_hmac(manifest["customer_code"], digest, plugin_secret)

    sig_meta = json.dumps({
        "alg": "RSA-PSS-SHA256",
        "signed_at": manifest["signed_at"],
        "signed_by": "test-master",
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")

    blob = SignatureBlob(
        magic=SIG_MAGIC,
        version=SIG_VERSION,
        public_key_fingerprint=fp,
        customer_hmac=customer_hmac,
        rsa_sig_len=len(rsa_sig),
        rsa_signature=rsa_sig,
        sig_metadata_len=len(sig_meta),
        sig_metadata=sig_meta,
    )
    (extracted / "signature.bin").write_bytes(write_signature_blob(blob))

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(extracted.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(extracted).as_posix())


def _verify_zip(
    zip_path: Path,
    pub_pem: bytes,
    plugin_secret: bytes,
    tmp_path: Path,
) -> tuple[bool, str]:
    """模拟主程序加载时的 10 阶段验签 (RSA + HMAC + digest)."""
    extracted = tmp_path / "verify_extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extracted)

    sig_path = extracted / "signature.bin"
    if not sig_path.is_file():
        return False, "no signature.bin"

    blob = parse_signature_blob(sig_path.read_bytes())
    expected_fp = calc_pubkey_fingerprint(pub_pem)
    if blob.public_key_fingerprint != expected_fp:
        return False, "fingerprint mismatch"

    with (extracted / "plugin.json").open(encoding="utf-8") as f:
        manifest = json.load(f)

    sig_path.unlink()
    recomputed = calc_files_digest(extracted)
    if recomputed != manifest["files_digest"]:
        return False, f"digest mismatch: {recomputed} vs {manifest['files_digest']}"

    canon = canonical_json(manifest)
    if not rsa_verify_pss(canon, blob.rsa_signature, pub_pem):
        return False, "rsa fail"

    if not verify_customer_hmac(
        manifest["customer_code"],
        manifest["files_digest"],
        plugin_secret,
        blob.customer_hmac,
    ):
        return False, "hmac fail"

    return True, "ok"


# ============================================================
# 测试
# ============================================================

def test_full_roundtrip_passes(sample_plugin_dir, rsa_keypair, plugin_secret, tmp_path):
    pri_pem, pub_pem = rsa_keypair

    unsigned = tmp_path / "p-uns.tjvplugin"
    signed = tmp_path / "p.tjvplugin"

    _build_unsigned_zip(sample_plugin_dir, unsigned)
    _sign_zip(unsigned, signed, pri_pem, pub_pem, plugin_secret, tmp_path / "sign_work")

    ok, msg = _verify_zip(signed, pub_pem, plugin_secret, tmp_path / "verify_work")
    assert ok, f"verify 失败: {msg}"


def test_tamper_file_after_sign_fails(sample_plugin_dir, rsa_keypair, plugin_secret, tmp_path):
    pri_pem, pub_pem = rsa_keypair
    unsigned = tmp_path / "p-uns.tjvplugin"
    signed = tmp_path / "p.tjvplugin"
    _build_unsigned_zip(sample_plugin_dir, unsigned)
    _sign_zip(unsigned, signed, pri_pem, pub_pem, plugin_secret, tmp_path / "sw")

    # 篡改: 解 → 改 frontend/theme.css → 再打
    extracted = tmp_path / "tampered"
    extracted.mkdir()
    with zipfile.ZipFile(signed) as zf:
        zf.extractall(extracted)
    (extracted / "frontend" / "theme.css").write_text(":root { --tj-primary: #000; }\n")
    tampered = tmp_path / "tampered.tjvplugin"
    with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(extracted.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(extracted).as_posix())

    ok, msg = _verify_zip(tampered, pub_pem, plugin_secret, tmp_path / "vw")
    assert not ok
    assert "digest" in msg or "rsa" in msg


def test_tamper_manifest_after_sign_fails(sample_plugin_dir, rsa_keypair, plugin_secret, tmp_path):
    """改 manifest 描述, 不重算 digest → RSA 应该失败 (因为 canonical 不同)."""
    pri_pem, pub_pem = rsa_keypair
    unsigned = tmp_path / "p-uns.tjvplugin"
    signed = tmp_path / "p.tjvplugin"
    _build_unsigned_zip(sample_plugin_dir, unsigned)
    _sign_zip(unsigned, signed, pri_pem, pub_pem, plugin_secret, tmp_path / "sw")

    extracted = tmp_path / "tampered"
    extracted.mkdir()
    with zipfile.ZipFile(signed) as zf:
        zf.extractall(extracted)
    with (extracted / "plugin.json").open(encoding="utf-8") as f:
        m = json.load(f)
    m["description"] = "EVIL HACK"
    (extracted / "plugin.json").write_bytes(canonical_json(m))

    tampered = tmp_path / "tampered.tjvplugin"
    with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(extracted.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(extracted).as_posix())

    ok, msg = _verify_zip(tampered, pub_pem, plugin_secret, tmp_path / "vw")
    assert not ok


def test_wrong_pubkey_fails(sample_plugin_dir, rsa_keypair, plugin_secret, tmp_path):
    pri_pem, pub_pem = rsa_keypair
    unsigned = tmp_path / "p-uns.tjvplugin"
    signed = tmp_path / "p.tjvplugin"
    _build_unsigned_zip(sample_plugin_dir, unsigned)
    _sign_zip(unsigned, signed, pri_pem, pub_pem, plugin_secret, tmp_path / "sw")

    # 用另一对 key 验签
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pub = other.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    ok, msg = _verify_zip(signed, other_pub, plugin_secret, tmp_path / "vw")
    assert not ok
    assert "fingerprint" in msg


def test_wrong_secret_fails_hmac(sample_plugin_dir, rsa_keypair, plugin_secret, tmp_path):
    pri_pem, pub_pem = rsa_keypair
    unsigned = tmp_path / "p-uns.tjvplugin"
    signed = tmp_path / "p.tjvplugin"
    _build_unsigned_zip(sample_plugin_dir, unsigned)
    _sign_zip(unsigned, signed, pri_pem, pub_pem, plugin_secret, tmp_path / "sw")

    wrong_secret = bytes(reversed(plugin_secret))
    ok, msg = _verify_zip(signed, pub_pem, wrong_secret, tmp_path / "vw")
    assert not ok
    assert "hmac" in msg


def test_rsa_sign_pss_overload_with_password(rsa_keypair):
    """rsa_sign_pss 应该接受 password=None 也能跑（测试 fixture 用 NoEncryption）."""
    pri_pem, pub_pem = rsa_keypair
    msg = b"hello"
    sig = rsa_sign_pss(msg, pri_pem, password=None)
    assert rsa_verify_pss(msg, sig, pub_pem)
