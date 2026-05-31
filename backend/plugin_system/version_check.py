"""插件 manifest 中 ``main_version_min`` / ``main_version_max`` 校验。

主程序版本号格式: ``MAJOR.MINOR.PATCH[-PRERELEASE]``，例 ``3.12.0`` / ``3.12.0-rc1``。
manifest 字段格式 (与 ``docs/plugin-system/plugin.schema.json`` 严格对齐):

- ``main_version_min``: 必填, 严格 ``^\\d+\\.\\d+\\.\\d+$`` (三段精确版本)
- ``main_version_max``: 可选, ``^\\d+\\.(\\d+|x)(\\.(\\d+|x))?$`` (支持 x 通配)

通配规则:
- ``3.x`` ≡ ``3.x.x`` ≡ MAJOR=3 全部 minor / patch 都通过
- ``3.12.x`` ≡ MAJOR=3, MINOR=12, patch 任意

**预发布版本号** (例 ``3.12.0-rc1``) 比对策略:
- 把 ``-rc1`` 截掉做基线版本 ``3.12.0`` 比较 (符合"预发布属于该版本范围内"的直觉)
- 不实现 PEP 440 / SemVer 完整预发布优先级 (插件场景不需要)

设计原则:
- 失败侧偏拒绝: 解析失败 / manifest 字段缺失 / 版本格式异常一律返回 (False, reason)
- 不改既有数据: 纯函数 + 自闭逻辑, 便于单测
"""
from __future__ import annotations

import re
from typing import Tuple

_MIN_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_MAX_RE = re.compile(r"^(\d+)\.(\d+|x)(?:\.(\d+|x))?$")
_MAIN_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z0-9.-]+)?$")


def _parse_main(ver: str) -> Tuple[int, int, int]:
    """主程序版本号 → (major, minor, patch) 元组，去掉预发布尾巴。"""
    m = _MAIN_RE.match(ver.strip())
    if not m:
        raise ValueError(f"主程序版本号格式不合规: {ver!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _parse_min(ver: str) -> Tuple[int, int, int]:
    m = _MIN_RE.match(ver.strip())
    if not m:
        raise ValueError(f"main_version_min 格式不合规 (需要 X.Y.Z): {ver!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _parse_max(ver: str) -> Tuple[int, object, object]:
    """``main_version_max`` 解析。

    返回 (major, minor_or_x, patch_or_x) — minor / patch 可能是 int 或字符串 ``"x"``。
    省略段 (例 ``3.x``) 等价于 ``3.x.x``，所以缺省段统一填 ``"x"``。
    """
    m = _MAX_RE.match(ver.strip())
    if not m:
        raise ValueError(f"main_version_max 格式不合规 (需要 X.Y[.Z], Y/Z 可为 x): {ver!r}")
    major = int(m.group(1))
    minor_raw = m.group(2)
    patch_raw = m.group(3) or "x"
    minor: object = "x" if minor_raw == "x" else int(minor_raw)
    patch: object = "x" if patch_raw == "x" else int(patch_raw)
    return major, minor, patch


def _le_max(main: Tuple[int, int, int], maxv: Tuple[int, object, object]) -> bool:
    """主程序版本是否 ≤ max (含 x 通配)。"""
    main_major, main_minor, main_patch = main
    max_major, max_minor, max_patch = maxv

    if main_major != max_major:
        return main_major < max_major

    if max_minor == "x":
        return True
    if main_minor != max_minor:
        return main_minor < max_minor  # type: ignore[operator]

    if max_patch == "x":
        return True
    return main_patch <= max_patch  # type: ignore[operator]


def check_main_version_compat(
    manifest: dict,
    main_version: str,
) -> Tuple[bool, str]:
    """校验当前主程序版本是否落在 manifest 声明的范围内。

    Args:
        manifest: 插件 manifest 字典, 至少含 ``main_version_min``.
        main_version: 主程序运行时版本号 (来自 ``backend._version.get_main_version``).

    Returns:
        (compatible, reason) 二元组. ``reason`` 在 ``compatible=True`` 时为 ``"OK"``,
        否则为人类可读拒绝理由 (写 audit log + 错误码用).
    """
    try:
        main_tuple = _parse_main(main_version)
    except ValueError as e:
        return False, f"主程序版本号无法解析: {e}"

    min_str = (manifest.get("main_version_min") or "").strip()
    if not min_str:
        return False, "manifest 缺失 main_version_min (插件 schema 要求必填)"

    try:
        min_tuple = _parse_min(min_str)
    except ValueError as e:
        return False, f"main_version_min 格式错误: {e}"

    if main_tuple < min_tuple:
        return False, (
            f"主程序版本 {main_version} 低于插件要求的 main_version_min {min_str} — "
            f"请升级主程序或换支持当前版本的插件包"
        )

    max_str = (manifest.get("main_version_max") or "").strip()
    if max_str:
        try:
            max_tuple = _parse_max(max_str)
        except ValueError as e:
            return False, f"main_version_max 格式错误: {e}"
        if not _le_max(main_tuple, max_tuple):
            return False, (
                f"主程序版本 {main_version} 高于插件支持的 main_version_max {max_str} — "
                f"请升级插件包或回滚主程序"
            )

    return True, "OK"
