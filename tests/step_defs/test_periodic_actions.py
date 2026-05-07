"""周期性强制动作 — BDD step 实现。

设计要点：
  - 用一个 StubMixin 模拟 VideoSourceManager 的最小依赖（counters, events_log,
    project_config, channel_id, _persist_counters）— 不需要真正的摄像头/推理管线。
  - 直接调 mixin 的 _apply_periodic_actions / _check_periodic_actions 验证逻辑。
"""
from __future__ import annotations

import re

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from backend.api.source_periodic_actions_mixin import PeriodicActionsMixin


# 加载 feature 文件中的所有场景
scenarios("../features/periodic_actions.feature")


# ============================================================
# Stub host — 模拟 VSM 的最小依赖
# ============================================================
class StubHost(PeriodicActionsMixin):
    """模拟 VSM 的最小依赖。

    重写了持久化方法为 noop —— scenarios 之间不通过磁盘文件串扰，
    持久化的功能性测试单独通过 _shared_storage 字典模拟。
    """

    # 共享存储用于"持久化恢复"的 scenario，由 test 显式控制
    _shared_storage: dict = {}

    def __init__(self, channel_id: int = 0, persist_key: str = None):
        self.channel_id = channel_id
        self.counters: dict = {}
        self.events_log: list = []
        self._event_seq = 0
        self.project_config: dict = {}
        self._persist_key = persist_key  # 设了才走共享存储

    def _persist_counters(self):
        """模拟 VSM 的计数器持久化（noop）"""
        pass

    def _persist_periodic_counters(self):
        if self._persist_key is not None:
            StubHost._shared_storage[self._persist_key] = dict(self._periodic_counters)

    def _restore_periodic_counters(self, config):
        if self._persist_key is not None and self._persist_key in StubHost._shared_storage:
            saved = StubHost._shared_storage[self._persist_key]
            for k, v in saved.items():
                if k in self._periodic_counters:
                    self._periodic_counters[k] = v


def _make_config(rule: dict, project_id: int = 9001, events: list = None):
    """构造一个合法的项目配置 dict（含 steps_config）"""
    return {
        "id": project_id,
        "steps_config": [
            {"id": "s_A", "label": "A", "enabled": True},
            {"id": "s_B", "label": "B", "enabled": True},
            {"id": "s_C", "label": "C", "enabled": True},
            {"id": "s_D", "label": "D", "enabled": True},
            {"id": "s_E", "label": "E", "enabled": True},
        ],
        "events_config": events or [],
        "pipeline_config": {"periodic_actions": [rule]},
    }


# ============================================================
# Background
# ============================================================
@given("一个 PeriodicActionsMixin 实例已经初始化")
def init_host(ctx):
    ctx["host"] = StubHost(channel_id=0)
    ctx["events_called"] = []
    StubHost._shared_storage.clear()


# ============================================================
# Givens
# ============================================================
@given("项目配置不包含 periodic_actions")
def given_no_periodic_actions(ctx):
    ctx["config"] = {
        "id": 9001,
        "steps_config": [{"id": "s_A", "label": "A", "enabled": True}],
        "events_config": [],
        "pipeline_config": {},
    }


@given(parsers.parse("配置一条规则 interval={interval:d} trigger={trigger}"))
def given_simple_rule(ctx, interval, trigger):
    rule = _build_rule(ctx, interval=interval, trigger=trigger)
    ctx["config"] = _make_config(rule)
    _apply_now(ctx)


@given(parsers.parse(
    "配置一条规则 interval={interval:d} trigger={trigger} "
    "due_warning_event_id={evt_id:d}"
))
def given_rule_with_due(ctx, interval, trigger, evt_id):
    rule = _build_rule(ctx, interval=interval, trigger=trigger,
                       due_warning_event_id=evt_id)
    ctx["config"] = _make_config(rule, events=ctx.get("events_config", []))
    ctx["_pending_rule"] = rule  # 等下一步加事件后再 apply


@given(parsers.parse(
    "配置一条规则 interval={interval:d} trigger={trigger} "
    "overdue_event_id={evt_id:d} overdue_repeat={repeat}"
))
def given_rule_with_overdue(ctx, interval, trigger, evt_id, repeat):
    rule = _build_rule(ctx, interval=interval, trigger=trigger,
                       overdue_event_id=evt_id, overdue_repeat=repeat)
    ctx["_pending_rule"] = rule


@given(parsers.parse(
    "配置一条规则 interval={interval:d} trigger={trigger} reset_policy={policy}"
))
def given_rule_with_reset(ctx, interval, trigger, policy):
    rule = _build_rule(ctx, interval=interval, trigger=trigger,
                       reset_policy=policy)
    ctx["config"] = _make_config(rule)
    _apply_now(ctx)


@given(parsers.parse(
    "配置一条规则 interval={interval:d} trigger={trigger} count_basis={basis}"
))
def given_rule_with_basis(ctx, interval, trigger, basis):
    rule = _build_rule(ctx, interval=interval, trigger=trigger,
                       count_basis=basis)
    ctx["config"] = _make_config(rule)
    _apply_now(ctx)


@given(parsers.parse(
    "配置一条规则 interval={interval:d} trigger={trigger} channel_filter={cf}"
))
def given_rule_with_channel_filter(ctx, interval, trigger, cf):
    # cf 形如 "[1,2]"
    channel_filter = [int(x) for x in re.findall(r"\d+", cf)]
    rule = _build_rule(ctx, interval=interval, trigger=trigger,
                       channel_filter=channel_filter)
    ctx["config"] = _make_config(rule)
    _apply_now(ctx)


@given(parsers.parse('events_config 包含 id={evt_id:d} 名称为"{name}"的事件'))
def given_event_added(ctx, evt_id, name):
    ev = {
        "id": evt_id,
        "name": name,
        "actions": [],
        "show_notification": True,
        "toast_id": "ng",
    }
    events = ctx.setdefault("events_config", [])
    events.append(ev)
    if "_pending_rule" in ctx:
        ctx["config"] = _make_config(ctx["_pending_rule"], events=events)
        _apply_now(ctx)
        del ctx["_pending_rule"]
    elif "config" in ctx:
        ctx["config"]["events_config"] = events


@given(parsers.parse("当前通道是 {ch:d}"))
def given_channel(ctx, ch):
    ctx["host"].channel_id = ch


@given(parsers.parse("events_config 中不包含 id={evt_id:d}"))
def given_no_event(ctx, evt_id):
    """noop — 由 _build_rule 引用了不存在的 event_id 即可"""
    if "_pending_rule" in ctx:
        ctx["config"] = _make_config(ctx["_pending_rule"], events=[])
        _apply_now(ctx)
        del ctx["_pending_rule"]


# ----- v3.5.2: run_on_start 规则参数化 given -----
@given(parsers.parse(
    "配置一条规则 interval={interval:d} trigger={trigger} "
    "run_on_start={ros} due_warning_event_id={evt_id:d}"
))
def given_rule_with_run_on_start_due(ctx, interval, trigger, ros, evt_id):
    rule = _build_rule(ctx, interval=interval, trigger=trigger,
                       run_on_start=_str_to_bool(ros),
                       due_warning_event_id=evt_id)
    ctx["_pending_rule"] = rule


@given(parsers.parse(
    "配置一条规则 interval={interval:d} trigger={trigger} "
    "run_on_start={ros} overdue_event_id={evt_id:d}"
))
def given_rule_with_run_on_start_overdue(ctx, interval, trigger, ros, evt_id):
    rule = _build_rule(ctx, interval=interval, trigger=trigger,
                       run_on_start=_str_to_bool(ros),
                       overdue_event_id=evt_id)
    ctx["_pending_rule"] = rule


@given(parsers.parse("配置一条规则 interval={interval:d} trigger={trigger} run_on_start={ros}"))
def given_rule_with_run_on_start_only(ctx, interval, trigger, ros):
    rule = _build_rule(ctx, interval=interval, trigger=trigger,
                       run_on_start=_str_to_bool(ros))
    ctx["config"] = _make_config(rule)
    _apply_now(ctx)


# v3.5.2: 完整参数版 — 同时设 due / overdue / reset_policy
@given(parsers.parse(
    "配置一条规则 interval={interval:d} trigger={trigger} "
    "run_on_start={ros} due_warning_event_id={due_id:d} overdue_event_id={over_id:d} "
    "reset_policy={policy}"
))
def given_rule_with_run_on_start_full(ctx, interval, trigger, ros, due_id, over_id, policy):
    rule = _build_rule(ctx, interval=interval, trigger=trigger,
                       run_on_start=_str_to_bool(ros),
                       due_warning_event_id=due_id,
                       overdue_event_id=over_id,
                       reset_policy=policy)
    ctx["_pending_rule"] = rule


# ============================================================
# Whens
# ============================================================
@when("我应用项目配置")
def when_apply(ctx):
    _apply_now(ctx)


@when(parsers.parse("我连续完成 {n:d} 个不含 E 的 cycle"))
def when_run_cycles(ctx, n):
    for _ in range(n):
        try:
            ctx["host"]._check_periodic_actions(["A", "B", "C", "D"], True)
        except Exception as e:
            ctx.setdefault("exceptions", []).append(e)


@when(parsers.parse("我完成 {n:d} 个不含 E 的 cycle"))
def when_run_n_cycles(ctx, n):
    when_run_cycles(ctx, n)


@when(parsers.parse("我完成 {n:d} 个含 E 的 cycle"))
def when_run_with_e(ctx, n):
    for _ in range(n):
        ctx["host"]._check_periodic_actions(["A", "B", "C", "D", "E"], True)


@when(parsers.parse("我连续完成 {n:d} 个含 E 的 cycle"))
def when_run_n_with_e(ctx, n):
    when_run_with_e(ctx, n)


@when(parsers.parse("我完成 {n:d} 个 NG 的 cycle"))
def when_run_ng(ctx, n):
    for _ in range(n):
        ctx["host"]._check_periodic_actions(["A", "B"], False)


@when(parsers.parse("我完成 {n:d} 个 OK 的 cycle"))
def when_run_ok(ctx, n):
    for _ in range(n):
        ctx["host"]._check_periodic_actions(["A", "B", "C", "D"], True)


# v3.5.2: reset_stats / run_on_start 用例
@when("我调用 reset_stats")
def when_reset_stats(ctx):
    """模拟 source.py:reset_stats() 中针对 _periodic_counters 的那一段。"""
    host = ctx["host"]
    if hasattr(host, "_periodic_counters") and isinstance(host._periodic_counters, dict):
        for rid in list(host._periodic_counters.keys()):
            host._periodic_counters[rid] = 0
        for rule in getattr(host, "_periodic_actions", []) or []:
            rule["last_overdue_count"] = -1
        host._persist_periodic_counters()


@when("我调用 _run_periodic_actions_on_start")
def when_run_on_start(ctx):
    ctx["host"]._run_periodic_actions_on_start()


@when(parsers.parse("完成步骤 {label}"))
def when_complete_single_step(ctx, label):
    """v3.5.2: 模拟 source_step_stats_mixin 在每个步骤完成后调钩子."""
    ctx["host"]._check_periodic_actions_on_first_step(label)


@when("我新建一个 mixin 实例并应用相同配置")
def when_new_instance(ctx):
    # 让前一个 host 用共享存储模拟磁盘
    persist_key = "persist_test_key"
    ctx["host"]._persist_key = persist_key
    ctx["host"]._persist_periodic_counters()  # 写"磁盘"
    new_host = StubHost(channel_id=ctx["host"].channel_id, persist_key=persist_key)
    new_host.project_config = ctx["config"]
    new_host._apply_periodic_actions(ctx["config"])
    ctx["host"] = new_host


# ============================================================
# Thens
# ============================================================
@then("解析后的规则列表应为空")
def then_no_rules(ctx):
    assert getattr(ctx["host"], "_periodic_actions", []) == []


@then("调用 _check_periodic_actions 不应抛任何异常")
def then_check_safe(ctx):
    try:
        ctx["host"]._check_periodic_actions(["A", "B"], True)
    except Exception as e:
        pytest.fail(f"不应抛异常: {e}")


@then(parsers.parse("该规则的 counter 应为 {expected:d}"))
def then_counter_equals(ctx, expected):
    counters = ctx["host"]._periodic_counters
    assert counters, "未解析出任何规则; counter 字典为空"
    rule_id = list(counters.keys())[0]
    actual = counters[rule_id]
    assert actual == expected, f"counter expected={expected} actual={actual}"


@then(parsers.parse("新实例上该规则的 counter 应为 {expected:d}"))
def then_new_instance_counter(ctx, expected):
    """跨实例恢复的断言（与上一个等价，做语义清晰）"""
    then_counter_equals(ctx, expected)


@then(parsers.parse("events_log 应记录到 id 为 {evt_id:d} 的事件"))
def then_event_logged(ctx, evt_id):
    found = [e for e in ctx["host"].events_log if str(e.get("event_id")) == str(evt_id)]
    assert found, f"events_log 中没有 id={evt_id} 的事件; events_log={ctx['host'].events_log}"


@then(parsers.parse("events_log 不应记录到 id 为 {evt_id:d} 的事件"))
def then_event_not_logged(ctx, evt_id):
    found = [e for e in ctx["host"].events_log if str(e.get("event_id")) == str(evt_id)]
    assert not found, (
        f"events_log 中**不应**有 id={evt_id} 的事件, 但找到 {len(found)} 条: {found}"
    )


@then(parsers.parse("id={evt_id:d} 的事件应被触发至少 {n:d} 次"))
def then_event_triggered_at_least(ctx, evt_id, n):
    count = sum(1 for e in ctx["host"].events_log if str(e.get("event_id")) == str(evt_id))
    assert count >= n, f"事件 {evt_id} 触发了 {count} 次，期望 >= {n}"


@then(parsers.parse("id={evt_id:d} 的事件触发次数应不超过 {n:d} 次"))
def then_event_triggered_at_most(ctx, evt_id, n):
    count = sum(1 for e in ctx["host"].events_log if str(e.get("event_id")) == str(evt_id))
    assert count <= n, f"事件 {evt_id} 触发了 {count} 次，期望 <= {n}"


@then("应不抛任何异常")
def then_no_exceptions(ctx):
    excs = ctx.get("exceptions", [])
    assert not excs, f"发生异常: {excs}"


@then(parsers.parse("status 应返回 state={state} remaining={remaining:d} counter={counter:d}"))
def then_status_check(ctx, state, remaining, counter):
    statuses = ctx["host"].get_periodic_actions_status()
    assert len(statuses) == 1
    s = statuses[0]
    assert s["state"] == state, f"state expected={state} actual={s['state']}"
    assert s["remaining"] == remaining, f"remaining expected={remaining} actual={s['remaining']}"
    assert s["counter"] == counter, f"counter expected={counter} actual={s['counter']}"


# ============================================================
# Helpers
# ============================================================
def _build_rule(ctx, interval, trigger, **overrides):
    # trigger 是 step label，需转换为 step_id
    label_to_id = {"A": "s_A", "B": "s_B", "C": "s_C", "D": "s_D", "E": "s_E"}
    rule = {
        "id": ctx.get("rule_id", "r1"),
        "name": "测试规则",
        "enabled": True,
        "trigger_step_ids": [label_to_id.get(trigger, trigger)],
        "interval": interval,
        "count_basis": "all",
        "reset_policy": "always",
        "due_warning_event_id": None,
        "overdue_event_id": None,
        "overdue_repeat": "every_cycle",
        "channel_filter": None,
        "run_on_start": False,
    }
    rule.update(overrides)
    return rule


def _str_to_bool(s) -> bool:
    if isinstance(s, bool):
        return s
    return str(s).strip().lower() in ("true", "1", "yes", "on")


def _apply_now(ctx):
    """把 ctx['config'] 应用到 host 上"""
    ctx["host"].project_config = ctx["config"]
    ctx["host"]._apply_periodic_actions(ctx["config"])
    # 给每条规则一个独立的持久化路径（用 host 的 _periodic_counter_path 自动算）
