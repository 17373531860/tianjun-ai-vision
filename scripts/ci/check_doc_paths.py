#!/usr/bin/env python3
"""文档路径校验（防腐三件套之一，抄 Blender check_docs_code_layout）。

扫描 docs/dev/、.claude/skills/、AGENTS.md 里引用的仓库文件路径，
校验其在源码树里真实存在。路径改名/删除后忘改文档 -> 本脚本 CI 红。

只校验"存在性"，不校验内容（控制误报率）。

用法：
    python scripts/ci/check_doc_paths.py            # 全量检查
    python scripts/ci/check_doc_paths.py --quiet    # 只输出错误
退出码：0 = 通过；1 = 有死路径。
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# 扫描范围
SCAN_TARGETS = [
    REPO / "docs" / "dev",
    REPO / ".claude" / "skills",
    REPO / "AGENTS.md",
]

# 形如 backend/xxx/yyy.py、frontend/src/...、electron/...、scripts/...、tests/...、docs/... 的路径
# 注意扩展名交替顺序：长的在前（json 在 js 前），否则 foo.json 会被截成 foo.js 误报
PATH_RE = re.compile(
    r"(?<![\w/])((?:backend|frontend/src|frontend|electron|scripts|tests|docs)"
    r"(?:/[\w.\-\u4e00-\u9fff@]+)+\.(?:py|json|jsonl|js|vue|md|iss|yml|yaml|sh|bat|feature))"
)

# 明确豁免：文档里举的"反例/假想路径"、模板占位、历史已删文件的考古引用
ALLOWLIST_SUBSTR = [
    "path/to/",
    "docs_src/",           # 引用 FastAPI 文档惯例的示例
    "0000-",               # ADR 模板占位
    "NNNN-",
    "docs/dev/_reading_notes/",  # 读码笔记为临时工作区
    "xxx",                 # 教学示例占位（add-api-endpoint 等 skill）
    "backend/data/",       # 运行时生成的数据目录（客户机存在，仓库不入库）
    "backend/patches/",    # 热补丁现场落盘目录，仓库无源
    "hotfix.py",           # 热补丁：现场落盘文件，仓库无源
    "test_X.py",           # 教学示例占位
    "m0001_scanner_new_field.py",  # modify-model skill 的未来命名示例
]

# 占位路径特征（vX.Y.Z / YYYY-MM-DD / X.X.X 这类模板）
PLACEHOLDER_RE = re.compile(r"vX\.|YYYY|X\.X|日期")

# 历史考古引用（文档刻意提及的已删除文件），按需追加
HISTORICAL = {
    "docs/产品交接手册.md",
}

# 所在行含这些标记 = 文档在刻意讲"这个文件已经没了/是反例"，不算死路径
LINE_MARKER_RE = re.compile(
    r"删|不存在|❌|~~|退役|废弃|已废|合并|老路由|反例|误传|示意|示例|改名|假设|虚构"
    r"|例如|新建|建一个|归档到"
)


def iter_md_files():
    for target in SCAN_TARGETS:
        if target.is_file():
            yield target
        elif target.is_dir():
            yield from target.rglob("*.md")


def main() -> int:
    quiet = "--quiet" in sys.argv
    dead: list[tuple[str, str]] = []
    n_refs = 0
    for md in iter_md_files():
        rel_md = md.relative_to(REPO)
        if "_reading_notes" in rel_md.parts:
            continue  # 读码笔记为工作区，允许 shorthand，不参与路径 CI
        for line in md.read_text(encoding="utf-8", errors="replace").splitlines():
            for m in PATH_RE.finditer(line):
                ref = m.group(1)
                if any(s in ref for s in ALLOWLIST_SUBSTR) or ref in HISTORICAL:
                    continue
                if PLACEHOLDER_RE.search(ref):
                    continue
                n_refs += 1
                if not (REPO / ref).exists() and not LINE_MARKER_RE.search(line):
                    dead.append((str(rel_md), ref))

    if dead:
        print(f"[check_doc_paths] ❌ {len(dead)} 个死路径（共扫描 {n_refs} 个引用）：")
        for md, ref in dead:
            print(f"  {md}: {ref}")
        print("修法：改文档指针，或该路径确属历史考古引用则加进 HISTORICAL。")
        return 1
    if not quiet:
        print(f"[check_doc_paths] ✅ {n_refs} 个路径引用全部存在。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
