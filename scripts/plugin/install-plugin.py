#!/usr/bin/env python3
"""
install-plugin.py — CLI 安装 .tjvplugin 到本地天骏后端

与 UI 安装等效 (design/07 §五)。适合工厂 IT 部署, 或客户机断网时本地装。

工作流:
  1. 用 verify-plugin.py 流程做离线验签
  2. POST /api/v1/plugins/install 到本地 backend (上传 ZIP)
     或者: 直接落地到 %APPDATA%/.../data/plugins/{cc}/, 同步通知 backend reload
  3. 提示重启 backend (或自动调 /api/v1/plugins/{cc}/activate)

骨架状态: 框架已搭, 实际 HTTP 调用需要 backend 实装 PluginManager API 后才能跑通。
        当前阶段先实现"本地落盘 + 提示重启"模式。

用法:
  python scripts/plugin/install-plugin.py \\
    --in   xxx.tjvplugin \\
    --pub  /opt/tianjun/plugin_master_pub.pem \\
    --backend http://127.0.0.1:8000 \\
    --activate
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

from _plugin_common import info, warn, err


def get_data_dir() -> Path:
    """模拟 backend.core.config.DATA_DIR 的逻辑。"""
    return Path(os.environ.get("TIANJUN_DATA_DIR", str(Path.home() / ".tianjun")))


def install_via_http(in_path: Path, backend: str, activate: bool) -> int:
    info(f"[TODO] HTTP 安装模式: POST {backend}/api/v1/plugins/install")
    info("       需要等 backend 实装 PluginManager API (design/06 §九 F12)")
    return 1


def install_offline(in_path: Path, pub_pem: Path, activate: bool) -> int:
    """离线模式: 验签 → 本地落盘 → 提示重启 backend。"""
    # 1) 验签
    sys.path.insert(0, str(THIS_DIR))
    import importlib.util
    spec = importlib.util.spec_from_file_location("verify_plugin", THIS_DIR / "verify-plugin.py")
    if spec is None or spec.loader is None:
        err("无法加载 verify-plugin.py")
        return 2
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if not mod.verify(in_path, pub_pem):
        err("验签失败, 拒绝安装")
        return 3

    # 2) 解 manifest 拿 customer_code + version
    with zipfile.ZipFile(in_path) as zf:
        with zf.open("plugin.json") as f:
            import json
            manifest = json.loads(f.read().decode("utf-8"))
    cc = manifest["customer_code"]
    ver = manifest["plugin_version"]
    info(f"plugin: {cc} v{ver}")

    # 3) 落盘到 DATA_DIR/plugins/{cc}/
    data_dir = get_data_dir()
    plug_dir = data_dir / "plugins" / cc
    if plug_dir.exists():
        warn(f"已存在: {plug_dir} — 备份为 .{ver}.bak")
        backup = plug_dir.with_suffix(f".{ver}.bak")
        if backup.exists():
            shutil.rmtree(backup)
        shutil.move(plug_dir, backup)
    plug_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(in_path) as zf:
        zf.extractall(plug_dir)
    info(f"已解到 {plug_dir}")

    # 4) 设 active.txt (单插件激活机制, design/03 §3 SystemConfig key)
    if activate:
        active_path = data_dir / "plugins" / "ACTIVE"
        active_path.write_text(cc + "\n", encoding="utf-8")
        info(f"已激活: {active_path} = {cc}")

    info("")
    info("⚠ 请重启 backend 进程使插件生效")
    info("   (v3.7 不支持热加载, 见 design/06 §五)")
    return 0


def main():
    parser = argparse.ArgumentParser(description="天骏插件 CLI 安装工具")
    parser.add_argument("--in", dest="in_path", type=Path, required=True)
    parser.add_argument("--pub", dest="pub_pem", type=Path, required=True, help="主签名公钥")
    parser.add_argument("--backend", default=None, help="后端地址 (HTTP 模式) 例: http://127.0.0.1:8000")
    parser.add_argument("--activate", action="store_true", help="安装完立即激活")
    args = parser.parse_args()

    if args.backend:
        sys.exit(install_via_http(args.in_path, args.backend, args.activate))
    else:
        sys.exit(install_offline(args.in_path, args.pub_pem, args.activate))


if __name__ == "__main__":
    main()
