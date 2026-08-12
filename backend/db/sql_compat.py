"""跨数据库 SQL 表达式兼容工具。

SQLAlchemy 在不同 dialect 下的内置函数差异：
- SQLite 用 strftime('%H:%M', col) / strftime('%Y-%m-%d', col)
- PostgreSQL 用 to_char(col, 'HH24:MI') / to_char(col, 'YYYY-MM-DD')

集中提供 helper 让上层查询保持一致。

已确认**无需**收编的写法（双方言天然兼容，直接用即可）：
- ``func.date(col)``：PG 有函数式 cast ``date(timestamp)``，与字符串比较时
  PG 会把 'YYYY-MM-DD' 字面量推断为 date；SQLite 返回同格式字符串。
- ``case((expr, 1), else_=0)`` 折 0/1 后再 SUM（reports.py 的 good_int 写法）。

必须收编的写法：
- ``func.sum(BooleanCol == True)``：PG 没有 SUM(boolean)，直接报错 → 用 sum_bool。
- ``func.json_extract(col, '$.a.b')``：SQLite 专属 → 用 SQLAlchemy JSON 列的
  原生 path 索引 ``col["a"]["b"].as_string()``（SQLite 编译为 JSON_EXTRACT，
  PG 编译为 #>>，两边缺 key 都返回 SQL NULL）。
"""
from __future__ import annotations

from sqlalchemy import case, func

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
    """SUM 一个布尔表达式，返回命中行数。

    SQLite 布尔即整数、SUM(bool) 能跑；PG 没有 SUM(boolean) 会直接报错。
    统一折成 CASE WHEN expr THEN 1 ELSE 0 END 再 SUM，双方言一致。
    """
    return func.sum(case((expr, 1), else_=0))
