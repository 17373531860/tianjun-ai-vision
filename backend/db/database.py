"""SQLAlchemy 数据库引擎与 Session 工厂。

v3.7.0 起首选 PostgreSQL（多客户部署 + 并发写入更稳）。
DSN 来源优先级：
  1. 环境变量 DATABASE_URL（推荐方式，部署时注入）
  2. settings.SQLALCHEMY_DATABASE_URI（兼容旧的 SQLite 路径，仅本机开发用）

通过 dialect 探测自动切换：
  - postgresql: 连接池 + JSONB 友好；不挂 PRAGMA
  - sqlite:    保持 WAL/busy_timeout 行为兼容旧本地数据库
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from backend.core.config import settings


def _resolve_dsn() -> str:
    env_dsn = os.environ.get("DATABASE_URL", "").strip()
    if env_dsn:
        return env_dsn
    return settings.SQLALCHEMY_DATABASE_URI


_DSN = _resolve_dsn()
_DIALECT = _DSN.split(":", 1)[0].split("+", 1)[0].lower()


def _build_engine_kwargs() -> dict:
    if _DIALECT == "sqlite":
        return {"connect_args": {"check_same_thread": False, "timeout": 15}}
    if _DIALECT == "postgresql":
        return {
            "pool_size": int(os.environ.get("DB_POOL_SIZE", "10")),
            "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "20")),
            "pool_pre_ping": True,
            "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", "1800")),
        }
    return {}


engine = create_engine(_DSN, **_build_engine_kwargs())


@event.listens_for(engine, "connect")
def _on_connect(dbapi_connection, connection_record):
    """SQLite 专用：每个新连接都开 WAL + 放宽 busy_timeout。

    PostgreSQL 不需要任何此类初始化（pool_pre_ping 已经覆盖断线探测）。
    非 SQLite 时静默跳过，避免污染 PG 日志。
    """
    if _DIALECT != "sqlite":
        return
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=15000;")
        cursor.close()
    except Exception as _e:
        print(f"[DB] PRAGMA 设置失败: {_e}", flush=True)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_dialect() -> str:
    """返回当前数据库 dialect: 'sqlite' | 'postgresql' | ..."""
    return _DIALECT
