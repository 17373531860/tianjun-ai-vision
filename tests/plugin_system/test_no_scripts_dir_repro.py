"""客户反馈剧本: v3.7.0 分包客户机 ModuleNotFoundError 回归护栏

现场: 2026-05-11 晚客户用 GitHub Release 上的 v3.7.0 分包安装包,
后端启动时 backend/plugin_system/verifier.py 报
ModuleNotFoundError: No module named '_plugin_common', 进程退出 code 1, splash 永远卡住.

根因: 老 verifier.py 把 scripts/plugin 路径塞 sys.path 再 import _plugin_common,
开发环境管用; 但 Nuitka 打包不复制 scripts/, 客户机找不到这个目录.

修复: _plugin_common 搬到 backend/plugin_system/ 作为权威版本,
scripts/plugin/_plugin_common.py 改成 shim 反向 import.

本测试 = 双向验证护栏: 临时把 scripts/plugin 隐藏 (模拟客户机),
import backend.plugin_system.verifier 必须仍然成功.

下次有人改回老写法, 这条测试会立刻 FAIL.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_PLUGIN = PROJECT_ROOT / "scripts" / "plugin"
HIDDEN = PROJECT_ROOT / "scripts" / ".plugin_hidden_for_regression"


@pytest.fixture
def hide_scripts_plugin():
    """临时把 scripts/plugin/ 改名, 模拟 Nuitka 打包后客户机的目录布局.
    跑完无论成功失败都恢复, 防止污染仓库.
    """
    if not SCRIPTS_PLUGIN.is_dir():
        pytest.skip("scripts/plugin 不存在, 跳过 (可能在已打包环境里跑)")

    if HIDDEN.exists():
        pytest.fail("上一次测试中断未清理, 请手动把 scripts/.plugin_hidden_for_regression 改回 scripts/plugin")

    os.rename(SCRIPTS_PLUGIN, HIDDEN)
    try:
        # 清掉可能缓存的相关 module, 强制重新 import 走真实路径
        for name in list(sys.modules):
            if "plugin_system" in name or name == "_plugin_common":
                del sys.modules[name]
        yield
    finally:
        os.rename(HIDDEN, SCRIPTS_PLUGIN)
        # 测试后再次清缓存, 让后续测试拿到正常 import
        for name in list(sys.modules):
            if "plugin_system" in name or name == "_plugin_common":
                del sys.modules[name]


def test_verifier_imports_without_scripts_plugin(hide_scripts_plugin):
    """客户机现场: 没有 scripts/plugin 也必须能 import verifier."""
    import backend.plugin_system.verifier as verifier
    assert callable(verifier.calc_pubkey_fingerprint)
    assert callable(verifier.calc_files_digest)
    assert callable(verifier.validate_manifest)


def test_backend_main_imports_without_scripts_plugin(hide_scripts_plugin):
    """客户机现场: 后端整体 import 必须通 (复现客户那一行炸点)."""
    from backend.main import app  # noqa: F401


def test_authoritative_module_lives_in_backend():
    """权威版本必须在 backend/plugin_system/, 不允许回到 scripts/plugin/."""
    backend_authoritative = PROJECT_ROOT / "backend" / "plugin_system" / "_plugin_common.py"
    assert backend_authoritative.is_file(), (
        f"backend/plugin_system/_plugin_common.py 必须存在 (权威版本), "
        f"否则客户机 Nuitka 包会再次崩 ModuleNotFoundError."
    )


def test_scripts_plugin_shim_size_is_small():
    """scripts/plugin/_plugin_common.py 必须是 thin shim (< 2KB), 不允许双写整份代码."""
    if not SCRIPTS_PLUGIN.is_dir():
        pytest.skip("scripts/plugin 不存在")
    shim = SCRIPTS_PLUGIN / "_plugin_common.py"
    size = shim.stat().st_size
    assert size < 2048, (
        f"scripts/plugin/_plugin_common.py 大小 {size} byte 超过 2KB, "
        f"看起来不是 thin shim — 可能误将权威实现写回 scripts 目录, "
        f"会跟 backend/plugin_system/_plugin_common.py 两份代码并存, 后续会维护错乱."
    )
