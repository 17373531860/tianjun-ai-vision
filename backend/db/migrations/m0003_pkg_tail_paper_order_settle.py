# -*- coding: utf-8 -*-
"""v3.43 包装结算「放工单=工单收尾」两个可配报警出口 (仅 as_close_action 模式生效)。

- tail_paper_scan_alarm: 扫新单报警 (默认开) — 下一工单扫码进来发现上一单没放工单
  → 报警 + 上一单 NG 收尾
- tail_paper_timeout_s: 时限报警 (默认 0=不限) — 尾箱落账到放工单动作之间超过 N 秒
  仍没等到 → 报一次警 (只提醒不收尾)
"""
from sqlalchemy import inspect, text

MIGRATION_ID = "m0003_pkg_tail_paper_order_settle"

_COLUMNS = [
    ("packaging_flow_configs", "tail_paper_scan_alarm", "BOOLEAN DEFAULT 1"),
    ("packaging_flow_configs", "tail_paper_timeout_s", "INTEGER DEFAULT 0"),
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
