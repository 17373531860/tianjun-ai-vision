#!/usr/bin/env python3
"""
pack-plugin.py — 插件打包（不签名）

用途：插件作者本地打 .tjvplugin-uns 包，发给主作者签名。

工作流（与 design/07 §四 一致）：
  1. 校验 plugin.json 符合 schema
  2. 校验 customer_code 正则
  3. (Tier 2/3) 触发 frontend/ 的 vite build → 输出 dist/
  4. 计算 files_digest → 回填到 manifest
  5. 用 canonical_json 重写 plugin.json
  6. 打 ZIP，输出 <name>-<version>-<cc>-uns.tjvplugin

签名步骤由主作者运行 sign-plugin.py 完成（见 design/07 §五）。

用法:
  python scripts/plugin/pack-plugin.py --src ./my-plugin --out ./dist

Author: tianjun-ai-master
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

from _plugin_common import (
    calc_files_digest,
    canonical_json,
    check_customer_registered,
    info,
    warn,
    err,
    validate_manifest,
    ManifestValidationError,
)


def run_frontend_build(src_dir: Path) -> None:
    """如果 frontend/package.json 存在, 跑 npm install + vite build。

    设计：design/07 §4.2.3 - vite build 输出到 dist/。
    """
    fe = src_dir / "frontend"
    if not fe.is_dir():
        info("frontend/ 不存在, 跳过前端构建")
        return
    pkg = fe / "package.json"
    if not pkg.is_file():
        info("frontend/package.json 不存在, 跳过前端构建")
        return

    info("[TODO] 前端构建尚未实现 — 应该跑 `npm install && npm run build`")
    info("       手动跑后再来 pack。本期 v3.7 实施时补完。")
    # subprocess.check_call(["npm", "ci"], cwd=fe)
    # subprocess.check_call(["npm", "run", "build"], cwd=fe)


def collect_zip_members(src_dir: Path) -> list[Path]:
    """收集要进 ZIP 的所有文件 (排除 .git/__pycache__/node_modules 等)。"""
    from _plugin_common import _iter_files_for_digest
    return list(_iter_files_for_digest(src_dir))


def pack(src_dir: Path, out_dir: Path, *, skip_build: bool = False) -> Path:
    src_dir = src_dir.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    info(f"src_dir = {src_dir}")
    manifest_path = src_dir / "plugin.json"
    if not manifest_path.is_file():
        err(f"plugin.json 不存在: {manifest_path}")
        sys.exit(1)

    # ---- 1) 读 manifest ----
    with manifest_path.open(encoding="utf-8") as f:
        manifest = json.load(f)
    info(f"manifest_version={manifest.get('manifest_version')} "
         f"name={manifest.get('name')} "
         f"version={manifest.get('plugin_version')} "
         f"customer={manifest.get('customer_code')}")

    # ---- 2) 校验 manifest ----
    try:
        validate_manifest(manifest)
        info("manifest schema 校验通过")
    except ManifestValidationError as e:
        err(str(e))
        sys.exit(2)

    cc = manifest["customer_code"]
    if not check_customer_registered(cc):
        warn(f"customer_code '{cc}' 未在 docs/plugin-system/customer-codes.md 注册, 但仍打包. 主作者 sign 时会再警告.")

    # ---- 3) 前端构建 ----
    if not skip_build:
        run_frontend_build(src_dir)

    # ---- 4) 计算 files_digest ----
    info("计算 files_digest...")
    digest = calc_files_digest(src_dir)
    info(f"files_digest = {digest}")
    manifest["files_digest"] = digest

    # 5) created_at 自动填 (如果作者没填)
    if "created_at" not in manifest or not manifest.get("created_at"):
        manifest["created_at"] = datetime.now(timezone.utc).isoformat()

    # signed_at / signed_by 留空 (sign-plugin.py 填)
    if "signed_at" not in manifest:
        manifest["signed_at"] = ""
    if "signed_by" not in manifest:
        manifest["signed_by"] = ""

    # ---- 5) 落盘 canonical manifest ----
    canon = canonical_json(manifest)
    manifest_path.write_bytes(canon)
    info(f"plugin.json 已重写 ({len(canon)} byte, canonical)")

    # ---- 6) 打 ZIP ----
    name = manifest["name"].replace(" ", "_").replace("/", "_")
    version = manifest["plugin_version"]
    out_name = f"{name}-{version}-{cc}-uns.tjvplugin"
    out_path = out_dir / out_name

    files = collect_zip_members(src_dir)
    info(f"打包 {len(files)} 个文件 → {out_path}")

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in files:
            rel = f.relative_to(src_dir).as_posix()
            zf.write(f, arcname=rel)

    size_kb = out_path.stat().st_size / 1024
    info(f"完成: {out_path} ({size_kb:.1f} KB)")
    info(f"下一步: 把 .tjvplugin-uns 发给主作者 → 用 sign-plugin.py 签名")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="天骏插件打包工具 (不签名)")
    parser.add_argument("--src", type=Path, required=True, help="插件源目录")
    parser.add_argument("--out", type=Path, default=Path("./dist"), help="输出目录")
    parser.add_argument("--skip-build", action="store_true", help="跳过前端构建 (仅打包当前文件)")
    args = parser.parse_args()
    pack(args.src, args.out, skip_build=args.skip_build)


if __name__ == "__main__":
    main()
