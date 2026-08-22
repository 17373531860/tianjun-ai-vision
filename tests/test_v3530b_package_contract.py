"""v3.53.0b 客户补丁成品边界与 manifest 契约。"""
from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "deliverables" / "patch_v3.53.0b"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _manifest(path: Path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rel, size, digest = line.split("|")
        result[rel] = (int(size), digest.upper())
    return result


def _assert_tree(root: Path, manifest_path: Path):
    expected = _manifest(manifest_path)
    actual = {
        path.relative_to(root).as_posix(): (path.stat().st_size, _sha(path))
        for path in root.rglob("*")
        if path.is_file()
    }
    assert actual == expected


def test_payload_backend_matches_built_runtime_sources():
    assert _sha(PACKAGE / "hotfix.py") == _sha(ROOT / "backend" / "hotfix.py")
    assert _sha(PACKAGE / "manual_pass_v3530a.py") == _sha(
        ROOT / "backend" / "manual_pass_v3530a.py"
    )
    expected = _manifest(PACKAGE / "payload-backend.manifest")
    assert expected == {
        "hotfix.py": ((PACKAGE / "hotfix.py").stat().st_size, _sha(PACKAGE / "hotfix.py")),
        "manual_pass_v3530a.py": (
            (PACKAGE / "manual_pass_v3530a.py").stat().st_size,
            _sha(PACKAGE / "manual_pass_v3530a.py"),
        ),
    }


def test_payload_dist_is_exact_and_excludes_task_c():
    _assert_tree(PACKAGE / "dist", PACKAGE / "payload-dist.manifest")
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (PACKAGE / "dist").rglob("*")
        if path.is_file()
    )
    assert "PATCHED_V3530B_CUMULATIVE" in text
    assert "manual_pass_enabled" in text
    assert "合格放行" in text
    assert "regionEventRuleSteps" not in text
    assert "data-layout" not in text


def test_no_core_pyd_or_product_source_is_shipped():
    assert not list(PACKAGE.rglob("*.pyd"))
    names = {path.name for path in PACKAGE.rglob("*") if path.is_file()}
    assert "source_session_lifecycle_mixin.py" not in names
    assert "scanner.py" not in names
    assert "work_order.py" not in names


def test_batch_files_are_ascii_crlf_and_have_safe_shell_shape():
    for path in PACKAGE.glob("*.bat"):
        raw = path.read_bytes()
        assert all(byte < 128 for byte in raw)
        assert raw.count(b"\n") == raw.count(b"\r\n")
        text = raw.decode("ascii").lower()
        assert "call :main" in text
        assert "pause" in text
        assert "chcp 65001" not in text
        assert "taskkill" not in text


def test_old_a_expected_manifest_has_known_exact_shape():
    manifest = _manifest(PACKAGE / "old-a-dist.manifest")
    assert len(manifest) == 33
    assert sum(size for size, _digest in manifest.values()) == 4_615_393
    assert manifest["index.html"][1] == (
        "2032B42D6B0099E2530099C798AA34AA398C58276CF14F94522CCAD8DE7D044F"
    )
