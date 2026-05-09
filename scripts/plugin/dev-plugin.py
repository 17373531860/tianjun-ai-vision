#!/usr/bin/env python3
"""
dev-plugin.py — 插件本地开发模式 (DEBUG_MODE 跳过验签)

工作流 (design/07 §六):
  1. 检查环境变量 DEBUG_MODE=1 + PLUGIN_DEV_MODE=1 (二者都要)
  2. 把插件源目录直接 symlink / copy 到 DATA_DIR/plugins/{cc}/
  3. 跳过签名校验 (PluginManager 看到 PLUGIN_DEV_MODE=1 时不做验签)
  4. 自动激活
  5. 监控 frontend/ 文件变化触发重新构建 (TODO)

⚠️ 仅本地开发用. release build (Nuitka) 强制移除 PLUGIN_DEV_MODE 检查 (design/07 §六)。

用法:
  export DEBUG_MODE=1
  export PLUGIN_DEV_MODE=1
  python scripts/plugin/dev-plugin.py --src ./my-plugin --activate
  # 然后启动 backend, 插件会被加载

骨架状态: 链接 + 激活已实现, watch mode 待实施。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

from _plugin_common import info, warn, err


def get_data_dir() -> Path:
    return Path(os.environ.get("TIANJUN_DATA_DIR", str(Path.home() / ".tianjun")))


def check_env() -> bool:
    if os.environ.get("DEBUG_MODE") != "1":
        err("需要 export DEBUG_MODE=1")
        return False
    if os.environ.get("PLUGIN_DEV_MODE") != "1":
        err("需要 export PLUGIN_DEV_MODE=1")
        return False
    return True


def link_plugin(src: Path, activate: bool, *, copy_mode: bool = False) -> int:
    src = src.resolve()
    if not (src / "plugin.json").is_file():
        err(f"plugin.json 不存在: {src}")
        return 1

    with (src / "plugin.json").open(encoding="utf-8") as f:
        manifest = json.load(f)
    cc = manifest["customer_code"]
    info(f"customer_code = {cc}")

    data_dir = get_data_dir()
    target = data_dir / "plugins" / cc

    if target.exists() or target.is_symlink():
        warn(f"{target} 已存在, 移除")
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    if copy_mode or os.name == "nt":
        # Windows 默认无 symlink 权限, 改用 copytree
        shutil.copytree(src, target)
        info(f"已 copy {src} → {target}")
    else:
        target.symlink_to(src, target_is_directory=True)
        info(f"已 symlink {target} → {src}")

    if activate:
        active_path = data_dir / "plugins" / "ACTIVE"
        active_path.write_text(cc + "\n", encoding="utf-8")
        info(f"已激活: {cc}")

    info("")
    info("启动 backend 后插件即生效 (DEBUG_MODE 下跳过验签)")
    info("注意: 改 backend Python 代码后需重启 backend (无 hot-reload)")
    info("      改 frontend 代码可在 vite dev server 热更新")
    return 0


def main():
    parser = argparse.ArgumentParser(description="天骏插件本地开发模式")
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--activate", action="store_true", help="link 后立即激活")
    parser.add_argument("--copy", action="store_true", help="用 copy 而非 symlink (Windows 自动开启)")
    args = parser.parse_args()

    if not check_env():
        sys.exit(2)

    sys.exit(link_plugin(args.src, args.activate, copy_mode=args.copy))


if __name__ == "__main__":
    main()
