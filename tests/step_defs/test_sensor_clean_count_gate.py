"""sensor-clean v1.3.0 同帧双类别计数门槛 BDD.

面向功能: 不直接调插件函数, 而是走主程序真实链路 —
  真实 PluginRegistry 注册 on_detection_frame → fire_plugin_hook 逐帧分发
  (与 source_inference_loop_mixin 推理循环同一入口), 验证:
  - 未配伴随标签: v1.2.0 行为零差异 (仅锚标签即计数)
  - 配了伴随标签: 同帧缺伴随 → 不计数不触发事件; 双类别齐 → 计数 + OK 事件
  - 跟踪中途伴随消失 → 按锚框离场处理
  - 配置保存热加载后门槛立即生效 (配置持久化闭环)
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from pytest_bdd import given, parsers, scenarios, then, when

REPO = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO / "plugins-examples" / "sensor-clean" / "backend"
PKG = "sc_gate_bdd"

scenarios("sensor_clean_count_gate.feature")

# 基础配置: 关掉与门槛无关的判定/锁, 让两帧即可驱动一次计件
_BASE_CFG = {
    "count_channels": [0], "count_anchor_label": "查看产品",
    "swap_channel": 1, "move_confirm_frames": 1, "force_lock_frames": 0,
    "max_uses_per_swab": 99, "normal_count_event_id": 1,
    "swab_over_limit_event_id": 0, "fake_wipe_event_id": 0,
    "move_threshold": 0.0116,
}


class FakeHost:
    def __init__(self):
        self.events = []
        self._raw = None

    def read_system_config(self, key):
        return self._raw

    def write_system_config(self, key, value, description=""):
        self._raw = value
        return True

    def trigger_event(self, channel_id, event_id, reason=""):
        self.events.append((channel_id, int(event_id), reason))
        return True

    def trigger_alarm(self, channel_id, event_type, reason=""):
        return True


def _box(label, x):
    return {"label": label, "x": x, "y": 0.5, "w": 0.04, "h": 0.04, "confidence": 0.9}


@pytest.fixture
def env(monkeypatch):
    """干净加载插件 backend 包 + 真实 PluginRegistry 挂钩子 + 注入 plugin_manager."""
    for m in list(sys.modules):
        if m == PKG or m.startswith(PKG + "."):
            del sys.modules[m]
    spec = importlib.util.spec_from_file_location(
        PKG, BACKEND_DIR / "__init__.py",
        submodule_search_locations=[str(BACKEND_DIR)],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = pkg
    spec.loader.exec_module(pkg)
    hooks = sys.modules[PKG + ".hooks"]

    from backend.plugin_system import manager as manager_mod
    from backend.plugin_system.registry import PluginRegistry

    reg = PluginRegistry(app=FastAPI(), engine=None, customer_code="sensor-clean")
    reg.hooks.register(
        hook_type="detection_frame", phase="post_inference", when="post",
        priority=100, handler=hooks.on_detection_frame,
    )
    monkeypatch.setattr(manager_mod.plugin_manager, "registry", reg)

    host = FakeHost()
    hooks.set_host(host)
    return {"hooks": hooks, "host": host}


def _apply_cfg(env_dict, patch):
    cfg = dict(_BASE_CFG)
    cfg.update(patch)
    env_dict["host"]._raw = json.dumps(cfg, ensure_ascii=False)
    env_dict["hooks"].reload_config()


def _fire(t, dets):
    from backend.plugin_system.hook_dispatch import fire_plugin_hook
    fire_plugin_hook("detection_frame", "post_inference", "post", {
        "channel_id": 0, "frame_seq": int(t * 30),
        "timestamp": t, "detections": dets,
    })


# ---------------------------- given ----------------------------


@given("传感器清洁插件已加载并挂上帧级检测钩子", target_fixture="ctx")
def plugin_loaded(env):
    return env


@given("插件配置未启用计数伴随标签")
def cfg_no_gate(ctx):
    _apply_cfg(ctx, {"count_require_label": ""})


@given(parsers.parse('插件配置的计数伴随标签为"{label}"'))
def cfg_gate(ctx, label):
    _apply_cfg(ctx, {"count_require_label": label})


# ---------------------------- when ----------------------------


@when('主程序逐帧广播"仅锚动作出现并移动"的检测序列')
def frames_anchor_only(ctx):
    _fire(0.0, [_box("查看产品", 0.5)])
    _fire(0.1, [_box("查看产品", 0.7)])


@when('主程序逐帧广播"锚动作与伴随动作同帧出现并移动"的检测序列')
def frames_both(ctx):
    _fire(0.0, [_box("查看产品", 0.5), _box("清洁产品", 0.3)])
    _fire(0.1, [_box("查看产品", 0.7), _box("清洁产品", 0.3)])


@when('主程序逐帧广播"第二帧起伴随动作消失"的检测序列')
def frames_companion_lost(ctx):
    _fire(0.0, [_box("查看产品", 0.5), _box("清洁产品", 0.3)])
    _fire(0.1, [_box("查看产品", 0.7)])


@when(parsers.parse('通过配置接口保存计数伴随标签为"{label}"并热加载'))
def save_gate_via_config(ctx, label):
    ctx["hooks"].save_config({"count_require_label": label})


# ---------------------------- then ----------------------------


@then(parsers.parse("看板总产量应为 {n:d}"))
def assert_total(ctx, n):
    assert ctx["hooks"].get_state()["total_products"] == n


@then("不应触发任何主程序事件")
def assert_no_events(ctx):
    assert ctx["host"].events == []


@then("应触发一次合格计件事件")
def assert_ok_event(ctx):
    ok = [e for e in ctx["host"].events if e[1] == 1 and "正常计件" in e[2]]
    assert len(ok) == 1


@then(parsers.parse('重新读取的配置伴随标签应为"{label}"'))
def assert_cfg_roundtrip(ctx, label):
    assert ctx["hooks"].get_config()["count_require_label"] == label
