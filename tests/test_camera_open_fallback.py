# -*- coding: utf-8 -*-
"""相机打开的后端兜底 + 启动恢复二次补开（现场"启动黑屏要人工重选输入源"）。

现场日志实录（捷昌 B 站工位2）:
  166 [Camera] MSMF() 实测 32fps        ← ch1 相机一度开成功
  184 keeping model, only stopping input source   ← 前端首屏激活项目, 停了输入源
  203 VIDEOIO(DSHOW): can't be used to capture by index
  204 [Camera] 打开摄像头失败，重试 2/3...
  208 ch1 视频源恢复失败: 无法打开摄像头 1
DSHOW 三连失败挤在 1.5s 内, 且完全不试 MSMF, 恢复流程直接放弃 → 黑屏。
"""
import platform

import cv2
import pytest

from backend.api import source_camera_start_mixin as cam


class _FakeCap:
    def __init__(self, opened: bool):
        self._opened = opened
        self.released = False

    def isOpened(self):
        return self._opened

    def release(self):
        self.released = True


@pytest.fixture(autouse=True)
def _clean_backend_memory():
    cam._LAST_OK_CAMERA_BACKEND.clear()
    yield
    cam._LAST_OK_CAMERA_BACKEND.clear()


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(cam.platform, "system", lambda: "Windows")


def _patch_capture(monkeypatch, opens):
    """opens: 判定某后端能否打开; 返回记录调用顺序的 list。"""
    calls = []

    def fake_vc(index, backend=None):
        calls.append(backend)
        return _FakeCap(opens(backend))

    monkeypatch.setattr(cam.cv2, "VideoCapture", fake_vc)
    return calls


def _patch_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(cam.time, "sleep", lambda s: waits.append(s))
    return waits


def test_falls_back_to_msmf_when_dshow_cannot_open(monkeypatch, win):
    calls = _patch_capture(monkeypatch, lambda b: b == cv2.CAP_MSMF)
    _patch_sleep(monkeypatch)

    capture, backend = cam._open_camera_capture(1)

    assert backend == cv2.CAP_MSMF, "DSHOW 开不了必须兜到 MSMF, 而不是整体放弃"
    assert capture is not None and capture.isOpened()
    assert calls[:2] == [cv2.CAP_DSHOW, cv2.CAP_MSMF], "顺序仍是先 DSHOW 后 MSMF"


def test_prefers_last_successful_backend(monkeypatch, win):
    _patch_capture(monkeypatch, lambda b: b == cv2.CAP_MSMF)
    _patch_sleep(monkeypatch)
    cam._open_camera_capture(1)

    calls = _patch_capture(monkeypatch, lambda b: True)
    cam._open_camera_capture(1)
    assert calls[0] == cv2.CAP_MSMF, "上次成功的后端应排在候选最前, 省掉必失败的一轮"


def test_retry_uses_increasing_backoff(monkeypatch, win):
    _patch_capture(monkeypatch, lambda b: False)
    waits = _patch_sleep(monkeypatch)

    capture, backend = cam._open_camera_capture(1, max_rounds=3)

    assert (capture, backend) == (None, None)
    assert waits == [0.5, 1.0], "两次轮间等待递增（末轮后不再等）"


def test_non_windows_uses_default_backend(monkeypatch):
    monkeypatch.setattr(cam.platform, "system", lambda: "Linux")
    calls = _patch_capture(monkeypatch, lambda b: True)

    capture, backend = cam._open_camera_capture(0)

    assert backend is None and calls == [None]
    assert not cam._LAST_OK_CAMERA_BACKEND, "非 Windows 不记后端"


def test_strategy3_v4l2_reopen_guard_is_linux_only():
    """v3.51.2: 格式探测 Strategy 3 的守门必须是"仅 Linux"。

    旧守门 `platform.system() != "Windows"` 在 macOS 上误命中: AVFoundation
    正常出帧的句柄被 release 后用 mac 上不存在的 CAP_V4L2 重开必失败, 留下
    isOpened()=False 的死句柄 → 接口全报成功但监控页永远 "No Source"。
    """
    import inspect
    src = inspect.getsource(cam)
    assert 'platform.system() == "Linux"' in src, \
        "Strategy 3 V4L2 重开必须只在 Linux 触发"
    assert '!= "Windows"' not in src.split("Strategy 3")[1].split("终检")[0], \
        "Strategy 3 分支不允许再出现 '非 Windows' 宽守门"


def test_final_sanity_check_rejects_dead_capture():
    """v3.51.2: 策略走完必须终检句柄活着, 否则显式抛错而不是僵尸态。"""
    import inspect
    src = inspect.getsource(cam)
    tail = src.split("终检", 1)
    assert len(tail) == 2, "缺少终检段"
    seg = tail[1][:600]
    assert "not self.capture.isOpened()" in seg and "raise Exception" in seg, \
        "终检必须校验 isOpened 并显式抛错"


def test_failed_channel_gets_second_attempt(monkeypatch):
    """启动恢复: 首轮被前端激活抢掉的通道, 隔几秒必须再补开一次。"""
    from backend import main as backend_main

    attempts = []

    def flaky(ch_id, ch_cfg, mgr):
        attempts.append(ch_id)
        if len(attempts) == 1:
            raise Exception("无法打开摄像头 1，请检查设备是否被其他程序占用")

    monkeypatch.setattr(backend_main, "_restore_one_video_source", flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)

    class _Mgr:
        is_running = False
        device = "auto"

    class _CM:
        channels = {1: _Mgr()}

        @staticmethod
        def get_channel_sources():
            return {"1": {"source_type": "camera", "device_index": 1}}

        @staticmethod
        def get_auto_resume_config():
            return {"enabled": False}

    monkeypatch.setattr("backend.api.channel_manager.channel_manager", _CM)

    backend_main.auto_restore_video_sources()

    assert attempts == [1, 1], "失败通道应被补开一次"
