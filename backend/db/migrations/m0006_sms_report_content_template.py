# -*- coding: utf-8 -*-
"""v3.46 短信日报规则「正文模板」列.

- content_template: 内容式通道 (AT 短信猫/通用 HTTP/WxPusher) 的短信正文模板,
  ${变量名} 占位符取自规则的变量映射。云模板通道 (aliyun/tencent) 忽略。
  sms_report_rules 表未随任何已发版本出厂, 本迁移只兜底开发期已建表的库;
  全新库由 create_all 直接建全列。
"""
from sqlalchemy import inspect, text

MIGRATION_ID = "m0006_sms_report_content_template"

_COLUMNS = [
    ("sms_report_rules", "content_template", "TEXT"),
]


def apply(engine):
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.connect() as conn:
        for table, column, col_type in _COLUMNS:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if column in cols:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            print(f"[DB][迁移][{MIGRATION_ID}] {table} + {column}")
        conn.commit()
