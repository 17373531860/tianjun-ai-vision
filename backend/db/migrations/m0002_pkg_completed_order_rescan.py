# -*- coding: utf-8 -*-
"""v3.42.1 包装结算「已完成(OK)工单重扫拦截」两列 (默认关, 存量零差异)。

- block_completed_order_rescan: 开 = 扫到最近一次运行已完成且 OK 的工单号时
  报警提示且不重新录入 (完成但 NG 的单仍允许重扫补做)
- event_completed_order_rescan: 提示走哪个「项目事件设置」事件 (留空=默认通用报警)
"""
from sqlalchemy import inspect, text

MIGRATION_ID = "m0002_pkg_completed_order_rescan"

_COLUMNS = [
    ("packaging_flow_configs", "block_completed_order_rescan", "BOOLEAN DEFAULT 0"),
    ("packaging_flow_configs", "event_completed_order_rescan", "INTEGER"),
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
                t = t.replace("BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE")
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {t}"))
            print(f"[DB][迁移][{MIGRATION_ID}] {table} + {column}")
        conn.commit()
