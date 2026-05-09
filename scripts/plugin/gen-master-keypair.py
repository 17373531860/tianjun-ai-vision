#!/usr/bin/env python3
"""
插件系统 — 主作者一次性密钥生成脚本

⚠️  只在主作者本地运行 ⚠️
⚠️  绝不要在 CI / 共享机器 / 客户工控机上运行 ⚠️
⚠️  生成出的私钥只允许保存在: 加密 USB×2 + 1Password / 保险柜 ⚠️

生成的产物：
  1. plugin_master_pri.pem      — RSA 4096-bit 私钥, AES-256 + 强密码加密
  2. plugin_master_pub.pem      — RSA 公钥, 准备嵌入主程序
  3. PLUGIN_SECRET.txt          — 32-byte HMAC 密钥, base64
  4. fingerprint.txt            — 公钥 SHA256 fingerprint (前 16 byte)
  5. METADATA.json              — 生成时间, 版本号, 算法

工作流：
  1. 主作者准备一台离线/隔离的 Linux 机器
  2. 安装依赖：pip install cryptography
  3. 运行：python scripts/plugin/gen-master-keypair.py --out ./out
  4. 把 ./out/* 全部复制到 USB-A
  5. 把 ./out/* 复制到 USB-B (异地备份)
  6. 把 PEM 密码 + PLUGIN_SECRET 抄到 1Password (主作者账号 + 备份账号)
  7. PHYSICALLY 放进保险柜（注意潮湿/磁场）
  8. 当前会话 shred ./out, history -c, 关机断电
  9. 仅把 plugin_master_pub.pem 与 fingerprint.txt 推到仓库

参考: design/02_signature.md §八/§九

Author: tianjun-ai-master
First version: 2026-05
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path

# ============================================================
# 依赖检查
# ============================================================

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    print("[FATAL] 缺少 cryptography 库")
    print("        pip install cryptography>=41.0.0")
    sys.exit(1)


# ============================================================
# 常量
# ============================================================

RSA_BITS = 4096
RSA_PUBLIC_EXPONENT = 65537
PLUGIN_SECRET_BYTES = 32
FINGERPRINT_LEN = 16
ALG_VERSION = "v1"
PASSWORD_MIN_LEN = 16


# ============================================================
# 工具
# ============================================================

def confirm_offline_environment() -> None:
    """让操作员确认环境安全。"""
    print("=" * 70)
    print(" 主作者密钥生成 — 安全确认")
    print("=" * 70)
    print()
    print("  在继续之前，请确认以下条件全部满足：")
    print()
    print("    [ ] 我正在物理隔离 / 断网的工作站")
    print("    [ ] 屏幕周围没有第三方人员或摄像头")
    print("    [ ] 已关闭浏览器、聊天软件、剪贴板同步、IM 工具")
    print("    [ ] 输出目录是临时挂载的加密分区（生成后会销毁）")
    print("    [ ] 我已准备 2 个加密 USB / 1Password 主备账号 / 保险柜")
    print()
    ans = input("  全部确认请输入大写 YES（其他输入即终止）: ").strip()
    if ans != "YES":
        print("\n[ABORTED] 未确认，已退出。\n")
        sys.exit(2)
    print()


def get_strong_password() -> str:
    """要求两次输入强密码（PEM AES-256 加密用）。"""
    while True:
        p1 = getpass(f"私钥 PEM 密码 (≥ {PASSWORD_MIN_LEN} 字符): ")
        if len(p1) < PASSWORD_MIN_LEN:
            print(f"  ✗ 太短（至少 {PASSWORD_MIN_LEN} 字符），重试")
            continue
        if p1.lower() == p1 or p1.upper() == p1 or p1.isalnum():
            print("  ⚠ 建议混用大小写 + 数字 + 符号，请重新输入（按 Ctrl+C 跳过此校验）")
            try:
                p_check = getpass(f"  确认要用这个密码？再输一遍以确认: ")
                if p_check != p1:
                    print("  ✗ 不匹配，重试")
                    continue
                # 用户坚持就接受
            except KeyboardInterrupt:
                print("\n  → 跳过强度校验")
        p2 = getpass("再输入一遍密码: ")
        if p1 != p2:
            print("  ✗ 两次输入不一致，重试\n")
            continue
        return p1


def calc_public_key_fingerprint(pub_pem: bytes) -> bytes:
    """公钥 fingerprint = SHA256(SubjectPublicKeyInfo) 取前 16 byte。

    与 design/02_signature.md §3.4 中 PluginManager._calc_public_key_fingerprint
    保持一致。
    """
    pub_obj = serialization.load_pem_public_key(pub_pem)
    der = pub_obj.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashlib.sha256(der).digest()
    return digest[:FINGERPRINT_LEN]


# ============================================================
# 主流程
# ============================================================

def generate_keypair(out_dir: Path) -> dict:
    """执行密钥生成，落盘到 out_dir。

    返回元数据 dict，方便调用者打印汇总。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        print(f"[FATAL] 输出目录非空：{out_dir}")
        print("        请清空或换一个目录避免覆盖既有密钥")
        sys.exit(3)

    # ---- 1) 强密码 ----
    print("\n[1/5] 设置私钥 PEM 加密密码")
    print("      规则: 长度 ≥ 16, 建议混用 26+26+10+符号 ≈ 72 种字符")
    password = get_strong_password().encode("utf-8")

    # ---- 2) 生成 RSA 4096 ----
    print("\n[2/5] 正在生成 RSA-4096 密钥对（约 5 ~ 30 秒）...")
    private_key = rsa.generate_private_key(
        public_exponent=RSA_PUBLIC_EXPONENT,
        key_size=RSA_BITS,
    )
    public_key = private_key.public_key()
    print("      ✓ 密钥对已生成")

    # ---- 3) 序列化私钥（PEM + AES-256） ----
    print("\n[3/5] 序列化私钥（PEM + AES-256 加密）...")
    pri_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )

    pri_path = out_dir / "plugin_master_pri.pem"
    pri_path.write_bytes(pri_pem)
    os.chmod(pri_path, 0o600)
    print(f"      ✓ 写入 {pri_path}（mode 0600）")

    # 立即清空内存里的密码
    del password

    # ---- 4) 序列化公钥 ----
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_path = out_dir / "plugin_master_pub.pem"
    pub_path.write_bytes(pub_pem)
    os.chmod(pub_path, 0o644)
    print(f"      ✓ 写入 {pub_path}（mode 0644）")

    fingerprint = calc_public_key_fingerprint(pub_pem)
    fp_hex = fingerprint.hex()
    fp_path = out_dir / "fingerprint.txt"
    fp_path.write_text(fp_hex + "\n", encoding="utf-8")
    os.chmod(fp_path, 0o644)
    print(f"      ✓ 写入 {fp_path}: {fp_hex}")

    # ---- 5) 生成 PLUGIN_SECRET ----
    print("\n[4/5] 生成 PLUGIN_SECRET（32-byte 随机, HMAC 用）...")
    plugin_secret = secrets.token_bytes(PLUGIN_SECRET_BYTES)
    secret_b64 = base64.b64encode(plugin_secret).decode("ascii")

    sec_path = out_dir / "PLUGIN_SECRET.txt"
    sec_path.write_text(
        "# PLUGIN_SECRET (base64, 32 bytes)\n"
        "# 用途: customer_code HMAC 输入, design/02_signature.md §1.3\n"
        "# ⚠ 本文件等同私钥, 必须 USB×2 + 1Password 备份\n"
        "# ⚠ 严禁推到 git / 邮件 / 聊天\n"
        f"{secret_b64}\n",
        encoding="utf-8",
    )
    os.chmod(sec_path, 0o600)
    print(f"      ✓ 写入 {sec_path}（mode 0600）")
    del plugin_secret

    # ---- 6) METADATA ----
    print("\n[5/5] 写入 METADATA.json（公开信息，可入库）")
    now = datetime.now(timezone.utc)
    metadata = {
        "alg_version": ALG_VERSION,
        "rsa_bits": RSA_BITS,
        "rsa_public_exponent": RSA_PUBLIC_EXPONENT,
        "hmac_secret_bytes": PLUGIN_SECRET_BYTES,
        "fingerprint_len_bytes": FINGERPRINT_LEN,
        "public_key_fingerprint_hex": fp_hex,
        "generated_at_utc": now.isoformat(),
        "generated_at_unix": int(now.timestamp()),
        "tool_version": "gen-master-keypair.py v1.0",
        "doc_ref": "docs/plugin-system/design/02_signature.md §八",
        "valid_until": now.replace(year=now.year + 4).date().isoformat(),
        "intended_purpose": "Master signing key for .tjvplugin packages",
    }
    meta_path = out_dir / "METADATA.json"
    meta_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(meta_path, 0o644)
    print(f"      ✓ 写入 {meta_path}")

    return metadata


def print_post_steps(out_dir: Path, metadata: dict) -> None:
    print()
    print("=" * 70)
    print(" ✓ 全部生成完成")
    print("=" * 70)
    print()
    print(f"  输出目录: {out_dir.resolve()}")
    print()
    print("  文件清单:")
    print(f"    [PRI]  plugin_master_pri.pem      — 私钥（密码加密, 600）")
    print(f"    [PUB]  plugin_master_pub.pem      — 公钥（644, 可入库）")
    print(f"    [SEC]  PLUGIN_SECRET.txt          — HMAC 密钥（600）")
    print(f"    [META] fingerprint.txt            — {metadata['public_key_fingerprint_hex']}")
    print(f"    [META] METADATA.json              — 公开元数据")
    print()
    print("=" * 70)
    print(" 下一步（按顺序）")
    print("=" * 70)
    print("""
  1. 把 4 个文件全部复制到加密 USB-A
       cp -av {out}/* /media/usb-a/plugin-master-key-{date}/

  2. 复制到加密 USB-B（异地备份）
       cp -av {out}/* /media/usb-b/plugin-master-key-{date}/

  3. 把 PEM 密码 + PLUGIN_SECRET 抄到 1Password
     - 主账号 (主作者)
     - 备份账号 (公司管理员)

  4. 把 USB-A 放进主作者保险柜，USB-B 放异地备份点

  5. 在仓库里只提交两个公开文件:
       cp {out}/plugin_master_pub.pem  backend/plugin_system/keys/
       cp {out}/fingerprint.txt         backend/plugin_system/keys/

  6. 把 plugin_master_pub.pem 转成 Python const 嵌入:
       python scripts/plugin/inject-public-key.py \\
         --pem  {out}/plugin_master_pub.pem \\
         --out  backend/plugin_system/plugin_public_keys.py

  7. 销毁本地副本:
       shred -uvz {out}/plugin_master_pri.pem
       shred -uvz {out}/PLUGIN_SECRET.txt
       rm -rf {out}
       history -c
       sync && halt -p

  8. 在 docs/plugin-system/customer-codes.md 历史变更里加一行:
       2026-XX-XX  生成主签名密钥, fingerprint={fp}
""".format(
        out=out_dir,
        date=datetime.now().strftime("%Y%m%d"),
        fp=metadata["public_key_fingerprint_hex"][:16] + "...",
    ))
    print("=" * 70)
    print()


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="主作者一次性生成 RSA-4096 + HMAC-secret",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="输出目录（必须空, 建议放在加密分区, 如 /mnt/encrypted/keygen-202605）",
    )
    parser.add_argument(
        "--skip-confirm",
        action="store_true",
        help="跳过环境安全确认（不推荐, 仅自测时用）",
    )
    args = parser.parse_args()

    if not args.skip_confirm:
        confirm_offline_environment()
    else:
        print("[WARN] 已跳过环境安全确认，仅供自测")

    metadata = generate_keypair(args.out)
    print_post_steps(args.out, metadata)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] 已用户中断，未完成生成，请清理输出目录")
        sys.exit(130)
