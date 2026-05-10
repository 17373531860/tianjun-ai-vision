#!/usr/bin/env python3
"""插件系统文档轻量 lint。

当前检查：
- Markdown 本地链接是否存在
- JSON 文件是否可解析
- plugin examples 的 plugin.json 是否符合 schema
- 文档中不应再出现已撤销的 INCONSIST-2 待修措辞
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
ROOT = THIS_DIR.parents[1]
DOC_ROOT = ROOT / "docs" / "plugin-system"
EXAMPLES_ROOT = ROOT / "plugins-examples"
sys.path.insert(0, str(THIS_DIR))

from _plugin_common import ManifestValidationError, validate_manifest


LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def iter_markdown_files():
    yield from DOC_ROOT.rglob("*.md")
    if EXAMPLES_ROOT.exists():
        yield from EXAMPLES_ROOT.rglob("*.md")


def check_markdown_links() -> list[str]:
    errors: list[str] = []
    for md in iter_markdown_files():
        text = md.read_text(encoding="utf-8")
        for match in LINK_RE.finditer(text):
            # Avoid false positives from code like ElMessage[type](message).
            if match.start() > 0 and text[match.start() - 1] in "._abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
                continue
            target = match.group(1).split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            path = (md.parent / target).resolve()
            if not path.exists():
                errors.append(f"{md}: broken link -> {target}")
    return errors


def check_json_files() -> list[str]:
    errors: list[str] = []
    for path in list(DOC_ROOT.rglob("*.json")) + list(EXAMPLES_ROOT.rglob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path}: invalid json: {exc}")
    return errors


def check_example_manifests() -> list[str]:
    errors: list[str] = []
    if not EXAMPLES_ROOT.exists():
        return errors
    for manifest_path in sorted(EXAMPLES_ROOT.glob("*/plugin.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            validate_manifest(manifest)
        except (json.JSONDecodeError, ManifestValidationError) as exc:
            errors.append(f"{manifest_path}: manifest invalid: {exc}")
    return errors


def check_voided_inconsist2() -> list[str]:
    errors: list[str] = []
    forbidden = [
        "修 INCONSIST-2 +",
        "mes_gateway 路径不一致 | inventory 05 INCONSIST-2 | [确认]",
        "这是潜在不一致",
    ]
    for md in iter_markdown_files():
        text = md.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                errors.append(f"{md}: stale INCONSIST-2 wording: {needle}")
    return errors


def main() -> int:
    errors: list[str] = []
    errors.extend(check_markdown_links())
    errors.extend(check_json_files())
    errors.extend(check_example_manifests())
    errors.extend(check_voided_inconsist2())

    if errors:
        print("[FAIL] plugin docs lint failed")
        for e in errors:
            print("  -", e)
        return 1

    print("[OK] plugin docs lint passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
