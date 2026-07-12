"""数据库直写适配器 (v3.35): 把事件数据直接 INSERT 进客户的关系数据库。

对接场景: 客户 IT 不开 HTTP 接口, 直接给一张中间表 (达梦/MySQL/PostgreSQL/
SQL Server/SQLite 皆可)。与 HTTP 适配器共用同一套模板体系 —— template 的
**顶层键名 = 目标表列名**, 值照常用 {key.path} 引用上下文字段。

config 示例:
{
  "db_type": "dm",                          # dm / mysql / postgresql / sqlserver / sqlite
  "host": "192.168.1.50", "db_port": 5236,
  "user": "SYSDBA", "password": "***",
  "database": "PROD",                       # dm 可留空 (走默认库), sqlite 填文件路径
  "table": "T_WEIGH_RECORD",
  "template": {
    "SN": "{sn}", "MODEL_NAME": "{model}", "OPERATOR": "{operator}",
    "NET_WEIGHT": "{item.net}", "VERDICT": "{item.verdict}",
    "CREATE_TIME": "{timestamp}",
    "_array_source": "results", "_item_template": { ...同上按行展开... }
  }
}

行展开约定 (与 HTTP 模板的 _array_source 语义一致):
- template 渲染结果是 dict         → 插 1 行
- template 顶层就是 _array_source  → 渲染结果是 list[dict] → 逐行插入 (同一事务)

驱动按 db_type 懒加载, 未安装时报错信息直接写清要装哪个包 (现场排障省一轮)。
连接不做池化: 网关本身有重试 + 事件频率低 (每周期/每件一次), 短连接最稳。
"""
import time
from typing import Any

from backend.services.mes_adapters.base import BaseAdapter

# db_type → (驱动包名, pip 安装名) 提示用
_DRIVER_HINTS = {
    "dm": ("dmPython", "dmPython (达梦官方驱动, 随 DM 客户端提供或 pip install dmPython)"),
    "mysql": ("pymysql", "pymysql"),
    "postgresql": ("psycopg2", "psycopg2-binary"),
    "sqlserver": ("pymssql", "pymssql"),
    "sqlite": ("sqlite3", "内置, 无需安装"),
}

_DEFAULT_PORTS = {"dm": 5236, "mysql": 3306, "postgresql": 5432, "sqlserver": 1433}

# 表名/列名白名单校验: 只允许字母数字下划线 (可带 schema 点分), 防注入
def _safe_ident(name: str) -> str:
    s = str(name or "").strip()
    if not s or not all(part.replace("_", "a").isalnum()
                        for part in s.split(".") if part):
        raise ValueError(f"非法表/列名: {name!r} (仅允许字母/数字/下划线, 可带 schema 前缀)")
    return s


class DatabaseAdapter(BaseAdapter):
    """通用关系库直写适配器 (达梦优先, 兼容 MySQL/PG/SQLServer/SQLite)。"""

    # ---------- 连接 ----------
    def _connect(self, config: dict):
        db_type = str(config.get("db_type") or "dm").lower()
        host = config.get("host") or "127.0.0.1"
        port = int(config.get("db_port") or _DEFAULT_PORTS.get(db_type, 0) or 0)
        user = config.get("user") or ""
        password = config.get("password") or ""
        database = config.get("database") or ""
        timeout = float(config.get("timeout") or 10)

        if db_type == "dm":
            try:
                import dmPython
            except ImportError:
                raise RuntimeError("达梦驱动未安装: 请安装 dmPython (随达梦客户端提供)")
            conn = dmPython.connect(user=user, password=password,
                                    server=host, port=port,
                                    autoCommit=False)
            return conn, "?"
        if db_type == "mysql":
            try:
                import pymysql
            except ImportError:
                raise RuntimeError("MySQL 驱动未安装: pip install pymysql")
            conn = pymysql.connect(host=host, port=port, user=user,
                                   password=password, database=database,
                                   connect_timeout=timeout, charset="utf8mb4")
            return conn, "%s"
        if db_type == "postgresql":
            try:
                import psycopg2
            except ImportError:
                raise RuntimeError("PostgreSQL 驱动未安装: pip install psycopg2-binary")
            conn = psycopg2.connect(host=host, port=port, user=user,
                                    password=password, dbname=database,
                                    connect_timeout=int(timeout))
            return conn, "%s"
        if db_type == "sqlserver":
            try:
                import pymssql
            except ImportError:
                raise RuntimeError("SQL Server 驱动未安装: pip install pymssql")
            conn = pymssql.connect(server=host, port=str(port), user=user,
                                   password=password, database=database,
                                   login_timeout=int(timeout))
            return conn, "%s"
        if db_type == "sqlite":
            import sqlite3
            conn = sqlite3.connect(database or ":memory:", timeout=timeout)
            return conn, "?"
        raise ValueError(f"未知数据库类型: {db_type}, 可用: {list(_DRIVER_HINTS.keys())}")

    # ---------- 发送 (INSERT) ----------
    def send(self, payload: Any, config: dict) -> dict:
        start = time.time()
        try:
            table = _safe_ident(config.get("table"))
            rows = payload if isinstance(payload, list) else [payload]
            rows = [r for r in rows if isinstance(r, dict) and r]
            if not rows:
                return {"status_code": 0, "body": None, "success": False,
                        "error": "模板渲染结果为空 (无可插入行), 请检查字段映射模板"}

            conn, ph = self._connect(config)
            inserted = 0
            try:
                cur = conn.cursor()
                for row in rows:
                    cols = [_safe_ident(c) for c in row.keys()]
                    sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
                           f"VALUES ({', '.join([ph] * len(cols))})")
                    cur.execute(sql, tuple(row.values()))
                    inserted += 1
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            return {
                "status_code": 200,
                "body": {"inserted": inserted, "table": table},
                "success": True,
                "duration_ms": int((time.time() - start) * 1000),
            }
        except Exception as e:
            return {
                "status_code": 0, "body": None, "success": False,
                "error": f"数据库直写失败: {e}",
                "duration_ms": int((time.time() - start) * 1000),
            }

    def check_response(self, response: dict, config: dict) -> bool:
        # 直写没有 HTTP 语义: 以 send 内部的 success 标志为准
        return bool(response.get("success"))
