# -*- coding: utf-8 -*-
"""v3.51.3 摄像头枚举去重 + 跨工位抢相机预检 + SQLite disk I/O 自愈。

现场实录（捷昌 2026-08-14 晚）:
  1. "连两个摄像头加我电脑检测出四个摄像头, 三四是同一个, 然后同时选可能
     就超时" — 同一台 USB 相机在 DirectShow 注册多个 filter, 老枚举按
     index 盲试开, 无物理去重; 双工位同选后开方卡满重试直到前端 60s 超时。
  2. "切换输入源的时候 Sqli 报 disk IO error ... 后端就 down 掉或者拉不
     起来" — 强杀残留损坏 WAL 后, import 期 create_all 直接挂死。
"""
import os

import pytest

from backend.api import source_routes as sr
from backend.api import source_camera_start_mixin as cam


# ============================================================
# ffmpeg dshow 设备列表解析
# ============================================================
FF5_OUTPUT = '''
[dshow @ 0000021ce8847380] "Integrated Camera" (video)
[dshow @ 0000021ce8847380]   Alternative name "@device_pnp_\\\\?\\usb#vid_04f2&pid_b6be&mi_00#6&5f2fe38&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\\global"
[dshow @ 0000021ce8847380] "USB Camera" (video)
[dshow @ 0000021ce8847380]   Alternative name "@device_pnp_\\\\?\\usb#vid_1bcf&pid_2284&mi_00#7&2d3a4b5&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\\global"
[dshow @ 0000021ce8847380] "Microphone Array" (audio)
[dshow @ 0000021ce8847380]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\\wave_{GUID}"
dummy: Immediate exit requested
'''

FF4_OUTPUT = '''
[dshow @ 000001] DirectShow video devices (some may be both video and audio devices)
[dshow @ 000001]  "Integrated Camera"
[dshow @ 000001]     Alternative name "@device_pnp_\\\\?\\usb#vid_04f2&pid_b6be&mi_00#6&5f2fe38&0&0000#{65e8773d}\\global"
[dshow @ 000001] DirectShow audio devices
[dshow @ 000001]  "Microphone Array"
[dshow @ 000001]     Alternative name "@device_cm_{33D9A762}\\wave_{GUID}"
'''


def test_parse_ffmpeg5_format_video_only():
    devices = sr._parse_dshow_device_list(FF5_OUTPUT)
    assert [d["name"] for d in devices] == ["Integrated Camera", "USB Camera"], \
        "只收 video 设备, audio 不进列表"
    assert "vid_04f2" in devices[0]["path"]
    assert "vid_1bcf" in devices[1]["path"]


def test_parse_ffmpeg4_section_format():
    devices = sr._parse_dshow_device_list(FF4_OUTPUT)
    assert len(devices) == 1 and devices[0]["name"] == "Integrated Camera"
    assert "vid_04f2" in devices[0]["path"], "段落式格式的 Alternative name 也要挂对"


def test_parse_audio_alt_name_does_not_pollute_video():
    devices = sr._parse_dshow_device_list(FF5_OUTPUT)
    assert "wave_" not in devices[-1]["path"], \
        "audio 的 Alternative name 不能误挂到最后一个 video 设备上"


def test_parse_garbage_returns_empty():
    assert sr._parse_dshow_device_list("") == []
    assert sr._parse_dshow_device_list("not ffmpeg output at all") == []


# ============================================================
# 同物理机去重
# ============================================================
def _dev(name, path):
    return {"name": name, "path": path}


def test_dedup_same_usb_composite_device():
    """同 (vid,pid,serial) 只差 mi_ 接口号 = 同一台物理机（如 RGB+IR 复合头）。"""
    devices = [
        _dev("USB Camera", "@device_pnp_\\\\?\\usb#vid_1bcf&pid_2284&mi_00#7&abc&0&0000#{g}\\global"),
        _dev("USB Camera IR", "@device_pnp_\\\\?\\usb#vid_1bcf&pid_2284&mi_02#7&abc&0&0000#{g}\\global"),
    ]
    kept = sr._dedup_dshow_devices(devices)
    assert len(kept) == 1 and kept[0][0] == 0, "保留第一个条目, 原始 index 不变"


def test_dedup_keeps_two_identical_model_cameras():
    """两台同型号相机 serial 不同 → 不许误合并（产线常见双同款）。"""
    devices = [
        _dev("USB Camera", "@device_pnp_\\\\?\\usb#vid_1bcf&pid_2284&mi_00#serialAAA#{g}\\global"),
        _dev("USB Camera", "@device_pnp_\\\\?\\usb#vid_1bcf&pid_2284&mi_00#serialBBB#{g}\\global"),
    ]
    kept = sr._dedup_dshow_devices(devices)
    assert len(kept) == 2


def test_dedup_field_scenario_2_usb_plus_builtin():
    """现场剧本: 2 USB + 1 内置列出 4 个, 三四同一台 → 去重后 3 台, index 保序。"""
    devices = [
        _dev("Integrated Camera", "@device_pnp_\\\\?\\usb#vid_04f2&pid_b6be&mi_00#6&x&0&0000#{g}\\global"),
        _dev("USB Camera A", "@device_pnp_\\\\?\\usb#vid_1bcf&pid_2284&mi_00#s1#{g}\\global"),
        _dev("USB Camera B", "@device_pnp_\\\\?\\usb#vid_0c45&pid_6366&mi_00#s2#{g}\\global"),
        _dev("USB Camera B", "@device_pnp_\\\\?\\usb#vid_0c45&pid_6366&mi_02#s2#{g}\\global"),
    ]
    kept = sr._dedup_dshow_devices(devices)
    assert [i for i, _ in kept] == [0, 1, 2], "第 4 个 (index 3) 是 index 2 的重复 filter, 应被去掉"


def test_dedup_virtual_camera_without_usb_path_kept():
    devices = [
        _dev("OBS Virtual Camera", "@device_sw_{860BB310-5D01-11D0-BD3B-00A0C911CE86}\\{A3FCE0F5}"),
        _dev("OBS Virtual Camera", ""),
    ]
    kept = sr._dedup_dshow_devices(devices)
    assert len(kept) == 2, "无 USB 路径的条目按位置各自唯一, 不做激进合并"


# ============================================================
# _detect_cameras_windows 主路径 + 兜底
# ============================================================
def test_detect_windows_uses_ffmpeg_list_with_names_and_in_use(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(sr, "_list_dshow_devices_ffmpeg", lambda: [
        _dev("Integrated Camera", "@device_pnp_\\\\?\\usb#vid_04f2&pid_b6be&mi_00#6&x#{g}\\global"),
        _dev("USB Camera", "@device_pnp_\\\\?\\usb#vid_1bcf&pid_2284&mi_00#s1#{g}\\global"),
        _dev("USB Camera", "@device_pnp_\\\\?\\usb#vid_1bcf&pid_2284&mi_02#s1#{g}\\global"),
    ])
    monkeypatch.setattr(sr, "_camera_indexes_in_use", lambda: {1: 0})

    cameras = sr._detect_cameras_windows()

    assert len(cameras) == 2, "重复 filter 去重后只剩 2 台"
    assert cameras[0]["name"].startswith("Integrated Camera"), "用设备真名, 不再是「摄像头 N」"
    assert "[工位1使用中]" in cameras[1]["name"]
    # 不许调 cv2 试开 → 不会干扰在采集的相机 (间接验证: 无 ffmpeg 结果字段外的副作用)
    assert cameras[0]["index"] == 0 and cameras[1]["index"] == 1


def test_detect_windows_falls_back_to_probe_when_ffmpeg_unavailable(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(sr, "_list_dshow_devices_ffmpeg", lambda: None)
    monkeypatch.setattr(sr, "_camera_indexes_in_use", lambda: {})

    opened = []

    class _Cap:
        def __init__(self, index, backend=None):
            opened.append(index)
            self._ok = index < 2

        def isOpened(self):
            return self._ok

        def release(self):
            pass

    monkeypatch.setattr(sr.cv2, "VideoCapture", _Cap)

    cameras = sr._detect_cameras_windows()
    assert [c["index"] for c in cameras] == [0, 1], "ffmpeg 不可用必须回退老试开法"
    assert opened == [0, 1, 2, 3, 4]


def test_in_use_covers_all_channels(monkeypatch):
    class _Mgr:
        def __init__(self, st, idx):
            self.source_type = st
            self.capture = object() if st == 'camera' else None
            self.camera_index = idx

    class _CM:
        channels = {0: _Mgr('camera', 0), 1: _Mgr('camera', 2), 2: _Mgr('rtsp', None)}

    monkeypatch.setattr("backend.api.channel_manager.channel_manager", _CM)
    assert sr._camera_indexes_in_use() == {0: 0, 2: 1}, \
        "多工位占用都要标出来, 不能只看 ch0"


# ============================================================
# 跨工位抢相机预检
# ============================================================
def test_conflict_guard_finds_other_channel(monkeypatch):
    class _Mgr:
        source_type = 'camera'
        capture = object()
        camera_index = 1

    class _CM:
        channels = {0: _Mgr(), 1: _Mgr()}

    monkeypatch.setattr("backend.api.channel_manager.channel_manager", _CM)
    assert cam._find_camera_index_conflict(1, self_channel_id=1) == 0, "工位0 正占用 index 1"
    assert cam._find_camera_index_conflict(3, self_channel_id=1) is None


def test_conflict_guard_skips_self_channel(monkeypatch):
    class _Mgr:
        source_type = 'camera'
        capture = object()
        camera_index = 0

    class _CM:
        channels = {0: _Mgr()}

    monkeypatch.setattr("backend.api.channel_manager.channel_manager", _CM)
    assert cam._find_camera_index_conflict(0, self_channel_id=0) is None, \
        "同通道重启同一相机是合法操作, 不许拦"


def test_start_camera_checks_conflict_before_stop():
    """预检必须在 self.stop() 之前——不能为一次注定失败的打开先停掉本通道旧源。"""
    import inspect
    src = inspect.getsource(cam.CameraStartMixin._start_camera_locked)
    guard_pos = src.find("_find_camera_index_conflict")
    stop_pos = src.find("self.stop(release_model=False)")
    assert 0 < guard_pos < stop_pos, "冲突预检要挡在 stop 之前"


# ============================================================
# SQLite disk I/O 自愈
# ============================================================
def test_quarantine_moves_wal_shm_keeps_main_db(tmp_path):
    from backend import main as backend_main
    db = tmp_path / "sql_app.db"
    db.write_bytes(b"main")
    (tmp_path / "sql_app.db-wal").write_bytes(b"wal")
    (tmp_path / "sql_app.db-shm").write_bytes(b"shm")

    moved = backend_main._quarantine_sqlite_sidecars(str(db))

    assert len(moved) == 2
    assert db.exists() and db.read_bytes() == b"main", "主库文件绝不能动"
    assert not (tmp_path / "sql_app.db-wal").exists()
    assert not (tmp_path / "sql_app.db-shm").exists()
    for m in moved:
        assert ".corrupt-" in m and os.path.exists(m), "侧车文件是隔离保留, 不是删除"


def test_quarantine_noop_when_no_sidecars(tmp_path):
    from backend import main as backend_main
    db = tmp_path / "sql_app.db"
    db.write_bytes(b"main")
    assert backend_main._quarantine_sqlite_sidecars(str(db)) == []


def test_cleanup_on_exit_does_wal_checkpoint():
    """退出钩子必须带 WAL checkpoint 收尾, 缩小强杀留损坏 WAL 的窗口。"""
    import inspect
    from backend import main as backend_main
    src = inspect.getsource(backend_main.cleanup_on_exit)
    assert "wal_checkpoint(TRUNCATE)" in src
