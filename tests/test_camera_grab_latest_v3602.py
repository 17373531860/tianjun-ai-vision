"""v3.60.2 USB 摄像头「只取最新帧」采集循环单元测试。

覆盖 backend/api/source_capture_loop_mixin.py 的:
  - _camera_grab_latest_frame 只取最新帧算法 (4 个核心用例 + grab 全失败边界);
  - _capture_loop 对 source_type 的分流 (相机走 grab-latest 且跳过末尾 fps sleep;
    视频文件仍走 read() 且仍按 fps sleep);
  - 丢弃旧帧节流日志 (每 10s 汇总一条, 不每帧刷屏)。

全部用可控 grab 耗时的假 VideoCapture + 假时钟, 不打开真实摄像头, 无真实等待。
"""
from __future__ import annotations

import os
import threading

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")

import numpy as np

import backend.api.source_capture_loop_mixin as cap_mod
from backend.api.source_capture_loop_mixin import CaptureLoopMixin

# 阈值: < 8ms = 队头旧帧 (瞬时); >= 8ms = 等到传感器新帧。用 1ms / 20ms 稳健区分。
_INSTANT = 0.001
_SLOW = 0.020


class _Host(CaptureLoopMixin):
    """裸宿主, 只带 _camera_grab_latest_frame / _capture_loop 所需的最小属性。"""


class FakeClock:
    """假时钟: 只有 grab()/retrieve()/sleep() 显式推进, 其余 time.time() 不流逝。"""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.sleeps.append(dt)
        self.now += float(dt)


class GrabCamera:
    """单次 grab-latest 用: grab() 按 durations 逐个"耗时"(推进 clock);
    None 表示该次 grab 失败。retrieve() 返回最后一次成功 grab 的帧号。"""

    def __init__(self, clock: FakeClock, durations):
        self.clock = clock
        self.durations = list(durations)
        self.grab_calls = 0
        self.retrieve_calls = 0
        self.read_calls = 0
        self._n = 0
        self._last = None

    def grab(self) -> bool:
        i = self.grab_calls
        self.grab_calls += 1
        if i < len(self.durations):
            dur = self.durations[i]
        else:
            dur = self.durations[-1] if self.durations else 0.0
        if dur is None:
            return False
        self.clock.now += float(dur)
        self._n += 1
        self._last = self._n
        return True

    def retrieve(self):
        self.retrieve_calls += 1
        return (True, self._last)

    def read(self):
        self.read_calls += 1
        ok = self.grab()
        return (True, self._last) if ok else (False, None)


def _make_host(cap):
    h = _Host()
    h.capture = cap
    return h


# ────────────────────────── _camera_grab_latest_frame ──────────────────────────

def test_first_grab_slow_uses_it_without_second_grab(monkeypatch):
    """第一次 grab 就慢 → 只 retrieve 一次, 丢帧数为 0, 没有第二次 grab。"""
    clock = FakeClock()
    monkeypatch.setattr(cap_mod, "time", clock)
    cam = GrabCamera(clock, [_SLOW])
    ret, frame, dropped = _make_host(cam)._camera_grab_latest_frame()

    assert ret is True
    assert frame == 1
    assert dropped == 0
    assert cam.grab_calls == 1        # 没有第二次 grab
    assert cam.retrieve_calls == 1    # 只 retrieve 一次
    assert clock.sleeps == []         # 没有防空转 sleep


def test_instant_grabs_then_slow_keeps_latest_and_drops_stale(monkeypatch):
    """若干次瞬时 grab 后一次慢 grab → retrieve 最后那张新帧, 瞬时旧帧被丢弃。"""
    clock = FakeClock()
    monkeypatch.setattr(cap_mod, "time", clock)
    cam = GrabCamera(clock, [_INSTANT, _INSTANT, _INSTANT, _SLOW])
    ret, frame, dropped = _make_host(cam)._camera_grab_latest_frame()

    assert ret is True
    assert frame == 4                 # 用最后 grab 到的新帧
    assert dropped == 3               # 前 3 张瞬时旧帧被丢弃
    assert cam.grab_calls == 4
    assert cam.retrieve_calls == 1    # 只 retrieve 最新的一张
    assert clock.sleeps == []


def test_all_instant_until_cap_sleeps_without_spin(monkeypatch):
    """连续到上限仍每次瞬时返回 → 会 sleep 5ms 防空转, 不会无限抓。"""
    clock = FakeClock()
    monkeypatch.setattr(cap_mod, "time", clock)
    cam = GrabCamera(clock, [_INSTANT] * 40)   # 驱动从不阻塞
    ret, frame, dropped = _make_host(cam)._camera_grab_latest_frame()

    assert ret is True
    assert cam.grab_calls == cap_mod._CAMERA_MAX_DRAIN      # 30, 到上限即止
    assert cam.retrieve_calls == 1
    assert dropped == cap_mod._CAMERA_MAX_DRAIN - 1         # 29
    assert clock.sleeps == [0.005]                          # 防空转小睡
    assert frame == cap_mod._CAMERA_MAX_DRAIN               # 用最后抓到的那张


def test_grab_failure_retrieves_last_successful(monkeypatch):
    """瞬时 grab 后一次 grab 失败 → retrieve 上一张成功的, 不再等下一帧。"""
    clock = FakeClock()
    monkeypatch.setattr(cap_mod, "time", clock)
    cam = GrabCamera(clock, [_INSTANT, _INSTANT, None])
    ret, frame, dropped = _make_host(cam)._camera_grab_latest_frame()

    assert ret is True
    assert frame == 2                 # 第 3 次 grab 失败, 用第 2 次
    assert dropped == 1
    assert cam.grab_calls == 3
    assert cam.retrieve_calls == 1
    assert clock.sleeps == []


def test_first_grab_failure_returns_read_failure(monkeypatch):
    """首次 grab 就失败 (一张都没抓到) → 返回读帧失败, 不 retrieve。"""
    clock = FakeClock()
    monkeypatch.setattr(cap_mod, "time", clock)
    cam = GrabCamera(clock, [None])
    ret, frame, dropped = _make_host(cam)._camera_grab_latest_frame()

    assert ret is False
    assert frame is None
    assert dropped == 0
    assert cam.grab_calls == 1
    assert cam.retrieve_calls == 0


def test_none_capture_returns_failure(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(cap_mod, "time", clock)
    ret, frame, dropped = _make_host(None)._camera_grab_latest_frame()
    assert (ret, frame, dropped) == (False, None, 0)


# ────────────────────────── _capture_loop 分流 ──────────────────────────

def _bare_loop_host(source_type, capture):
    """构造能跑 _capture_loop 的最小裸宿主 (is_detecting=False, 不起推理/录制线程)。"""
    h = _Host()
    h.source_type = source_type
    h.capture = capture
    h.is_running = True
    h.is_detecting = False
    h.model = None
    h.video_speed = 1.0
    h.mediapipe_enabled = False
    h.frame_lock = threading.Lock()
    h.detection_lock = threading.Lock()
    h._frame_seq = 0
    h._fps_counter = 0
    h._fps_time = 0.0
    h.fps_actual = 0
    h.fps = 30
    h.current_frame = None
    h.video_path = "dummy.mp4"
    h.video_current_frame = 0
    h.video_ended = False
    h._has_display_transform = lambda: False
    h._fire_source_status_change = lambda *a, **k: None
    h._stop_inference_thread = lambda: None
    h._stop_recording_thread = lambda: None
    return h


class VideoCam:
    """视频文件假源: read() 首帧返回真图, 之后 (False, None) 触发"播放结束"。"""

    def __init__(self):
        self.read_calls = 0
        self.grab_calls = 0

    def read(self):
        self.read_calls += 1
        if self.read_calls == 1:
            return (True, np.zeros((4, 4, 3), dtype=np.uint8))
        return (False, None)

    def grab(self):
        self.grab_calls += 1
        return True

    def get(self, prop):
        return 0.0


def test_video_path_still_uses_read_and_fps_sleep(monkeypatch):
    """视频文件路径仍然调用 read(), 并且仍会做 fps sleep (1/fps)。"""
    clock = FakeClock()
    monkeypatch.setattr(cap_mod, "time", clock)
    monkeypatch.setattr(cap_mod.cv2, "VideoCapture",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no reopen")))
    cam = VideoCam()
    _bare_loop_host("video", cam)._capture_loop()

    assert cam.read_calls >= 2   # 首帧 + 播放结束探测, 全部走 read()
    assert any(abs(s - (1.0 / 30)) < 1e-9 for s in clock.sleeps)


class DriveCamera:
    """驱动 _capture_loop 的相机假源: grab() 首次即"慢"(>=8ms), 保证只取最新一帧;
    第 stop_after 次 retrieve 后让宿主 is_running=False 收尾。read() 永不应被调用。"""

    def __init__(self, clock: FakeClock, host, stop_after: int = 1):
        self.clock = clock
        self.host = host
        self.stop_after = stop_after
        self.grab_calls = 0
        self.retrieve_calls = 0
        self.read_calls = 0
        self._last = None

    def grab(self) -> bool:
        self.grab_calls += 1
        self.clock.now += _SLOW      # 慢 grab = 等传感器出新帧
        self._last = np.zeros((4, 4, 3), dtype=np.uint8)
        return True

    def retrieve(self):
        self.retrieve_calls += 1
        if self.retrieve_calls >= self.stop_after:
            self.host.is_running = False
        return (True, self._last)

    def read(self):
        self.read_calls += 1
        return (True, self._last)

    def get(self, prop):
        return 0.0


def test_camera_dispatch_uses_grab_latest_and_skips_target_sleep(monkeypatch):
    """相机走「只取最新帧」(grab+retrieve, 从不 read()), 并跳过末尾 target_interval sleep。"""
    clock = FakeClock()
    monkeypatch.setattr(cap_mod, "time", clock)
    monkeypatch.setattr(cap_mod.cv2, "VideoCapture",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no reopen")))
    h = _bare_loop_host("camera", None)
    cam = DriveCamera(clock, h, stop_after=1)
    h.capture = cam
    h._capture_loop()

    assert cam.grab_calls >= 1
    assert cam.retrieve_calls >= 1
    assert cam.read_calls == 0
    # 相机不做 1/fps 的节奏 sleep (grab 已按传感器节奏阻塞)
    assert all(abs(s - (1.0 / 30)) > 1e-9 for s in clock.sleeps)


class ThrottleCamera:
    """按 plans 逐个 publish 服务 grab 耗时列表; retrieve 时按 retrieve_steps 推进
    clock 模拟 publish 间真实时间流逝; 第 stop_after 次 retrieve 后收尾。"""

    def __init__(self, clock: FakeClock, host, plans, retrieve_steps, stop_after: int):
        self.clock = clock
        self.host = host
        self.plans = [list(p) for p in plans]
        self.retrieve_steps = list(retrieve_steps)
        self.stop_after = stop_after
        self.publish_idx = 0
        self._pos = 0
        self.grab_calls = 0
        self.retrieve_calls = 0
        self.read_calls = 0
        self._last = None

    def _plan(self):
        return self.plans[min(self.publish_idx, len(self.plans) - 1)]

    def grab(self) -> bool:
        self.grab_calls += 1
        plan = self._plan()
        dur = plan[self._pos] if self._pos < len(plan) else plan[-1]
        self._pos += 1
        if dur is None:
            return False
        self.clock.now += float(dur)
        self._last = np.zeros((4, 4, 3), dtype=np.uint8)
        return True

    def retrieve(self):
        self.retrieve_calls += 1
        step = (self.retrieve_steps[self.publish_idx]
                if self.publish_idx < len(self.retrieve_steps) else 0.0)
        self.clock.now += float(step)
        frame = self._last
        self.publish_idx += 1
        self._pos = 0
        if self.retrieve_calls >= self.stop_after:
            self.host.is_running = False
        return (True, frame)

    def read(self):
        self.read_calls += 1
        return (True, self._last)

    def get(self, prop):
        return 0.0


def test_stale_drop_log_is_throttled_and_accumulated(monkeypatch, capsys):
    """丢弃旧帧节流日志: 10s 内静默累计, 满 10s 才打印一条累计值 (不每帧刷屏)。"""
    clock = FakeClock()
    monkeypatch.setattr(cap_mod, "time", clock)
    monkeypatch.setattr(cap_mod.cv2, "VideoCapture",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no reopen")))
    h = _bare_loop_host("camera", None)
    cam = ThrottleCamera(
        clock, h,
        plans=[[_INSTANT, _INSTANT, _SLOW],    # 每轮丢 2 张旧帧
               [_INSTANT, _INSTANT, _SLOW]],
        retrieve_steps=[0.0, 11.0],            # 第 1 轮 <10s 静默, 第 2 轮模拟已过 11s
        stop_after=2,
    )
    h.capture = cam
    h._capture_loop()

    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if "stale frames dropped=" in ln]
    # 只打 1 条 (非每帧), 且为两轮累计 2+2=4
    assert lines == ["[Camera] stale frames dropped=4"]
