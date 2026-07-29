# -*- coding: utf-8 -*-
"""v3.45 包装结算「箱标签扫码授权 + 标签取本箱数量」配置列 (组⑧, 全可选默认关).

- box_label_scan_required: 每箱开做前必须扫箱标签 (等扫箱标签态, 未扫开做报警)
- label_qty_enabled/segment/pattern: 从标签复合串取"本箱数量"当本箱滑块目标 (逐箱可变)
- label_rescan_action: 已授权后重扫同号标签的处置 (ignore/update)
- unauthorized_cycle_action: 未授权箱做完周期的处置 (hold=挂起等人工 / book=照常落账)
- label_total_check: 工单收尾 Σ各箱标签数量 vs 排产量对账报警
- event_box_not_scanned / event_label_qty_missing / event_label_total_mismatch: 三个可配报警事件
"""
from sqlalchemy import inspect, text

MIGRATION_ID = "m0004_pkg_box_label_scan"

_COLUMNS = [
    ("packaging_flow_configs", "box_label_scan_required", "BOOLEAN DEFAULT 0"),
    ("packaging_flow_configs", "label_qty_enabled", "BOOLEAN DEFAULT 0"),
    ("packaging_flow_configs", "label_qty_segment", "INTEGER DEFAULT 3"),
    ("packaging_flow_configs", "label_qty_pattern", "VARCHAR(128)"),
    ("packaging_flow_configs", "label_rescan_action", "VARCHAR(8) DEFAULT 'ignore'"),
    ("packaging_flow_configs", "unauthorized_cycle_action", "VARCHAR(8) DEFAULT 'hold'"),
    ("packaging_flow_configs", "label_total_check", "BOOLEAN DEFAULT 0"),
    ("packaging_flow_configs", "event_box_not_scanned", "INTEGER"),
    ("packaging_flow_configs", "event_label_qty_missing", "INTEGER"),
    ("packaging_flow_configs", "event_label_total_mismatch", "INTEGER"),
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
