"""scripts/plugin/_plugin_common.py — 反向 shim, 转发到后端权威版本

v3.7.x 起, 插件签名核心实现搬到 backend/plugin_system/_plugin_common.py:
- 原因: Nuitka 打包后客户机的 resources/ 只含 backend/, 不含 scripts/,
  导致 backend.plugin_system.verifier 在客户机上 import _plugin_common 失败 (ModuleNotFoundError)
- 现在: 权威版本归后端, CLI 脚本 (sign-plugin / pack-plugin / install-plugin /
  inject-public-key / dev-plugin / verify-plugin / lint-plugin-docs) 不改一行,
  仍 'from _plugin_common import ...', 通过本 shim 拿到后端版本

技术做法: sys.path 注入项目根 + sys.modules 替换, 使本模块完全等同后端模块
(包括所有公开/私有/带下划线符号)。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_authoritative = importlib.import_module("backend.plugin_system._plugin_common")

# 让 'from _plugin_common import X' 直接走后端那份, 不复制符号、不漏导出。
sys.modules[__name__] = _authoritative
