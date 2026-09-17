# -*- coding: utf-8 -*-
"""一拖多局域网工位屏: users 表加「可操作工位」白名单列.

- allowed_channels: JSON 数组, 例如 [0] = 该账号只能操作 0 号工位。
  NULL / [] = 不限工位, 与 v3.56 及以前完全一致 (存量账号零差异)。

场景: 每个工位一台一体机, 用浏览器开本工位 kiosk 页。工位账号只该启停自己那
一路, 不该能对着 ?channel=3 把隔壁工位停了。

合入 dev-qing 时原编号 m0011 与 m0011_model_capability 撞号, 重编号为 m0012。
apply 幂等: inspector 探查缺列才 ALTER。
"""
from sqlalchemy import inspect, text

MIGRATION_ID = "m0012_user_allowed_channels"


def apply(engine):
    insp = inspect(engine)
    if "users" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "allowed_channels" in cols:
        return
    json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
    with engine.connect() as conn:
        conn.execute(text(
            f"ALTER TABLE users ADD COLUMN allowed_channels {json_type}"))
        conn.commit()
    print(f"[DB][迁移][{MIGRATION_ID}] users + allowed_channels")
