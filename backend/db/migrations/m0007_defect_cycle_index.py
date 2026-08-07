# -*- coding: utf-8 -*-
"""m0007: defect_records.cycle_id 索引（2026-08 启动提速批次）。

启动孤儿扫描 (main.py cleanup_orphan_inspections) 对 defect_records 按 cycle_id
做 NOT EXISTS 关联探查; workpiece_inspections.cycle_id 有索引而 defect_records
的一直缺失, MES 缺陷数据多的客户机上每次启动都要全表扫描。

索引名与 ORM (mes_models.DefectRecord.cycle_id index=True) 的 SQLAlchemy 默认
命名一致 (ix_defect_records_cycle_id), 新装库 create_all 与老库迁移收敛到同一形态;
CREATE INDEX IF NOT EXISTS 保证两条路径互不冲突。
"""
from __future__ import annotations

import time

from sqlalchemy import text

MIGRATION_ID = "m0007_defect_cycle_index"


def apply(engine):
    with engine.connect() as conn:
        t0 = time.time()
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_defect_records_cycle_id "
            "ON defect_records (cycle_id)"))
        conn.commit()
        print(f"[DB][迁移] 索引 ix_defect_records_cycle_id 就绪 "
              f"({(time.time() - t0) * 1000:.0f}ms)")
