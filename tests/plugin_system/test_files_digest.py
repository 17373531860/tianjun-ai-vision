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
