"""跨数据库 SQL 表达式兼容工具。

SQLAlchemy 在不同 dialect 下的内置函数差异：
- SQLite 用 strftime('%H:%M', col) / strftime('%Y-%m-%d', col)
- PostgreSQL 用 to_char(col, 'HH24:MI') / to_char(col, 'YYYY-MM-DD')
- SQLite 把 boolean 当 1/0 隐式累加, PostgreSQL 严格不允许 SUM(boolean)

集中提供 helper 让上层查询保持一致。
"""
from __future__ import annotations

from sqlalchemy import Integer, cast, func

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


def sum_bool(expr):
    """SUM(BooleanColumn / boolean expression) — 跨 dialect 兼容版本。

    SQLite 允许 ``SUM(boolean_col)``, 把 True/False 隐式当 1/0 累加;
    PostgreSQL 严格不允许 (报错 ``function sum(boolean) does not exist``).
    统一改成 ``SUM(CAST(expr AS INTEGER))``, SQLite/PG 都能稳定工作.

    Args:
        expr: BooleanColumn 或 ``col == True`` 这种 BinaryExpression 都可以.

    Example:
        ``sum_bool(DetectionCycle.is_good == True)`` 替代
        ``func.sum(DetectionCycle.is_good == True)``.
    """
    return func.sum(cast(expr, Integer))
