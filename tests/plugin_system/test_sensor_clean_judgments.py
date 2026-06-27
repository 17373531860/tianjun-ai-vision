"""sensor-clean v1.1.0 — 三判定接入主程序事件体系 单测.

客户视角叙事:
  传感器清洁工序插件在 v1.1.0 把三个过程判定接入主程序事件 (host.trigger_event):
  1. 棉签寿命超限: 一根棉签擦满 K 个产品后 → 触发用户配的事件 (默认 NG)
  2. 假擦拭:       视角1 "擦拭产品"框停留超时但几乎没动 → 触发事件
  3. 操作员离开:   视角2 长时间无任何检测目标 (离岗) → 触发事件
  (明确不做 detect7(1) 的"假换棉签": 棉签框静止那条)

  三判定都通过 host.trigger_event(channel_id, event_id) 借主程序事件响应面,
  报警/计数器/Toast/主页显示全由主程序原生链路联动, 插件只负责"跑判定 + 触发".

本测试守护:
  - 判定器算法本身 (StillFakeActionDetector / AbsentCountdown) 的命中/不误报语义
  - on_detection_frame 三判定命中后调对的 channel_id + event_id
  - 位移够大不误报 / 操作员回来重置不误报
  - 关掉判定 (event_id=0 或开关 False) 不触发
  - host.trigger_event 异常被错误隔离 (检测不崩)
"""
from __future__ import annotations

import sys
import json
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO / "plugins-examples" / "sensor-clean" / "backend"
PKG = "sc_clean_test"


# ----------------------- 插件 backend 包加载 fixture -----------------------


@pytest.fixture
def sc():
    """干净加载 sensor-clean backend 包 (每次重置模块级 _state, 测试隔离)."""
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


class FakeHost:
    """伪 PluginHost — 记录 trigger_event / trigger_alarm 调用, 提供配置读写."""

    def __init__(self, cfg_patch=None, event_ret=True):
        self.events = []   # [(channel_id, event_id, reason)]
        self.alarms = []   # [(channel_id, event_type, reason)]
        self._raw = json.dumps(cfg_patch) if cfg_patch else None
        self._event_ret = event_ret

    def read_system_config(self, key):
        return self._raw

    def write_system_config(self, key, value, description=""):
        self._raw = value
        return True

    def trigger_event(self, channel_id, event_id, reason=""):
        self.events.append((channel_id, int(event_id), reason))
        return self._event_ret

    def trigger_alarm(self, channel_id, event_type, reason=""):
        self.alarms.append((channel_id, event_type, reason))
        return True


def _setup(sc_hooks, cfg_patch, event_ret=True):
    host = FakeHost(cfg_patch, event_ret=event_ret)
    sc_hooks.set_host(host)
    sc_hooks.reload_config()
    return host


def _box(label, x, y, conf=0.9, w=0.04, h=0.04):
    return {"label": label, "x": x, "y": y, "w": w, "h": h, "confidence": conf}


def _frame(sc_hooks, channel_id, t, dets):
    sc_hooks.on_detection_frame(
        {"channel_id": channel_id, "timestamp": t, "detections": dets})


# ============================================================
# A. 判定器算法单元 (纯逻辑, 不依赖主程序)
# ============================================================


def test_still_detector_hits_when_still(sc):
    """框停留超 still_time 且位移 < still_disp → 命中一次 (不重复)."""
    d = sc.StillFakeActionDetector(still_time=2.0, still_disp=0.0058)
    box = {"x": 0.5, "y": 0.5, "w": 0.04, "h": 0.04}
    assert d.update(box, 0.0) is False   # 建追踪
    assert d.update(box, 1.0) is False   # 未超时
    assert d.update(box, 2.5) is True    # 超时 + 位移 0 → 命中
    assert d.update(box, 3.0) is False   # 已告警, 不重复


def test_still_detector_no_hit_when_moving(sc):
    """位移够大 (>= still_disp) → 视为正常动作, 不告警."""
    d = sc.StillFakeActionDetector(still_time=2.0, still_disp=0.0058)
    assert d.update({"x": 0.5, "y": 0.5, "w": 0.04, "h": 0.04}, 0.0) is False
    assert d.update({"x": 0.6, "y": 0.5, "w": 0.04, "h": 0.04}, 2.5) is False


def test_still_detector_resets_after_box_gone(sc):
    """框消失累计 lost 帧后清理, 新框可再次判定."""
    d = sc.StillFakeActionDetector(still_time=2.0, still_disp=0.0058, lost_frame_thresh=2)
    box = {"x": 0.5, "y": 0.5, "w": 0.04, "h": 0.04}
    d.update(box, 0.0)
    assert d.update(box, 2.5) is True     # 命中
    d.update(None, 3.0)                    # 消失 1
    d.update(None, 3.1)                    # 消失 2 → 清理
    d.update(box, 4.0)                     # 新追踪
    assert d.update(box, 6.5) is True     # 可再次命中


def test_absent_countdown_hits_on_timeout(sc):
    """持续无人超 timeout → 命中一次; 人回来重置不再误报."""
    c = sc.AbsentCountdown(timeout_sec=10)
    assert c.feed(False, 0.0)[0] is False    # deadline=10
    assert c.feed(False, 11.0)[0] is True     # 超时命中
    assert c.feed(False, 12.0)[0] is False    # 已告警
    assert c.feed(True, 13.0)[0] is False     # 人回来 → deadline=23, 解除
    assert c.feed(False, 20.0)[0] is False    # 23 未到, 不误报


def test_swab_window_backward_compatible_default(sc):
    """min_sustain_sec=0 (默认) → 退化旧'稳定 2 帧即解锁'行为 (向后兼容)."""
    w = sc.SwabChangeWindow(lock_time=2.0)        # 默认 min_sustain_sec=0
    assert w.feed(True, 10.00) is False            # 第 1 帧
    assert w.feed(True, 10.02) is True             # 第 2 帧稳定 → 解锁 (旧行为)


def test_swab_window_filters_transient_jitter(sc):
    """防抖: 仅 2~3 帧的瞬时误检 (持续 < min_sustain_sec) → 不解锁."""
    w = sc.SwabChangeWindow(lock_time=2.0, min_sustain_sec=0.12, gap_sec=0.2)
    assert w.feed(True, 10.000) is False
    assert w.feed(True, 10.017) is False           # 段持续 0.017s < 0.12 → 滤掉
    assert w.feed(True, 10.033) is False           # 仍 < 0.12


def test_swab_window_passes_real_action(sc):
    """防抖: 持续 >= min_sustain_sec 的真实动作 → 解锁一次 (用秒, 跨帧率鲁棒)."""
    w = sc.SwabChangeWindow(lock_time=2.0, min_sustain_sec=0.12, gap_sec=0.2)
    hit, t = False, 10.0
    while t <= 10.20:                              # 持续 0.2s > 0.12
        if w.feed(True, t):
            hit = True
            break
        t += 1 / 60.0
    assert hit is True


def test_swab_window_dedups_within_lock_time(sc):
    """防抖: 同一次持续动作在 lock_time 内只解锁一次."""
    w = sc.SwabChangeWindow(lock_time=2.0, min_sustain_sec=0.12, gap_sec=0.2)
    hits, t = 0, 10.0
    while t <= 11.5:                               # 1.5s 持续动作 < lock_time 2.0
        if w.feed(True, t):
            hits += 1
        t += 1 / 60.0
    assert hits == 1


def test_swab_window_new_segment_after_gap(sc):
    """防抖: 动作中断超 gap_sec 后算新段, 重新计 sustain, 过 lock_time 可再解锁."""
    w = sc.SwabChangeWindow(lock_time=0.5, min_sustain_sec=0.12, gap_sec=0.2)
    h1, t = False, 10.0
    while t <= 10.2:                               # 动作1 持续 0.2s
        if w.feed(True, t):
            h1 = True
        t += 1 / 60.0
    assert h1 is True
    h2, t2 = False, 11.0                           # 间隔 0.8s > gap_sec 且 > lock_time
    while t2 <= 11.2:                              # 动作2 持续 0.2s
        if w.feed(True, t2):
            h2 = True
        t2 += 1 / 60.0
    assert h2 is True


# ============================================================
# B. on_detection_frame 三判定 → host.trigger_event
# ============================================================


def test_fake_wipe_fires_event(sc):
    """假擦拭: 视角1 擦拭产品框停留超时位移小 → trigger_event(0, fake_wipe_event_id)."""
    host = _setup(sc, {
        "count_channels": [0], "fake_wipe_event_id": 3,
        "fake_wipe_still_time": 2.0, "fake_wipe_still_disp": 0.0058,
        "swab_over_limit_event_id": 0,  # 关超限干扰
    })
    _frame(sc, 0, 0.0, [_box("擦拭产品", 0.5, 0.5)])
    _frame(sc, 0, 2.5, [_box("擦拭产品", 0.5, 0.5)])
    assert any(e[0] == 0 and e[1] == 3 for e in host.events)
    assert "假擦拭" in host.events[0][2]


def test_fake_wipe_no_event_when_moving(sc):
    """擦拭框位移够大 → 正常擦拭, 不触发假擦拭事件."""
    host = _setup(sc, {
        "count_channels": [0], "fake_wipe_event_id": 3,
        "fake_wipe_still_time": 2.0, "fake_wipe_still_disp": 0.0058,
        "swab_over_limit_event_id": 0,
    })
    _frame(sc, 0, 0.0, [_box("擦拭产品", 0.5, 0.5)])
    _frame(sc, 0, 2.5, [_box("擦拭产品", 0.7, 0.5)])  # 位移 0.2 >> 阈值
    assert host.events == []


def _count_one(sc, host, ch, t0):
    """驱动一次计件 (建跟踪 + 移动确认), 用 move_confirm_frames=1 时两帧即计一件."""
    _frame(sc, ch, t0, [_box("查看产品有无脏污", 0.5, 0.5)])
    _frame(sc, ch, t0 + 0.1, [_box("查看产品有无脏污", 0.7, 0.5)])


def test_swab_hit_limit_alarms_via_hardware(sc):
    """棉签刚擦满 K (used==k): 立即硬件报警提示换棉签, 这件仍按合格计 (不进不良)."""
    host = _setup(sc, {
        "count_channels": [0], "count_anchor_label": "查看产品有无脏污",
        "max_uses_per_swab": 1, "move_confirm_frames": 1,
        "normal_count_event_id": 1, "swab_over_limit_event_id": 7,
        "fake_wipe_event_id": 0, "move_threshold": 0.0116, "force_lock_frames": 0,
    })
    _count_one(sc, host, 0, 0.0)               # used=1=K
    st = sc.get_state()
    assert st["swab_used"] == 1
    assert st["over_limit"] is True            # 擦满即点亮 banner/报警
    assert st["ng_count"] == 0                 # K 件本身仍合格
    assert any(e[1] == 1 for e in host.events)  # 合格件 → OK 事件
    assert any(a[1] == "event7" for a in host.alarms)  # 硬件报警 event{超限事件id}


def test_swab_over_limit_each_ng_fires_event(sc):
    """超限后继续擦, 每件都触发 NG 事件 (红灯+蜂鸣+不良计数)."""
    host = _setup(sc, {
        "count_channels": [0], "count_anchor_label": "查看产品有无脏污",
        "max_uses_per_swab": 1, "move_confirm_frames": 1,
        "normal_count_event_id": 1, "swab_over_limit_event_id": 2,
        "fake_wipe_event_id": 0, "move_threshold": 0.0116, "force_lock_frames": 0,
        "lock_time": 0.0, "lock_spatial": 0.0,  # 关位置/时间锁, 让连续计件
    })
    _count_one(sc, host, 0, 0.0)    # used=1=K (合格)
    _count_one(sc, host, 0, 1.0)    # used=2>K → 不良
    _count_one(sc, host, 0, 2.0)    # used=3>K → 不良
    st = sc.get_state()
    assert st["ng_count"] == 2
    # 两件超限 NG 各触发一次 swab_over_limit_event(=2)
    ng_events = [e for e in host.events if e[1] == 2 and "不良" in e[2]]
    assert len(ng_events) == 2


def test_swab_over_limit_backward_compat_alarm(sc):
    """老配置直配 alarm_event 时, 擦满/超限仍触发硬件报警 (向后兼容, 优先 legacy)."""
    host = _setup(sc, {
        "count_channels": [0], "count_anchor_label": "查看产品有无脏污",
        "max_uses_per_swab": 1, "move_confirm_frames": 1,
        "swab_over_limit_event_id": 2, "fake_wipe_event_id": 0,
        "alarm_event": "event2", "force_lock_frames": 0,
    })
    _count_one(sc, host, 0, 0.0)
    assert any(a[1] == "event2" for a in host.alarms)


def test_operator_absent_fires_event(sc):
    """操作员离开: 视角2 持续空帧超 timeout → trigger_event(swap_channel, event)."""
    host = _setup(sc, {
        "swap_channel": 1, "operator_absent_enabled": True,
        "operator_absent_event_id": 5, "operator_absent_timeout_sec": 10,
    })
    _frame(sc, 1, 0.0, [])     # deadline=10
    _frame(sc, 1, 11.0, [])    # 超时 → 命中
    assert any(e[0] == 1 and e[1] == 5 for e in host.events)


def test_operator_absent_reset_when_present(sc):
    """视角2 检测到人 → 重置倒计时, 不误报."""
    host = _setup(sc, {
        "swap_channel": 1, "operator_absent_enabled": True,
        "operator_absent_event_id": 5, "operator_absent_timeout_sec": 10,
        "swap_label": "更换棉签",
    })
    _frame(sc, 1, 0.0, [])
    _frame(sc, 1, 5.0, [_box("更换棉签", 0.5, 0.5)])  # 人在 → 重置
    _frame(sc, 1, 12.0, [])                          # 距重置仅 7s < 10s
    assert host.events == []


def test_operator_absent_disabled_no_event(sc):
    """操作员离开开关关 → 永不触发 (默认零差异)."""
    host = _setup(sc, {
        "swap_channel": 1, "operator_absent_enabled": False,
        "operator_absent_event_id": 5, "operator_absent_timeout_sec": 10,
    })
    _frame(sc, 1, 0.0, [])
    _frame(sc, 1, 99.0, [])
    assert host.events == []


def test_no_fake_swab_change_judgment(sc):
    """明确不做"假换棉签": 视角2 棉签框静止不触发任何 fake 事件."""
    host = _setup(sc, {
        "swap_channel": 1, "operator_absent_enabled": False,
        "count_channels": [0], "fake_wipe_event_id": 2,
    })
    # 视角2 通道里"擦拭产品/更换棉签"框静止 — 假擦拭只在视角1, 这里不该触发
    _frame(sc, 1, 0.0, [_box("更换棉签", 0.5, 0.5)])
    _frame(sc, 1, 3.0, [_box("更换棉签", 0.5, 0.5)])
    assert host.events == []


def test_trigger_event_exception_isolated(sc):
    """host.trigger_event 抛异常 → on_detection_frame 不崩 (错误隔离)."""
    host = _setup(sc, {
        "count_channels": [0], "fake_wipe_event_id": 3,
        "fake_wipe_still_time": 2.0, "swab_over_limit_event_id": 0,
    })

    def boom(channel_id, event_id, reason=""):
        raise RuntimeError("simulated")

    host.trigger_event = boom
    _frame(sc, 0, 0.0, [_box("擦拭产品", 0.5, 0.5)])
    _frame(sc, 0, 2.5, [_box("擦拭产品", 0.5, 0.5)])  # 命中但 trigger 抛 → 被 swallow


# ============================================================
# C. 配置读写 round-trip (/swab/config)
# ============================================================


def test_save_and_get_config_roundtrip(sc):
    """save_config 合并写库 → get_config 回填 (前端配置 Tab 闭环)."""
    _setup(sc, {})
    sc.save_config({"fake_wipe_event_id": 9, "operator_absent_timeout_sec": 300})
    cfg = sc.get_config()
    assert cfg["fake_wipe_event_id"] == 9
    assert cfg["operator_absent_timeout_sec"] == 300
    # 未提交的字段保留默认
    assert cfg["max_uses_per_swab"] == 11


def test_operator_absent_enabled_bool_roundtrip(sc):
    """操作员离开开关 true 写库后 get_config 必须仍是 bool True (不能丢成默认 False)."""
    host = _setup(sc, {})
    sc.save_config({"operator_absent_enabled": True, "operator_absent_event_id": 2})
    assert sc.get_config()["operator_absent_enabled"] is True
    sc.reload_config()
    assert sc.get_config()["operator_absent_enabled"] is True
    # 模拟历史脏数据: 字符串 '1' / 'true' 也应读成 True
    host._raw = host._raw.replace('"operator_absent_enabled": true', '"operator_absent_enabled": "1"')
    sc.reload_config()
    assert sc.get_config()["operator_absent_enabled"] is True


def test_operator_absent_timeout_roundtrip(sc):
    """离岗超时秒数保存后必须持久 (客户反馈'改不了/保存后回退'回归守护)."""
    _setup(sc, {})
    sc.save_config({"operator_absent_timeout_sec": 30})
    assert sc.get_config()["operator_absent_timeout_sec"] == 30
    sc.reload_config()
    assert sc.get_config()["operator_absent_timeout_sec"] == 30


# ============================================================
# D. v1.1.2 正常计件事件 + 抑制主程序周期结算塔灯
# ============================================================


def test_normal_count_fires_configured_ok_event(sc):
    """正常计件默认连合格 OK (event_id=1)."""
    host = _setup(sc, {
        "count_channels": [0], "count_anchor_label": "查看产品有无脏污",
        "normal_count_event_id": 1, "move_confirm_frames": 1,
        "swab_over_limit_event_id": 0, "fake_wipe_event_id": 0,
        "max_uses_per_swab": 99, "move_threshold": 0.0116,
    })
    _frame(sc, 0, 0.0, [_box("查看产品有无脏污", 0.5, 0.5)])
    _frame(sc, 0, 0.1, [_box("查看产品有无脏污", 0.7, 0.5)])
    assert any(e[0] == 0 and e[1] == 1 for e in host.events)
    assert any("正常计件" in e[2] for e in host.events)


def test_normal_count_can_fire_ng_event(sc):
    """正常计件可配置连不良 NG."""
    host = _setup(sc, {
        "count_channels": [0], "count_anchor_label": "查看产品有无脏污",
        "normal_count_event_id": 2, "move_confirm_frames": 1,
        "swab_over_limit_event_id": 0, "fake_wipe_event_id": 0,
        "max_uses_per_swab": 99, "move_threshold": 0.0116,
    })
    _frame(sc, 0, 0.0, [_box("查看产品有无脏污", 0.5, 0.5)])
    _frame(sc, 0, 0.1, [_box("查看产品有无脏污", 0.7, 0.5)])
    assert any(e[0] == 0 and e[1] == 2 for e in host.events)


def test_normal_count_disabled_when_event_id_zero(sc):
    """normal_count_event_id=0 → 计件不触发主程序事件."""
    host = _setup(sc, {
        "count_channels": [0], "count_anchor_label": "查看产品有无脏污",
        "normal_count_event_id": 0, "move_confirm_frames": 1,
        "swab_over_limit_event_id": 0, "fake_wipe_event_id": 0,
        "max_uses_per_swab": 99, "move_threshold": 0.0116,
    })
    _frame(sc, 0, 0.0, [_box("查看产品有无脏污", 0.5, 0.5)])
    _frame(sc, 0, 0.1, [_box("查看产品有无脏污", 0.7, 0.5)])
    assert host.events == []
    assert sc.get_state()["total_products"] == 1


def test_event_fire_suppresses_main_settle_ng_alarm(sc):
    """主程序周期结算 NG reason → suppress_alarm (默认开)."""
    _setup(sc, {"suppress_main_settle_alarm": True, "count_channels": [0], "swap_channel": 1})
    ret = sc.on_event_fire({
        "channel_id": 0,
        "event_id": 2,
        "event_kind": "NG",
        "reason": "缺少步骤: ['查看产品有无脏污']",
    })
    assert ret == {"suppress_alarm": True}


def test_event_fire_suppresses_main_settle_ok_alarm(sc):
    """主程序周期结算 OK reason → 同样抑制塔灯 (避免并行 OK 抢插件计件)."""
    _setup(sc, {"suppress_main_settle_alarm": True, "count_channels": [0]})
    ret = sc.on_event_fire({
        "channel_id": 0,
        "event_id": 1,
        "event_kind": "OK",
        "reason": "检测完成",
    })
    assert ret == {"suppress_alarm": True}


def test_event_fire_does_not_suppress_unrelated_reason(sc):
    """非周期结算 reason 不抑制 (插件三判定走 trigger_event, 不经 event_fire)."""
    _setup(sc, {"suppress_main_settle_alarm": True, "count_channels": [0]})
    assert sc.on_event_fire({
        "channel_id": 0, "event_id": 2, "reason": "人工强制结案",
    }) is None


def test_event_fire_suppress_disabled_by_config(sc):
    """suppress_main_settle_alarm=False → 不抑制."""
    _setup(sc, {"suppress_main_settle_alarm": False, "count_channels": [0]})
    assert sc.on_event_fire({
        "channel_id": 0, "event_id": 2, "reason": "缺少步骤: ['x']",
    }) is None


def test_event_fire_suppress_only_on_plugin_channels(sc):
    """非插件管辖通道不抑制."""
    _setup(sc, {"suppress_main_settle_alarm": True, "count_channels": [0], "swap_channel": 1})
    assert sc.on_event_fire({
        "channel_id": 5, "event_id": 2, "reason": "缺少步骤: ['x']",
    }) is None


def test_get_state_exposes_absent_enabled(sc):
    """看板状态须带离岗启用态 + 超时秒数 (前端离岗告警条依赖)."""
    _setup(sc, {"operator_absent_enabled": True, "operator_absent_timeout_sec": 45})
    st = sc.get_state()
    assert st["operator_absent_enabled"] is True
    assert st["operator_absent_timeout_sec"] == 45
    assert "absent_remaining" in st
