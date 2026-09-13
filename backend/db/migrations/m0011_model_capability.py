# -*- coding: utf-8 -*-
"""2026-09 内置能力模型入仓: models 表加能力类型与内置行标记.

- capability: 能力类型 ('detect'/'segment'/'pose'/'headpose'/'ocr'/'anomaly'/'vlm'),
  老库存量行为 NULL, 读取端 NULL 视同 'detect'
- builtin: 出厂内置行标记 (禁删; 启动 seed 幂等维护), 老库存量行 NULL 视同 False

RFC: docs/rfc/内置能力模型入仓与能力选用体系_RFC.md
"""
from sqlalchemy import inspect, text

MIGRATION_ID = "m0011_model_capability"


def apply(engine):
    insp = inspect(engine)
    if "models" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("models")}
    is_pg = engine.dialect.name == "postgresql"
    bool_default = "FALSE" if is_pg else "0"
    with engine.connect() as conn:
        if "capability" not in cols:
            conn.execute(text(
                "ALTER TABLE models ADD COLUMN capability VARCHAR(50) DEFAULT 'detect'"))
            print(f"[DB][迁移][{MIGRATION_ID}] models + capability")
        if "builtin" not in cols:
            conn.execute(text(
                f"ALTER TABLE models ADD COLUMN builtin BOOLEAN DEFAULT {bool_default}"))
            print(f"[DB][迁移][{MIGRATION_ID}] models + builtin")
        conn.commit()
