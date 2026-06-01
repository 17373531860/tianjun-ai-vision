"""主程序版本号唯一来源（运行时只读）。

设计取舍:
- **唯一权威源**: ``electron/package.json`` 的 ``version`` 字段（与发版/CI 对齐）
- 后端启动时一次读 + 进程级缓存（避免热路径重复 I/O）
- 找不到或解析失败回退 ``"0.0.0"`` —— 让任何 ``main_version_min`` 检查都失败,
  相当于"插件系统不工作 > 误判兼容"的安全失败侧

为什么不在 ``backend/__init__.py`` 写常量:
- 主版本号跟着 electron/package.json 走 (Inno Setup / GitHub Action / 客户机
  统一从这里读), 后端再维护一份常量必然漂移
- 通过运行时读保证"代码 + 配置 + 安装包" 三者版本一致
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

_PACKAGE_JSON = Path(__file__).resolve().parent.parent / "electron" / "package.json"
_FALLBACK = "0.0.0"
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?$")


@lru_cache(maxsize=1)
def get_main_version() -> str:
    """返回主程序版本号字符串，例如 ``"3.12.0"``。

    取值优先级 (与 export_context._read_app_info 对齐):
    1. 环境变量 ``TIANJUN_APP_VERSION`` —— Electron 启动后端时注入 (backend-manager.js).
       **正式安装包必走这条**: 打包后 package.json 进了 app.asar, Python fs 读不到,
       只能靠 env 传. 之前漏读此 env 导致客户机插件版本校验恒为 0.0.0 而误拒.
    2. 回退读 ``electron/package.json`` 文件 —— 仅开发模式直接 uvicorn 启动时命中.
    3. 都失败回退 ``"0.0.0"``，让插件 ``main_version_min`` 校验天然不通过。
    """
    env_ver = os.environ.get("TIANJUN_APP_VERSION", "").strip()
    if _VERSION_RE.match(env_ver):
        return env_ver

    try:
        if not _PACKAGE_JSON.exists():
            return _FALLBACK
        data = json.loads(_PACKAGE_JSON.read_text(encoding="utf-8"))
        ver = str(data.get("version") or "").strip()
        if not _VERSION_RE.match(ver):
            return _FALLBACK
        return ver
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return _FALLBACK
