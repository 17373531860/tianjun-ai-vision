"""M1.1 末项: source_status_change hook 接入点 + dedup 契约

客户视角叙事:
  RFC 09 M1.1 首批 5 个 hook 落地后 (cycle_start / step_change / event_fire /
  scan_received / project_activated), source_status_change 因 "VideoSourceManager
  没有统一 status setter" 被延后. v3.13 末项实现:
    - 5 个 lifecycle 公共方法 (pause / resume / standby / resume_inference / stop)
    - capture_loop 3 处异常中断点 (video_ended / reopen_failed / recover_failed)
    - helper _fire_source_status_change 自带 dedup (无变化静默)
    - contextmanager _track_status_change 备用 (本批次不强制使用)

  设计要点:
    - 30+ 处散落的 is_running / is_detecting 写点 (各种 source_type 启动 / init) 不动
    - 只在 lifecycle 公共 API 接 fire 点 — 这是客户真正关心的"工位开始/停止"事件
    - helper 内 dedup: 同状态再赋值 / early return False 路径自动 skip → 防虚报

  测试策略:
    单测纯 helper (不需起 VideoSourceManager) + 5 个 lifecycle 方法静态扫描契约 +
    capture_loop 3 处静态扫描契约 + e2e fake VSM 真消费.

如果 lifecycle 方法有人新加 status 写点忘了配 fire helper, 静态扫描测试会 FAIL.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


REPO_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_FILE = REPO_ROOT / "backend" / "api" / "source_lifecycle_mixin.py"
CAPTURE_LOOP_FILE = REPO_ROOT / "backend" / "api" / "source_capture_loop_mixin.py"


# ============================================================
# A. helper _fire_source_status_change dedup 契约
# ============================================================


def _make_min_lifecycle_host(is_running=False, is_detecting=False, source_type=None):
    """造最小 host 给 _fire_source_status_change 单测用 (不需起 VideoSourceManager)."""
    from backend.api.source_lifecycle_mixin import LifecycleMixin

    class _Host(LifecycleMixin):
        pass

    h = _Host()
    h.is_running = is_running
    h.is_detecting = is_detecting
    h.channel_id = 0
    h.source_type = source_type
    return h


def test_helper_no_change_does_not_fire(monkeypatch):
    """状态前后无变化 → helper 不 fire (即使插件 registry 已装载)."""
    from backend.plugin_system import hook_dispatch as hd

    fire_calls = []
    monkeypatch.setattr(
        hd, 'fire_plugin_hook',
        lambda hook_type, *a, **kw: fire_calls.append(hook_type) or {},
    )

    h = _make_min_lifecycle_host(is_running=True, is_detecting=True)
    # before == after
    h._fire_source_status_change(
        before_running=True, before_detecting=True, reason="pause",
    )

    assert fire_calls == [], (
        f"无变化时不应 fire, 但 fire 了 {fire_calls}. dedup 失效会让 "
        f"insider source.py 各种同状态再赋值都触发 hook, 客户机日志爆炸"
    )


def test_helper_running_changed_fires(monkeypatch):
    """is_running 单字段变化 → fire."""
    from backend.plugin_system import hook_dispatch as hd

    fire_calls = []
    def fake_fire(hook_type, phase, when, ctx):
        fire_calls.append((hook_type, ctx))
        return {}
    monkeypatch.setattr(hd, 'fire_plugin_hook', fake_fire)

    h = _make_min_lifecycle_host(is_running=False, is_detecting=False)
    # 模拟 pause(): 当前是 stopped 状态, 业务调 helper 时已经赋值过了, 这里 before=True
    h.is_running = False
    h.is_detecting = False
    h._fire_source_status_change(
        before_running=True, before_detecting=False, reason="pause",
    )

    assert len(fire_calls) == 1
    hook_type, ctx = fire_calls[0]
    assert hook_type == "source_status_change"
    assert ctx["before"] == {"is_running": True, "is_detecting": False}
    assert ctx["after"] == {"is_running": False, "is_detecting": False}
    assert ctx["reason"] == "pause"
    assert ctx["channel_id"] == 0


def test_helper_detecting_changed_fires(monkeypatch):
    """is_detecting 单字段变化 → fire (standby / resume_inference 场景)."""
    from backend.plugin_system import hook_dispatch as hd

    fire_calls = []
    monkeypatch.setattr(
        hd, 'fire_plugin_hook',
        lambda *a, **kw: fire_calls.append(a) or {},
    )

    h = _make_min_lifecycle_host(is_running=True, is_detecting=False)
    h._fire_source_status_change(
        before_running=True, before_detecting=True, reason="standby",
    )

    assert len(fire_calls) == 1
    ctx = fire_calls[0][3]
    assert ctx["before"] == {"is_running": True, "is_detecting": True}
    assert ctx["after"] == {"is_running": True, "is_detecting": False}


def test_helper_both_changed_fires_once(monkeypatch):
    """双字段同时变化 → fire 一次 (不是两次)."""
    from backend.plugin_system import hook_dispatch as hd

    fire_calls = []
    monkeypatch.setattr(
        hd, 'fire_plugin_hook',
        lambda *a, **kw: fire_calls.append(a) or {},
    )

    h = _make_min_lifecycle_host(is_running=False, is_detecting=False)
    h._fire_source_status_change(
        before_running=True, before_detecting=True, reason="stop",
    )

    assert len(fire_calls) == 1


def test_helper_exception_swallowed(monkeypatch):
    """fire 内部抛异常 → swallow + print, 不影响 lifecycle."""
    from backend.plugin_system import hook_dispatch as hd

    def buggy_fire(*a, **kw):
        raise RuntimeError("插件爆了")
    monkeypatch.setattr(hd, 'fire_plugin_hook', buggy_fire)

    h = _make_min_lifecycle_host(is_running=False, is_detecting=False)
    # 不应抛出, 即使 fire 内异常
    h._fire_source_status_change(
        before_running=True, before_detecting=True, reason="stop",
    )


def test_helper_ctx_source_type_propagated(monkeypatch):
    """ctx 携带 source_type 让插件知道是哪类视频源."""
    from backend.plugin_system import hook_dispatch as hd

    fire_calls = []
    monkeypatch.setattr(
        hd, 'fire_plugin_hook',
        lambda *a, **kw: fire_calls.append(a) or {},
    )

    h = _make_min_lifecycle_host(is_running=False, source_type="hikvision")
    h._fire_source_status_change(
        before_running=True, before_detecting=False, reason="pause",
    )

    ctx = fire_calls[0][3]
    assert ctx["source_type"] == "hikvision"


def test_helper_bool_coercion():
    """非 bool 输入 (truthy / falsy 整数) 转 bool 后再 dedup, 不当作变化."""
    from backend.plugin_system import hook_dispatch as hd
    from unittest.mock import patch

    h = _make_min_lifecycle_host(is_running=1, is_detecting=0)  # 1 / 0 而非 True/False

    fire_calls = []
    with patch.object(hd, 'fire_plugin_hook',
                      lambda *a, **kw: fire_calls.append(a) or {}):
        # before bool(1)=True, after bool(1)=True → 不应 fire
        h._fire_source_status_change(
            before_running=1, before_detecting=0, reason="x",
        )

    assert fire_calls == []


# ============================================================
# B. context manager _track_status_change 行为契约
# ============================================================


def test_context_manager_normal_path_fires_on_exit(monkeypatch):
    """正常返回路径: with 块退出时 helper fire."""
    from backend.plugin_system import hook_dispatch as hd

    fire_calls = []
    monkeypatch.setattr(
        hd, 'fire_plugin_hook',
        lambda *a, **kw: fire_calls.append(a) or {},
    )

    h = _make_min_lifecycle_host(is_running=False, is_detecting=False)
    with h._track_status_change("test_reason"):
        h.is_running = True
        h.is_detecting = True

    assert len(fire_calls) == 1
    ctx = fire_calls[0][3]
    assert ctx["reason"] == "test_reason"
    assert ctx["after"]["is_running"] is True


def test_context_manager_exception_still_fires(monkeypatch):
    """块内抛异常: helper 仍 fire (finally 保证); 异常仍上抛."""
    from backend.plugin_system import hook_dispatch as hd

    fire_calls = []
    monkeypatch.setattr(
        hd, 'fire_plugin_hook',
        lambda *a, **kw: fire_calls.append(a) or {},
    )

    h = _make_min_lifecycle_host(is_running=False)

    with pytest.raises(RuntimeError, match="块内 bug"):
        with h._track_status_change("test_exc"):
            h.is_running = True
            raise RuntimeError("块内 bug")

    assert len(fire_calls) == 1, "异常路径 helper 仍 fire (finally 保证)"


def test_context_manager_no_change_does_not_fire(monkeypatch):
    """块内未改状态: helper dedup → 不 fire."""
    from backend.plugin_system import hook_dispatch as hd

    fire_calls = []
    monkeypatch.setattr(
        hd, 'fire_plugin_hook',
        lambda *a, **kw: fire_calls.append(a) or {},
    )

    h = _make_min_lifecycle_host(is_running=True, is_detecting=True)
    with h._track_status_change("no_op"):
        pass

    assert fire_calls == []


# ============================================================
# C. lifecycle 公共方法静态扫描 — 每个方法必须 fire helper
# ============================================================


def _lifecycle_method_body(method_name: str) -> str:
    """抓出指定 lifecycle 方法的方法体源码.

    严格匹配行首 4 空格的真定义, 防误抓 docstring 内示例的 ``def pause(self)``.
    """
    src = LIFECYCLE_FILE.read_text(encoding="utf-8")
    pattern = rf'\n    def {method_name}\(self.*?\n(.+?)(?=\n    def )'
    m = re.search(pattern, src, re.DOTALL)
    assert m, f"{method_name} 方法消失"
    return m.group(1)


@pytest.mark.parametrize("method_name,reason_pattern", [
    ("pause", "pause"),
    ("resume", "resume"),
    ("standby", "standby"),
    ("resume_inference", "resume_inference"),
    ("stop", "stop"),
])
def test_lifecycle_method_calls_fire_helper(method_name, reason_pattern):
    """5 个 lifecycle 公共方法每个都必须调 _fire_source_status_change.

    如果新增 lifecycle 方法忘了配 fire, 或重构时移除了调用, 本测试 FAIL.
    """
    body = _lifecycle_method_body(method_name)
    assert "self._fire_source_status_change(" in body, (
        f"{method_name} 没调 _fire_source_status_change — M1.1 source_status_change "
        f"hook 接入点漏了. 必须在方法入口录 before, 出口调 helper."
    )
    # reason 字符串得在方法体里 (允许 resume_failed / resume_inference_failed 之类的变体)
    assert f'"{reason_pattern}' in body, (
        f"{method_name} 的 reason 字段不含 '{reason_pattern}' — "
        f"M1.1 ctx reason 枚举契约破"
    )


def test_lifecycle_method_records_before_state():
    """5 个 lifecycle 方法都必须在入口录 _before_running / _before_detecting."""
    for method_name in ("pause", "resume", "standby", "resume_inference", "stop"):
        body = _lifecycle_method_body(method_name)
        assert re.search(
            r'_before_running\s*,\s*_before_detecting\s*=\s*self\.is_running\s*,\s*self\.is_detecting',
            body,
        ), (
            f"{method_name} 没在入口录 (_before_running, _before_detecting) — "
            f"helper 的 before 参数无来源"
        )


def test_resume_early_return_paths_have_fire_call():
    """resume() 的 3 个 early return False 路径都要 fire (resume_failed reason).

    虽然 helper 内 dedup 会 skip (因为 is_running 没变), 但显式 fire 让插件有
    "尝试 resume 失败"的语义信号 (可用于客户机日志 / 监控).
    """
    body = _lifecycle_method_body("resume")
    # 数 "resume_failed" 出现次数, 应 == 3 (对应 3 个 early return False)
    failed_count = body.count('"resume_failed"')
    assert failed_count == 3, (
        f"resume() early return False 路径数 != 3, 实际 {failed_count}. "
        f"修改 early return 时需同步配 _fire_source_status_change reason='resume_failed'"
    )


def test_resume_inference_early_return_paths_have_fire_call():
    """resume_inference() 的 2 个 early return False 路径都要 fire (resume_inference_failed)."""
    body = _lifecycle_method_body("resume_inference")
    failed_count = body.count('"resume_inference_failed"')
    assert failed_count == 2, (
        f"resume_inference() early return False 路径数 != 2, 实际 {failed_count}."
    )


# ============================================================
# D. capture_loop 静态扫描 — 3 处异常中断都要 fire
# ============================================================


def test_capture_loop_three_break_points_fire_helper():
    """capture_loop 3 处 is_running = False 都要 fire helper."""
    src = CAPTURE_LOOP_FILE.read_text(encoding="utf-8")

    for reason in (
        "capture_loop_video_ended",
        "capture_loop_reopen_failed",
        "capture_loop_recover_failed",
    ):
        assert f'"{reason}"' in src, (
            f"capture_loop 没 fire reason='{reason}' — 视频流断开类异常无插件通知"
        )


def test_capture_loop_fire_calls_count_equals_break_points():
    """capture_loop 中 _fire_source_status_change 调用次数 == 3 (对应 3 个 break)."""
    src = CAPTURE_LOOP_FILE.read_text(encoding="utf-8")
    fire_count = src.count("self._fire_source_status_change(")
    assert fire_count == 3, (
        f"capture_loop 中 _fire_source_status_change 调用数 != 3, 实际 {fire_count}. "
        f"如果新增 break 点请同步配 helper"
    )


# ============================================================
# E. e2e — 在 fake VSM 上调真 pause/resume, 验证 hook 真 fire
# ============================================================


def _make_minimal_vsm_for_lifecycle(monkeypatch, fire_recorder):
    """造能跑 pause / standby 的 fake VSM, 其他 lifecycle 依赖全 mock.

    pause / standby 比 resume / resume_inference / stop 依赖少, e2e 用这两个就够.
    """
    from backend.api.source_lifecycle_mixin import LifecycleMixin
    from backend.plugin_system import hook_dispatch as hd

    class _FakeVSM(LifecycleMixin):
        pass

    vsm = _FakeVSM()
    vsm.is_running = True
    vsm.is_detecting = True
    vsm.channel_id = 0
    vsm.source_type = "camera"
    vsm.capture = None
    vsm._thread = None
    vsm.detection_lock = MagicMock()
    vsm.detection_lock.__enter__ = lambda s: None
    vsm.detection_lock.__exit__ = lambda s, *a: None
    vsm.current_detections = []
    vsm._stop_inference_thread = MagicMock()
    vsm._stop_recording_thread = MagicMock()
    vsm._close_all_writers = MagicMock()
    vsm._clear_inference_caches = MagicMock()
    vsm._release_hik_camera = MagicMock()
    vsm._release_hcnet_session = MagicMock()

    # patch alarm_router (pause / standby 用到)
    from backend.api import alarm as alarm_mod
    monkeypatch.setattr(
        alarm_mod.alarm_router, 'stop_idle_light', lambda channel_id=0: None,
    )

    # patch fire_plugin_hook 录调用
    def fake_fire(hook_type, phase, when, ctx):
        fire_recorder.append((hook_type, phase, when, ctx))
        return {}
    monkeypatch.setattr(hd, 'fire_plugin_hook', fake_fire)

    return vsm


def test_e2e_pause_fires_status_change(monkeypatch):
    """e2e: 真调 pause() → hook fire 1 次, reason='pause'."""
    fire_calls = []
    vsm = _make_minimal_vsm_for_lifecycle(monkeypatch, fire_calls)
    assert vsm.is_running is True and vsm.is_detecting is True

    vsm.pause()

    status_changes = [c for c in fire_calls if c[0] == "source_status_change"]
    assert len(status_changes) == 1, (
        f"pause() 应 fire 1 次 source_status_change, 实际 {len(status_changes)}: "
        f"{status_changes}"
    )
    ctx = status_changes[0][3]
    assert ctx["reason"] == "pause"
    assert ctx["before"] == {"is_running": True, "is_detecting": True}
    assert ctx["after"] == {"is_running": False, "is_detecting": False}
    assert ctx["channel_id"] == 0
    assert ctx["source_type"] == "camera"


def test_e2e_pause_when_already_stopped_does_not_fire(monkeypatch):
    """e2e dedup: VSM 已停, 再 pause() → helper 内 dedup → 不 fire."""
    fire_calls = []
    vsm = _make_minimal_vsm_for_lifecycle(monkeypatch, fire_calls)
    vsm.is_running = False
    vsm.is_detecting = False  # 已经停了

    vsm.pause()

    status_changes = [c for c in fire_calls if c[0] == "source_status_change"]
    assert len(status_changes) == 0, (
        "VSM 已停状态再 pause() 不应 fire (dedup), "
        f"实际 fire 了 {len(status_changes)} 次"
    )


def test_e2e_standby_fires_only_detecting_change(monkeypatch):
    """e2e: 真调 standby() → hook fire 1 次, after.is_detecting=False, is_running 不变."""
    fire_calls = []
    vsm = _make_minimal_vsm_for_lifecycle(monkeypatch, fire_calls)
    # standby 只改 is_detecting, is_running 保持 True

    vsm.standby()

    status_changes = [c for c in fire_calls if c[0] == "source_status_change"]
    assert len(status_changes) == 1
    ctx = status_changes[0][3]
    assert ctx["reason"] == "standby"
    assert ctx["before"] == {"is_running": True, "is_detecting": True}
    assert ctx["after"] == {"is_running": True, "is_detecting": False}


# ============================================================
# F. 签名 + ctx 字段集合稳定性
# ============================================================


def test_fire_helper_signature_locked():
    """``_fire_source_status_change`` 签名锁定."""
    import inspect
    from backend.api.source_lifecycle_mixin import LifecycleMixin
    sig = inspect.signature(LifecycleMixin._fire_source_status_change)
    params = list(sig.parameters.keys())
    assert params == ["self", "before_running", "before_detecting", "reason"], (
        f"helper 签名变了: {params}. 改签名 = 升 plugin SDK"
    )


def test_track_context_manager_signature_locked():
    """``_track_status_change`` 签名锁定."""
    import inspect
    from backend.api.source_lifecycle_mixin import LifecycleMixin
    sig = inspect.signature(LifecycleMixin._track_status_change)
    params = list(sig.parameters.keys())
    assert params == ["self", "reason"]


def test_ctx_field_set_locked(monkeypatch):
    """source_status_change ctx 顶层字段集合稳定 (改字段 = 升 plugin SDK)."""
    from backend.plugin_system import hook_dispatch as hd

    fire_calls = []
    monkeypatch.setattr(
        hd, 'fire_plugin_hook',
        lambda *a, **kw: fire_calls.append(a) or {},
    )

    h = _make_min_lifecycle_host(is_running=False, source_type="camera")
    h._fire_source_status_change(
        before_running=True, before_detecting=False, reason="pause",
    )

    ctx = fire_calls[0][3]
    expected_keys = {"channel_id", "before", "after", "reason", "source_type"}
    assert set(ctx.keys()) == expected_keys, (
        f"ctx 顶层字段集合变了: {set(ctx.keys())} != {expected_keys}. "
        f"改字段 = 升 plugin SDK + 同步 RFC 09 §4.2"
    )
    assert set(ctx["before"].keys()) == {"is_running", "is_detecting"}
    assert set(ctx["after"].keys()) == {"is_running", "is_detecting"}
