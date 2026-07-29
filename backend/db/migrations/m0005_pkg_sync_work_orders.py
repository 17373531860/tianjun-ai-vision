# -*- coding: utf-8 -*-
"""v3.45 包装结算「工单同步进工单管理」开关列 (默认开).

- sync_work_orders: 扫码开工/收尾/中止时把包装工单镜像到 work_orders 表
  (来源=packaging), 工单管理页可见可管理。默认开 — 只补数据可见性,
  不改任何判定行为; 关 = 老行为 (包装单只存运行记录)。
"""
from sqlalchemy import inspect, text

MIGRATION_ID = "m0005_pkg_sync_work_orders"

_COLUMNS = [
    ("packaging_flow_configs", "sync_work_orders", "BOOLEAN DEFAULT 1"),
]


def apply(engine):
    from backend.db.database import get_dialect
    dialect = get_dialect()
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.connect() as conn:
        for table, column, col_type in _COLUMNS:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if column in cols:
                continue
            t = col_type
            if dialect == "postgresql":
                t = t.replace("BOOLEAN DEFAULT 1", "BOOLEAN DEFAULT TRUE")
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {t}"))
            print(f"[DB][迁移][{MIGRATION_ID}] {table} + {column}")
        conn.commit()
