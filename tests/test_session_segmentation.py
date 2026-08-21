# -*- coding: utf-8 -*-
"""会话录像按小时分段 (v3.54 长录像治理) 单元测试。

覆盖:
  1. 到点轮转: 新段 writer 换入 / seg_index 递增 / deadline 重置 /
     老段进 draining 排空后释放 / 新段 VideoClip 落库 (真 ffmpeg 真文件)
  2. 开新段失败: 老 writer 保住续录 + 60s 后重试, 录像不中断
  3. 未到点零动作; 停录清 deadline 不再轮转
  4. _finalize_session_clip_row 按 file_path 补 end_time/file_size
  5. 分段列表 API GET /data/sessions/{id}/videos 升序返回
"""
import os
import shutil
import threading
import time
from datetime import datetime

import numpy as np
import pytest

from backend.api import source_recording_api_mixin as seg_mod
from backend.api.source_recording_api_mixin import RecordingApiMixin
from backend.db.database import SessionLocal
from backend.models.models import DetectionSession, VideoClip


def _find_ffmpeg():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(root, "ffmpeg", "ffmpeg"),
                 os.path.join(root, "ffmpeg", "ffmpeg.exe")):
        if os.path.isfile(cand):
            return cand
    return shutil.which("ffmpeg")


FFMPEG = _find_ffmpeg()


class _FakePersist:
    """同步执行的 _persist 替身 (真实现是本通道 FIFO 落库线程)。"""

    def __init__(self):
        self.jobs = []

    def submit(self, name, fn):
        self.jobs.append(name)
        fn()


class _Host(RecordingApiMixin):
    """最小宿主: 只带分段轮转依赖的状态面。"""

    def __init__(self, session_id=None):
        self._writer_lock = threading.Lock()
        self.video_writer = None
        self.cycle_video_writer = None
        self.width = 320
        self.height = 240
        self.current_session_uuid = "seguuid1234"
        self.current_session_id = session_id
        self.channel_id = 0
        self._persist = _FakePersist()
        self.export_settings = {"record_session_video": True, "video_fps": 25}
        self.recording_failures = []
        self._recording_failure_lock = threading.Lock()

    def _append_recording_failure(self, *a, **k):
        self.recording_failures.append((a, k))

    def _get_db_session(self):
        return SessionLocal()


@pytest.fixture()
def host(monkeypatch, tmp_path):
    """宿主 + 录像目录钉到 tmp + ffmpeg 钉真路径。"""
    from backend.api import source_recorder
    if FFMPEG:
        monkeypatch.setattr(source_recorder, "_get_ffmpeg_path_cached",
                            lambda: FFMPEG)
    monkeypatch.setattr(
        seg_mod, "get_video_dirs",
        lambda: {"sessions": str(tmp_path / "sessions"),
                 "cycles": str(tmp_path / "cycles"),
                 "steps": str(tmp_path / "steps"),
                 "cache": str(tmp_path / "cache")})
    return _Host()


def _open_writer(host, name="seg1.mp4"):
    from backend.api.source_recorder import FFmpegRecorder
    path = os.path.join(seg_mod.get_video_dirs()["sessions"], name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    w = FFmpegRecorder(path, 320, 240, 25)
    assert w.open()
    return w


def _write_some(writer, n=30):
    for i in range(n):
        frame = np.full((240, 320, 3), (i * 5) % 255, dtype=np.uint8)
        writer.write(frame)


needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="环境无 ffmpeg")


# ============================================================
# 1. 到点轮转
# ============================================================

@needs_ffmpeg
class TestRotate:
    def test_rotate_swaps_writer_and_persists_clip(self, host):
        old = _open_writer(host)
        _write_some(old)
        host.video_writer = old
        host._session_seg_index = 1
        host._session_seg_deadline = time.time() - 1  # 已到点
        host._session_rec_params = (320, 240, 25)

        # 建一条真实 session 行, 供新段 VideoClip 挂 related_id
        db = SessionLocal()
        try:
            s = DetectionSession(session_uuid="seg_test_rotate",
                                 start_time=datetime.now())
            db.add(s)
            db.commit()
            host.current_session_id = s.id
            sid = s.id
        finally:
            db.close()

        host._maybe_rotate_session_recording()

        try:
            assert host.video_writer is not old, "writer 未换段"
            assert host._session_seg_index == 2
            assert host._session_seg_deadline > time.time() + 5, "deadline 未重置"
            assert "_p002.mp4" in host.video_writer.filepath

            # 新段可继续写 (录像不断流)
            _write_some(host.video_writer, 10)

            # 新段 VideoClip 已落库 (FakePersist 同步执行)
            db = SessionLocal()
            try:
                clips = db.query(VideoClip).filter(
                    VideoClip.clip_type == "session",
                    VideoClip.related_id == sid).all()
                assert len(clips) == 1
                assert clips[0].file_path == host.video_writer.filepath
            finally:
                db.close()

            # 老段 1.5s 排空后被释放并补 end_time (等它走完)
            deadline = time.time() + 8
            while time.time() < deadline and old.isOpened():
                time.sleep(0.2)
            assert not old.isOpened(), "老段 writer 未被延迟释放"
        finally:
            try:
                host.video_writer.release(remux=False)
            except Exception:
                pass

    def test_not_due_is_noop(self, host):
        old = _open_writer(host)
        host.video_writer = old
        host._session_seg_index = 1
        host._session_seg_deadline = time.time() + 3600
        host._maybe_rotate_session_recording()
        assert host.video_writer is old
        assert host._session_seg_index == 1
        old.release(remux=False)

    def test_no_deadline_is_noop(self, host):
        host._maybe_rotate_session_recording()  # 无会话录像时不得抛错
        assert host.video_writer is None


# ============================================================
# 2. 开新段失败 → 老 writer 保住
# ============================================================

@needs_ffmpeg
class TestRotateOpenFailure:
    def test_keep_old_writer_and_retry_later(self, host, monkeypatch):
        old = _open_writer(host)
        host.video_writer = old
        host._session_seg_index = 1
        host._session_seg_deadline = time.time() - 1
        host.current_session_id = None

        from backend.api import source_recording_api_mixin as m
        monkeypatch.setattr(m.FFmpegRecorder, "open", lambda self: False)

        before = time.time()
        host._maybe_rotate_session_recording()

        assert host.video_writer is old, "开新段失败必须保住老 writer"
        assert old.isOpened(), "老 writer 不得被动过"
        assert host._session_seg_index == 1
        assert 50 <= host._session_seg_deadline - before <= 70, "应 60s 后重试"
        assert host.recording_failures, "应记录 rotate_open_failed 事件"
        old.release(remux=False)


# ============================================================
# 3. 停录清 deadline
# ============================================================

@needs_ffmpeg
def test_stop_session_recording_clears_deadline(host):
    w = _open_writer(host)
    _write_some(w)
    host.video_writer = w
    host._session_seg_deadline = time.time() + 100

    host.stop_session_recording()
    assert host._session_seg_deadline is None
    assert host.video_writer is None
    # 延迟释放线程 1.5s 后关掉 writer
    deadline = time.time() + 8
    while time.time() < deadline and w.isOpened():
        time.sleep(0.2)
    assert not w.isOpened()


# ============================================================
# 4. _finalize_session_clip_row
# ============================================================

def test_finalize_clip_row_updates_end_time_and_size(tmp_path):
    p = str(tmp_path / "clip_x.mp4")
    with open(p, "wb") as f:
        f.write(b"x" * 1234)
    db = SessionLocal()
    try:
        db.add(VideoClip(video_uuid="finalize_test_uuid", clip_type="session",
                         file_path=p, file_name="clip_x.mp4",
                         start_time=datetime.now()))
        db.commit()
    finally:
        db.close()

    assert seg_mod._finalize_session_clip_row(p) is True

    db = SessionLocal()
    try:
        row = db.query(VideoClip).filter(
            VideoClip.video_uuid == "finalize_test_uuid").first()
        assert row.end_time is not None
        assert row.file_size == 1234
    finally:
        db.close()


def test_finalize_clip_row_missing_returns_false(tmp_path):
    assert seg_mod._finalize_session_clip_row(
        str(tmp_path / "nonexist.mp4"), retries=1) is False


# ============================================================
# 5. 分段列表 API
# ============================================================

def test_session_videos_api_ordered(client):
    db = SessionLocal()
    try:
        s = DetectionSession(session_uuid="seg_api_test",
                             start_time=datetime.now())
        db.add(s)
        db.commit()
        sid = s.id
        for i, hh in enumerate(("08", "09", "10")):
            db.add(VideoClip(
                video_uuid=f"segapi_{i}", clip_type="session", related_id=sid,
                file_path=f"/tmp/none_{i}.mp4", file_name=f"none_{i}.mp4",
                start_time=datetime(2026, 8, 21, int(hh), 0, 0)))
        db.commit()
    finally:
        db.close()

    r = client.get(f"/api/v1/data/sessions/{sid}/videos")
    assert r.status_code == 200
    body = r.json()
    assert [x["video_uuid"] for x in body] == ["segapi_0", "segapi_1", "segapi_2"]
    assert body[0]["segment_index"] == 1
    assert body[2]["segment_index"] == 3
    assert all(x["file_exists"] is False for x in body)
