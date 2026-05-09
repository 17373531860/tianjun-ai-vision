#!/usr/bin/env python3
"""
inject-public-key.py — 把公钥嵌入到主程序 backend/plugin_system/plugin_public_keys.py

用途 (design/02 §8.4):
  - 首次发版时把主签名公钥嵌入主程序
  - 紧急轮换公钥时更新
  - 多公钥并存 (旧版本宽限期)

输出文件结构 (Python const):
  PLUGIN_PUBLIC_KEYS = [
      {
          "fingerprint": "abcdef...",  # hex, 16 byte = 32 字符
          "pem": b\"\"\"-----BEGIN PUBLIC KEY-----...-----END PUBLIC KEY-----\"\"\",
          "valid_from": "2026-05-09T...",
          "valid_to":   "2030-05-09T...",
          "comment":    "首版主签名公钥",
      },
      ...
  ]

⚠️  本脚本会修改主程序源码, 必须经过 review + 引发主程序版本号 bump。
⚠️  不可在 CI 自动跑。

用法:
  # 替换全部 (危险, 会让旧插件全失效)
  python scripts/plugin/inject-public-key.py \\
    --pem /mnt/usb-a/plugin_master_pub.pem \\
    --comment "v1 主签名公钥" \\
    --replace-all \\
    --out backend/plugin_system/plugin_public_keys.py

  # 追加 (推荐, 用于轮换宽限期)
  python scripts/plugin/inject-public-key.py \\
    --pem /mnt/usb-a/plugin_master_pub_v2.pem \\
    --comment "v2 主签名公钥" \\
    --append \\
    --out backend/plugin_system/plugin_public_keys.py

骨架状态: 完全实现, 可跑。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

from _plugin_common import calc_pubkey_fingerprint, info, warn, err

HEADER = '''"""
插件签名公钥列表 — 由 scripts/plugin/inject-public-key.py 生成

⚠️ 严禁手工修改本文件
⚠️ 修改后必须 bump 主程序版本号 (见 design/02 §8.4)
"""
from __future__ import annotations
'''


def fmt_entry(entry: dict) -> str:
    pem_lines = entry["pem"].decode("utf-8").splitlines()
    pem_indented = "\n".join("        " + l for l in pem_lines)
    return (
        "    {\n"
        f'        "fingerprint": "{entry["fingerprint"]}",\n'
        f'        "valid_from":  "{entry["valid_from"]}",\n'
        f'        "valid_to":    "{entry["valid_to"]}",\n'
        f'        "comment":     "{entry["comment"]}",\n'
        f'        "pem":         b"""\n{pem_indented}\n"""'.rstrip()
        + ",\n    },"
    )


def render_module(entries: list[dict]) -> str:
    body = "\n".join(fmt_entry(e) for e in entries)
    return (
        HEADER
        + "\n\nPLUGIN_PUBLIC_KEYS = [\n"
        + body
        + "\n]\n\n"
        + "def get_pubkey_by_fingerprint(fp_hex: str) -> dict | None:\n"
        + "    for entry in PLUGIN_PUBLIC_KEYS:\n"
        + '        if entry["fingerprint"].lower() == fp_hex.lower():\n'
        + "            return entry\n"
        + "    return None\n"
    )


def parse_existing(path: Path) -> list[dict]:
    """简陋地从已生成的 plugin_public_keys.py 解析出现有 entry。

    设计上不允许手工编辑, 所以只支持解析自己生成的格式。
    """
    if not path.is_file():
        return []

    src = path.read_text(encoding="utf-8")
    # 用 ast 安全求值 PLUGIN_PUBLIC_KEYS
    import ast
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "PLUGIN_PUBLIC_KEYS":
                    return ast.literal_eval(node.value)
    warn("既有文件中未发现 PLUGIN_PUBLIC_KEYS, 视为新建")
    return []


def main():
    parser = argparse.ArgumentParser(description="把公钥嵌入主程序")
    parser.add_argument("--pem", type=Path, required=True, help="公钥 PEM 路径")
    parser.add_argument("--comment", required=True, help="本公钥用途描述")
    parser.add_argument("--out", type=Path, required=True,
                        help="输出 backend/plugin_system/plugin_public_keys.py")
    parser.add_argument("--valid-years", type=int, default=4, help="有效期 (年, 默认 4)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replace-all", action="store_true", help="清空现有列表, 仅保留本公钥")
    mode.add_argument("--append", action="store_true", help="追加到现有列表 (轮换用)")
    args = parser.parse_args()

    pub_pem = args.pem.read_bytes()
    fp = calc_pubkey_fingerprint(pub_pem).hex()
    info(f"公钥 fingerprint = {fp}")

    now = datetime.now(timezone.utc)
    new_entry = {
        "fingerprint": fp,
        "valid_from": now.isoformat(),
        "valid_to": now.replace(year=now.year + args.valid_years).isoformat(),
        "comment": args.comment,
        "pem": pub_pem.strip(),
    }

    if args.append:
        existing = parse_existing(args.out)
        for e in existing:
            if e["fingerprint"] == fp:
                err(f"公钥 fingerprint {fp} 已存在, 不需要追加")
                sys.exit(2)
        entries = existing + [new_entry]
    else:
        entries = [new_entry]

    rendered = render_module(entries)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    info(f"已写入 {args.out} ({len(entries)} 个公钥)")
    info("下一步:")
    info("  1. git diff backend/plugin_system/plugin_public_keys.py")
    info("  2. bump 主程序版本号 (见 design/02 §8.4)")
    info("  3. PR review + 合并")


if __name__ == "__main__":
    main()
