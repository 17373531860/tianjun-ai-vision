#!/usr/bin/env python3
"""从 SQLAlchemy ORM 生成数据库表参考文档（P3 生成管线之一）。

单一事实源 = ORM 模型（Base.metadata）。手写表清单必然漂移（历史上"37 张表"
写错过多处），本脚本让 docs/dev/reference/db-schema.md 永远与代码一致。

用法：
    python scripts/docgen/gen_db_schema.py          # 生成/覆盖 docs/dev/reference/db-schema.md
    python scripts/docgen/gen_db_schema.py --check  # 只校验现有文档是否过期（CI 用）
退出码：0 = 成功/一致；1 = --check 时发现文档过期。
"""

import os
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "dev" / "reference" / "db-schema.md"

sys.path.insert(0, str(REPO))
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
os.environ.setdefault("BACKEND_SKIP_INIT", "1")


def build_markdown() -> str:
    # 导入全部模型模块，确保表都注册进 metadata
    import backend.models  # noqa: F401
    from backend.db.database import Base

    # 有些模型文件不在 backend.models 包的 __init__ 里，逐个显式拉起
    import importlib
    import pkgutil

    import backend.models as models_pkg

    for m in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"backend.models.{m.name}")

    tables = sorted(Base.metadata.tables.items())

    # 反查 ORM 类名与源文件
    class_by_table = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        tname = getattr(cls, "__tablename__", None)
        if tname:
            mod = sys.modules.get(cls.__module__)
            src = getattr(mod, "__file__", "?")
            rel = str(Path(src).relative_to(REPO)) if src and str(src).startswith(str(REPO)) else str(src)
            class_by_table[tname] = (cls.__name__, rel)

    lines = [
        "# 数据库表参考",
        "",
        "> **类型**：reference（生成物勿手改）",
        f"> **生成命令**：`python scripts/docgen/gen_db_schema.py`（生成日 {date.today().isoformat()}）",
        "> **单一事实源**：SQLAlchemy ORM（Base.metadata）。字段含义看模型源文件行内注释；",
        "> 迁移历史看 backend/db/migrations/ 与 backend/main.py 的 migrate_database。",
        "",
        f"共 **{len(tables)}** 张表。",
        "",
        "## 表索引",
        "",
        "| 表名 | ORM 类 | 定义文件 |",
        "|---|---|---|",
    ]
    for tname, _ in tables:
        cls_name, src = class_by_table.get(tname, ("?", "?"))
        lines.append(f"| [`{tname}`](#{tname}) | `{cls_name}` | `{src}` |")
    lines.append("")

    for tname, table in tables:
        cls_name, src = class_by_table.get(tname, ("?", "?"))
        lines += [f"## {tname}", "", f"ORM 类 `{cls_name}`，定义于 `{src}`。", ""]
        lines += ["| 字段 | 类型 | 约束 | 默认 |", "|---|---|---|---|"]
        for col in table.columns:
            constraints = []
            if col.primary_key:
                constraints.append("PK")
            for fk in col.foreign_keys:
                constraints.append(f"FK→{fk.column.table.name}.{fk.column.name}")
            if col.unique:
                constraints.append("UNIQUE")
            if col.index:
                constraints.append("INDEX")
            if not col.nullable and not col.primary_key:
                constraints.append("NOT NULL")
            default = ""
            if col.default is not None and getattr(col.default, "arg", None) is not None:
                arg = col.default.arg
                default = arg.__name__ + "()" if callable(arg) else repr(arg)
            elif col.server_default is not None:
                default = "server"
            lines.append(
                f"| `{col.name}` | {col.type} | {' '.join(constraints)} | {default} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    md = build_markdown()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        # 忽略生成日期行再比对
        strip = lambda s: "\n".join(  # noqa: E731
            l for l in s.splitlines() if not l.startswith("> **生成命令**")
        )
        if strip(current) != strip(md):
            print(f"[gen_db_schema] ❌ {OUT.relative_to(REPO)} 已过期，请重新生成。")
            return 1
        print("[gen_db_schema] ✅ 文档与 ORM 一致。")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(md, encoding="utf-8")
    print(f"[gen_db_schema] 已生成 {OUT.relative_to(REPO)}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
