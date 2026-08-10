# -*- coding: utf-8 -*-
"""v3.47 训练平台互连: models 表加来源与扩展元数据列.

- source: 模型来源 ('local'=本地上传 / 'yolovision'=训练平台推送), 老库存量行
  为 NULL, 读取端 NULL 视同 'local'
- meta: 扩展元数据 JSON (训练分析 x-analysis / 包 provenance / 项目对齐信息),
  SQLite 落地 TEXT, PostgreSQL 用 JSONB 分道
"""
from sqlalchemy import inspect, text

MIGRATION_ID = "m0008_model_interconnect_meta"


def apply(engine):
    insp = inspect(engine)
    if "models" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("models")}
    is_pg = engine.dialect.name == "postgresql"
    json_type = "JSONB" if is_pg else "JSON"
    with engine.connect() as conn:
        if "source" not in cols:
            conn.execute(text(
                "ALTER TABLE models ADD COLUMN source VARCHAR(50) DEFAULT 'local'"))
            print(f"[DB][迁移][{MIGRATION_ID}] models + source")
        if "meta" not in cols:
            conn.execute(text(f"ALTER TABLE models ADD COLUMN meta {json_type}"))
            print(f"[DB][迁移][{MIGRATION_ID}] models + meta")
        conn.commit()
