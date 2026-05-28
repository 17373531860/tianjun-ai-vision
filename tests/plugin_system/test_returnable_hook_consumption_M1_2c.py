"""M1.2c 业务侧消费 event_fire.suppress_alarm

客户视角叙事:
  M1.2b 让 pre_cycle_end.override_result 和 step_change.warn 都能被业务侧消费, 但
  客户需求 2 "步骤级 NG 不报红" 还差最后一块: 让插件能抑制本次 alarm 触发.

  改进前 (M1.2a / M1.2b, v3.13 中间状态):
    fire_plugin_hook("event_fire", ...) 在 _trigger_event 末尾 fire, 但 alarm
    已经在 274 行触发 — suppress_alarm 来不及作用.
    所以 RETURNABLE_HOOK_FIELDS["event_fire"] = set() (空白名单), 即使插件返回
    suppress_alarm 也被静默丢弃.

  改进后 (M1.2c, 本测试守护):
    - _trigger_event 重排尾部: events_log → _pending_ack → event_fire hook →
      resolve suppress → [alarm?] → router → _last_event_time
    - 抽 _resolve_event_fire_suppress_alarm 纯函数, 严格 ``is True`` 判定
    - 抽 _dispatch_event_alarm 私有方法封装 alarm 触发
    - RETURNABLE_HOOK_FIELDS["event_fire"] = {"suppress_alarm"}
    - 安全侧默认: 异常 / 非 dict / 缺字段 / 非 bool truthy → 不抑制

  无插件场景行为不变 (基线测试 test_trigger_event_baseline_M1_2c.py 已锁定).

测试策略:
  纯函数 _resolve_event_fire_suppress_alarm 8 个 + 白名单契约 2 个 +
  静态扫描 5 个 + e2e fire→resolve→suppress 5 个 + 安全侧默认 2 个 = 22 个.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


REPO_ROOT = Path(__file__).resolve().parents[2]
EVENT_TRIGGER_FILE = REPO_ROOT / "backend" / "api" / "source_event_trigger_mixin.py"


# ============================================================
# A. _resolve_event_fire_suppress_alarm 纯函数契约
# ============================================================


def _resolve_suppress():
    from backend.api.source_event_trigger_mixin import _resolve_event_fire_suppress_alarm
    return _resolve_event_fire_suppress_alarm


def test_resolve_empty_dict_returns_false():
    """plugin_result={} (无 active 插件 / 无 handler) → False."""
    assert _resolve_suppress()({}) is False


def test_resolve_non_dict_returns_false():
    """plugin_result 非 dict (None / 字符串 / 列表 / 数字) → False (容错)."""
    fn = _resolve_suppress()
    assert fn(None) is False
    assert fn("xxx") is False
    assert fn([]) is False
    assert fn(42) is False


def test_resolve_explicit_true_returns_true():
    """``{"suppress_alarm": True}`` → True (唯一抑制路径)."""
    assert _resolve_suppress()({"suppress_alarm": True}) is True


def test_resolve_explicit_false_returns_false():
    """``{"suppress_alarm": False}`` → False."""
    assert _resolve_suppress()({"suppress_alarm": False}) is False


def test_resolve_missing_field_returns_false():
    """字段不存在 → False (缺省 == 不抑制)."""
    assert _resolve_suppress()({"other_field": True}) is False


def test_resolve_truthy_non_bool_returns_false_strict_is_true():
    """严格 ``is True`` — 非 bool truthy 值不算抑制.

    防客户插件传 ``1`` / ``"on"`` / ``[True]`` 等被误识为抑制 — 必须显式 ``True`` 才生效.
    这与 M1.2b override_result 的"严格枚举 OK/NG"理念一致.
    """
    fn = _resolve_suppress()
    assert fn({"suppress_alarm": 1}) is False
    assert fn({"suppress_alarm": "true"}) is False
    assert fn({"suppress_alarm": "True"}) is False
    assert fn({"suppress_alarm": "yes"}) is False
    assert fn({"suppress_alarm": [True]}) is False
    assert fn({"suppress_alarm": {"value": True}}) is False


def test_resolve_none_value_returns_false():
    """``{"suppress_alarm": None}`` → False (容错)."""
    assert _resolve_suppress()({"suppress_alarm": None}) is False


def test_resolve_other_returnable_fields_ignored():
    """同时给 suppress_alarm + 其他字段: 只看 suppress_alarm, 其他不影响."""
    fn = _resolve_suppress()
    assert fn({"suppress_alarm": True, "extra_data": "x"}) is True
    assert fn({"suppress_alarm": False, "extra_data": "x"}) is False
    assert fn({"extra_data": "x", "tags": ["y"]}) is False


# ============================================================
# B. 白名单契约 — RETURNABLE_HOOK_FIELDS["event_fire"]
# ============================================================


def test_event_fire_whitelist_contains_suppress_alarm():
    """白名单契约: ``event_fire`` 字段集合 == ``{"suppress_alarm"}``.

    改字段 = 升 plugin SDK 主版本, 必须同步:
    - RFC 09 §4.3 白名单表
    - manifest schema (capabilities/returnable_fields 节)
    - 本测试
    """
    from backend.plugin_system.hook_dispatch import RETURNABLE_HOOK_FIELDS
    expected = {"suppress_alarm"}
    assert RETURNABLE_HOOK_FIELDS["event_fire"] == expected, (
        f"event_fire 白名单变了: {RETURNABLE_HOOK_FIELDS['event_fire']} != {expected}. "
        f"改白名单 = 升 plugin SDK, 必须同步 RFC 09 + manifest schema."
    )


def test_merge_handler_results_filters_non_whitelist_fields():
    """非白名单字段被 ``_merge_handler_results`` 丢弃 + 走 audit warning."""
    from backend.plugin_system.hook_dispatch import _merge_handler_results

    results = [{"suppress_alarm": True, "secret_field": "leak"}]
    merged = _merge_handler_results("event_fire", results)
    assert merged == {"suppress_alarm": True}
    assert "secret_field" not in merged


# ============================================================
# C. 静态扫描 — _trigger_event 重构后顺序契约
# ============================================================


def _trigger_event_body() -> str:
    src = EVENT_TRIGGER_FILE.read_text(encoding="utf-8")
    m = re.search(
        r'def _trigger_event\(self, event_id.*?\n(.+?)(?=\n    def )',
        src,
        re.DOTALL,
    )
    assert m, "_trigger_event 方法消失"
    return m.group(1)


def test_dispatch_event_alarm_method_exists():
    """``_dispatch_event_alarm`` 私有方法必须存在 (M1.2c 抽出)."""
    from backend.api.source_event_trigger_mixin import EventTriggerMixin
    assert hasattr(EventTriggerMixin, "_dispatch_event_alarm"), (
        "_dispatch_event_alarm 方法消失 — M1.2c 契约破"
    )


def test_resolve_event_fire_suppress_alarm_function_exists():
    """模块级 ``_resolve_event_fire_suppress_alarm`` 纯函数必须存在."""
    from backend.api.source_event_trigger_mixin import _resolve_event_fire_suppress_alarm
    assert callable(_resolve_event_fire_suppress_alarm)


def test_event_fire_hook_called_before_dispatch_event_alarm():
    """关键顺序: ``fire_plugin_hook("event_fire"...)`` 必须在 ``_dispatch_event_alarm`` 之前.

    这是 M1.2c 重构的核心契约 — suppress_alarm 才能来得及作用.
    """
    body = _trigger_event_body()
    hook_idx = body.find('fire_plugin_hook("event_fire"')
    dispatch_idx = body.find('self._dispatch_event_alarm(')

    assert hook_idx > 0, "fire_plugin_hook event_fire 调用消失"
    assert dispatch_idx > 0, "_dispatch_event_alarm 调用消失"
    assert hook_idx < dispatch_idx, (
        f"M1.2c 顺序违反: fire_plugin_hook (idx={hook_idx}) 必须在 "
        f"_dispatch_event_alarm (idx={dispatch_idx}) 之前. "
        f"否则 suppress_alarm 来不及作用."
    )


def test_dispatch_event_alarm_guarded_by_suppress_check():
    """``_dispatch_event_alarm`` 必须被 ``if not suppress_alarm:`` 守门."""
    body = _trigger_event_body()
    # 找 if not suppress_alarm: 紧跟 self._dispatch_event_alarm
    m = re.search(
        r'if\s+not\s+suppress_alarm\s*:\s*\n\s*self\._dispatch_event_alarm\(',
        body,
    )
    assert m, (
        "_dispatch_event_alarm 调用没被 `if not suppress_alarm:` 守门 — "
        "M1.2c suppress_alarm 消费失效"
    )


def test_anchor_still_after_alarm_and_router():
    """v3.10.1 契约不变: ``_last_event_time`` 仍在 alarm + router 之后更新.

    这是 settle_dedup 冷却窗口的核心 — 不能跟 hook 一起前移.
    """
    body = _trigger_event_body()
    dispatch_idx = body.find('self._dispatch_event_alarm(')
    router_idx = body.find('router.trigger_event(')
    anchor_idx = body.find('self._last_event_time = time.time()')

    assert dispatch_idx > 0 and router_idx > 0 and anchor_idx > 0
    assert dispatch_idx < anchor_idx, (
        f"_last_event_time (idx={anchor_idx}) 必须在 _dispatch_event_alarm "
        f"(idx={dispatch_idx}) 之后 — v3.10.1 settle_dedup 契约"
    )
    assert router_idx < anchor_idx, (
        f"_last_event_time 必须在 router.trigger_event 之后"
    )


# ============================================================
# D. e2e — fire_plugin_hook → resolve → suppress 链路
# ============================================================


class _FakePluginRegistry:
    """模仿 PluginRegistry 暴露 .hooks 让 fire_plugin_hook 走真 reg."""
    def __init__(self, hooks_registry):
        self.hooks = hooks_registry


def _install_fake_registry(monkeypatch, hooks_registry):
    from backend.plugin_system import manager as mgr_mod

    class _FakePM:
        registry = _FakePluginRegistry(hooks_registry)
    monkeypatch.setattr(mgr_mod, "plugin_manager", _FakePM())


def test_e2e_suppress_alarm_true_fires_through_pipeline(monkeypatch):
    """handler 返回 ``{"suppress_alarm": True}`` → fire 返聚合 → resolve True."""
    from backend.plugin_system.registry import HooksRegistry
    from backend.plugin_system import hook_dispatch as hd

    reg = HooksRegistry(customer_code="acme-suppress")
    reg.register(
        "event_fire", "post_event", "post",
        priority=100,
        handler=lambda ctx: {"suppress_alarm": True},
    )
    _install_fake_registry(monkeypatch, reg)

    result = hd.fire_plugin_hook("event_fire", "post_event", "post", {
        "channel_id": 0,
        "event_id": 2,
        "event_kind": "NG",
    })
    assert result == {"suppress_alarm": True}

    fn = _resolve_suppress()
    assert fn(result) is True


def test_e2e_suppress_alarm_false_does_not_suppress(monkeypatch):
    """handler 返回 ``{"suppress_alarm": False}`` → 不抑制."""
    from backend.plugin_system.registry import HooksRegistry
    from backend.plugin_system import hook_dispatch as hd

    reg = HooksRegistry(customer_code="acme-nofalse")
    reg.register(
        "event_fire", "post_event", "post",
        priority=100,
        handler=lambda ctx: {"suppress_alarm": False},
    )
    _install_fake_registry(monkeypatch, reg)

    result = hd.fire_plugin_hook("event_fire", "post_event", "post", {})
    assert result == {"suppress_alarm": False}

    fn = _resolve_suppress()
    assert fn(result) is False


def test_e2e_handler_exception_default_safe_no_suppress(monkeypatch):
    """handler 抛错 → fire 返 {} → resolve False (安全侧默认不抑制).

    客户机现场 alarm 必须照常触发, 防客户插件 bug 导致漏报警.
    """
    from backend.plugin_system.registry import HooksRegistry
    from backend.plugin_system import hook_dispatch as hd

    def buggy_handler(ctx):
        raise RuntimeError("插件代码 bug")

    reg = HooksRegistry(customer_code="acme-bug")
    reg.register("event_fire", "post_event", "post", priority=100, handler=buggy_handler)
    _install_fake_registry(monkeypatch, reg)

    result = hd.fire_plugin_hook("event_fire", "post_event", "post", {})
    # HooksRegistry.fire 给异常 handler 返 _error dict, _merge_handler_results 跳过
    assert result == {}

    fn = _resolve_suppress()
    assert fn(result) is False


def test_e2e_priority_high_overrides_low(monkeypatch):
    """两个 handler, priority 大的覆盖小的: 高 prio True 覆盖低 prio False."""
    from backend.plugin_system.registry import HooksRegistry
    from backend.plugin_system import hook_dispatch as hd

    reg = HooksRegistry(customer_code="acme-prio-supp")
    reg.register(
        "event_fire", "post_event", "post",
        priority=10,
        handler=lambda ctx: {"suppress_alarm": False},
    )
    reg.register(
        "event_fire", "post_event", "post",
        priority=100,  # 大覆盖小
        handler=lambda ctx: {"suppress_alarm": True},
    )
    _install_fake_registry(monkeypatch, reg)

    result = hd.fire_plugin_hook("event_fire", "post_event", "post", {})
    assert result == {"suppress_alarm": True}

    fn = _resolve_suppress()
    assert fn(result) is True


def test_e2e_priority_high_false_overrides_low_true(monkeypatch):
    """反向: 高 prio False 覆盖低 prio True (priority 大的总赢)."""
    from backend.plugin_system.registry import HooksRegistry
    from backend.plugin_system import hook_dispatch as hd

    reg = HooksRegistry(customer_code="acme-prio-supp-rev")
    reg.register(
        "event_fire", "post_event", "post",
        priority=10,
        handler=lambda ctx: {"suppress_alarm": True},
    )
    reg.register(
        "event_fire", "post_event", "post",
        priority=100,
        handler=lambda ctx: {"suppress_alarm": False},
    )
    _install_fake_registry(monkeypatch, reg)

    result = hd.fire_plugin_hook("event_fire", "post_event", "post", {})
    assert result == {"suppress_alarm": False}

    fn = _resolve_suppress()
    assert fn(result) is False


# ============================================================
# E. e2e — 在 fake VSM 上跑真 _trigger_event, 验证 alarm 真被抑制
# ============================================================


def _make_fake_vsm_for_e2e(monkeypatch, alarm_recorder: list):
    """造能跑 _trigger_event 的 fake VSM, 拦 alarm_router.trigger_alarm 调用."""
    from backend.api.source_event_trigger_mixin import EventTriggerMixin
    from backend.api import alarm as alarm_mod

    class _FakeVSM(EventTriggerMixin):
        pass

    vsm = _FakeVSM()
    vsm.project_config = {
        'pipeline_config': {},
        'events_config': [
            {'id': 1, 'name': 'OK', 'actions': []},
            {'id': 2, 'name': 'NG', 'actions': []},
        ],
        'logic_mode': 'sequential',
        'steps_config': [],
    }
    vsm._pending_ack = False
    vsm._last_event_time = 0.0
    vsm._last_ng_time = 0
    vsm.cycle_start_time = None
    vsm.cycle_start_frame_pos = 0
    vsm.cycle_times = []
    vsm.ng_cycle_times = []
    vsm.current_cycle_id = 100
    vsm.channel_id = 0
    vsm._mes_hook = None
    vsm.counters = {}
    vsm.current_cycle_steps = []
    vsm.ng_step_cycle_counts = {}
    vsm.events_log = []
    vsm._event_seq = 0
    vsm._router = None

    vsm.end_cycle = lambda **kw: None
    vsm._discard_empty_cycle = lambda: None
    vsm._persist_counters = lambda: None
    vsm._compute_duration_sec = lambda *a, **kw: 1.0
    vsm._video_frame_pos = lambda: 0

    monkeypatch.setattr(
        alarm_mod.alarm_router,
        'trigger_alarm',
        lambda et, channel_id=0: alarm_recorder.append((et, channel_id)),
    )
    return vsm


def test_e2e_suppress_alarm_true_alarm_router_not_called(monkeypatch):
    """端到端: 注册 handler 返 True → _trigger_event 跑完 → alarm_router 0 次调用.

    这是 M1.2c 的核心验收 — 客户需求 2 步骤级 NG 不报红.
    """
    from backend.plugin_system.registry import HooksRegistry

    reg = HooksRegistry(customer_code="acme-e2e-supp")
    reg.register(
        "event_fire", "post_event", "post",
        priority=100,
        handler=lambda ctx: {"suppress_alarm": True},
    )
    _install_fake_registry(monkeypatch, reg)

    alarm_calls = []
    vsm = _make_fake_vsm_for_e2e(monkeypatch, alarm_calls)

    ret = vsm._trigger_event(event_id=2, reason="缺少: [step1]")

    assert ret is True
    assert len(alarm_calls) == 0, (
        f"suppress_alarm=True 时 alarm_router.trigger_alarm 不能被调用. "
        f"实际调了 {len(alarm_calls)} 次: {alarm_calls}"
    )
    # _last_event_time 仍更新 (anchor 不受 suppress 影响, 防 settle_dedup 失效)
    assert vsm._last_event_time > 0


def test_e2e_suppress_alarm_false_alarm_still_triggers(monkeypatch):
    """端到端: handler 返 False → alarm 照常触发 1 次."""
    from backend.plugin_system.registry import HooksRegistry

    reg = HooksRegistry(customer_code="acme-e2e-nosup")
    reg.register(
        "event_fire", "post_event", "post",
        priority=100,
        handler=lambda ctx: {"suppress_alarm": False},
    )
    _install_fake_registry(monkeypatch, reg)

    alarm_calls = []
    vsm = _make_fake_vsm_for_e2e(monkeypatch, alarm_calls)

    vsm._trigger_event(event_id=2, reason="x")

    assert len(alarm_calls) == 1
    assert alarm_calls[0] == ('event2', 0)


def test_e2e_handler_no_suppress_field_alarm_triggers(monkeypatch):
    """handler 返其他字段不含 suppress_alarm → alarm 照常触发 (字段缺省 == False)."""
    from backend.plugin_system.registry import HooksRegistry

    reg = HooksRegistry(customer_code="acme-e2e-other")
    reg.register(
        "event_fire", "post_event", "post",
        priority=100,
        handler=lambda ctx: {"extra_data": "x"},  # 不在白名单, 被丢弃
    )
    _install_fake_registry(monkeypatch, reg)

    alarm_calls = []
    vsm = _make_fake_vsm_for_e2e(monkeypatch, alarm_calls)

    vsm._trigger_event(event_id=2, reason="x")

    assert len(alarm_calls) == 1


def test_e2e_handler_exception_alarm_still_triggers_safe_default(monkeypatch):
    """端到端安全侧默认: handler 抛错 → alarm 仍触发 (宁可误报警也不漏报警)."""
    from backend.plugin_system.registry import HooksRegistry

    def buggy(ctx):
        raise RuntimeError("插件 bug")

    reg = HooksRegistry(customer_code="acme-e2e-bug")
    reg.register("event_fire", "post_event", "post", priority=100, handler=buggy)
    _install_fake_registry(monkeypatch, reg)

    alarm_calls = []
    vsm = _make_fake_vsm_for_e2e(monkeypatch, alarm_calls)

    ret = vsm._trigger_event(event_id=2, reason="x")
    assert ret is True
    assert len(alarm_calls) == 1, (
        "handler 抛错时 alarm 必须仍触发 (安全侧默认), "
        f"实际 {len(alarm_calls)} 次"
    )


def test_e2e_no_active_plugin_alarm_triggers(monkeypatch):
    """无 active 插件 (registry is None) → alarm 照常触发 (与重构前等价)."""
    from backend.plugin_system import manager as mgr_mod

    class _FakePM:
        registry = None
    monkeypatch.setattr(mgr_mod, "plugin_manager", _FakePM())

    alarm_calls = []
    vsm = _make_fake_vsm_for_e2e(monkeypatch, alarm_calls)

    vsm._trigger_event(event_id=2, reason="x")

    assert len(alarm_calls) == 1
    assert alarm_calls[0] == ('event2', 0)


# ============================================================
# F. 签名稳定性
# ============================================================


def test_resolve_suppress_signature_locked():
    """``_resolve_event_fire_suppress_alarm`` 签名锁定 (改签名 = 升 plugin SDK)."""
    import inspect
    fn = _resolve_suppress()
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    assert params == ["plugin_result"]


def test_dispatch_event_alarm_signature_locked():
    """``_dispatch_event_alarm`` 签名锁定."""
    import inspect
    from backend.api.source_event_trigger_mixin import EventTriggerMixin
    sig = inspect.signature(EventTriggerMixin._dispatch_event_alarm)
    params = list(sig.parameters.keys())
    assert params == ["self", "current_event_id"]
