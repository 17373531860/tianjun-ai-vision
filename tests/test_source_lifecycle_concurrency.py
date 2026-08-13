# -*- coding: utf-8 -*-
"""输入源启停的并发安全（现场后端整进程崩: 0xC0000374 堆损坏）。

现场日志实录（捷昌 B 站, 打完 v3.50.0a 补丁后）:
  202 [Capture] thread started                        ← 相机开起来了
  206 [VideoManager] fully stopped and released resources
  212 keeping model, only stopping input source       ← 同一段时间里被反复停
  245 Health check failed  ×3
  251 Process exited (code: 3221226356)               ← 0xC0000374 = 堆损坏

启动那几秒有三方在动同一个通道: 前端首屏激活绑定项目、待机(pause)、后台
视频源恢复。它们各自都跑 `if self.capture: self.capture.release()`, 两个线程
同时通过 if 再各自 release 同一个 VideoCapture = double free, 在 Windows 上
直接堆损坏崩进程（相机开不起来时 capture 是 None, 所以以前撞不到）。
"""
import os
import threading
import time

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
os.environ.setdefault("BACKEND_SKIP_INIT", "1")

import pytest

from backend.api import source_camera_start_mixin as cam
from backend.api.source import VideoSourceManager


class _FakeCap:
    """记录被 release 了几次 —— 大于 1 次就是现场那个 double free。"""

    def __init__(self):
        self.release_count = 0

    def release(self):
        self.release_count += 1
        time.sleep(0.01)   # 放大窗口, 让没上锁的实现必然翻车

    def isOpened(self):
        return True


@pytest.fixture
def vsm():
    return VideoSourceManager(channel_id=7)


def test_concurrent_release_only_frees_handle_once(vsm):
    cap = _FakeCap()
    vsm.capture = cap

    threads = [threading.Thread(target=vsm._release_capture, args=("stop",))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert cap.release_count == 1, "同一句柄被 release 多次 = double free = 崩后端"
    assert vsm.capture is None


def test_release_capture_survives_broken_handle(vsm):
    class _Boom:
        def release(self):
            raise RuntimeError("device gone")

    vsm.capture = _Boom()
    vsm._release_capture("pause")      # 不许把异常抛给调用方
    assert vsm.capture is None, "释放失败也要置空, 否则下次又去 release 同一个坏句柄"


def test_release_capture_is_noop_without_handle(vsm):
    vsm.capture = None
    vsm._release_capture("stop")
    assert vsm.capture is None


def test_lifecycle_lock_is_per_channel_and_reentrant():
    a = VideoSourceManager(channel_id=11)
    b = VideoSourceManager(channel_id=12)

    assert a._source_lifecycle_lock is not b._source_lifecycle_lock, "通道之间不该互相阻塞"
    assert a._source_lifecycle_lock is VideoSourceManager(channel_id=11)._source_lifecycle_lock, \
        "同一通道必须拿到同一把锁（锁按 channel_id 存模块级, 不挂实例）"

    lock = a._source_lifecycle_lock
    assert lock.acquire(timeout=1)
    assert lock.acquire(timeout=1), "必须可重入: start_camera 持锁后内部还会调 stop"
    lock.release()
    lock.release()


def test_stop_waits_for_start_camera_to_finish(monkeypatch, vsm):
    """start_camera 打开过程中会 release+换后端重开, 中途被 stop 抽走句柄就是现场那个崩溃。"""
    events = []

    def fake_start(**kwargs):
        events.append("start:enter")
        time.sleep(0.15)
        events.append("start:exit")
        return True

    monkeypatch.setattr(vsm, "_start_camera_locked", fake_start)
    # _release_capture 只在 stop 的锁内跑, 拿它当 stop 已进入临界区的探针
    monkeypatch.setattr(vsm, "_release_capture",
                        lambda context="stop": events.append("stop:release"))

    starter = threading.Thread(target=vsm.start_camera)
    starter.start()
    time.sleep(0.03)                     # 确保 stop 是在 start 中途插进来的
    vsm.stop()
    starter.join()

    assert events == ["start:enter", "start:exit", "stop:release"], \
        f"启停必须串行（stop 不得在开相机中途抽走句柄）, 实际: {events}"


def test_lock_table_does_not_leak_across_channels():
    before = len(cam._LIFECYCLE_LOCKS)
    VideoSourceManager(channel_id=21)._source_lifecycle_lock
    VideoSourceManager(channel_id=21)._source_lifecycle_lock
    assert len(cam._LIFECYCLE_LOCKS) == before + 1, "同通道重复取锁不该新建"
