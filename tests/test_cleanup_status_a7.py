"""
A7 回归测试: 清理可见反馈。

客户痛点: 看不到自动清理有没有执行、释放了多少空间, 没有感知。
本测试锁定: 跑一次清理后, GET /api/v1/data/cleanup/status 能返回:
  - 上次清理时间(last_cleanup_at) 非空;
  - 清理明细(summary) 含各项删除计数;
  - 释放空间字段存在。

先红后绿: 把 _record_cleanup_status 函数体短路(直接 return) → last_cleanup_at 为 None → FAIL;
恢复后 PASS。
"""
import os
import tempfile
from datetime import datetime, timedelta


def test_a7_cleanup_status_recorded_and_exposed(client):
    from backend.db.database import SessionLocal
    from backend.models.models import SystemConfig
    from backend.models.export_models import ExportRunLog
    from backend.api.sessions_maintenance import _perform_auto_cleanup, _set_system_config

    tmpd = tempfile.mkdtemp(prefix="a7_")
    f = os.path.join(tmpd, "old.csv")
    with open(f, "w") as fh:
        fh.write("x" * 2048)
    ts = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(f, (ts, ts))

    db = SessionLocal()
    try:
        _set_system_config(db, "retention_days", "30", "")
        _set_system_config(db, "auto_cleanup", "true", "")
        _set_system_config(db, "export_retention_days", "0", "")
        # 清掉历史清理状态, 保证下面断言来自本次清理
        for k in ("last_cleanup_at", "last_cleanup_freed_bytes", "last_cleanup_summary"):
            row = db.query(SystemConfig).filter(SystemConfig.key == k).first()
            if row:
                db.delete(row)
        db.query(ExportRunLog).delete(synchronize_session=False)
        db.add(ExportRunLog(source_type="realtime", status="success",
                            output_file=f,
                            triggered_at=datetime.now() - timedelta(days=40)))
        db.commit()
    finally:
        db.close()

    _perform_auto_cleanup()

    r = client.get("/api/v1/data/cleanup/status")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["last_cleanup_at"] is not None, "未记录上次清理时间"
    assert data["last_cleanup_summary"] is not None, "未记录清理明细"
    assert data["last_cleanup_summary"].get("export_files", 0) >= 1, "明细未反映导出文件删除"
    assert "last_cleanup_freed_bytes" in data and data["last_cleanup_freed_bytes"] >= 0
    assert "last_cleanup_freed_mb" in data

    # 收尾
    db = SessionLocal()
    try:
        db.query(ExportRunLog).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
