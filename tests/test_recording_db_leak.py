"""录像写库防泄漏 + 唯一编号防撞号回归测试 (v3.23.x 现场卡顿根治)。

背景: 工步录像唯一编号被截断成 8 位 -> 高频启停撞 UNIQUE 约束 -> 写库抛
IntegrityError -> 异常分支没 close 数据库连接 -> SQLite 写锁泄漏累积 ->
结算写库在 busy_timeout 上死等 -> 状态机停摆卡顿、滞后误判, 重启才恢复。

本测试钉死两件事:
  1. 录像唯一编号用完整 uuid (32 位), 不再 8 位截断。
  2. 任意写库失败时, 数据库连接必被 close (不泄漏) 且 rollback。
"""
import threading
from unittest.mock import MagicMock

import pytest

from backend.api.source_recording_api_mixin import RecordingApiMixin


class _InlinePersist:
    """v3.38 落库线程桩: 原地执行作业 (等价 TIANJUN_SYNC_PERSIST=1 同步回退路径),
    让本测试继续钉"连接必 close"的防泄漏契约。异常隔离由 test_persist_worker 钉。"""

    def submit(self, desc, fn):
        fn()


class _FakeHost(RecordingApiMixin):
    """只搭起 mixin 运行所需的最小宿主状态。"""

    def __init__(self):
        self.export_settings = {"record_step_video": True, "video_fps": 25}
        self.step_video_writers = {}
        self._step_writers_lock = threading.RLock()
        self.width = 1280
        self.height = 720
        self.current_cycle_id = 1
        self.current_cycle_uuid = "cyc-uuid-1"
        self._persist = _InlinePersist()
        self._db_sessions = []  # 记录所有发出去的连接, 用于核查是否都 close 了

    def _append_recording_failure(self, *a, **k):
        pass


def _make_db():
    db = MagicMock()
    db.closed = False

    def _close():
        db.closed = True

    db.close.side_effect = _close
    return db


def test_step_uuid_is_full_length(monkeypatch):
    """工步录像编号必须是完整 uuid (32 位), 杜绝 8 位截断撞号。"""
    host = _FakeHost()

    fake_writer = MagicMock()
    fake_writer.open.return_value = True
    monkeypatch.setattr(
        "backend.api.source_recording_api_mixin.FFmpegRecorder",
        lambda *a, **k: fake_writer,
    )
    # 数据库在 start 阶段用不到, 给个空的
    host._get_db_session = lambda: _make_db()

    video_uuid = host.start_step_recording("放置产品")
    assert video_uuid is not None
    assert len(video_uuid) == 32, f"录像编号长度应为 32, 实际 {len(video_uuid)}"
    assert host.step_video_writers["放置产品"]["video_uuid"] == video_uuid


def test_stop_step_recording_closes_db_on_commit_failure(monkeypatch):
    """写库 commit 抛异常 (模拟撞号) 时, 连接必 close, 不泄漏。

    v3.38 起写库走落库作业 (作业内 SessionLocal + try/finally close);
    Session.close() 自带隐式 rollback, 不再显式断言 rollback 调用。
    """
    host = _FakeHost()
    db = _make_db()
    db.commit.side_effect = Exception("UNIQUE constraint failed: video_clips.video_uuid")
    monkeypatch.setattr("backend.db.database.SessionLocal", lambda: db)

    # 预置一个待停止的工步录像
    host.step_video_writers["放置产品"] = {
        "writer": MagicMock(),
        "filepath": "/tmp/x.mp4",
        "filename": "x.mp4",
        "video_uuid": "a" * 32,
        "start_time": __import__("datetime").datetime.now(),
        "frame_size": (1280, 720),
    }

    # 不应抛出 (内部已兜底)
    result = host.stop_step_recording("放置产品")

    assert result is None, "撞号时应返回 None"
    assert db.closed is True, "连接必须被 close (否则就是泄漏卡顿根因)"
    # writer 仍被正常移除, 不残留
    assert "放置产品" not in host.step_video_writers


def test_stop_step_recording_closes_db_on_success(monkeypatch):
    """正常路径也必须 close 连接。"""
    host = _FakeHost()
    db = _make_db()
    monkeypatch.setattr("backend.db.database.SessionLocal", lambda: db)

    host.step_video_writers["检查外观"] = {
        "writer": MagicMock(),
        "filepath": "/tmp/y.mp4",
        "filename": "y.mp4",
        "video_uuid": "b" * 32,
        "start_time": __import__("datetime").datetime.now(),
        "frame_size": (1280, 720),
    }

    result = host.stop_step_recording("检查外观")

    assert result is not None
    assert result["video_uuid"] == "b" * 32
    assert db.closed is True
    db.commit.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
