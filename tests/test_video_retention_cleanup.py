"""OK/NG 录像分开存 + 分别保留期清理 的单元测试。

覆盖：
  1. 开启分开存时 OK/NG 周期录像各自保留期 + 数据记录"至少全局、不短于录像"语义
  2. NG 录像更久 → 周期数据记录同步保留（不孤儿）
  3. OK 录像更短 → 录像先删、周期记录保留、回放引用置空
  4. 无录像标记的旧周期走全局保留期
  5. 不开分开存 = 等价旧的全局保留（零差异）
  6. cleanup-settings 接口新字段往返
"""
import os
from datetime import datetime, timedelta

import pytest

from backend.db.database import SessionLocal
from backend.core.config import settings
from backend.models.models import (
    DetectionSession, DetectionCycle, VideoClip, SystemConfig,
)
from backend.api.sessions_maintenance import _perform_auto_cleanup


def _mk_file(tmp_path, name):
    p = os.path.join(str(tmp_path), name)
    with open(p, "wb") as f:
        f.write(b"x")
    return p


def _set_cfg(db, key, value):
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))


def _mk_cycle(db, tmp_path, *, days_ago, result, with_video=True):
    """造一个 (session, cycle[, video])。result: 'OK'/'NG'/None。"""
    now = datetime.now()
    ts = now - timedelta(days=days_ago)
    sess = DetectionSession(
        session_uuid=f"s-{result}-{days_ago}-{id(tmp_path)}-{db.query(DetectionSession).count()}",
        start_time=ts,
    )
    db.add(sess)
    db.flush()
    cyc = DetectionCycle(
        cycle_uuid=f"c-{result}-{days_ago}-{sess.id}",
        session_id=sess.id,
        start_time=ts,
        is_good=(result != 'NG'),
    )
    db.add(cyc)
    db.flush()
    vpath = None
    if with_video:
        vpath = _mk_file(tmp_path, f"cycle_{cyc.cycle_uuid}.mp4")
        vid = VideoClip(
            video_uuid=f"v-{cyc.cycle_uuid}"[:50],
            clip_type='cycle',
            related_id=cyc.id,
            file_path=vpath,
            file_name=os.path.basename(vpath),
            result=result,
            created_at=ts,
        )
        db.add(vid)
        cyc.video_id = vid.video_uuid
        cyc.video_path = vpath
    db.flush()
    return sess.id, cyc.id, vpath


def _alive_cycle(cid):
    db = SessionLocal()
    try:
        return db.query(DetectionCycle).filter(DetectionCycle.id == cid).first()
    finally:
        db.close()


def _alive_video(cid):
    db = SessionLocal()
    try:
        return db.query(VideoClip).filter(
            VideoClip.clip_type == 'cycle', VideoClip.related_id == cid
        ).first()
    finally:
        db.close()


def test_split_retention_matrix(clean_db, tmp_path):
    db = SessionLocal()
    try:
        _set_cfg(db, "auto_cleanup", "true")
        _set_cfg(db, "retention_days", "30")
        _set_cfg(db, "video_split_ok_ng", "true")
        _set_cfg(db, "video_ok_retention_days", "7")
        _set_cfg(db, "video_ng_retention_days", "180")

        # C1 OK 35天: 数据保留 max(30,7)=30 → 35>30 周期+录像都删
        _, c1, f1 = _mk_cycle(db, tmp_path, days_ago=35, result='OK')
        # C2 NG 100天: 数据保留 max(30,180)=180 → 100<180 全保留
        _, c2, f2 = _mk_cycle(db, tmp_path, days_ago=100, result='NG')
        # C3 NG 200天: 200>180 → 删
        _, c3, f3 = _mk_cycle(db, tmp_path, days_ago=200, result='NG')
        # C4 OK 15天: 数据保留30→15<30 记录留; 录像保留7→15>7 录像删+引用置空
        _, c4, f4 = _mk_cycle(db, tmp_path, days_ago=15, result='OK')
        # C5 无 result 旧周期 40天: 全局30 → 删
        _, c5, f5 = _mk_cycle(db, tmp_path, days_ago=40, result=None, with_video=False)
        # C6 OK 5天: 全保留
        _, c6, f6 = _mk_cycle(db, tmp_path, days_ago=5, result='OK')
        db.commit()
    finally:
        db.close()

    _perform_auto_cleanup()

    # C1 删
    assert _alive_cycle(c1) is None
    assert not os.path.isfile(f1)
    # C2 保留（记录 + 录像 + 文件）
    assert _alive_cycle(c2) is not None
    assert _alive_video(c2) is not None
    assert os.path.isfile(f2)
    # C3 删
    assert _alive_cycle(c3) is None
    assert not os.path.isfile(f3)
    # C4 记录在、录像删、引用置空
    c4row = _alive_cycle(c4)
    assert c4row is not None
    assert _alive_video(c4) is None
    assert not os.path.isfile(f4)
    assert c4row.video_path is None and c4row.video_id is None
    # C5 删
    assert _alive_cycle(c5) is None
    # C6 全保留
    assert _alive_cycle(c6) is not None
    assert _alive_video(c6) is not None
    assert os.path.isfile(f6)


def test_no_split_equivalent_to_global(clean_db, tmp_path):
    """不开分开存：OK/NG 一视同仁按全局保留期 = 旧行为。"""
    db = SessionLocal()
    try:
        _set_cfg(db, "auto_cleanup", "true")
        _set_cfg(db, "retention_days", "30")
        _set_cfg(db, "video_split_ok_ng", "false")
        # 35天前 OK 与 NG 都应被删（全局30）
        _, c_ok, f_ok = _mk_cycle(db, tmp_path, days_ago=35, result='OK')
        _, c_ng, f_ng = _mk_cycle(db, tmp_path, days_ago=35, result='NG')
        # 5天前 OK 保留
        _, c_new, f_new = _mk_cycle(db, tmp_path, days_ago=5, result='OK')
        db.commit()
    finally:
        db.close()

    _perform_auto_cleanup()

    assert _alive_cycle(c_ok) is None and not os.path.isfile(f_ok)
    assert _alive_cycle(c_ng) is None and not os.path.isfile(f_ng)
    assert _alive_cycle(c_new) is not None and os.path.isfile(f_new)


def test_cleanup_settings_api_roundtrip(client):
    r = client.put("/api/v1/data/cleanup-settings", json={
        "retention_days": 45,
        "auto_cleanup": True,
        "video_split_ok_ng": True,
        "video_ok_retention_days": 10,
        "video_ng_retention_days": 365,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["video_split_ok_ng"] is True
    assert body["video_ok_retention_days"] == 10
    assert body["video_ng_retention_days"] == 365

    g = client.get("/api/v1/data/cleanup-settings")
    assert g.status_code == 200
    gb = g.json()
    assert gb["video_split_ok_ng"] is True
    assert gb["video_ng_retention_days"] == 365
