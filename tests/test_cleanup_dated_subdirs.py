"""录像按日期分子目录存储 + 清理递归 + 空目录回收 回归测试。

诉求(2026-06): 录像按 YYYY-MM-DD 分目录存放; 到期(滚动 mtime)清理要能递归进
日期子目录把过期文件删掉, 并回收清空后的空日期目录; 老的平铺录像仍兼容。

覆盖:
  - _ensure_dated_dir: 建当天日期子目录; 建目录失败时回退 base_dir(不丢录像)
  - 自动清理: 递归进日期子目录清过期文件 + 保留近期 + 回收空日期目录
  - 兼容: 老的平铺(无日期目录)录像仍被清理
  - _prune_empty_dated_dirs: 删空子目录、保留 base 与非空子目录
"""
import os
from datetime import datetime, timedelta


def _set_mtime(path, days_ago):
    ts = (datetime.now() - timedelta(days=days_ago)).timestamp()
    os.utime(path, (ts, ts))


def _isolate_dirs(monkeypatch, tmp_path):
    """把所有录像/上传目录指向 tmp, 隔离真实数据。"""
    from backend.core.config import settings
    sess = tmp_path / "rec" / "sessions"
    cyc = tmp_path / "rec" / "cycles"
    step = tmp_path / "rec" / "steps"
    up = tmp_path / "uploads"
    for d in (sess, cyc, step, up):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "SESSION_VIDEO_DIR", str(sess))
    monkeypatch.setattr(settings, "CYCLE_VIDEO_DIR", str(cyc))
    monkeypatch.setattr(settings, "STEP_VIDEO_DIR", str(step))
    monkeypatch.setattr(settings, "RECORDING_DIR", str(tmp_path / "rec"))
    monkeypatch.setattr(settings, "VIDEO_UPLOAD_DIR", str(up))
    return sess, cyc, step


def _enable_cleanup():
    from backend.db.database import SessionLocal
    from backend.api.sessions_maintenance import _set_system_config
    db = SessionLocal()
    try:
        _set_system_config(db, "retention_days", "30", "")
        _set_system_config(db, "auto_cleanup", "true", "")
        _set_system_config(db, "video_split_ok_ng", "false", "")
        _set_system_config(db, "export_cleanup_scan_dir", "false", "")
        _set_system_config(db, "export_cleanup_dir", "", "")
        db.commit()
    finally:
        db.close()


# ==================== _ensure_dated_dir ====================

def test_ensure_dated_dir_creates_yyyymmdd_subfolder(tmp_path):
    from backend.api.source_recording_api_mixin import _ensure_dated_dir
    base = tmp_path / "cycles"
    base.mkdir()
    out = _ensure_dated_dir(str(base))
    day = datetime.now().strftime("%Y-%m-%d")
    assert out == os.path.join(str(base), day), "未落到当天日期子目录"
    assert os.path.isdir(out), "日期子目录未被创建"


def test_ensure_dated_dir_falls_back_on_makedirs_failure(tmp_path, monkeypatch):
    """建子目录失败时回退到 base_dir 本身, 绝不因此丢录像。"""
    import backend.api.source_recording_api_mixin as m
    base = tmp_path / "cycles"
    base.mkdir()

    def _boom(*a, **k):
        raise OSError("makedirs failed")

    monkeypatch.setattr(m.os, "makedirs", _boom)
    out = m._ensure_dated_dir(str(base))
    assert out == str(base), "建目录失败未回退到 base_dir"


# ==================== 清理递归 + 空目录回收 ====================

def test_orphan_cleanup_recurses_into_dated_subdir(tmp_path, monkeypatch):
    sess, cyc, step = _isolate_dirs(monkeypatch, tmp_path)
    from backend.api.sessions_maintenance import _perform_auto_cleanup
    _enable_cleanup()

    day_old = cyc / "2026-05-01"
    day_old.mkdir()
    old_f = day_old / "cycle_old.mp4"
    old_f.write_text("x")
    _set_mtime(str(old_f), 40)

    day_new = cyc / "2026-06-23"
    day_new.mkdir()
    new_f = day_new / "cycle_new.mp4"
    new_f.write_text("x")
    _set_mtime(str(new_f), 1)

    _perform_auto_cleanup()

    assert not old_f.exists(), "日期子目录里的过期录像未被递归清理"
    assert new_f.exists(), "近期录像被误删"
    assert not day_old.exists(), "清空后的日期子目录未被回收"
    assert day_new.exists(), "仍有文件的日期目录被误删"


def test_flat_legacy_recording_still_cleaned(tmp_path, monkeypatch):
    """老的平铺(无日期目录)录像仍被扫到清理(向后兼容)。"""
    sess, cyc, step = _isolate_dirs(monkeypatch, tmp_path)
    from backend.api.sessions_maintenance import _perform_auto_cleanup
    _enable_cleanup()

    flat_old = cyc / "cycle_flat_old.mp4"
    flat_old.write_text("x")
    _set_mtime(str(flat_old), 40)

    _perform_auto_cleanup()

    assert not flat_old.exists(), "平铺老录像未被清理(兼容性回归)"


# ==================== 端到端: 被追踪的周期录像确实被清 ====================

def test_tracked_cycle_video_in_dated_dir_is_deleted(tmp_path, monkeypatch):
    """造一条 5 天前的 会话+周期+真实 .mp4(放日期子目录), 设 1 天保留期,
    跑真实清理函数 → 录像文件 + 录像记录 + 周期 + 会话 全部被清。

    这是对'视频不会自动清'担忧的端到端反证: 被追踪的录像走 DB 周期归属删除路径,
    与是否按日期分目录无关——分目录后照样删干净。
    """
    sess, cyc, step = _isolate_dirs(monkeypatch, tmp_path)
    from backend.db.database import SessionLocal
    from backend.models.models import DetectionSession, DetectionCycle, VideoClip
    from backend.api.sessions_maintenance import _perform_auto_cleanup, _set_system_config

    # 录像文件放在周期录像目录的日期子目录里
    day_dir = cyc / "2026-06-18"
    day_dir.mkdir()
    vfile = day_dir / "cycle_e2e_old.mp4"
    vfile.write_text("video-bytes")
    _set_mtime(str(vfile), 5)

    db = SessionLocal()
    sess_id = cyc_id = None
    try:
        # 1 天保留期, 关闭 OK/NG 分开存(直接走全局)
        _set_system_config(db, "retention_days", "1", "")
        _set_system_config(db, "auto_cleanup", "true", "")
        _set_system_config(db, "video_split_ok_ng", "false", "")
        _set_system_config(db, "export_cleanup_scan_dir", "false", "")
        _set_system_config(db, "export_cleanup_dir", "", "")

        old = datetime.now() - timedelta(days=5)
        s = DetectionSession(session_uuid="e2e-clean-sess", start_time=old)
        db.add(s)
        db.flush()
        sess_id = s.id
        c = DetectionCycle(session_id=s.id, cycle_uuid="e2e-clean-cyc",
                           start_time=old, is_good=True)
        db.add(c)
        db.flush()
        cyc_id = c.id
        db.add(VideoClip(video_uuid="e2e-clean-vid", clip_type="cycle", related_id=c.id,
                         file_path=str(vfile), file_name=vfile.name,
                         created_at=old))
        db.commit()
    finally:
        db.close()

    _perform_auto_cleanup()

    assert not vfile.exists(), "5 天前的周期录像文件未被删除(视频清理失效!)"
    db = SessionLocal()
    try:
        assert db.query(DetectionCycle).filter(DetectionCycle.id == cyc_id).first() is None, \
            "过期周期记录未被删除"
        assert db.query(VideoClip).filter(VideoClip.related_id == cyc_id,
                                          VideoClip.clip_type == "cycle").first() is None, \
            "过期录像记录未被删除"
        assert db.query(DetectionSession).filter(DetectionSession.id == sess_id).first() is None, \
            "已无周期的过期会话未被删除"
    finally:
        db.close()


# ==================== _prune_empty_dated_dirs ====================

def test_prune_empty_dated_dirs_keeps_base_and_nonempty(tmp_path):
    from backend.api.sessions_maintenance import _prune_empty_dated_dirs
    base = tmp_path / "cycles"
    base.mkdir()
    empty = base / "2026-05-01"
    empty.mkdir()
    full = base / "2026-05-02"
    full.mkdir()
    (full / "x.mp4").write_text("x")

    _prune_empty_dated_dirs(str(base))

    assert os.path.isdir(str(base)), "base 目录被误删"
    assert not empty.exists(), "空日期目录未被回收"
    assert full.exists(), "非空日期目录被误删"
