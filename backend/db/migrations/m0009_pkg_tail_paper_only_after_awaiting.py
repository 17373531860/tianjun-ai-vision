# -*- coding: utf-8 -*-
"""v3.46 放工单只认「等收尾之后」的出现 (默认关 = 老行为零差异)。

老行为: 尾箱落账时回查步骤账本, 尾箱周期(含上一周期)里出现过放工单就算已放。
好处是允许工人封箱前先放纸, 代价是那个窗口里的一次误检会让工单当场收尾 ——
工人其实没放, 也没有任何提醒。

开了本开关: 尾箱落账一律先挂「等放工单收尾」, 历史检出不算数, 只认挂起之后
新出现的放工单动作。代价是工人必须在封箱之后再放工单。
"""
from sqlalchemy import inspect, text

# SY9 分支原编号 m0006, 合入主线时因与 m0006_sms_report_content_template 撞号重编为 m0009
MIGRATION_ID = "m0009_pkg_tail_paper_only_after_awaiting"

_COLUMNS = [
    ("packaging_flow_configs", "tail_paper_only_after_awaiting", "BOOLEAN DEFAULT 0"),
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
