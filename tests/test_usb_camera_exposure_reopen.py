"""USB/UVC camera exposure persistence across all VideoCapture reopen paths."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")

import cv2

from backend.api.source_camera_start_mixin import _apply_exposure_setting
from backend.api.source_lifecycle_mixin import LifecycleMixin


class _FakeCapture:
    def __init__(self, backend=cv2.CAP_MSMF, exposure_readback=None):
        self.backend = backend
        self.opened = True
        self.values = {
            cv2.CAP_PROP_BACKEND: float(backend),
            cv2.CAP_PROP_AUTO_EXPOSURE: 1.0,
            cv2.CAP_PROP_EXPOSURE: -6.0,
        }
        self.exposure_readback = exposure_readback
        self.set_calls = []

    def isOpened(self):
        return self.opened

    def getBackendName(self):
        return 'MSMF' if self.backend == cv2.CAP_MSMF else 'DSHOW'

    def set(self, prop, value):
        self.set_calls.append((prop, value))
        self.values[prop] = value
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_EXPOSURE and self.exposure_readback is not None:
            return self.exposure_readback
        return self.values.get(prop, 0.0)


def test_msmf_manual_exposure_uses_backend_specific_ae_value(monkeypatch, capsys):
    # 被测分支按宿主 OS 走 Windows/V4L2 两条路, 必须钉死 Windows 才测得到 MSMF 语义
    # (Linux 开发机上不 mock 会走进 V4L2 分支, AE=1.0/EXPOSURE=312 导致断言失败)
    monkeypatch.setattr('platform.system', lambda: 'Windows')
    cap = _FakeCapture(backend=cv2.CAP_MSMF, exposure_readback=-6.0)

    result = _apply_exposure_setting(cap, False, -5.0, context='unit_test')

    assert result['applied'] is True
    assert (cv2.CAP_PROP_AUTO_EXPOSURE, 0.0) in cap.set_calls
    assert (cv2.CAP_PROP_AUTO_EXPOSURE, 0.25) not in cap.set_calls
    assert (cv2.CAP_PROP_EXPOSURE, -5.0) in cap.set_calls
    log = capsys.readouterr().out
    assert 'backend=MSMF(1400)' in log
    assert 'set_return=True' in log
    assert 'readback=-6.0' in log


def test_exposure_set_failure_is_logged(monkeypatch, capsys):
    cap = _FakeCapture()
    original_set = cap.set

    def fail_exposure(prop, value):
        if prop == cv2.CAP_PROP_EXPOSURE:
            cap.set_calls.append((prop, value))
            return False
        return original_set(prop, value)

    monkeypatch.setattr(cap, 'set', fail_exposure)

    result = _apply_exposure_setting(cap, False, -5.0, context='failure_test')

    assert result['exposure']['set_return'] is False
    log = capsys.readouterr().out
    assert 'ERROR context=failure_test' in log
    assert 'prop=CAP_PROP_EXPOSURE' in log
    assert 'set_return=False' in log


def test_resume_reopen_preserves_selected_backend_and_replays_exposure(monkeypatch):
    import backend.api.source_lifecycle_mixin as lifecycle_module

    opened = []

    def fake_video_capture(index, backend=None):
        cap = _FakeCapture(backend=backend)
        opened.append((index, backend, cap))
        return cap

    monkeypatch.setattr('platform.system', lambda: 'Windows')
    monkeypatch.setattr(lifecycle_module.cv2, 'VideoCapture', fake_video_capture)

    class _Host(LifecycleMixin):
        pass

    host = _Host()
    host.camera_index = 1
    host.width = 1280
    host.height = 720
    host.fps = 60
    host._camera_backend = cv2.CAP_MSMF
    host._auto_exposure = False
    host._exposure_value = -5.0
    host.capture = None

    assert host._reopen_camera() is True
    assert opened[0][0:2] == (1, cv2.CAP_MSMF)
    assert host._camera_backend == cv2.CAP_MSMF
    assert (cv2.CAP_PROP_AUTO_EXPOSURE, 0.0) in host.capture.set_calls
    assert (cv2.CAP_PROP_EXPOSURE, -5.0) in host.capture.set_calls


def test_capture_reconnect_replays_exposure_contract_is_present():
    source = (Path(__file__).resolve().parents[1] / 'backend' / 'api' /
              'source_capture_loop_mixin.py').read_text(encoding='utf-8')
    camera_branch = source[source.index("elif self.source_type == 'camera':"):]
    assert "context='capture_reconnect'" in camera_branch
    assert "getattr(self, '_auto_exposure', True)" in camera_branch
    assert "getattr(self, '_exposure_value', -6.0)" in camera_branch


def test_msmf_hardware_transforms_are_disabled_before_cv2_import():
    """MSMF HW transforms make this UVC camera renegotiate for ~8s per set()."""
    source = (Path(__file__).resolve().parents[1] / 'backend' /
              'main.py').read_text(encoding='utf-8')
    bootstrap = (
        '_bootstrap_os.environ.setdefault('
        '"OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")'
    )

    assert bootstrap in source
    assert source.index(bootstrap) < source.index('import cv2')


def test_monitor_localstorage_restore_forwards_exposure_fields():
    source = (Path(__file__).resolve().parents[1] / 'frontend' / 'src' / 'views' /
              'Monitor' / 'index.vue').read_text(encoding='utf-8')
    restore = source[source.index("console.log('[AutoRestore]"):]
    camera_start = restore[restore.index("api.post('/source/camera/start'"):]
    camera_start = camera_start[:camera_start.index('});')]
    assert 'auto_exposure: cameraSettings.autoExposure !== false' in camera_start
    assert "typeof cameraSettings.exposureValue === 'number'" in camera_start
