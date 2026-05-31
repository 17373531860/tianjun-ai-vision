"""坑 1 护栏: 插件 main_version_min / main_version_max 兼容性校验

客户视角叙事:
  XYZ 客户买了天骏 v3.10 设备, 主作者寄了个签名插件包, 但客户后来自己升到 v3.13
  又顺便忘了同步插件. 重启工控机时, 插件如果还是被加载, 一通 hook 触发可能因为
  ctx 字段漂掉、ORM 字段缺失而炸. 改进前后效果:

  改进前 (v3.7.0 ~ v3.12.0):
    manifest 里的 main_version_min=3.10.0 / max=3.10.x 形同虚设, 后端从来不读.
    带病插件直接加载, 第一个 cycle_end hook 一炸日志才知道.

  改进后 (本测试守护的契约):
    PluginManager.load_active 在 register_plugin 之前先调用
    check_main_version_compat(manifest, get_main_version()), 不兼容立刻拒绝,
    state=failed / error_code=PLUGIN_INCOMPATIBLE_VERSION + audit log.

如果有人改回"不读 manifest 里 min/max", 这条测试会立刻 FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.plugin_system.version_check import check_main_version_compat


# ----------------------- 精确版本匹配 -----------------------


def test_main_version_exactly_min_passes():
    manifest = {"main_version_min": "3.12.0"}
    ok, reason = check_main_version_compat(manifest, "3.12.0")
    assert ok is True
    assert reason == "OK"


def test_main_version_above_min_passes():
    manifest = {"main_version_min": "3.10.0"}
    ok, _ = check_main_version_compat(manifest, "3.12.0")
    assert ok is True


def test_main_version_below_min_rejected():
    manifest = {"main_version_min": "3.13.0"}
    ok, reason = check_main_version_compat(manifest, "3.12.0")
    assert ok is False
    assert "低于" in reason
    assert "3.13.0" in reason


# ----------------------- max 字段 + x 通配 -----------------------


def test_max_omitted_means_no_upper_bound():
    manifest = {"main_version_min": "3.0.0"}  # 没填 max
    ok, _ = check_main_version_compat(manifest, "9.99.99")
    assert ok is True


def test_max_exact_patch_inclusive():
    manifest = {"main_version_min": "3.10.0", "main_version_max": "3.12.0"}
    ok, _ = check_main_version_compat(manifest, "3.12.0")
    assert ok is True


def test_max_exact_patch_above_rejected():
    manifest = {"main_version_min": "3.10.0", "main_version_max": "3.12.0"}
    ok, reason = check_main_version_compat(manifest, "3.12.1")
    assert ok is False
    assert "高于" in reason


def test_max_minor_x_wildcard():
    """3.x 应该等价于 3.x.x — 任何 3.* 都过, 4.0.0 拒绝."""
    manifest = {"main_version_min": "3.0.0", "main_version_max": "3.x"}
    assert check_main_version_compat(manifest, "3.0.0")[0] is True
    assert check_main_version_compat(manifest, "3.99.99")[0] is True
    assert check_main_version_compat(manifest, "4.0.0")[0] is False


def test_max_minor_fixed_patch_x():
    """3.12.x — minor=12 全 patch 通过, 3.13.0 拒绝."""
    manifest = {"main_version_min": "3.12.0", "main_version_max": "3.12.x"}
    assert check_main_version_compat(manifest, "3.12.0")[0] is True
    assert check_main_version_compat(manifest, "3.12.99")[0] is True
    assert check_main_version_compat(manifest, "3.13.0")[0] is False


# ----------------------- 预发布版本号 -----------------------


def test_prerelease_main_version_treated_as_base():
    """主程序 3.12.0-rc1 应被视作 3.12.0 用于范围比较."""
    manifest = {"main_version_min": "3.12.0", "main_version_max": "3.12.x"}
    ok, _ = check_main_version_compat(manifest, "3.12.0-rc1")
    assert ok is True


# ----------------------- 错误格式 -----------------------


def test_missing_min_rejected():
    manifest = {}  # 没填 main_version_min
    ok, reason = check_main_version_compat(manifest, "3.12.0")
    assert ok is False
    assert "main_version_min" in reason


def test_min_with_x_wildcard_rejected():
    """main_version_min 不允许 x 通配 (schema 严格 X.Y.Z)."""
    manifest = {"main_version_min": "3.x.0"}
    ok, reason = check_main_version_compat(manifest, "3.12.0")
    assert ok is False
    assert "格式" in reason


def test_main_version_garbage_rejected():
    manifest = {"main_version_min": "3.12.0"}
    ok, reason = check_main_version_compat(manifest, "not-a-version")
    assert ok is False
    assert "无法解析" in reason


def test_max_format_garbage_rejected():
    manifest = {"main_version_min": "3.0.0", "main_version_max": "garbage"}
    ok, reason = check_main_version_compat(manifest, "3.12.0")
    assert ok is False
    assert "main_version_max" in reason


# ----------------------- get_main_version (运行时) -----------------------


def test_get_main_version_reads_package_json_or_fallback():
    """运行时主版本号要么读到合规版本号, 要么是 fallback "0.0.0".

    不强测具体值 (CI / 本地都能跑), 只校验语义:
    - 必返回字符串
    - 满足 X.Y.Z 模式 (含可选预发布尾巴)
    """
    import re

    from backend._version import get_main_version

    ver = get_main_version()
    assert isinstance(ver, str)
    assert re.match(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?$", ver), f"非法版本号: {ver!r}"
