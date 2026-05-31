"""files_digest — 跨平台、确定性、防篡改。"""
from pathlib import Path

import pytest

from _plugin_common import (
    DIGEST_EXCLUDE_NAMES,
    FILES_DIGEST_REGEX,
    FilesDigestError,
    calc_files_digest,
    calc_files_digest_in_zip,
)


def _make_dir(root: Path, files: dict[str, bytes]) -> Path:
    for rel, content in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content)
    return root


def test_digest_format(tmp_path):
    _make_dir(tmp_path, {"a.txt": b"hello"})
    d = calc_files_digest(tmp_path)
    assert FILES_DIGEST_REGEX.match(d), f"格式不对: {d}"


def test_digest_deterministic(tmp_path):
    """同一目录两次跑必须完全相同。"""
    _make_dir(tmp_path, {"a.txt": b"hello", "b/c.txt": b"world"})
    d1 = calc_files_digest(tmp_path)
    d2 = calc_files_digest(tmp_path)
    assert d1 == d2


def test_digest_different_for_different_content(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make_dir(a, {"x.txt": b"hello"})
    _make_dir(b, {"x.txt": b"world"})
    assert calc_files_digest(a) != calc_files_digest(b)


def test_digest_different_for_different_filename(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make_dir(a, {"x.txt": b"hello"})
    _make_dir(b, {"y.txt": b"hello"})
    assert calc_files_digest(a) != calc_files_digest(b)


def test_digest_excludes_pycache(tmp_path):
    """__pycache__ 不参与 digest。"""
    _make_dir(tmp_path, {
        "a.py": b"x",
        "__pycache__/a.cpython-310.pyc": b"compiled",
        "sub/__pycache__/b.pyc": b"compiled",
    })
    d_with = calc_files_digest(tmp_path)

    # 删 pycache 应得到相同 digest
    import shutil
    shutil.rmtree(tmp_path / "__pycache__")
    shutil.rmtree(tmp_path / "sub" / "__pycache__")
    d_without = calc_files_digest(tmp_path)

    assert d_with == d_without


def test_digest_excludes_pyc(tmp_path):
    _make_dir(tmp_path, {
        "a.py": b"src",
        "a.pyc": b"compiled",
    })
    d_with_pyc = calc_files_digest(tmp_path)

    (tmp_path / "a.pyc").unlink()
    d_without_pyc = calc_files_digest(tmp_path)
    assert d_with_pyc == d_without_pyc


def test_digest_excludes_signature_bin(tmp_path):
    """signature.bin 自身不参与 digest（重要：否则签名循环依赖）。"""
    _make_dir(tmp_path, {
        "plugin.json": b"{}",
        "signature.bin": b"\xde\xad\xbe\xef",
        "frontend/theme.css": b":root{}",
    })
    d_with = calc_files_digest(tmp_path)

    (tmp_path / "signature.bin").unlink()
    d_without = calc_files_digest(tmp_path)
    assert d_with == d_without


def test_digest_excludes_plugin_json(tmp_path):
    """plugin.json 由 RSA 签名保护，不参与 digest，避免 files_digest 字段自引用循环。"""
    _make_dir(tmp_path, {
        "plugin.json": b'{"files_digest":"sha256:' + b"0" * 64 + b'"}',
        "frontend/theme.css": b":root{}",
    })
    d1 = calc_files_digest(tmp_path)

    (tmp_path / "plugin.json").write_bytes(
        b'{"files_digest":"sha256:' + b"f" * 64 + b'","signed_by":"x"}'
    )
    d2 = calc_files_digest(tmp_path)
    assert d1 == d2


def test_digest_excludes_git(tmp_path):
    _make_dir(tmp_path, {
        "code.py": b"x",
        ".git/HEAD": b"ref",
        ".git/config": b"x",
    })
    d_with = calc_files_digest(tmp_path)
    import shutil
    shutil.rmtree(tmp_path / ".git")
    d_without = calc_files_digest(tmp_path)
    assert d_with == d_without


def test_digest_empty_dir_raises(tmp_path):
    with pytest.raises(FilesDigestError):
        calc_files_digest(tmp_path)


def test_digest_not_dir_raises(tmp_path):
    f = tmp_path / "x.txt"
    f.write_bytes(b"x")
    with pytest.raises(FilesDigestError):
        calc_files_digest(f)


def test_digest_zip_matches_dir(tmp_path):
    """ZIP 内 digest 应该与对应目录 digest 相同。"""
    src = tmp_path / "src"
    _make_dir(src, {
        "plugin.json": b'{"x":1}',
        "frontend/theme.css": b":root{}",
        "backend/__init__.py": b"",
    })
    d_dir = calc_files_digest(src)

    import zipfile
    zip_path = tmp_path / "p.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(src).as_posix())

    d_zip = calc_files_digest_in_zip(zip_path)
    assert d_dir == d_zip


def test_digest_path_separator_independent(tmp_path):
    """digest 应该用 POSIX 分隔符, 不受 Windows 影响 (我们手动 .as_posix())."""
    _make_dir(tmp_path, {
        "a/b/c.txt": b"x",
        "a/d.txt": b"y",
    })
    d1 = calc_files_digest(tmp_path)

    # 重建在另一个父目录, 期望 digest 一致
    other = tmp_path.parent / "other"
    other.mkdir()
    _make_dir(other, {
        "a/b/c.txt": b"x",
        "a/d.txt": b"y",
    })
    d2 = calc_files_digest(other)
    assert d1 == d2


def test_digest_changes_when_file_added(tmp_path):
    _make_dir(tmp_path, {"a.txt": b"x"})
    d1 = calc_files_digest(tmp_path)

    (tmp_path / "b.txt").write_bytes(b"y")
    d2 = calc_files_digest(tmp_path)
    assert d1 != d2


def test_digest_changes_when_file_renamed(tmp_path):
    _make_dir(tmp_path, {"a.txt": b"x"})
    d1 = calc_files_digest(tmp_path)

    (tmp_path / "a.txt").rename(tmp_path / "b.txt")
    d2 = calc_files_digest(tmp_path)
    assert d1 != d2


def test_digest_sort_key_is_platform_independent():
    """回归 (v3.15.1 P0): 摘要的文件排序键必须是相对路径 POSIX 字符串 (大小写敏感),
    绝不能用平台 Path 对象排序。

    根因: Windows 的 WindowsPath 排序大小写不敏感, 把 README.md 当 readme.md 排到
    与 Linux (PosixPath, 大小写敏感) 不同的位置。摘要按文件顺序拼接哈希, 顺序不同 →
    Windows 客户机重算的 files_digest 与打包端 (POSIX) 不一致 → 装插件报
    "插件文件摘要不一致" (PLUGIN_FILES_DIGEST_FAIL)。

    本测试在 Linux 上用 PureWindowsPath 显式模拟两平台分裂, 否则 Linux 上 PosixPath
    排序恰好等于字符串序, 旧 bug 测不出来。
    """
    from pathlib import PurePosixPath, PureWindowsPath

    # 含大写文件名 (README.md) 与小写目录混排 — 这是真实插件包的典型结构
    names = [
        "README.md",
        "backend/__init__.py",
        "frontend/dist/index.esm.js",
        "frontend/i18n/zh-CN.json",
    ]

    # 反例: 直接按 Path 对象排序, 两平台顺序不同 — 证明 bug 真实存在
    by_posix_obj = [p.as_posix() for p in sorted(map(PurePosixPath, names))]
    by_win_obj = [p.as_posix() for p in sorted(map(PureWindowsPath, names))]
    assert by_posix_obj != by_win_obj, "平台 Path 排序本应分裂 (README.md 漂移)"

    # 正解: 按 as_posix() 字符串作 key, 两平台必须一致 — 这是修复保证的不变量
    key = lambda p: p.as_posix()
    by_posix_str = [p.as_posix() for p in sorted(map(PurePosixPath, names), key=key)]
    by_win_str = [p.as_posix() for p in sorted(map(PureWindowsPath, names), key=key)]
    assert by_posix_str == by_win_str == sorted(names)


def test_digest_with_uppercase_filenames_matches_zip(tmp_path):
    """回归 (v3.15.1 P0): 含大写文件名的目录, 目录算与 ZIP 算 (POSIX 字符串序基准)
    必须一致。calc_files_digest 若退回 sorted(Path 对象), 此契约在 Windows 上会破裂。
    """
    src = tmp_path / "src"
    _make_dir(src, {
        "README.md": b"# plugin",
        "backend/__init__.py": b"",
        "frontend/dist/index.esm.js": b"export{}",
        "frontend/i18n/zh-CN.json": b"{}",
    })
    d_dir = calc_files_digest(src)

    import zipfile
    zip_path = tmp_path / "p.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(src).as_posix())
    d_zip = calc_files_digest_in_zip(zip_path)
    assert d_dir == d_zip
