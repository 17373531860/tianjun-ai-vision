"""BDD: OK/NG 录像分开保留 (跨模块业务行为护栏).

客户现场叙事:
  质量管理员在数据维护页打开"OK/NG 录像分开存", 设合格录像保留 7 天、不良录像保留
  180 天. 自动清理时合格录像到期就删、不良录像继续留, 且统计记录不因录像先删而丢.

对应单测: tests/test_video_retention_cleanup.py (清理矩阵). 本 BDD 用客户语言锁定
行为, 防后续改清理逻辑时回归.
"""
import os
from datetime import datetime, timedelta

from pytest_bdd import scenarios, given, when, then, parsers
import pytest

from backend.db.database import SessionLocal
from backend.models.models import DetectionSession, DetectionCycle, VideoClip, SystemConfig
from backend.api.sessions_maintenance import _perform_auto_cleanup

scenarios("../features/video_retention.feature")


@pytest.fixture
def ctx():
    return {"cycles": {}}  # {days_ago_result_key: (cycle_id, file_path)}


def _set_cfg(db, key, value):
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))


def _mk_cycle(db, tmp_path, *, days_ago, result):
    now = datetime.now()
    ts = now - timedelta(days=days_ago)
    sess = DetectionSession(
        session_uuid=f"bdd-s-{result}-{days_ago}-{db.query(DetectionSession).count()}",
        start_time=ts,
    )
    db.add(sess)
    db.flush()
    cyc = DetectionCycle(
        cycle_uuid=f"bdd-c-{result}-{days_ago}-{sess.id}",
        session_id=sess.id, start_time=ts, is_good=(result != "NG"),
    )
    db.add(cyc)
    db.flush()
    vpath = os.path.join(str(tmp_path), f"cycle_{cyc.cycle_uuid}.mp4")
    with open(vpath, "wb") as f:
        f.write(b"x")
    vid = VideoClip(
        video_uuid=f"bdd-v-{cyc.cycle_uuid}"[:50], clip_type="cycle", related_id=cyc.id,
        file_path=vpath, file_name=os.path.basename(vpath), result=result, created_at=ts,
    )
    db.add(vid)
    cyc.video_id = vid.video_uuid
    cyc.video_path = vpath
    db.flush()
    return cyc.id, vpath


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
            VideoClip.clip_type == "cycle", VideoClip.related_id == cid
        ).first()
    finally:
        db.close()


# ==================== 背景配置 ====================

@given(parsers.parse("自动清理已开启且全局保留{days:d}天"))
def _auto_cleanup_on(clean_db, days):
    db = SessionLocal()
    try:
        _set_cfg(db, "auto_cleanup", "true")
        _set_cfg(db, "retention_days", str(days))
        db.commit()
    finally:
        db.close()


@given(parsers.parse("开启分开存OK保留{ok:d}天NG保留{ng:d}天"))
def _split_on(ok, ng):
    db = SessionLocal()
    try:
        _set_cfg(db, "video_split_ok_ng", "true")
        _set_cfg(db, "video_ok_retention_days", str(ok))
        _set_cfg(db, "video_ng_retention_days", str(ng))
        db.commit()
    finally:
        db.close()


@given("关闭分开存")
def _split_off():
    db = SessionLocal()
    try:
        _set_cfg(db, "video_split_ok_ng", "false")
        db.commit()
    finally:
        db.close()


# ==================== 造数据 ====================

@given(parsers.parse("存在一个{days:d}天前的合格周期录像"))
def _mk_ok(ctx, tmp_path, days):
    db = SessionLocal()
    try:
        cid, fp = _mk_cycle(db, tmp_path, days_ago=days, result="OK")
        db.commit()
    finally:
        db.close()
    ctx["cycles"][f"OK-{days}"] = (cid, fp)


@given(parsers.parse("存在一个{days:d}天前的不良周期录像"))
def _mk_ng(ctx, tmp_path, days):
    db = SessionLocal()
    try:
        cid, fp = _mk_cycle(db, tmp_path, days_ago=days, result="NG")
        db.commit()
    finally:
        db.close()
    ctx["cycles"][f"NG-{days}"] = (cid, fp)


# ==================== 执行 ====================

@when("执行自动清理")
def _run_cleanup():
    _perform_auto_cleanup()


# ==================== 断言 ====================

@then(parsers.parse("那个{days:d}天前的合格周期被删除"))
def _ok_deleted(ctx, days):
    cid, fp = ctx["cycles"][f"OK-{days}"]
    assert _alive_cycle(cid) is None, f"{days}天前合格周期应被删除"
    assert not os.path.isfile(fp)


@then(parsers.parse("那个{days:d}天前的不良周期被保留"))
def _ng_kept(ctx, days):
    cid, fp = ctx["cycles"][f"NG-{days}"]
    assert _alive_cycle(cid) is not None, f"{days}天前不良周期应保留"
    assert _alive_video(cid) is not None
    assert os.path.isfile(fp)


@then(parsers.parse("那个{days:d}天前的不良周期被删除"))
def _ng_deleted(ctx, days):
    cid, fp = ctx["cycles"][f"NG-{days}"]
    assert _alive_cycle(cid) is None, f"{days}天前不良周期应被删除"
    assert not os.path.isfile(fp)


@then("两个35天前的周期都被删除")
def _both_35_deleted(ctx):
    for key in ("OK-35", "NG-35"):
        cid, fp = ctx["cycles"][key]
        assert _alive_cycle(cid) is None, f"{key} 应被删除"
        assert not os.path.isfile(fp)


@then(parsers.parse("那个{days:d}天前的合格周期被保留"))
def _ok_kept(ctx, days):
    cid, fp = ctx["cycles"][f"OK-{days}"]
    assert _alive_cycle(cid) is not None, f"{days}天前合格周期应保留"
    assert os.path.isfile(fp)


@then(parsers.parse("那个{days:d}天前的合格周期记录保留但录像文件被删且回放引用置空"))
def _ok_record_kept_video_gone(ctx, days):
    cid, fp = ctx["cycles"][f"OK-{days}"]
    row = _alive_cycle(cid)
    assert row is not None, "统计记录应保留(防孤儿)"
    assert _alive_video(cid) is None, "录像记录应删"
    assert not os.path.isfile(fp), "录像文件应删"
    assert row.video_path is None and row.video_id is None, "回放引用应置空"
