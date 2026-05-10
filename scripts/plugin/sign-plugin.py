#!/usr/bin/env python3
"""
sign-plugin.py — 主作者签名 .tjvplugin-uns → .tjvplugin

⚠️  仅在主作者隔离机器上跑 ⚠️
⚠️  不要在 CI 上跑 (除非按 design/07 §6.2 设置 GitHub Environment Secret) ⚠️

工作流 (design/07 §五):
  1. 解开 .tjvplugin-uns 到临时目录
  2. 重新 validate manifest + 重新计算 files_digest 校验一致
  3. 提示输入 PEM 密码, 加载主签名私钥
  4. 计算 customer_hmac = HMAC-SHA256(plugin_secret, customer_code|files_digest)
  5. 签名输入 = canonical_json(manifest)
  6. RSA-PSS-SHA256 签名
  7. 拼装 signature.bin
  8. 写 manifest 的 signed_at / signed_by
  9. 重新打 ZIP, 输出 .tjvplugin (无 -uns 后缀)

用法:
  python scripts/plugin/sign-plugin.py \\
    --in   ./xxx-uns.tjvplugin \\
    --out  ./signed/ \\
    --key  /mnt/usb-a/plugin_master_pri.pem \\
    --secret /mnt/usb-a/PLUGIN_SECRET.txt \\
    --signed-by tianjun-ai-master

骨架状态: 流程已实现, 但需要主作者的真实私钥 + PLUGIN_SECRET 才能跑通。
单元测试: tests/plugin_system/test_sign_verify_roundtrip.py (生成临时密钥对端到端跑通)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

from _plugin_common import (
    SIG_MAGIC,
    SIG_VERSION,
    SignatureBlob,
    calc_customer_hmac,
    calc_files_digest,
    calc_pubkey_fingerprint,
    canonical_json,
    check_customer_registered,
    err,
    info,
    rsa_sign_pss,
    validate_manifest,
    warn,
    write_signature_blob,
)


def load_plugin_secret(secret_path: Path) -> bytes:
    """从 PLUGIN_SECRET.txt 读 base64, 返回 32-byte 原始密钥。"""
    text = secret_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            secret = base64.b64decode(line)
            if len(secret) != 32:
                raise SystemExit(f"PLUGIN_SECRET 必须 32 byte, 当前 {len(secret)}")
            return secret
    raise SystemExit(f"PLUGIN_SECRET 文件无有效内容: {secret_path}")


def derive_pubkey_pem(pri_pem: bytes, password: bytes | None) -> bytes:
    """从私钥 PEM 推导公钥 PEM (用来计算 fingerprint)."""
    from cryptography.hazmat.primitives import serialization
    pri = serialization.load_pem_private_key(pri_pem, password=password)
    pub = pri.public_key()
    return pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def sign(
    in_path: Path,
    out_dir: Path,
    pri_key_path: Path,
    secret_path: Path,
    signed_by: str,
) -> Path:
    if not in_path.is_file():
        raise SystemExit(f"输入不存在: {in_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    info(f"签名: {in_path}")

    # ---- 1) 解到临时目录 ----
    with tempfile.TemporaryDirectory(prefix="tjv-sign-") as tmpd:
        tmp = Path(tmpd)
        with zipfile.ZipFile(in_path) as zf:
            zf.extractall(tmp)
        info(f"已解包到 {tmp}")

        manifest_path = tmp / "plugin.json"
        if not manifest_path.is_file():
            raise SystemExit(f"plugin.json 不存在")

        with manifest_path.open(encoding="utf-8") as f:
            manifest = json.load(f)
        validate_manifest(manifest)
        info("manifest schema 校验通过")

        cc = manifest["customer_code"]
        if not check_customer_registered(cc):
            warn(f"customer_code '{cc}' 未在 customer-codes.md 注册. 仍可签名但请在签后补 PR 登记.")

        # ---- 2) 重新计算 files_digest 与 manifest 比对 ----
        recomputed = calc_files_digest(tmp)
        declared = manifest.get("files_digest", "")
        if recomputed != declared:
            err(f"files_digest 不一致! manifest={declared}, recomputed={recomputed}")
            err("可能原因: 包被篡改, 或打包后被改过. 必须重新 pack.")
            raise SystemExit(3)
        info(f"files_digest 一致: {recomputed}")

        # ---- 3) 加载私钥 ----
        info(f"加载私钥: {pri_key_path}")
        pri_pem = pri_key_path.read_bytes()
        # 优先级: 环境变量 PLUGIN_KEY_PASSWORD > 交互式 getpass
        # CI / 自动化场景: export PLUGIN_KEY_PASSWORD=xxx 即可避开 tty.
        # 设置 PLUGIN_KEY_PASSWORD="" (空字符串) 表示私钥未加密.
        env_pwd = os.environ.get("PLUGIN_KEY_PASSWORD")
        if env_pwd is not None:
            info("从环境变量 PLUGIN_KEY_PASSWORD 读取私钥密码")
            password = env_pwd.encode("utf-8")
        else:
            password = getpass("私钥 PEM 密码 (无密码请直接回车): ").encode("utf-8")
        if password == b"":
            password = None  # cryptography API 约定: None 表示无密码

        try:
            pub_pem = derive_pubkey_pem(pri_pem, password)
            fingerprint = calc_pubkey_fingerprint(pub_pem)
            info(f"公钥 fingerprint = {fingerprint.hex()}")
        except Exception as e:
            err(f"私钥加载失败: {e}")
            raise SystemExit(4)

        # ---- 4) HMAC ----
        plugin_secret = load_plugin_secret(secret_path)
        customer_hmac = calc_customer_hmac(cc, recomputed, plugin_secret)
        info(f"customer_hmac (前 8 byte): {customer_hmac[:8].hex()}...")

        # ---- 5) RSA-PSS 签名 (输入 = canonical manifest) ----
        # 5.1) 先把 signed_at / signed_by 填上
        manifest["signed_at"] = datetime.now(timezone.utc).isoformat()
        manifest["signed_by"] = signed_by
        canon = canonical_json(manifest)
        manifest_path.write_bytes(canon)
        info(f"manifest 已加 signed_at / signed_by")

        # 5.2) RSA 签名
        rsa_sig = rsa_sign_pss(canon, pri_pem, password)
        info(f"RSA 签名长度: {len(rsa_sig)} byte")

        # 立即清密码内存
        del password
        del plugin_secret
        del pri_pem

        # ---- 6) 拼 signature.bin ----
        sig_meta = {
            "alg": "RSA-PSS-SHA256",
            "rsa_bits": len(rsa_sig) * 8,
            "hmac_alg": "HMAC-SHA256",
            "signed_at": manifest["signed_at"],
            "signed_by": signed_by,
        }
        meta_bytes = json.dumps(sig_meta, sort_keys=True, separators=(",", ":")).encode("utf-8")

        blob = SignatureBlob(
            magic=SIG_MAGIC,
            version=SIG_VERSION,
            public_key_fingerprint=fingerprint,
            customer_hmac=customer_hmac,
            rsa_sig_len=len(rsa_sig),
            rsa_signature=rsa_sig,
            sig_metadata_len=len(meta_bytes),
            sig_metadata=meta_bytes,
        )
        sig_bytes = write_signature_blob(blob)
        (tmp / "signature.bin").write_bytes(sig_bytes)
        info(f"signature.bin = {len(sig_bytes)} byte")

        # ---- 7) 重新打 ZIP ----
        name = in_path.stem
        if name.endswith("-uns"):
            name = name[:-4]  # 去掉 -uns 后缀
        out_path = out_dir / f"{name}.tjvplugin"

        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in sorted(tmp.rglob("*")):
                if p.is_file():
                    rel = p.relative_to(tmp).as_posix()
                    zf.write(p, arcname=rel)

        info(f"已签名: {out_path}")
        info(f"  fingerprint: {fingerprint.hex()}")
        info(f"  customer:    {cc}")
        info(f"  digest:      {recomputed}")
        return out_path


def main():
    parser = argparse.ArgumentParser(description="天骏插件签名工具 (主作者用)")
    parser.add_argument("--in", dest="in_path", type=Path, required=True, help="输入 .tjvplugin-uns")
    parser.add_argument("--out", dest="out_dir", type=Path, default=Path("./signed"), help="签后输出目录")
    parser.add_argument("--key", dest="pri_key", type=Path, required=True, help="私钥 PEM 路径 (建议 USB 上的)")
    parser.add_argument("--secret", dest="secret", type=Path, required=True, help="PLUGIN_SECRET.txt 路径")
    parser.add_argument("--signed-by", default="tianjun-ai-master", help="签名者标识 (写入 manifest)")
    args = parser.parse_args()
    sign(args.in_path, args.out_dir, args.pri_key, args.secret, args.signed_by)


if __name__ == "__main__":
    main()
