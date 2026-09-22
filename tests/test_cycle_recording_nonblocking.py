# -*- coding: utf-8 -*-
"""周期录像不得堵推理线程 (开录像才顿 / 缺步违序误 NG)。

检测中每个新周期都会 start_cycle_recording。旧实现在推理线程里 Popen ffmpeg,
上一份 writer 还可能 release() 等到 10 秒 → 框冻住, 工人已做到下一步,
结算报缺步/违序/提前出现。本文件守住: RecThread 已在跑时, start_cycle_recording
必须马上返回, ffmpeg 开录在录制线程完成。
"""
import time

import pytest

from backend.api import source_recorder
from backend.api.source import VideoSourceManager
from tests.test_recorder_fmp4 import FFMPEG

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="环境无 ffmpeg")


@pytest.fixture()
def vsm_cycle_rec(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.api.source_recording_api_mixin.get_video_dirs",
        lambda: {"cycles": str(tmp_path), "sessions": str(tmp_path), "steps": str(tmp_path)},
    )
    vsm = VideoSourceManager(0)
    vsm.export_settings = {"record_cycle_video": True, "video_fps": 25}
    vsm.width, vsm.height = 64, 64
    vsm.current_cycle_uuid = f"nb-{time.time_ns()}"
    yield vsm
    try:
        vsm._stop_recording_thread()
    except Exception:
        pass
    try:
        vsm._close_all_writers()
    except Exception:
        pass


def test_start_cycle_recording_sync_when_rec_thread_idle(vsm_cycle_rec):
    """未开检测(无 RecThread): 返回后 writer 已就绪, 单测/闲置路径零差异。"""
    vsm_cycle_rec.start_cycle_recording()
    assert vsm_cycle_rec.cycle_video_writer is not None
    assert vsm_cycle_rec.cycle_video_writer.isOpened()


def test_start_cycle_recording_does_not_wait_ffmpeg_when_detecting(
        vsm_cycle_rec, monkeypatch):
    """检测中 RecThread 已在跑: start_cycle 不得等 Popen (模拟 350ms 的 ffmpeg 启动)。"""
    orig_open = source_recorder.FFmpegRecorder.open

    def slow_open(self):
        time.sleep(0.35)
        return orig_open(self)

    monkeypatch.setattr(source_recorder.FFmpegRecorder, "open", slow_open)

    vsm_cycle_rec._start_recording_thread()
    t0 = time.monotonic()
    vsm_cycle_rec.start_cycle_recording()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.15, f"start_cycle_recording 堵了 {elapsed:.3f}s, 仍在推理线程开 ffmpeg"

    deadline = time.monotonic() + 3.0
    while vsm_cycle_rec.cycle_video_writer is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert vsm_cycle_rec.cycle_video_writer is not None, "录制线程未完成开录"
    assert vsm_cycle_rec.cycle_video_writer.isOpened()


def test_replacing_cycle_writer_does_not_wait_old_release(vsm_cycle_rec):
    """新周期到来时旧 writer.release() 不得在调用线程 wait。"""

    class SlowWriter:
        def __init__(self):
            self.released = False

        def release(self, remux=True):
            time.sleep(0.35)
            self.released = True

        def isOpened(self):
            return True

    old = SlowWriter()
    vsm_cycle_rec.cycle_video_writer = old
    t0 = time.monotonic()
    vsm_cycle_rec.start_cycle_recording()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.15, f"旧 writer.release 堵了调用线程 {elapsed:.3f}s"
    assert old.released is False
    assert vsm_cycle_rec.cycle_video_writer is not old
