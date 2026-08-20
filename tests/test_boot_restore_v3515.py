"""v3.51.5 开机恢复三修复的后端单测。

现场背景 (2026-08-15 捷昌 B 站):
  1. 恢复全程 30s+, 工人点"停止/待机"被重试轮/收尾兜底轮无脑顶掉 ("很难停下来");
  2. 开机先装原始 .pt 再被前端按项目格式换装 TensorRT 引擎, 同一模型装两遍。

对应修复 (backend/main.py):
  - _restore_detection_pass 多轮之间识别用户手动停止 (源没断但检测停了 → 否决);
  - _resolve_startup_model_path 按项目 model_format 直接解析转换产物。
"""
import threading
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 辅助: 最小假通道 manager
# ---------------------------------------------------------------------------

class FakeMgr:
    def __init__(self):
        self.is_running = True
        self.is_detecting = False
        self.model = object()
        self._thread = threading.Thread(target=lambda: None)  # 仅当身份标识用
        self.start_calls = 0

    def start_detection(self):
        self.start_calls += 1
        self.is_detecting = True


def _run_restore(monkeypatch, mgrs: dict, sources: dict, sleep_hook=None):
    """驱动 auto_restore_video_sources 走完三轮 (sleep 全部打桩为 0)。

    sleep_hook(seconds): 每次 time.sleep 调用时回调, 用于在"轮与轮之间"
    注入外部事件 (用户点待机 / 激活流程重启源), 贴近真实时序。
    """
    import backend.main as bm

    fake_cm = types.SimpleNamespace(
        channels=mgrs,
        get_channel_sources=lambda: sources,
        get_auto_resume_config=lambda: {"enabled": True},
    )
    fake_module = types.SimpleNamespace(channel_manager=fake_cm)
    monkeypatch.setitem(
        __import__('sys').modules, 'backend.api.channel_manager', fake_module)

    def _fake_sleep(seconds):
        if sleep_hook:
            sleep_hook(seconds)

    with patch('time.sleep', side_effect=_fake_sleep):
        bm.auto_restore_video_sources()


# ---------------------------------------------------------------------------
# 修复 C: 用户手动停止/待机否决后续自动开始轮
# ---------------------------------------------------------------------------

def test_user_stop_vetoes_later_passes(monkeypatch):
    """首轮拉起后用户停了 (源没断) → 重试/收尾轮不得再开。"""
    mgr = FakeMgr()

    def user_presses_standby(seconds):
        # 首轮之后的轮间 sleep: 用户点了待机 — 检测停, 采集线程不变
        if mgr.start_calls >= 1 and mgr.is_detecting:
            mgr.is_detecting = False

    _run_restore(monkeypatch, {0: mgr}, {"0": {"source_type": "camera"}},
                 sleep_hook=user_presses_standby)

    assert mgr.start_calls == 1, \
        f"用户手动停止后仍被自动开始 {mgr.start_calls} 次 (期望仅首轮 1 次)"


def test_source_bounce_still_recovers(monkeypatch):
    """激活项目把源整停再拉起 (采集线程换新) → 兜底轮照常补开检测。"""
    mgr = FakeMgr()
    bounced = {"done": False}

    def activation_bounces_source(seconds):
        if mgr.start_calls >= 1 and not bounced["done"]:
            # 激活流程: 源停掉再拉起 — 检测停 + 采集线程换成新的一根
            mgr.is_detecting = False
            mgr._thread = threading.Thread(target=lambda: None)
            bounced["done"] = True

    _run_restore(monkeypatch, {0: mgr}, {"0": {"source_type": "camera"}},
                 sleep_hook=activation_bounces_source)

    assert mgr.start_calls == 2, \
        f"源断过重启后应被兜底轮补开 (期望 2 次, 实际 {mgr.start_calls})"


def test_untouched_channel_keeps_detecting(monkeypatch):
    """正常通道: 首轮开了就一直在检测, 后续轮零动作。"""
    mgr = FakeMgr()
    _run_restore(monkeypatch, {0: mgr}, {"0": {"source_type": "camera"}})
    assert mgr.start_calls == 1
    assert mgr.is_detecting


# ---------------------------------------------------------------------------
# 修复 A: 开机按项目 model_format 解析转换产物
# ---------------------------------------------------------------------------

def _fake_model(mid=33, path="/models/orig.pt"):
    m = MagicMock()
    m.id = mid
    m.name = "m"
    m.file_path = path
    return m


def test_resolve_pytorch_returns_original():
    from backend.main import _resolve_startup_model_path
    model = _fake_model()
    assert _resolve_startup_model_path(MagicMock(), model, "pytorch_fp32") \
        == "/models/orig.pt"
    assert _resolve_startup_model_path(MagicMock(), model, None) \
        == "/models/orig.pt"


def test_resolve_tensorrt_uses_converted(tmp_path):
    from backend.main import _resolve_startup_model_path
    engine = tmp_path / "33_tensorrt_fp16_sm_86.engine"
    engine.write_bytes(b"x")

    conv = MagicMock()
    conv.status = "ready"
    conv.file_path = str(engine)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = conv

    with patch('backend.api.models._get_gpu_info',
               return_value=(True, "RTX 3070", "sm_86")):
        got = _resolve_startup_model_path(db, _fake_model(), "tensorrt_fp16")
    assert got == str(engine)


def test_resolve_missing_conversion_falls_back():
    from backend.main import _resolve_startup_model_path
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    with patch('backend.api.models._get_gpu_info',
               return_value=(True, "RTX 3070", "sm_86")):
        got = _resolve_startup_model_path(db, _fake_model(), "tensorrt_fp16")
    assert got == "/models/orig.pt"


def test_load_fallback_retries_original():
    from backend.main import _load_channel_model_with_fallback
    model = _fake_model()
    cm = MagicMock()
    cm.load_model_for_channel.side_effect = [False, True]  # 引擎失败 → .pt 成功
    ok = _load_channel_model_with_fallback(cm, 0, model, "/conv/33.engine", "auto")
    assert ok
    assert cm.load_model_for_channel.call_count == 2
    assert cm.load_model_for_channel.call_args_list[1][0][1] == "/models/orig.pt"
