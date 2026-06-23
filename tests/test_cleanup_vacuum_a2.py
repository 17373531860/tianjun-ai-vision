"""A2 手动数据库收缩（VACUUM）测试。

覆盖:
  1. 正常收缩: 造数据→删→VACUUM, 端点返回 success 且库文件体积下降。
  2. 互斥锁: 已有清理/收缩在执行时, 端点返回 409。
"""
import os

from fastapi.testclient import TestClient


def _db_file_size():
    from backend.core import config as _cfg
    db_path = os.path.join(_cfg.DATA_DIR, "sql_app.db")
    return os.path.getsize(db_path) if os.path.exists(db_path) else 0


def test_a2_vacuum_shrinks_after_delete(app):
    """写一批数据撑大库, 删掉后 VACUUM 应释放空间。"""
    from backend.db.database import SessionLocal, engine
    if engine.dialect.name != "sqlite":
        import pytest
        pytest.skip("非 SQLite 跳过")

    import uuid
    from datetime import datetime
    from backend.models.models import DetectionSession
    db = SessionLocal()
    try:
        # 写入足量行撑大库文件
        for i in range(2000):
            db.add(DetectionSession(
                session_uuid=uuid.uuid4().hex[:8] + f"{i:06d}",
                start_time=datetime.now(),
            ))
        db.commit()
    finally:
        db.close()

    # 落盘 WAL 让主库文件先变大
    from backend.api.sessions_maintenance import _checkpoint_wal
    _checkpoint_wal()
    size_grown = _db_file_size()

    # 删掉全部
    db = SessionLocal()
    try:
        db.query(DetectionSession).delete()
        db.commit()
    finally:
        db.close()
    _checkpoint_wal()

    # 启动时后台会跑一次自动清理并短暂持有清理锁, 等它放手再测正常收缩路径
    import time
    from backend.api import sessions_maintenance as sm
    for _ in range(100):
        if sm._cleanup_lock.acquire(blocking=False):
            sm._cleanup_lock.release()
            break
        time.sleep(0.1)

    client = TestClient(app)
    resp = client.post("/api/v1/data/cleanup/vacuum")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True

    size_after = _db_file_size()
    # VACUUM 把删除行留下的空闲页还给磁盘, 主库文件应严格变小
    assert size_after < size_grown, f"VACUUM 未收缩: before={size_grown} after={size_after}"


def test_a2_vacuum_blocked_when_cleanup_busy(app):
    """清理锁被占用时, 收缩端点返回 409 而非强占。"""
    from backend.db.database import engine
    if engine.dialect.name != "sqlite":
        import pytest
        pytest.skip("非 SQLite 跳过")

    from backend.api import sessions_maintenance as sm
    client = TestClient(app)

    assert sm._cleanup_lock.acquire(blocking=False) is True
    try:
        resp = client.post("/api/v1/data/cleanup/vacuum")
        assert resp.status_code == 409
    finally:
        sm._cleanup_lock.release()
