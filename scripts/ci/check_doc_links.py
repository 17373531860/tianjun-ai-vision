#!/usr/bin/env python3
"""文档内部死链检查（防腐三件套之二，抄 Django make check / Sphinx linkcheck）。

只检查 docs/dev/ 内 Markdown 的**相对链接**（[文本](相对路径) 与 [文本](相对路径#锚点)），
外部 http(s) 链接不查（避免 CI 依赖网络）。

用法：python scripts/ci/check_doc_links.py
退出码：0 = 通过；1 = 有死链。
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs" / "dev"

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def main() -> int:
    dead: list[tuple[str, str]] = []
    n_links = 0
    for md in DOCS.rglob("*.md"):
        text = md.read_text(encoding="utf-8", errors="replace")
        for m in LINK_RE.finditer(text):
            target = m.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            n_links += 1
            if path_part.startswith("/"):
                resolved = REPO / path_part.lstrip("/")
            else:
                resolved = (md.parent / path_part).resolve()
            if not resolved.exists():
                dead.append((str(md.relative_to(REPO)), target))

    if dead:
        print(f"[check_doc_links] ❌ {len(dead)} 个死链（共 {n_links} 个相对链接）：")
        for md, target in dead:
            print(f"  {md}: {target}")
        return 1
    print(f"[check_doc_links] ✅ {n_links} 个相对链接全部可达。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
