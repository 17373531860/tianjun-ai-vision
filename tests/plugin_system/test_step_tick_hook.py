"""RFC 12: step_tick hook (步骤进行中计时广播) 契约测试.

客户视角叙事:
  福建金龙现场要"步骤耗时三档 + 实时警告/超时报警": 步骤进行中, 超过警告时间只
  报警不计 NG, 超过最大时间报警 + NG, 未达最短时间报警 + NG. "实时"=步骤还在进行
  时就当场响. 现有 hook (step_change / pre_cycle_end) 都在步骤/周期**完成后**才 fire,
  无法支撑"进行中实时报警". 故平台加一个通用计时广播 hook:
    - 主程序在推理热路径里对每个正在计时的步骤按 ~1Hz fire step_tick;
    - ctx 携带 elapsed_sec, 阈值/分档/报警策略全交给插件 (主程序不内嵌策略);
    - 只读 observe hook (不在 returnable 白名单).

测试策略:
  造最小 host 只挂 StepStatsMixin._broadcast_step_ticks 单测 (不起 VideoSourceManager):
  节流契约 + ctx 字段集合 + 无步骤不 fire + 异常隔离 + 只读语义.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _make_host(step_start_time=None, step_time_config=None, channel_id=0, cycle_id=None):
    """造最小 host 给 _broadcast_step_ticks 单测用."""
    from backend.api.source_step_stats_mixin import StepStatsMixin

    class _Host(StepStatsMixin):
        pass

    h = _Host()
    h.channel_id = channel_id
    h.current_cycle_id = cycle_id
    h.step_start_time = dict(step_start_time or {})
    h.step_time_config = dict(step_time_config or {})
    h.step_display_names = {}
    return h


def _patch_fire(monkeypatch, recorder):
    from backend.plugin_system import hook_dispatch as hd

    def fake_fire(hook_type, phase, when, ctx):
        recorder.append((hook_type, phase, when, ctx))
        return {}
    monkeypatch.setattr(hd, "fire_plugin_hook", fake_fire)


# ============================================================
# A. 基本 fire + ctx 字段
# ============================================================


def test_active_step_fires(monkeypatch):
    """有正在计时的步骤 → fire step_tick, 携带 elapsed_sec."""
    calls = []
    _patch_fire(monkeypatch, calls)

    h = _make_host(
        step_start_time={"screw1": 100.0},
        step_time_config={"screw1": {"min_duration": 2, "max_duration": 10}},
        channel_id=1, cycle_id=42,
    )
    h.step_display_names = {"screw1": "拧螺丝1"}

    h._broadcast_step_ticks(current_time=105.0)

    assert len(calls) == 1
    hook_type, phase, when, ctx = calls[0]
    assert hook_type == "step_tick"
    assert phase == "step_in_progress"
    assert when == "post"
    assert ctx["channel_id"] == 1
    assert ctx["cycle_id"] == 42
    assert ctx["step_label"] == "screw1"
    assert ctx["step_name"] == "拧螺丝1"
    assert ctx["elapsed_sec"] == 5.0
    assert ctx["min_duration"] == 2
    assert ctx["max_duration"] == 10


def test_ctx_field_set_locked(monkeypatch):
    """step_tick ctx 顶层字段集合稳定 (改字段 = 升 plugin SDK)."""
    calls = []
    _patch_fire(monkeypatch, calls)

    h = _make_host(step_start_time={"a": 0.0})
    h._broadcast_step_ticks(current_time=1.5)

    ctx = calls[0][3]
    expected = {"channel_id", "cycle_id", "step_label", "step_name",
                "elapsed_sec", "min_duration", "max_duration"}
    assert set(ctx.keys()) == expected, (
        f"ctx 顶层字段集合变了: {set(ctx.keys())} != {expected}. 改字段 = 升 plugin SDK"
    )


def test_no_active_step_does_not_fire(monkeypatch):
    """没有正在计时的步骤 → 不 fire (热路径早退)."""
    calls = []
    _patch_fire(monkeypatch, calls)

    h = _make_host(step_start_time={})
    h._broadcast_step_ticks(current_time=10.0)

    assert calls == []


# ============================================================
# B. 节流 1Hz 契约
# ============================================================


def test_throttle_within_one_second(monkeypatch):
    """同一步骤 1s 内重复调用 → 只 fire 一次."""
    calls = []
    _patch_fire(monkeypatch, calls)

    h = _make_host(step_start_time={"s": 0.0})
    h._broadcast_step_ticks(current_time=1.0)   # fire
    h._broadcast_step_ticks(current_time=1.5)   # 距上次 0.5s, 节流跳过
    h._broadcast_step_ticks(current_time=1.9)   # 距上次 0.9s, 节流跳过

    assert len(calls) == 1, f"1s 内应只 fire 1 次, 实际 {len(calls)}"


def test_throttle_fires_again_after_one_second(monkeypatch):
    """超过 1s 后再次 fire."""
    calls = []
    _patch_fire(monkeypatch, calls)

    h = _make_host(step_start_time={"s": 0.0})
    h._broadcast_step_ticks(current_time=1.0)   # fire
    h._broadcast_step_ticks(current_time=2.1)   # 距上次 1.1s, fire

    assert len(calls) == 2
    assert calls[1][3]["elapsed_sec"] == 2.1


def test_multiple_steps_each_throttled_independently(monkeypatch):
    """多步骤并行计时, 各自独立节流."""
    calls = []
    _patch_fire(monkeypatch, calls)

    h = _make_host(step_start_time={"a": 0.0, "b": 0.0})
    h._broadcast_step_ticks(current_time=1.0)   # a, b 各 fire 一次

    labels = {c[3]["step_label"] for c in calls}
    assert labels == {"a", "b"}
    assert len(calls) == 2


def test_stale_throttle_record_cleaned(monkeypatch):
    """步骤结束后, 节流记录被清理, 防 dict 无限增长."""
    calls = []
    _patch_fire(monkeypatch, calls)

    h = _make_host(step_start_time={"a": 0.0})
    h._broadcast_step_ticks(current_time=1.0)
    assert "a" in h._step_tick_last

    # 步骤 a 结束
    h.step_start_time = {}
    h._broadcast_step_ticks(current_time=2.0)
    assert "a" not in h._step_tick_last, "结束步骤的节流记录应被清理"


# ============================================================
# C. 错误隔离 + 只读语义
# ============================================================


def test_exception_swallowed(monkeypatch):
    """fire 内部抛异常 → swallow, 不抛回推理热路径."""
    from backend.plugin_system import hook_dispatch as hd

    def buggy_fire(*a, **kw):
        raise RuntimeError("插件爆了")
    monkeypatch.setattr(hd, "fire_plugin_hook", buggy_fire)

    h = _make_host(step_start_time={"s": 0.0})
    h._broadcast_step_ticks(current_time=1.0)  # 不应抛出


def test_step_tick_not_returnable():
    """step_tick 不在 RETURNABLE_HOOK_FIELDS 白名单 → handler 返回值被丢弃."""
    from backend.plugin_system.hook_dispatch import RETURNABLE_HOOK_FIELDS, _merge_handler_results

    assert "step_tick" not in RETURNABLE_HOOK_FIELDS
    merged = _merge_handler_results("step_tick", [{"override_result": "OK"}, {"foo": 1}])
    assert merged == {}, "只读 hook 的 handler 返回值必须整体丢弃"
