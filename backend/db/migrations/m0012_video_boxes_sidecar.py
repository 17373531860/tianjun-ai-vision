# -*- coding: utf-8 -*-
"""2026-09 录像检测框 sidecar 与带框版投递:

- data_export_settings.record_boxes_data: 周期录像旁成对记录帧号对齐的
  检测框 JSON (回放叠加 / 带框渲染导出用), 默认关零差异
- video_archive_rules.annotated_video: 归档规则「投递带框版」开关,
  归档 worker 渲染烧框 MP4 后交付 (失败降级原片), 默认关零差异
"""
from sqlalchemy import inspect, text

MIGRATION_ID = "m0012_video_boxes_sidecar"


def apply(engine):
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    is_pg = engine.dialect.name == "postgresql"
    bool_default = "FALSE" if is_pg else "0"
    with engine.connect() as conn:
        if "data_export_settings" in tables:
            cols = {c["name"] for c in insp.get_columns("data_export_settings")}
            if "record_boxes_data" not in cols:
                conn.execute(text(
                    f"ALTER TABLE data_export_settings ADD COLUMN "
                    f"record_boxes_data BOOLEAN DEFAULT {bool_default}"))
                print(f"[DB][迁移][{MIGRATION_ID}] data_export_settings + record_boxes_data")
        if "video_archive_rules" in tables:
            cols = {c["name"] for c in insp.get_columns("video_archive_rules")}
            if "annotated_video" not in cols:
                conn.execute(text(
                    f"ALTER TABLE video_archive_rules ADD COLUMN "
                    f"annotated_video BOOLEAN DEFAULT {bool_default}"))
                print(f"[DB][迁移][{MIGRATION_ID}] video_archive_rules + annotated_video")
        conn.commit()
