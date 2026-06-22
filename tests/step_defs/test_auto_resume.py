"""开机自动恢复检测开关 BDD step 实现。

覆盖两层:
  1. HTTP 层: GET/PUT /api/v1/workstations/auto-resume 读写 + 默认值。
  2. 启动逻辑层: auto_restore_video_sources() 按开关决定是否自动开始检测。
     用 monkeypatch 注入假通道, 避免真实视频源 / 真模型 / 3.5s sleep。
"""
from __future__ import annotations

from pytest_bdd import scenarios, given, when, then, parsers


scenarios("../features/auto_resume.feature")


def _to_bool(s: str) -> bool:
    return str(s).strip().lower() in ("true", "1", "yes", "on")


# ============================================================
# 假通道 — 模拟一个已运行、已加载模型、未在检测的工位
# ============================================================
class _FakeMgr:
    def __init__(self):
        self.is_running = True       # 已起源, 跳过 start_camera 等
        self.model = object()        # 模型已加载 (非 None)
        self.is_detecting = False
        self.device = "auto"
        self.start_detection_calls = 0

    def start_detection(self):
        self.start_detection_calls += 1
        self.is_detecting = True


# ============================================================
# HTTP 场景
# ============================================================
@when("我 GET /api/v1/workstations/auto-resume")
def when_get_auto_resume(ctx, client):
    ctx["resp"] = client.get("/api/v1/workstations/auto-resume")


@when(parsers.parse("我 PUT /api/v1/workstations/auto-resume 字段 enabled={val}"))
def when_put_auto_resume(ctx, client, val):
    ctx["resp"] = client.put(
        "/api/v1/workstations/auto-resume",
        json={"enabled": _to_bool(val)},
    )


@then(parsers.parse("响应状态应为 {code:d}"))
def then_status(ctx, code):
    assert ctx["resp"].status_code == code, \
        f"期望 {code}, got {ctx['resp'].status_code}, body={ctx['resp'].text[:300]}"


@then(parsers.parse("auto-resume 返回 enabled={val}"))
def then_inline_enabled(ctx, val):
    body = ctx["resp"].json()
    assert body.get("enabled") is _to_bool(val), f"enabled={body.get('enabled')}"


@then(parsers.parse("GET /api/v1/workstations/auto-resume 应返回 enabled={val}"))
def then_get_enabled(ctx, client, val):
    resp = client.get("/api/v1/workstations/auto-resume")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("enabled") is _to_bool(val), f"enabled={body.get('enabled')}"


# ============================================================
# 启动逻辑场景
# ============================================================
def _arrange_startup(ctx, monkeypatch, enabled: bool):
    """装配启动恢复场景。was_detecting 由前置 given 决定 (默认 False)。"""
    from backend.api.channel_manager import channel_manager

    fake = _FakeMgr()
    ctx["fake_mgr"] = fake
    was_detecting = ctx.get("was_detecting", False)

    # source_type 用未知值, 视频源恢复段不匹配任何 start_*; is_running=True 也会先跳过
    sources = {"0": {"source_type": "synthetic",
                     "was_detecting": was_detecting, "gpu_device": "auto"}}
    monkeypatch.setattr(channel_manager, "get_channel_sources", lambda: sources)
    monkeypatch.setattr(channel_manager, "channels", {0: fake}, raising=False)
    monkeypatch.setattr(channel_manager, "get_auto_resume_config",
                        lambda: {"enabled": enabled})
    # 跳过启动逻辑里的 0.5s + 3s 等待
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *a, **k: None)


@given("上次关机时通道正在检测")
def given_last_detecting(ctx):
    ctx["was_detecting"] = True


@given("上次关机时通道未在检测")
def given_last_idle(ctx):
    ctx["was_detecting"] = False


@given("开机自动恢复检测开关已关闭")
def given_disabled(ctx, monkeypatch):
    _arrange_startup(ctx, monkeypatch, enabled=False)


@given("开机自动恢复检测开关已开启")
def given_enabled(ctx, monkeypatch):
    _arrange_startup(ctx, monkeypatch, enabled=True)


@when("后端执行启动自动恢复")
def when_run_restore(ctx):
    from backend.main import auto_restore_video_sources
    auto_restore_video_sources()


@then("不应有任何通道被自动开始检测")
def then_no_detection(ctx):
    assert ctx["fake_mgr"].start_detection_calls == 0, \
        f"开关关闭却调用了 start_detection {ctx['fake_mgr'].start_detection_calls} 次"


@then("应执行自动开始检测流程")
def then_detection_started(ctx):
    assert ctx["fake_mgr"].start_detection_calls >= 1, \
        "开关开启却未调用 start_detection"
