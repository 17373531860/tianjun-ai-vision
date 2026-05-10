#!/usr/bin/env python3
"""SQLite → PostgreSQL 数据搬迁。

用法：
    # 把 backend/sql_app.db 搬到环境变量 DATABASE_URL 指向的 PG
    python scripts/db/sqlite_to_pg.py --sqlite backend/sql_app.db

    # 或显式指定 PG DSN
    python scripts/db/sqlite_to_pg.py \\
        --sqlite backend/sql_app.db \\
        --pg postgresql+psycopg2://tianjun:pwd@127.0.0.1:5433/tianjun

流程：
    1. 校验 PG 端 schema 已通过 alembic upgrade head 建好（脚本会 abort 如果没有 alembic_version 表）
    2. 按 ORM Base.metadata.sorted_tables 顺序逐表搬：先父表后子表，避免 FK 失败
    3. 搬完后 reset 序列（PG 的 SERIAL/IDENTITY）让下一次 insert id 接续
    4. 全程事务化：失败 rollback，成功 commit

设计取舍：
    - 一次性把整张表读进内存再 insert（Tianjun 数据量级 MB 级别，安全）
    - 不做字段类型转换：依赖 SQLAlchemy 的 Core 自动适配
    - 不做 schema 检查/diff：相信 alembic 已经把 schema 对齐
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 让 backend.* 可导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from backend.db.database import Base
from backend.models import models  # noqa: F401
from backend.models import export_models  # noqa: F401
from backend.models import mes_models  # noqa: F401
from backend.models import plugin_models  # noqa: F401


def _abort(msg: str) -> None:
    print(f"[迁移][ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def _check_pg_ready(pg_engine) -> None:
    insp = inspect(pg_engine)
    if "alembic_version" not in insp.get_table_names():
        _abort(
            "PG 端没有 alembic_version 表。请先跑：\n"
            "    DATABASE_URL=postgresql+psycopg2://... alembic upgrade head"
        )


def _reset_sequences(pg_engine) -> None:
    """把 SERIAL 序列推到当前 max(id) + 1，避免后续 INSERT 主键冲突。"""
    with pg_engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT s.relname AS sequence_name,
                   t.relname AS table_name,
                   a.attname AS column_name
            FROM pg_class s
            JOIN pg_depend d ON d.objid = s.oid
            JOIN pg_class t ON d.refobjid = t.oid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
            WHERE s.relkind = 'S'
            """
        )).fetchall()
        for seq_name, tbl, col in rows:
            try:
                max_id = conn.execute(text(f'SELECT COALESCE(MAX("{col}"), 0) FROM "{tbl}"')).scalar()
                conn.execute(text(f'SELECT setval(\'{seq_name}\', {max_id + 1}, false)'))
            except Exception as e:
                print(f"[迁移][WARN] 重置序列 {seq_name} 失败: {e}", file=sys.stderr)
        conn.commit()


def migrate(sqlite_path: str, pg_dsn: str, truncate_first: bool, dry_run: bool) -> None:
    if not os.path.isfile(sqlite_path):
        _abort(f"SQLite 文件不存在: {sqlite_path}")
    if not pg_dsn.startswith("postgresql"):
        _abort(f"PG DSN 必须以 postgresql 开头: {pg_dsn}")

    sqlite_engine = create_engine(f"sqlite:///{os.path.abspath(sqlite_path)}", connect_args={"check_same_thread": False})
    pg_engine = create_engine(pg_dsn)

    _check_pg_ready(pg_engine)

    # 按依赖顺序：父表先，子表后
    tables = list(Base.metadata.sorted_tables)
    insp_src = inspect(sqlite_engine)
    src_tables = set(insp_src.get_table_names())

    print(f"[迁移] SQLite: {sqlite_path}")
    print(f"[迁移] PG:     {pg_dsn.split('@')[-1]}")
    print(f"[迁移] 待搬迁的表: {len(tables)}（按 FK 依赖排序）")
    if dry_run:
        print("[迁移][DRY-RUN] 只统计行数，不写入 PG\n")

    total_rows = 0
    skipped: list[str] = []
    with Session(pg_engine) as pg_sess:
        # 子→父反向 truncate
        if truncate_first and not dry_run:
            for tbl in reversed(tables):
                try:
                    pg_sess.execute(text(f'TRUNCATE TABLE "{tbl.name}" RESTART IDENTITY CASCADE'))
                except Exception as e:
                    print(f"[迁移][WARN] truncate {tbl.name} 失败（可能不存在）: {e}", file=sys.stderr)
            pg_sess.commit()

        with sqlite_engine.connect() as src:
            for tbl in tables:
                if tbl.name not in src_tables:
                    skipped.append(tbl.name)
                    continue
                rows = src.execute(text(f'SELECT * FROM "{tbl.name}"')).mappings().all()
                if not rows:
                    print(f"  - {tbl.name:<32} : 0 行（跳过）")
                    continue
                if dry_run:
                    print(f"  - {tbl.name:<32} : {len(rows):>6} 行（DRY-RUN）")
                    total_rows += len(rows)
                    continue
                try:
                    pg_sess.execute(tbl.insert(), [dict(r) for r in rows])
                    pg_sess.commit()
                    print(f"  + {tbl.name:<32} : {len(rows):>6} 行  ✓")
                    total_rows += len(rows)
                except Exception as e:
                    pg_sess.rollback()
                    print(f"  ! {tbl.name:<32} : 失败 → {e}", file=sys.stderr)
                    skipped.append(f"{tbl.name} ({type(e).__name__})")

    if not dry_run:
        _reset_sequences(pg_engine)
        print("\n[迁移] 序列已重置")

    print(f"\n[迁移] 完成。总计 {total_rows} 行")
    if skipped:
        print(f"[迁移] 跳过/失败: {len(skipped)}")
        for s in skipped:
            print(f"    - {s}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 数据搬迁")
    parser.add_argument("--sqlite", required=True, help="源 SQLite 文件路径")
    parser.add_argument("--pg", default=os.environ.get("DATABASE_URL", ""),
                        help="目标 PG DSN（默认读 DATABASE_URL）")
    parser.add_argument("--truncate-first", action="store_true",
                        help="搬迁前先 TRUNCATE 所有目标表（用于重跑/覆盖）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计行数，不写 PG")
    args = parser.parse_args()

    if not args.pg:
        _abort("--pg 或 DATABASE_URL 必须提供 PG DSN")

    migrate(args.sqlite, args.pg, args.truncate_first, args.dry_run)


if __name__ == "__main__":
    main()
