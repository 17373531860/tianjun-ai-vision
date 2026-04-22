from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from backend.core.config import settings

engine = create_engine(
    settings.SQLALCHEMY_DATABASE_URI,
    connect_args={"check_same_thread": False, "timeout": 15},
)


@event.listens_for(engine, "connect")
def _sqlite_on_connect(dbapi_connection, connection_record):
    """每个新连接都开 WAL + 放宽 busy_timeout。

    原因：默认 journal_mode=delete 下写操作独占整库锁，timeout_checker
    后台线程 + cycle_end 主线程 + 偶尔的命令行 sqlite3 并发时，
    集群 receive_station_report 的 INSERT 会立即抛 database is locked，
    导致主机本地数据入不了 box_aggregations、集群汇总永远空。
    WAL 模式允许多读+单写并发，busy_timeout=15s 兜底等待锁释放。
    """
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=15000;")
        cursor.close()
    except Exception:
        pass


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
