"""MJPEG 同通道多窗口订阅回归。

客户现场：副工位扩展窗持续显示 channel=1，主屏放大同一工位时也会读取
channel=1。两条合法连接必须共存、共享同一份 JPEG 编码；同一个窗口重连时
仍要淘汰它自己的旧连接，不能让僵尸 generator 累积。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import numpy as np
import pytest


MJPEG_CHUNK = (
    b"--frame\r\n"
    b"Content-Type: image/jpeg\r\n\r\n"
    b"test-jpeg"
    b"\r\n"
)


def _make_stream_manager():
    from backend.api.source import VideoSourceManager
    from backend.api.source_state_init import _init_fps_stats, _init_streaming_state

    manager = VideoSourceManager.__new__(VideoSourceManager)
    manager.channel_id = 1
    manager.is_running = True
    manager.current_frame = np.zeros((8, 8, 3), dtype=np.uint8)
    manager.frame_lock = threading.Lock()
    manager.frame_limit_enabled = False
    manager.target_stream_fps = 30
    manager._pending_ack = False
    manager._inference_thread = object()
    manager._inference_generation = 9
    _init_fps_stats(manager)
    _init_streaming_state(manager)
    manager.fps_actual = 30
    manager.fps_inference = 17
    return manager


def _publish_frame(manager, value: int) -> None:
    with manager.frame_lock:
        manager.current_frame = np.full((8, 8, 3), value, dtype=np.uint8)
        manager._frame_seq += 1


def test_main_and_station_share_one_encode_without_touching_inference(monkeypatch):
    """主屏与扩展窗同时取流：每个源帧只编码一次，推理状态保持不变。"""
    from backend.api.channel_manager import channel_manager

    manager = _make_stream_manager()
    monkeypatch.setattr(channel_manager, "channel_count", 2)
    encoded_values: list[int] = []

    def fake_encode(frame):
        encoded_values.append(int(frame[0, 0, 0]))
        return MJPEG_CHUNK

    manager._encode_and_yield = fake_encode
    manager._start_inference_thread = lambda: pytest.fail("查看视频不得启动推理线程")
    manager._stop_inference_thread = lambda: pytest.fail("查看视频不得停止推理线程")
    inference_thread = manager._inference_thread
    inference_generation = manager._inference_generation
    inference_fps = manager.fps_inference
    station = manager.generate_mjpeg(viewer="station")
    main = manager.generate_mjpeg(viewer="main")

    try:
        assert next(station) == MJPEG_CHUNK
        assert next(main) == MJPEG_CHUNK
        assert encoded_values == [0]
        assert manager._mjpeg_active_streams == 2
        assert set(manager._mjpeg_active_conn_ids) == {"station", "main"}

        for value in range(1, 5):
            _publish_frame(manager, value)
            assert next(main) == MJPEG_CHUNK
            assert next(station) == MJPEG_CHUNK

        assert encoded_values == [0, 1, 2, 3, 4]
        assert manager._inference_thread is inference_thread
        assert manager._inference_generation == inference_generation
        assert manager.fps_inference == inference_fps
    finally:
        station.close()
        main.close()

    assert manager._mjpeg_active_streams == 0
    assert manager._mjpeg_active_conn_ids == {}


def test_reconnect_replaces_only_the_same_viewer_slot(monkeypatch):
    """station 重连只淘汰旧 station，不能踢掉正在放大的 main。"""
    from backend.api.channel_manager import channel_manager

    manager = _make_stream_manager()
    monkeypatch.setattr(channel_manager, "channel_count", 2)
    manager._encode_and_yield = lambda _frame: MJPEG_CHUNK

    old_station = manager.generate_mjpeg(viewer="station")
    main = manager.generate_mjpeg(viewer="main")
    new_station = manager.generate_mjpeg(viewer="station")

    try:
        assert next(old_station) == MJPEG_CHUNK
        assert next(main) == MJPEG_CHUNK
        assert next(new_station) == MJPEG_CHUNK

        with pytest.raises(StopIteration):
            next(old_station)

        _publish_frame(manager, 1)
        assert next(main) == MJPEG_CHUNK
        assert next(new_station) == MJPEG_CHUNK
        assert set(manager._mjpeg_active_conn_ids) == {"station", "main"}
    finally:
        old_station.close()
        main.close()
        new_station.close()


def test_concurrent_viewers_encode_a_source_frame_once(monkeypatch):
    """两个响应线程同时抢新帧也只能有一个 JPEG 编码者。"""
    from backend.api.channel_manager import channel_manager

    manager = _make_stream_manager()
    monkeypatch.setattr(channel_manager, "channel_count", 2)
    encoded_values: list[int] = []

    def fake_encode(frame):
        encoded_values.append(int(frame[0, 0, 0]))
        time.sleep(0.03)  # 放大无锁实现的重复编码窗口
        return MJPEG_CHUNK

    manager._encode_and_yield = fake_encode
    main = manager.generate_mjpeg(viewer="main")
    station = manager.generate_mjpeg(viewer="station")
    try:
        assert next(main) == MJPEG_CHUNK
        assert next(station) == MJPEG_CHUNK
        _publish_frame(manager, 7)

        gate = threading.Barrier(3)

        def read_next(stream):
            gate.wait()
            return next(stream)

        with ThreadPoolExecutor(max_workers=2) as pool:
            main_next = pool.submit(read_next, main)
            station_next = pool.submit(read_next, station)
            gate.wait()
            assert main_next.result(timeout=2) == MJPEG_CHUNK
            assert station_next.result(timeout=2) == MJPEG_CHUNK

        assert encoded_values == [0, 7]
    finally:
        main.close()
        station.close()


def test_jpeg_encode_never_holds_capture_frame_lock(monkeypatch):
    """JPEG 较慢时采集线程仍可立即拿 frame_lock 发布下一帧。"""
    from backend.api.channel_manager import channel_manager

    manager = _make_stream_manager()
    monkeypatch.setattr(channel_manager, "channel_count", 2)
    encode_entered = threading.Event()
    allow_encode_finish = threading.Event()

    def slow_encode(_frame):
        encode_entered.set()
        assert allow_encode_finish.wait(timeout=1)
        return MJPEG_CHUNK

    manager._encode_and_yield = slow_encode
    stream = manager.generate_mjpeg(viewer="station")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first_frame = pool.submit(next, stream)
            assert encode_entered.wait(timeout=1)
            assert manager.frame_lock.acquire(timeout=0.2)
            manager.frame_lock.release()
            allow_encode_finish.set()
            assert first_frame.result(timeout=1) == MJPEG_CHUNK
    finally:
        allow_encode_finish.set()
        stream.close()


def test_unchanged_sequence_is_not_copied_after_cache_expires(monkeypatch):
    """采集短暂停帧时只查 seq，不得高频复制整张 current_frame。"""
    from backend.api.channel_manager import channel_manager
    from backend.api.source import VideoSourceManager

    manager = _make_stream_manager()
    monkeypatch.setattr(channel_manager, "channel_count", 2)
    tracked_frame = MagicMock()
    tracked_frame.copy.return_value = np.zeros((8, 8, 3), dtype=np.uint8)
    manager.current_frame = tracked_frame
    manager._encode_and_yield = lambda _frame: MJPEG_CHUNK

    version, _, chunk, _, _ = VideoSourceManager._shared_mjpeg_chunk(
        manager, last_cache_version=-1, stream_interval=0
    )
    assert chunk == MJPEG_CHUNK
    assert tracked_frame.copy.call_count == 1

    # 即使 cache 节拍已经到期，source seq 未推进也不能再 copy/encode。
    _, _, chunk, _, _ = VideoSourceManager._shared_mjpeg_chunk(
        manager, last_cache_version=version, stream_interval=0
    )
    assert chunk is None
    assert tracked_frame.copy.call_count == 1


def test_slow_viewer_skips_old_frames_without_blocking_fast_viewer(monkeypatch):
    """慢窗口恢复时直接读最新 cache，不积压旧帧，也不反压快窗口。"""
    from backend.api.channel_manager import channel_manager

    manager = _make_stream_manager()
    monkeypatch.setattr(channel_manager, "channel_count", 2)

    def value_chunk(frame):
        value = int(frame[0, 0, 0])
        return MJPEG_CHUNK.replace(b"test-jpeg", f"frame-{value}".encode())

    manager._encode_and_yield = value_chunk
    fast = manager.generate_mjpeg(viewer="main")
    slow = manager.generate_mjpeg(viewer="station")
    try:
        assert b"frame-0" in next(fast)
        assert b"frame-0" in next(slow)
        for value in range(1, 5):
            _publish_frame(manager, value)
            assert f"frame-{value}".encode() in next(fast)

        # slow 没有自己的队列；恢复后拿到的是共享 cache 最新帧 frame-4。
        assert b"frame-4" in next(slow)
    finally:
        fast.close()
        slow.close()


def test_stopped_source_shares_one_placeholder_encode_and_cleans_slots(monkeypatch):
    """暂停源的保活帧同样共享，两个窗口关闭后连接账本归零。"""
    from backend.api.channel_manager import channel_manager

    manager = _make_stream_manager()
    monkeypatch.setattr(channel_manager, "channel_count", 2)
    manager.is_running = False
    manager.current_frame = None
    encode_count = 0

    def fake_encode(_frame):
        nonlocal encode_count
        encode_count += 1
        return MJPEG_CHUNK

    manager._encode_and_yield = fake_encode
    main = manager.generate_mjpeg(viewer="main")
    station = manager.generate_mjpeg(viewer="station")
    try:
        assert next(main) == MJPEG_CHUNK
        assert next(station) == MJPEG_CHUNK
        assert encode_count == 1
        assert set(manager._mjpeg_active_conn_ids) == {"main", "station"}
    finally:
        main.close()
        station.close()

    assert manager._mjpeg_active_conn_ids == {}
    assert manager._mjpeg_active_streams == 0


def test_legacy_connections_keep_later_connection_wins(monkeypatch):
    """缺省/非法 viewer 都归 legacy，同槽仍保持后来者上位。"""
    from backend.api.channel_manager import channel_manager

    manager = _make_stream_manager()
    monkeypatch.setattr(channel_manager, "channel_count", 2)
    manager._encode_and_yield = lambda _frame: MJPEG_CHUNK
    old_stream = manager.generate_mjpeg()
    new_stream = manager.generate_mjpeg(viewer="arbitrary-window-id")

    try:
        assert next(old_stream) == MJPEG_CHUNK
        assert next(new_stream) == MJPEG_CHUNK
        assert set(manager._mjpeg_active_conn_ids) == {"legacy"}
        with pytest.raises(StopIteration):
            next(old_stream)
    finally:
        old_stream.close()
        new_stream.close()


def test_video_feed_forwards_viewer_to_channel_manager(monkeypatch):
    """HTTP viewer 参数必须原样传给目标工位，不能串到其他 channel。"""
    from backend.api.channel_manager import channel_manager
    from backend.main import video_feed

    manager = MagicMock()
    manager.is_running = True
    manager.source_type = "camera"
    manager.generate_mjpeg.return_value = iter([MJPEG_CHUNK])
    get_manager = MagicMock(return_value=manager)
    monkeypatch.setattr(channel_manager, "get", get_manager)

    response = video_feed(channel=1, viewer="station")

    get_manager.assert_called_once_with(1)
    manager.generate_mjpeg.assert_called_once_with(viewer="station")
    assert response.media_type == "multipart/x-mixed-replace; boundary=frame"
