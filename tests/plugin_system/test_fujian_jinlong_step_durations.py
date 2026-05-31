"""福建金龙插件 — 步骤耗时三档策略单元测试 (合成 hook ctx 驱动).

客户需求 (郑经理 2026-05-31):
  1) 步骤走完未达「最短时间」    → 报警 + 判 NG
  2) 步骤进行中超「警告时间」    → 只报警, 不判 NG
  3) 步骤进行中超「最长时间」    → 报警 + 判 NG
  最终结算只产出 OK/NG.

这里直接 import 插件后端模块, 注入 FakeHost (录 trigger_alarm 调用 + 供 config),
按合成 ctx 调 on_step_tick / on_step_change / on_pre_cycle_end / on_cycle_start,
断言报警次数 + dedup + override_result. 不依赖真 VideoSourceManager / 真 DB.
"""
from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN_BACKEND = REPO / "plugins-examples" / "fujian-jinlong" / "backend" / "__init__.py"


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location("fjjl_plugin_backend_under_test", PLUGIN_BACKEND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeHost:
    def __init__(self, config=None):
        self._config = config
        self.alarms = []
        self.written = {}

    def read_system_config(self, key):
        return json.dumps(self._config) if self._config is not None else None

    def write_system_config(self, key, value, description=None):
        self.written[key] = value
        return True

    def trigger_alarm(self, channel_id, event_type, reason=""):
        self.alarms.append({"channel_id": channel_id, "event_type": event_type, "reason": reason})
        return True


@pytest.fixture
def plugin():
    """每个测试一份干净的插件模块 (重置模块级 _STATE / 配置缓存)."""
    mod = _load_plugin_module()
    yield mod


def _wire(mod, config):
    mod._HOST = FakeHost(config)
    mod._STATE.clear()
    mod._cfg_cache.update({"value": None, "ts": 0.0})
    return mod._HOST


# 标准三档配置: 最短2s / 警告5s / 最长8s
_CFG = {
    "enabled": True,
    "default": {"min_sec": 2, "warn_sec": 5, "max_sec": 8},
    "steps": {},
    "alarm_event": {"warn": "event2", "ng": "event2"},
}


# ============================================================
# 需求 1: 未达最短时间 → 报警 + NG
# ============================================================
def test_too_fast_step_alarms_and_ng(plugin):
    host = _wire(plugin, _CFG)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 100})
    # 步骤 1.0s 走完 < 最短 2s
    plugin.on_step_change({"channel_id": 0, "cycle_id": 100, "step_label": "螺丝",
                           "step_name": "拧螺丝", "duration": 1.0})
    assert len(host.alarms) == 1, "未达最短应报警一次"
    assert host.alarms[0]["event_type"] == "event2"
    result = plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 100, "is_good": True})
    assert result == {"override_result": "NG"}, "未达最短应强制判 NG"


def test_step_meeting_min_no_ng(plugin):
    host = _wire(plugin, _CFG)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 101})
    plugin.on_step_change({"channel_id": 0, "cycle_id": 101, "step_label": "螺丝",
                           "step_name": "拧螺丝", "duration": 3.0})  # 达标
    assert host.alarms == []
    result = plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 101, "is_good": True})
    assert result == {}, "达标步骤不应判 NG"


# ============================================================
# 需求 2: 进行中超警告 → 只报警, 不 NG
# ============================================================
def test_warn_tier_alarm_only_no_ng(plugin):
    host = _wire(plugin, _CFG)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 102})
    # 进行中: 6s ≥ 警告5s 且 < 最长8s
    plugin.on_step_tick({"channel_id": 0, "cycle_id": 102, "step_label": "螺丝",
                         "step_name": "拧螺丝", "elapsed_sec": 6.0})
    assert len(host.alarms) == 1, "超警告应报警"
    # 步骤随后正常走完 (耗时达标)
    plugin.on_step_change({"channel_id": 0, "cycle_id": 102, "step_label": "螺丝",
                           "step_name": "拧螺丝", "duration": 6.5})
    result = plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 102, "is_good": True})
    assert result == {}, "仅超警告不应判 NG"


def test_warn_tier_dedup(plugin):
    host = _wire(plugin, _CFG)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 103})
    for el in (5.0, 5.5, 6.0, 7.0):  # 多次 tick 都在警告档 (<最长8)
        plugin.on_step_tick({"channel_id": 0, "cycle_id": 103, "step_label": "螺丝", "elapsed_sec": el})
    assert len(host.alarms) == 1, f"警告档应只报一次, 实际 {len(host.alarms)}"


# ============================================================
# 需求 3: 进行中超最长 → 报警 + NG
# ============================================================
def test_max_tier_alarm_and_ng(plugin):
    host = _wire(plugin, _CFG)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 104})
    plugin.on_step_tick({"channel_id": 0, "cycle_id": 104, "step_label": "螺丝",
                         "step_name": "拧螺丝", "elapsed_sec": 9.0})  # ≥ 最长8
    assert len(host.alarms) == 1
    result = plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 104, "is_good": True})
    assert result == {"override_result": "NG"}, "超最长应判 NG"


def test_max_tier_suppresses_later_warn(plugin):
    host = _wire(plugin, _CFG)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 105})
    plugin.on_step_tick({"channel_id": 0, "cycle_id": 105, "step_label": "螺丝", "elapsed_sec": 9.0})
    plugin.on_step_tick({"channel_id": 0, "cycle_id": 105, "step_label": "螺丝", "elapsed_sec": 10.0})
    assert len(host.alarms) == 1, "超最长后不应再叠加报警"


def test_warn_then_max_two_alarms(plugin):
    """先过警告(报1次), 再过最长(再报1次+NG)."""
    host = _wire(plugin, _CFG)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 106})
    plugin.on_step_tick({"channel_id": 0, "cycle_id": 106, "step_label": "螺丝", "elapsed_sec": 6.0})  # warn
    plugin.on_step_tick({"channel_id": 0, "cycle_id": 106, "step_label": "螺丝", "elapsed_sec": 8.5})  # max
    assert len(host.alarms) == 2
    result = plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 106, "is_good": True})
    assert result == {"override_result": "NG"}


# ============================================================
# 状态隔离 / 配置 / 错误隔离
# ============================================================
def test_per_step_override_threshold(plugin):
    cfg = {
        "enabled": True,
        "default": {"min_sec": 2, "warn_sec": 5, "max_sec": 8},
        "steps": {"快步": {"min_sec": 0.5, "warn_sec": 0, "max_sec": 0}},
        "alarm_event": {"warn": "event2", "ng": "event2"},
    }
    host = _wire(plugin, cfg)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 107})
    # 快步最短只 0.5s, 0.8s 走完 → 达标不报警
    plugin.on_step_change({"channel_id": 0, "cycle_id": 107, "step_label": "快步", "duration": 0.8})
    assert host.alarms == []
    assert plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 107, "is_good": True}) == {}


def test_two_channels_isolated(plugin):
    host = _wire(plugin, _CFG)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 200})
    plugin.on_cycle_start({"channel_id": 1, "cycle_id": 201})
    # 通道0 触发 NG, 通道1 正常
    plugin.on_step_change({"channel_id": 0, "cycle_id": 200, "step_label": "螺丝", "duration": 1.0})
    plugin.on_step_change({"channel_id": 1, "cycle_id": 201, "step_label": "螺丝", "duration": 3.0})
    assert plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 200, "is_good": True}) == {"override_result": "NG"}
    assert plugin.on_pre_cycle_end({"channel_id": 1, "cycle_id": 201, "is_good": True}) == {}


def test_new_cycle_resets_state(plugin):
    host = _wire(plugin, _CFG)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 300})
    plugin.on_step_change({"channel_id": 0, "cycle_id": 300, "step_label": "螺丝", "duration": 1.0})  # NG
    plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 300, "is_good": True})  # 消费并清理
    # 新周期不应继承上周期 NG
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 301})
    assert plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 301, "is_good": True}) == {}


def test_disabled_config_no_ng(plugin):
    cfg = dict(_CFG)
    cfg["enabled"] = False
    host = _wire(plugin, cfg)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 400})
    plugin.on_step_change({"channel_id": 0, "cycle_id": 400, "step_label": "螺丝", "duration": 0.5})
    assert host.alarms == [], "禁用时不报警"
    assert plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 400, "is_good": True}) == {}


def test_handlers_swallow_exceptions(plugin):
    """host.trigger_alarm 抛异常也不能抛回主流程."""
    host = _wire(plugin, _CFG)

    def boom(**kw):
        raise RuntimeError("报警器炸了")
    host.trigger_alarm = boom
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 500})
    # 不应抛出
    plugin.on_step_change({"channel_id": 0, "cycle_id": 500, "step_label": "螺丝", "duration": 0.5})
    plugin.on_step_tick({"channel_id": 0, "cycle_id": 500, "step_label": "螺丝", "elapsed_sec": 9.0})


def test_config_default_when_absent(plugin):
    """配置缺失 → 走内置默认 (三档全 0 = 不判任何 NG)."""
    host = _wire(plugin, None)
    plugin.on_cycle_start({"channel_id": 0, "cycle_id": 600})
    plugin.on_step_change({"channel_id": 0, "cycle_id": 600, "step_label": "螺丝", "duration": 0.1})
    plugin.on_step_tick({"channel_id": 0, "cycle_id": 600, "step_label": "螺丝", "elapsed_sec": 99})
    assert host.alarms == [], "默认阈值全 0, 不应触发任何报警"
    assert plugin.on_pre_cycle_end({"channel_id": 0, "cycle_id": 600, "is_good": True}) == {}
