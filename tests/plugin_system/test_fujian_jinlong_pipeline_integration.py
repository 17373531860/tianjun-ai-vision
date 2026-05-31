"""福建金龙 步骤耗时三档 — 主程序集成测试 (真分发 + 真结算消费).

与 test_fujian_jinlong_step_durations.py (纯策略单测) 的区别:
  这里走**主程序真实链路**:
    - fire_plugin_hook(...) 真分发 (plugin_manager.registry → HooksRegistry.fire)
    - _resolve_pre_cycle_end_overrides(...) 真消费 pre_cycle_end 返回的 override_result
    - 插件 register_plugin 进真 PluginRegistry (含路由挂载 + 4 个 hook 注册)

证明: 步骤耗时越线 → 插件经真 hook 分发收到 → trigger_alarm + 标记 NG →
      pre_cycle_end 真分发 → 主程序 _resolve 真把 cycle is_good 从 OK 翻成 NG.

"先红后绿": plugin_manager.registry=None (等价没装插件) 时, 同样的过长步骤
           结算仍是 OK — 证明 NG 确实来自插件而非主程序自身.
"""
from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI

REPO = Path(__file__).resolve().parents[2]
PLUGIN_BACKEND = REPO / "plugins-examples" / "fujian-jinlong" / "backend" / "__init__.py"

THRESHOLDS = {"enabled": True, "default": {"min_sec": 1, "warn_sec": 4, "max_sec": 6},
              "steps": {}, "alarm_event": {"warn": "event2", "ng": "event2"}}


class FakeHost:
    def __init__(self, config):
        self._config = config
        self.alarms = []

    def read_system_config(self, key):
        return json.dumps(self._config)

    def write_system_config(self, key, value, description=None):
        return True

    def trigger_alarm(self, channel_id, event_type, reason=""):
        self.alarms.append({"channel_id": channel_id, "event_type": event_type, "reason": reason})
        return True


def _load_plugin():
    spec = importlib.util.spec_from_file_location("fjjl_plugin_integ", PLUGIN_BACKEND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wired():
    """装插件进真 registry + 接到 plugin_manager.registry; 收尾还原."""
    from backend.plugin_system.manager import plugin_manager
    from backend.plugin_system.registry import PluginRegistry

    mod = _load_plugin()
    host = FakeHost(THRESHOLDS)
    registry = PluginRegistry(app=FastAPI(), engine=None, customer_code="internal-demo")
    mod.register_plugin(None, registry, {}, host)

    old = plugin_manager.registry
    plugin_manager.registry = registry
    try:
        yield mod, host
    finally:
        plugin_manager.registry = old


def _fire(hook, phase, when, ctx):
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    return fire_plugin_hook(hook, phase, when, ctx)


def _settle(cycle_id, original_is_good=True):
    """跑主程序 pre_cycle_end 真分发 + 真消费, 返回最终 is_good."""
    from backend.api.source_session_lifecycle_mixin import _resolve_pre_cycle_end_overrides
    res = _fire("pre_cycle_end", "pre_cycle", "pre",
                {"channel_id": 0, "cycle_id": cycle_id, "is_good": original_is_good,
                 "result": "OK", "reason": "顺序正确完成"})
    final_is_good, final_reason, _ = _resolve_pre_cycle_end_overrides(
        original_is_good=original_is_good, original_reason="顺序正确完成", plugin_result=res)
    return final_is_good, final_reason


def test_max_tier_real_pipeline_ng(wired):
    mod, host = wired
    _fire("cycle_start", "post_cycle_start", "post", {"channel_id": 0, "cycle_id": 1})
    _fire("step_tick", "step_in_progress", "post",
          {"channel_id": 0, "cycle_id": 1, "step_label": "step_b", "step_name": "工序B", "elapsed_sec": 6.5})
    assert any("超过最长" in a["reason"] for a in host.alarms), f"应触发超最长报警: {host.alarms}"
    is_good, reason = _settle(1)
    assert is_good is False, "超最长 → 主程序应被插件改写为 NG"
    assert "plugin override" in reason


def test_min_tier_real_pipeline_ng(wired):
    mod, host = wired
    _fire("cycle_start", "post_cycle_start", "post", {"channel_id": 0, "cycle_id": 2})
    _fire("step_change", "post_step", "post",
          {"channel_id": 0, "cycle_id": 2, "step_label": "step_b", "step_name": "工序B", "duration": 0.4})
    assert any("未达最短" in a["reason"] for a in host.alarms), f"应触发未达最短报警: {host.alarms}"
    is_good, _ = _settle(2)
    assert is_good is False, "未达最短 → NG"


def test_warn_tier_real_pipeline_alarm_only(wired):
    mod, host = wired
    _fire("cycle_start", "post_cycle_start", "post", {"channel_id": 0, "cycle_id": 3})
    _fire("step_tick", "step_in_progress", "post",
          {"channel_id": 0, "cycle_id": 3, "step_label": "step_b", "step_name": "工序B", "elapsed_sec": 4.5})
    assert any("超过警告" in a["reason"] for a in host.alarms), "应触发警告报警"
    assert not any("超过最长" in a["reason"] for a in host.alarms)
    is_good, _ = _settle(3)
    assert is_good is True, "仅超警告 → 周期仍 OK"


def test_ok_no_violation_real_pipeline(wired):
    mod, host = wired
    _fire("cycle_start", "post_cycle_start", "post", {"channel_id": 0, "cycle_id": 4})
    _fire("step_tick", "step_in_progress", "post",
          {"channel_id": 0, "cycle_id": 4, "step_label": "step_b", "elapsed_sec": 2.0})
    _fire("step_change", "post_step", "post",
          {"channel_id": 0, "cycle_id": 4, "step_label": "step_b", "duration": 2.5})
    assert host.alarms == [], "正常步骤不应报警"
    is_good, _ = _settle(4)
    assert is_good is True


def test_red_green_without_plugin_stays_ok(wired):
    """先红后绿: 摘掉插件 (registry=None), 同样过长步骤结算仍 OK → 证 NG 来自插件."""
    from backend.plugin_system.manager import plugin_manager
    mod, host = wired
    plugin_manager.registry = None  # 等价没装插件
    # 没插件: step_tick / pre_cycle_end 分发都返回空
    res = _fire("step_tick", "step_in_progress", "post",
                {"channel_id": 0, "cycle_id": 5, "step_label": "step_b", "elapsed_sec": 99})
    assert res == {}
    is_good, _ = _settle(5)
    assert is_good is True, "没插件时不应有 NG 改写"
    assert host.alarms == [], "没插件时不应有报警"
