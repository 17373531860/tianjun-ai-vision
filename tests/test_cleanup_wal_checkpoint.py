"""
A3 回归测试: 删行后收缩 WAL 文件。

客户实锤(2026-06): 删了数据但磁盘没变小。根因之一: 项目开了 SQLite WAL 模式,
但从不主动 checkpoint(TRUNCATE), -wal 文件只涨不收。

本测试锁定: 写入产生 WAL 后, 调 _checkpoint_wal() 能把 -wal 文件截断(TRUNCATE)。

先红后绿: 把 _checkpoint_wal 里的 PRAGMA 执行短路掉, 写入后 -wal > 0 → 断言 ==0 FAIL;
恢复后 PASS。
"""
from datetime import datetime

import pytest


def test_a3_wal_checkpoint_truncates_wal_file():
    from backend.db.database import engine, SessionLocal
    from backend.models.mes_models import ScanLog
    from backend.api.sessions_maintenance import _checkpoint_wal
    import os

    if engine.dialect.name != "sqlite":
        pytest.skip("仅 SQLite 有 WAL 文件")

    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA journal_mode")
        mode = str(cur.fetchone()[0]).lower()
        cur.close()
    finally:
        raw.close()
    if mode != "wal":
        pytest.skip(f"测试库非 WAL 模式: {mode}")

    db_path = engine.url.database
    wal_path = db_path + "-wal"

    # 写一批数据产生 WAL 增长（不自己 checkpoint）
    db = SessionLocal()
    try:
        for i in range(800):
            db.add(ScanLog(raw_data=f"wal-fill-{i}", created_at=datetime.now()))
        db.commit()
    finally:
        db.close()

    wal_before = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
    assert wal_before > 0, "写入后 -wal 文件应有内容(否则无法验证收缩)"

    _checkpoint_wal()

    wal_after = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
    assert wal_after == 0, f"WAL 未被 TRUNCATE 收缩: before={wal_before} after={wal_after}"

    # 收尾: 清掉填充数据, 不污染其他测试
    db = SessionLocal()
    try:
        db.query(ScanLog).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    _checkpoint_wal()
