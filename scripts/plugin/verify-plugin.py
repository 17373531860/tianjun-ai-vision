#!/usr/bin/env python3
"""
verify-plugin.py — 离线验签 .tjvplugin

不需要 PLUGIN_SECRET, 只需公钥就能验证:
  - signature.bin 格式
  - public_key_fingerprint 匹配
  - files_digest 没被篡改
  - manifest canonical 形式被正确签名

⚠️ HMAC 部分无法离线验证 (HMAC 需要 secret), 主程序加载时才会验。

用途:
  - 主作者签后自验
  - CI 校验插件包
  - 客户工控机故障时排查 (公钥可入 PATH 一起带)

用法:
  python scripts/plugin/verify-plugin.py --in xxx.tjvplugin --pub plugin_master_pub.pem
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

from _plugin_common import (
    calc_files_digest,
    calc_pubkey_fingerprint,
    canonical_json,
    err,
    info,
    parse_signature_blob,
    rsa_verify_pss,
    validate_manifest,
    warn,
)


def verify(in_path: Path, pub_pem_path: Path, *, strict: bool = True) -> bool:
    if not in_path.is_file():
        err(f"输入不存在: {in_path}")
        return False
    if not pub_pem_path.is_file():
        err(f"公钥不存在: {pub_pem_path}")
        return False

    pub_pem = pub_pem_path.read_bytes()
    expected_fp = calc_pubkey_fingerprint(pub_pem)
    info(f"期待 fingerprint: {expected_fp.hex()}")

    with tempfile.TemporaryDirectory(prefix="tjv-verify-") as tmpd:
        tmp = Path(tmpd)
        with zipfile.ZipFile(in_path) as zf:
            zf.extractall(tmp)

        sig_path = tmp / "signature.bin"
        if not sig_path.is_file():
            err("signature.bin 不存在 (可能是未签名 -uns 包)")
            return False

        manifest_path = tmp / "plugin.json"
        if not manifest_path.is_file():
            err("plugin.json 不存在")
            return False

        try:
            blob = parse_signature_blob(sig_path.read_bytes())
        except Exception as e:
            err(f"signature.bin 解析失败: {e}")
            return False

        info(f"包中 fingerprint:  {blob.public_key_fingerprint.hex()}")

        if blob.public_key_fingerprint != expected_fp:
            err("公钥 fingerprint 不匹配 (可能用了错误的公钥, 或包被替换)")
            return False
        info("✓ 公钥 fingerprint 匹配")

        # 校验 files_digest
        with manifest_path.open(encoding="utf-8") as f:
            manifest = json.load(f)
        try:
            validate_manifest(manifest)
            info("✓ manifest schema 校验通过")
        except Exception as e:
            err(f"manifest 校验失败: {e}")
            return False

        # 重新计算 (排除 signature.bin 自身)
        sig_path.unlink()  # 移走再算 digest
        recomputed = calc_files_digest(tmp)
        if recomputed != manifest["files_digest"]:
            err(f"files_digest 不一致! manifest={manifest['files_digest']}, recomputed={recomputed}")
            return False
        info(f"✓ files_digest 一致: {recomputed}")

        # 校验 RSA 签名 (输入是 canonical manifest)
        canon = canonical_json(manifest)
        if not rsa_verify_pss(canon, blob.rsa_signature, pub_pem):
            err("RSA 签名校验失败 (manifest 被篡改, 或换了 manifest 重签)")
            return False
        info("✓ RSA-PSS-SHA256 签名校验通过")

        # 解析 sig_metadata
        try:
            meta = json.loads(blob.sig_metadata.decode("utf-8"))
            info(f"  alg = {meta.get('alg')}")
            info(f"  signed_at = {meta.get('signed_at')}")
            info(f"  signed_by = {meta.get('signed_by')}")
        except Exception:
            warn("sig_metadata 不是合法 JSON, 但不影响主链路验签")

        info("")
        info("⚠ HMAC (customer_code 绑定) 未在此处验证, 仅主程序运行时校验")
        info("✓ 离线验签全部通过")
        return True


def main():
    parser = argparse.ArgumentParser(description="天骏插件离线验签 (无需 PLUGIN_SECRET)")
    parser.add_argument("--in", dest="in_path", type=Path, required=True)
    parser.add_argument("--pub", dest="pub_pem", type=Path, required=True, help="主签名公钥 PEM")
    args = parser.parse_args()
    ok = verify(args.in_path, args.pub_pem)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
