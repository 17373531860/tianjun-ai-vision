"""跨数据库 SQL 表达式兼容工具。

SQLAlchemy 在不同 dialect 下的内置函数差异：
- SQLite 用 strftime('%H:%M', col) / strftime('%Y-%m-%d', col)
- PostgreSQL 用 to_char(col, 'HH24:MI') / to_char(col, 'YYYY-MM-DD')

集中提供 helper 让上层查询保持一致。
"""
from __future__ import annotations

from sqlalchemy import func

from backend.db.database import get_dialect


def hour_minute(col):
    """返回一个 'HH:MM' 字符串表达式，可用于过滤/分组。"""
    if get_dialect() == "postgresql":
        return func.to_char(col, "HH24:MI")
    return func.strftime("%H:%M", col)


def date_str(col):
    """返回 'YYYY-MM-DD' 字符串表达式（与 SQLite func.date 行为一致）。"""
    if get_dialect() == "postgresql":
        return func.to_char(col, "YYYY-MM-DD")
    return func.date(col)
