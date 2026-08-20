# -*- coding: utf-8 -*-
"""v3.50 扫码器生命周期 (捷昌二期): scanner_devices 加 3 列.

- resume_on: 周期结束后重新亮灯时机 ('cycle_end'=默认现状 OK/NG 都亮 /
  'ok_only'=仅 OK 自动亮, NG 灭灯等人工恢复)
- rearm_forget_last: 重新亮灯时作废未绑定旧码 + 重置物理去重缓存 (默认关)
- strict_ok_dedup: 已判 OK 的条码永久拒绝 (默认关)

三列默认值均等于现状行为, 老库升级零差异.
"""
from sqlalchemy import inspect, text

MIGRATION_ID = "m0010_scanner_lifecycle"


def apply(engine):
    insp = inspect(engine)
    if "scanner_devices" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("scanner_devices")}
    false_lit = "FALSE" if engine.dialect.name == "postgresql" else "0"
    with engine.connect() as conn:
        if "resume_on" not in cols:
            conn.execute(text(
                "ALTER TABLE scanner_devices ADD COLUMN "
                "resume_on VARCHAR(16) DEFAULT 'cycle_end'"))
            print(f"[DB][迁移][{MIGRATION_ID}] scanner_devices + resume_on")
        if "rearm_forget_last" not in cols:
            conn.execute(text(
                "ALTER TABLE scanner_devices ADD COLUMN "
                f"rearm_forget_last BOOLEAN DEFAULT {false_lit}"))
            print(f"[DB][迁移][{MIGRATION_ID}] scanner_devices + rearm_forget_last")
        if "strict_ok_dedup" not in cols:
            conn.execute(text(
                "ALTER TABLE scanner_devices ADD COLUMN "
                f"strict_ok_dedup BOOLEAN DEFAULT {false_lit}"))
            print(f"[DB][迁移][{MIGRATION_ID}] scanner_devices + strict_ok_dedup")
        conn.commit()
