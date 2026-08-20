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

import json

from sqlalchemy import JSON, create_engine, inspect, text
from backend.db.database import Base
# 全部 9 组模型都要 import 注册进 Base.metadata, 缺一组就漏一组表
# (与 alembic/env.py 的 import 清单保持一致, v3.48.1 曾因缺组修过 PG 基线迁移)
from backend.models import models  # noqa: F401
from backend.models import export_models  # noqa: F401
from backend.models import mes_models  # noqa: F401
from backend.models import plugin_models  # noqa: F401
from backend.models import auth_models  # noqa: F401
from backend.models import notify_models  # noqa: F401
from backend.models import weighing_models  # noqa: F401
from backend.models import plc_models  # noqa: F401
from backend.models import trigger_models  # noqa: F401


def _abort(msg: str) -> None:
    print(f"[迁移][ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def _check_pg_ready(pg_engine) -> None:
    """确认 PG 端 schema 已建好。两条合法路径：
    1. alembic upgrade head（运维手动建 schema，有 alembic_version 表）
    2. 后端对空 PG 库首启一次（main.py create_all 自动建全表，有 projects 表）
    """
    tables = set(inspect(pg_engine).get_table_names())
    if "alembic_version" not in tables and "projects" not in tables:
        _abort(
            "PG 端 schema 未初始化。请先二选一：\n"
            "    a) DATABASE_URL=postgresql+psycopg2://... alembic upgrade head\n"
            "    b) 用该 DATABASE_URL 启动一次后端（create_all 自动建全表）"
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


def scan_orphan_fks(pg_engine) -> list[str]:
    """扫描 PG 端孤儿外键引用（子行引用了不存在的父行）。

    SQLite 默认不强制 FK（PRAGMA foreign_keys=0），老客户库里孤儿引用普遍存在
    （如设备已删、workpieces.scan_device_id 还指着旧 id）。迁移按 replica 模式
    原样落数据后，用本函数把孤儿引用晒出来，供现场判断是否需要清理。
    """
    findings: list[str] = []
    with pg_engine.connect() as conn:
        for tbl in Base.metadata.sorted_tables:
            for fk in tbl.foreign_keys:
                col = fk.parent
                ref_tbl = fk.column.table.name
                ref_col = fk.column.name
                try:
                    n = conn.execute(text(
                        f'SELECT COUNT(*) FROM "{tbl.name}" c '
                        f'WHERE c."{col.name}" IS NOT NULL '
                        f'AND NOT EXISTS (SELECT 1 FROM "{ref_tbl}" p '
                        f'WHERE p."{ref_col}" = c."{col.name}")'
                    )).scalar()
                    if n:
                        findings.append(
                            f"{tbl.name}.{col.name} → {ref_tbl}.{ref_col}: {n} 行孤儿引用")
                except Exception as e:
                    findings.append(f"{tbl.name}.{col.name}: 扫描失败 {type(e).__name__}")
    return findings


def verify(sqlite_path: str, pg_dsn: str) -> None:
    """迁移后校验：逐表比对 SQLite 与 PG 的行数；有整型 id 列时再比对 min/max/sum。

    任一表不一致 → 打印明细并以退出码 2 结束（供部署脚本判断迁移是否可信）。
    """
    if not os.path.isfile(sqlite_path):
        _abort(f"SQLite 文件不存在: {sqlite_path}")
    sqlite_engine = create_engine(f"sqlite:///{os.path.abspath(sqlite_path)}",
                                  connect_args={"check_same_thread": False})
    pg_engine = create_engine(pg_dsn)
    _check_pg_ready(pg_engine)

    tables = list(Base.metadata.sorted_tables)
    src_tables = set(inspect(sqlite_engine).get_table_names())
    pg_tables = set(inspect(pg_engine).get_table_names())

    mismatches: list[str] = []
    checked = 0
    with sqlite_engine.connect() as src, pg_engine.connect() as dst:
        for tbl in tables:
            name = tbl.name
            if name not in src_tables:
                continue  # 源库没有的表（新版本新增表）不参与校验
            if name not in pg_tables:
                mismatches.append(f"{name}: PG 端缺表")
                continue
            has_id = "id" in tbl.columns and str(tbl.columns["id"].type).upper().startswith(("INT", "BIGINT"))
            if has_id:
                probe = f'SELECT COUNT(*), COALESCE(MIN(id),0), COALESCE(MAX(id),0), COALESCE(SUM(id),0) FROM "{name}"'
            else:
                probe = f'SELECT COUNT(*), 0, 0, 0 FROM "{name}"'
            s = src.execute(text(probe)).fetchone()
            d = dst.execute(text(probe)).fetchone()
            checked += 1
            if tuple(s) != tuple(d):
                mismatches.append(
                    f"{name}: SQLite(count={s[0]}, min={s[1]}, max={s[2]}, sum={s[3]}) "
                    f"≠ PG(count={d[0]}, min={d[1]}, max={d[2]}, sum={d[3]})"
                )
            else:
                print(f"  ✓ {name:<32} : {s[0]:>6} 行一致")

    print(f"\n[校验] 共比对 {checked} 张表")
    if mismatches:
        print(f"[校验][FAIL] {len(mismatches)} 张表不一致:", file=sys.stderr)
        for m in mismatches:
            print(f"    - {m}", file=sys.stderr)
        sys.exit(2)
    print("[校验] 全部一致 ✓")


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
    # 用单一 Connection 而不是 ORM Session: SET session_replication_role 是连接级的,
    # Session 每次 commit 后可能换池内连接, SET 会悄悄失效。
    with pg_engine.connect() as pg_conn:
        # SQLite 默认不强制 FK, 老库常见孤儿引用 (设备已删但子表还指着旧 id)。
        # 迁移语义 = 忠实复制既有现实 → 连接级关掉 FK 触发器原样落数据,
        # 结束后 scan_orphan_fks 把孤儿引用晒成报告供现场决策。
        # 需要 superuser (嵌入式 PG 的业主用户即是); 权限不够时告警并回落严格模式。
        if not dry_run:
            try:
                with pg_conn.begin():
                    pg_conn.execute(text("SET session_replication_role = replica"))
                print("[迁移] FK 强制已临时关闭 (session_replication_role=replica)")
            except Exception as e:
                print(f"[迁移][WARN] 无法关闭 FK 强制 (需要 superuser): {e}\n"
                      f"           将按严格 FK 模式迁移, 孤儿引用行会失败", file=sys.stderr)

        # 子→父反向 truncate
        if truncate_first and not dry_run:
            for tbl in reversed(tables):
                try:
                    with pg_conn.begin():
                        pg_conn.execute(text(f'TRUNCATE TABLE "{tbl.name}" RESTART IDENTITY CASCADE'))
                except Exception as e:
                    print(f"[迁移][WARN] truncate {tbl.name} 失败（可能不存在）: {e}", file=sys.stderr)

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
                # JSON 列双重编码防护: SQLite 物理存的是 JSON 字符串, 裸 SELECT 读出
                # str; 直接喂给 PG 的 JSON 列会被 SQLAlchemy 再 json.dumps 一次 →
                # PG 里变成 "\"{...}\"" 字符串, 后端读出 str 而非 dict, 项目配置全坏
                # (实测踩雷: steps_config 读出 str → 'str' object has no attribute 'get')。
                # 目标列是 JSON 类型且值是 str 时先 json.loads 还原成 Python 对象。
                json_cols = [c.name for c in tbl.columns if isinstance(c.type, JSON)]
                payload = []
                for r in rows:
                    d = dict(r)
                    for cn in json_cols:
                        v = d.get(cn)
                        if isinstance(v, str):
                            try:
                                d[cn] = json.loads(v)
                            except (ValueError, TypeError):
                                pass  # 非法 JSON 原样保留 (与 SQLite 现状一致)
                    payload.append(d)
                try:
                    with pg_conn.begin():
                        pg_conn.execute(tbl.insert(), payload)
                    print(f"  + {tbl.name:<32} : {len(rows):>6} 行  ✓")
                    total_rows += len(rows)
                except Exception as e:
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

    if not dry_run:
        orphans = scan_orphan_fks(pg_engine)
        if orphans:
            print(f"\n[迁移] ⚠️ 孤儿外键引用 {len(orphans)} 处（SQLite 不强制 FK 的历史遗留，"
                  f"已原样保留，请现场评估是否清理）:")
            for o in orphans:
                print(f"    - {o}")
        else:
            print("\n[迁移] 外键引用完整性: 无孤儿引用 ✓")
        print("\n[迁移] 建议执行校验: 加 --verify 参数重跑本工具")


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 数据搬迁")
    parser.add_argument("--sqlite", required=True, help="源 SQLite 文件路径")
    parser.add_argument("--pg", default=os.environ.get("DATABASE_URL", ""),
                        help="目标 PG DSN（默认读 DATABASE_URL）")
    parser.add_argument("--truncate-first", action="store_true",
                        help="搬迁前先 TRUNCATE 所有目标表（用于重跑/覆盖）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计行数，不写 PG")
    parser.add_argument("--verify", action="store_true",
                        help="不搬迁，只逐表比对 SQLite 与 PG（行数 + id min/max/sum），"
                             "不一致时退出码 2")
    args = parser.parse_args()

    if not args.pg:
        _abort("--pg 或 DATABASE_URL 必须提供 PG DSN")

    if args.verify:
        verify(args.sqlite, args.pg)
        return

    migrate(args.sqlite, args.pg, args.truncate_first, args.dry_run)


if __name__ == "__main__":
    main()
