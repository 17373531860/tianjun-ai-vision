"""WS5-P0: sql_compat 方言兼容 helper + /system/db-info 端点回归。

PG 方言行为已由本地 PG16 实测（date_str/sum_bool/hour_minute/JSON path/
pg_dump 备份/迁移工具 --verify），本文件锁 SQLite 方言下的行为不回退，
并在 CI db-matrix 的 PG job 下自动覆盖双方言。
"""
import os
import uuid
from datetime import datetime

import pytest

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
os.environ.setdefault("BACKEND_SKIP_INIT", "1")


@pytest.fixture()
def db_session(tmp_path):
    """独立文件 DB（遵守 fixture 隔离铁律），注册全部模型建表。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.db.database import Base
    from backend.models import (models, export_models, mes_models,  # noqa: F401
                                plugin_models, auth_models, notify_models,
                                weighing_models, plc_models, trigger_models)

    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith("postgresql"):
        engine = create_engine(url)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'ws5.db'}",
                               connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.rollback()
    s.close()
    engine.dispose()


def _seed_cycles(db):
    from backend.models.models import DetectionSession, DetectionCycle
    sess = DetectionSession(session_uuid=str(uuid.uuid4()),
                            start_time=datetime(2026, 8, 11, 9, 30))
    db.add(sess)
    db.flush()
    for i, good in enumerate([True, True, False]):
        db.add(DetectionCycle(cycle_uuid=str(uuid.uuid4()), session_id=sess.id,
                              cycle_number=i + 1, is_good=good,
                              start_time=datetime(2026, 8, 11, 9, 31 + i)))
    db.flush()
    return sess


def test_sum_bool_counts_true_rows(db_session):
    """sum_bool 折 CASE 0/1 再 SUM，双方言一致（PG 没有 SUM(boolean)）。"""
    from sqlalchemy import func as sa_func

    from backend.db.sql_compat import sum_bool
    from backend.models.models import DetectionCycle

    sess = _seed_cycles(db_session)
    total, good = db_session.query(
        sa_func.count(DetectionCycle.id),
        sum_bool(DetectionCycle.is_good == True),  # noqa: E712
    ).filter(DetectionCycle.session_id == sess.id).one()
    assert total == 3
    assert int(good) == 2


def test_date_str_group_by_daily(db_session):
    """date_str 输出 'YYYY-MM-DD' 字符串表达式，可 group_by/order_by（daily_stats 口径）。"""
    from sqlalchemy import func as sa_func

    from backend.db.sql_compat import date_str, sum_bool
    from backend.models.models import DetectionCycle

    sess = _seed_cycles(db_session)
    rows = db_session.query(
        date_str(DetectionCycle.start_time).label("d"),
        sa_func.count(DetectionCycle.id),
        sum_bool(DetectionCycle.is_good == True),  # noqa: E712
    ).filter(DetectionCycle.session_id == sess.id
             ).group_by("d").order_by("d").all()
    assert len(rows) == 1
    assert str(rows[0][0]) == "2026-08-11"
    assert rows[0][1] == 3 and int(rows[0][2]) == 2


def test_hour_minute_format(db_session):
    """hour_minute 输出 'HH:MM'（sessions.py 时段过滤口径）。"""
    from backend.db.sql_compat import hour_minute
    from backend.models.models import DetectionCycle

    sess = _seed_cycles(db_session)
    hm = db_session.query(hour_minute(DetectionCycle.start_time)).filter(
        DetectionCycle.session_id == sess.id
    ).order_by(DetectionCycle.id).first()
    assert hm[0] == "09:31"


def test_external_alarm_json_path_match(db_session):
    """扩展维度匹配走 SQLAlchemy JSON path 索引（不再是 SQLite 专属 json_extract）。"""
    from backend.services import external_alarm as ea

    r1 = ea.record_active_alarm(
        db_session,
        {"task_no": "T-WS5", "warning_text": "w", "batch_no": "B1"},
        match_fields=["task_no", "batch_no"], dedup_sec=60)
    assert r1["recorded"] is True
    # 去重窗口内同键 -> skipped
    r2 = ea.record_active_alarm(
        db_session, {"task_no": "T-WS5", "batch_no": "B1", "warning_text": "w"},
        match_fields=["task_no", "batch_no"], dedup_sec=60)
    assert r2.get("skipped") is True
    # 按扩展维度精准消除
    c = ea.clear_alarms(db_session, {"task_no": "T-WS5", "batch_no": "B1"},
                        match_fields=["task_no", "batch_no"])
    assert c == {"matched": True, "cleared": 1}


def test_db_info_endpoint_shape():
    """/system/db-info 返回结构稳定（Settings 数据库卡片依赖）。"""
    from backend.api.system_display import get_db_info
    from backend.db.database import SessionLocal, get_dialect

    db = SessionLocal()
    try:
        info = get_db_info(db)
    finally:
        db.close()
    assert info["dialect"] == get_dialect()
    assert info["connected"] is True
    assert "location" in info and "server_version" in info
    if info["dialect"] == "sqlite":
        assert info.get("pool") is None
    else:
        assert isinstance(info.get("pool"), dict)
