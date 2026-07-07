"""传感器清洁插件 事件→计数/提示框/报警 闭环 BDD step 实现。

与 tests/plugin_system/test_sensor_clean_judgments.py(用 FakeHost 只记调用) 不同:
本特性走 **真主程序链路** —— 真 ChannelManager + 真 PluginHost +
真 VideoSourceManager.fire_external_event_response + 真 alarm_router(监听派发)。

证明四个可观测信号在"达阈值"那一刻同时到位:
  ① 触发事件      → 目标通道 events_log 多出对应事件条目 (trigger_event 命中)
  ② 计数          → mgr.counters 对应项 +1 (事件 actions 联动)
  ③ 提示框数据    → events_log 条目带 show_notification=True (前端 Toast/语音源)
  ④ 物理报警      → alarm_router.trigger_alarm 被调用 (灯/蜂鸣派发)
另含插件自有"看板横幅"态 (over_limit / absent) 的验证 —— 这是操作员真正看到的提示。
"""
from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins-examples" / "sensor-clean"
BACKEND_DIR = PLUGIN_DIR / "backend"
PKG = "sc_bdd_pkg"

COUNT_CH = 0   # 工位1: 计件 / 棉签超限 / 假擦拭
SWAP_CH = 1    # 工位2: 换棉签 / 操作员离开
COUNT_LABEL = "查看产品有无脏污"
WIPE_LABEL = "擦拭产品"


scenarios("../features/sensor_clean_event_chain.feature")


# ----------------------- 基建 helper -----------------------

def _load_plugin_fresh():
    """干净重载插件 backend 包 (重置模块级 _state, 保证 scenario 隔离)。"""
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
    return sys.modules[PKG + ".hooks"]


def _project_payload(pid, name):
    """含 合格OK(id=1)/不良NG(id=2) 事件 + 计数器的最小项目, 两工位共用。"""
    return {
        "id": pid,
        "name": name,
        "task_type": "detection",
        "logic_mode": "detection",
        "pipeline_config": {"logic_mode": "detection"},
        "steps_config": [{"id": 1, "label": COUNT_LABEL, "order": 1, "confidence": 0.5}],
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [{"counter_name": "合格总数", "delta": 1}],
             "show_notification": True, "toast_id": "ok"},
            {"id": 2, "name": "不良(NG)", "actions": [{"counter_name": "不良总数", "delta": 1}],
             "show_notification": True, "toast_id": "ng"},
        ],
        "counters_config": [
            {"name": "合格总数", "value": 0},
            {"name": "不良总数", "value": 0},
        ],
        "alarm_config": {}, "detection_config": {}, "data_config": {},
    }


def _box(label, x, y, conf=0.9, w=0.04, h=0.04):
    return {"label": label, "x": x, "y": y, "w": w, "h": h, "confidence": conf}


# ============================================================
# 背景: 真工位管理 + 真 PluginHost + 监听报警路由
# ============================================================

@given("工位1(计件)与工位2(换棉签)已挂含合格OK和不良NG事件的项目")
def given_two_channels_with_events(ctx):
    from backend.api.channel_manager import channel_manager
    channel_manager.set_channel_count(2)
    mgr0 = channel_manager.channels[COUNT_CH]
    mgr1 = channel_manager.channels[SWAP_CH]
    mgr0.set_project_config(_project_payload(901, "bdd-工位1-计件"))
    mgr1.set_project_config(_project_payload(902, "bdd-工位2-换棉签"))
    ctx["mgr0"] = mgr0
    ctx["mgr1"] = mgr1


@given("传感器清洁插件后端已加载并接入主程序事件与报警链路")
def given_plugin_loaded(ctx, monkeypatch):
    from backend.api import alarm as alarm_mod
    from backend.plugin_system.registry import PluginHost

    # ④ 监听"派发到报警器"的调用 (event 链路 _dispatch_event_alarm 与插件直呼 trigger_alarm 都落这)
    alarm_calls = []

    def _spy(event_type, channel_id=None, *a, **kw):
        alarm_calls.append((event_type, channel_id))
        return True

    monkeypatch.setattr(alarm_mod.alarm_router, "trigger_alarm", _spy)
    ctx["alarm_calls"] = alarm_calls

    host = PluginHost(
        customer_code="sensor-clean",
        plugin_dir=str(PLUGIN_DIR),
        main_version="3.27.0",
        capabilities=["runtime.event_trigger", "runtime.alarm_trigger",
                      "runtime.system_config_write"],
    )
    sc = _load_plugin_fresh()
    sc.set_host(host)
    ctx["sc"] = sc
    ctx["host"] = host


# ----------------------- 配置 (给定) -----------------------

@given(parsers.parse("配置棉签上限为 {k:d}, 正常计件连OK事件, 超限连NG事件"))
def given_cfg_count(ctx, k):
    ctx["sc"].save_config({
        "count_channels": [COUNT_CH], "swap_channel": SWAP_CH,
        "count_anchor_label": COUNT_LABEL,
        "max_uses_per_swab": k, "move_confirm_frames": 1,
        "move_threshold": 0.0116, "force_lock_frames": 0,
        "lock_time": 0.0, "lock_spatial": 0.0,   # 关位置/时间锁, 让连续计件
        "normal_count_event_id": 1, "swab_over_limit_event_id": 2,
        "fake_wipe_event_id": 0, "operator_absent_enabled": False,
    })


@given("配置假擦拭连NG事件, 关闭超限干扰")
def given_cfg_fake_wipe(ctx):
    ctx["sc"].save_config({
        "count_channels": [COUNT_CH], "swap_channel": SWAP_CH,
        "fake_wipe_label": WIPE_LABEL, "fake_wipe_event_id": 2,
        "fake_wipe_still_time": 2.0, "fake_wipe_still_disp": 0.0058,
        "swab_over_limit_event_id": 0, "normal_count_event_id": 0,
        "operator_absent_enabled": False,
    })


@given("配置操作员离开启用, 超时 1 秒, 连NG事件")
def given_cfg_absent(ctx):
    ctx["sc"].save_config({
        "count_channels": [COUNT_CH], "swap_channel": SWAP_CH,
        "operator_absent_enabled": True, "operator_absent_event_id": 2,
        "operator_absent_timeout_sec": 1,
        "swab_over_limit_event_id": 0, "normal_count_event_id": 0,
        "fake_wipe_event_id": 0,
    })


# ----------------------- 动作 (当) -----------------------

def _frame(sc, ch, t, dets):
    sc.on_detection_frame({"channel_id": ch, "timestamp": t, "detections": dets})


def _count_one(sc, t0):
    """驱动一次计件: 锚点标签出现 + 移动确认 (move_confirm_frames=1 → 两帧计一件)。"""
    _frame(sc, COUNT_CH, t0, [_box(COUNT_LABEL, 0.5, 0.5)])
    _frame(sc, COUNT_CH, t0 + 0.1, [_box(COUNT_LABEL, 0.7, 0.5)])


@when(parsers.parse("操作员在工位1擦了 {n:d} 个产品"))
def when_count_n(ctx, n):
    sc = ctx["sc"]
    for i in range(n):
        _count_one(sc, float(i))


@when("操作员在工位1对着产品假擦拭(停留超时几乎不动)")
def when_fake_wipe(ctx):
    sc = ctx["sc"]
    _frame(sc, COUNT_CH, 0.0, [_box(WIPE_LABEL, 0.5, 0.5)])
    _frame(sc, COUNT_CH, 2.5, [_box(WIPE_LABEL, 0.5, 0.5)])   # 停留 2.5s 位移 0 → 假擦拭


@when("工位2持续无人超过超时秒数")
def when_absent(ctx):
    sc = ctx["sc"]
    _frame(sc, SWAP_CH, 0.0, [])    # 设截止 = 0 + 1s
    _frame(sc, SWAP_CH, 2.0, [])    # 已超 1s → 命中离岗


# ----------------------- 断言 (那么) -----------------------

def _events_log(ctx, ch):
    return list(getattr(ctx["mgr0" if ch == COUNT_CH else "mgr1"], "events_log", []))


@then(parsers.parse('主程序应触发"{kind}"事件至少 {m:d} 次'))
def then_event_fired(ctx, kind, m):
    # 离岗在工位2, 其余在工位1; 合并两工位日志按事件名匹配
    logs = _events_log(ctx, COUNT_CH) + _events_log(ctx, SWAP_CH)
    hits = [e for e in logs if kind in (e.get("event_name") or "")]
    assert len(hits) >= m, f'期望"{kind}"事件≥{m}次, 实际{len(hits)}; 全部日志={[e.get("event_name") for e in logs]}'


@then(parsers.parse('"{counter}"计数器应大于 {n:d}'))
def then_counter_gt(ctx, counter, n):
    total = ctx["mgr0"].counters.get(counter, 0) + ctx["mgr1"].counters.get(counter, 0)
    assert total > n, f'计数器"{counter}"={total} 不大于 {n}'


@then(parsers.parse('"{counter}"计数器应等于 {n:d}'))
def then_counter_eq(ctx, counter, n):
    total = ctx["mgr0"].counters.get(counter, 0) + ctx["mgr1"].counters.get(counter, 0)
    assert total == n, f'计数器"{counter}"={total} 不等于 {n}'


@then(parsers.parse("应至少产生 {m:d} 条要弹提示框的事件日志"))
def then_toast_log(ctx, m):
    logs = _events_log(ctx, COUNT_CH) + _events_log(ctx, SWAP_CH)
    toasts = [e for e in logs if e.get("show_notification")]
    assert len(toasts) >= m, f"期望要弹提示框的事件日志≥{m}, 实际{len(toasts)}"


@then("应向报警器派发硬件报警")
def then_hw_alarm(ctx):
    assert len(ctx["alarm_calls"]) >= 1, f"未向报警器派发任何硬件报警: {ctx['alarm_calls']}"


@then("看板应处于棉签超限告警态")
def then_banner_over_limit(ctx):
    st = ctx["sc"].get_state()
    assert st.get("over_limit") is True, f"看板未进入棉签超限态: {st}"


@then("看板不应处于棉签超限告警态")
def then_banner_normal(ctx):
    st = ctx["sc"].get_state()
    assert st.get("over_limit") is False, f"看板不应超限却超限了: {st}"


@then("看板应处于离岗告警态")
def then_banner_absent(ctx):
    st = ctx["sc"].get_state()
    assert st.get("operator_absent_enabled") is True and st.get("absent_remaining") == 0, \
        f"看板未进入离岗告警态: {st}"
