"""Fleet Hub 数据库 (独立 SQLite, 与主程序 sql_app.db 无关)。

engine/SessionLocal 由 init_db() 运行期绑定 (不做模块导入期副作用,
测试可以先设 HUB_DATA_DIR 再 create_app)。
"""
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

engine = None
SessionLocal: Optional[sessionmaker] = None


def init_db(data_dir: Path) -> None:
    """建引擎 + 建表。create_app() 调用; 重复调用重绑 (测试每例独立 DB)。"""
    global engine, SessionLocal
    db_path = data_dir / "hub.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # import 模型让 Base 认识全部表再建表
    from hub.backend import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # 缺列自愈: create_all 只建新表不补列, 开发期 (未发版) 加列走这里兜底。
    # 正式发版后若再改 schema, 应换成版本化迁移。
    _ensure_columns(engine, "hub_nodes", {
        "cycle_cursor": "INTEGER",
    })


def _ensure_columns(eng, table: str, columns: dict) -> None:
    from sqlalchemy import text
    with eng.connect() as conn:
        existing = {row[1] for row in
                    conn.execute(text(f"PRAGMA table_info({table})"))}
        for name, ddl_type in columns.items():
            if name not in existing:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
        conn.commit()


def get_db():
    """FastAPI 依赖: 每请求一个 session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
