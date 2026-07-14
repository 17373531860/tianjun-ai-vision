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


def test_swab_window_first_action_not_blocked_by_cold_start_lock(sc):
    """lock_time 只约束相邻两次命中，不能误伤视频开头的第一次真实动作。"""
    w = sc.SwabChangeWindow(lock_time=2.0, min_sustain_sec=0.12, gap_sec=0.2)
    assert w.feed(True, 0.00) is False
    assert w.feed(True, 0.13) is True


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


def test_swab_window_continuous_action_emits_only_once_after_lock_time(sc):
    """同一连续动作段即使持续超过 lock_time，也只能算一次有效更换。

    旧逻辑只靠冷却时间去重，长动作在冷却到期后会再次触发；生产语义必须先看到
    换棉签框真实消失超过 gap_sec，才允许下一次动作重新计数。
    """
    w = sc.SwabChangeWindow(lock_time=0.5, min_sustain_sec=0.12, gap_sec=0.2)
    hits, t = 0, 10.0
    while t <= 12.0:                               # 连续 2 秒，跨过多个 lock_time
        if w.feed(True, t):
            hits += 1
        t += 1 / 60.0
    assert hits == 1


def test_swab_window_brief_dropout_does_not_rearm_same_action(sc):
    """动作中短时丢框不切段；即使跨过 lock_time，也不能把同一动作计第二次。"""
    w = sc.SwabChangeWindow(lock_time=0.5, min_sustain_sec=0.12, gap_sec=0.2)
    hits = 0
    for t in (10.00, 10.13):
        hits += int(w.feed(True, t))
    w.feed(False, 10.20)                            # 缺框 0.07s < gap_sec
    for t in (10.25, 10.35, 10.45, 10.55, 10.65, 10.75, 10.85, 10.95):
        hits += int(w.feed(True, t))
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


def test_swab_production_default_uses_truth_calibrated_sustain(sc):
    """生产出厂值必须使用三段客户真值共同标定的时间门槛。"""
    assert sc.DEFAULT_CONFIG["swab_min_sustain_sec"] == pytest.approx(0.12)
    assert sc.DEFAULT_CONFIG["swab_lock_time"] == pytest.approx(0.25)
    assert sc.DEFAULT_CONFIG["lost_gone_sec"] == pytest.approx(0.15)
    assert sc.DEFAULT_CONFIG["force_lock_sec"] == pytest.approx(1.4)


def test_swab_v142_saved_defaults_migrate_to_truth_calibrated_profile(sc):
    """升级旧插件时，已落盘的 v1.4.2 出厂值必须整体迁到新真值配置。"""
    _setup(sc, {
        "swab_min_sustain_sec": 0.0, "swab_lock_time": 2.0,
        "lost_gone_sec": 0.25, "force_lock_sec": 1.6,
    })
    cfg = sc.get_config()
    assert cfg["_config_revision"] == 2
    assert cfg["swab_min_sustain_sec"] == pytest.approx(0.12)
    assert cfg["swab_lock_time"] == pytest.approx(0.25)
    assert cfg["lost_gone_sec"] == pytest.approx(0.15)
    assert cfg["force_lock_sec"] == pytest.approx(1.4)


def test_swab_current_config_can_explicitly_disable_sustain_gate(sc):
    """迁移完成后仍保留显式调参能力：revision=2 时允许人工设回 0。"""
    _setup(sc, {"_config_revision": 2, "swab_min_sustain_sec": 0.0})
    assert sc.get_config()["swab_min_sustain_sec"] == pytest.approx(0.0)


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
        "count_require_label": "",  # 本用例只测联动, 关掉 v1.4.2 默认许可
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
        "count_require_label": "",  # 本用例只测联动, 关掉 v1.4.2 默认许可
        "max_uses_per_swab": 1, "move_confirm_frames": 1,
        "normal_count_event_id": 1, "swab_over_limit_event_id": 2,
        "fake_wipe_event_id": 0, "move_threshold": 0.0116, "force_lock_frames": 0,
        "lock_time": 0.0, "lock_spatial": 0.0,  # 关位置/时间锁, 让连续计件
        "force_lock_sec": 0,                    # 连续计件需关 v1.4.1 时间制强锁
    })
    _count_one(sc, host, 0, 0.0)    # used=1=K (合格)
    _count_one(sc, host, 0, 1.0)    # used=2>K → 不良
    _count_one(sc, host, 0, 2.0)    # used=3>K → 不良
    st = sc.get_state()
    assert st["ng_count"] == 2
    # 两件超限 NG 各触发一次 swab_over_limit_event(=2)
    ng_events = [e for e in host.events if e[1] == 2 and "不良" in e[2]]
    assert len(ng_events) == 2


def test_swab_truth_rule_11th_ok_12th_ng(sc):
    """客户真值规则：第 11 件仍合格，第 12 件起逐件判 NG。"""
    host = _setup(sc, {
        "count_channels": [0], "count_anchor_label": "查看产品有无脏污",
        "count_require_label": "", "max_uses_per_swab": 11,
        "move_confirm_frames": 1, "move_threshold": 0.0116,
        "lock_time": 0.0, "lock_spatial": 0.0,
        "force_lock_frames": 0, "force_lock_sec": 0,
        "normal_count_event_id": 1, "swab_over_limit_event_id": 2,
        "fake_wipe_event_id": 0,
    })
    for item_index in range(12):
        _count_one(sc, host, 0, float(item_index))

    state = sc.get_state()
    assert state["swab_used"] == 12
    assert state["ng_count"] == 1
    assert len([event for event in host.events if event[1] == 1]) == 11
    assert len([event for event in host.events if event[1] == 2 and "不良" in event[2]]) == 1


def test_swab_over_limit_backward_compat_alarm(sc):
    """老配置直配 alarm_event 时, 擦满/超限仍触发硬件报警 (向后兼容, 优先 legacy)."""
    host = _setup(sc, {
        "count_channels": [0], "count_anchor_label": "查看产品有无脏污",
        "count_require_label": "",  # 本用例只测联动, 关掉 v1.4.2 默认许可
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
        "count_require_label": "",  # 本用例只测联动, 关掉 v1.4.2 默认许可
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
        "count_require_label": "",  # 本用例只测联动, 关掉 v1.4.2 默认许可
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
        "count_require_label": "",  # 本用例只测联动, 关掉 v1.4.2 默认许可
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


# ============================================================
# E. v1.4.0 双类别计数许可 (detect9(1) _both_seen 对齐)
# ============================================================

_GATE_CFG = {
    "count_channels": [0], "count_anchor_label": "正常产品",
    "count_require_label": "脏污产品", "move_confirm_frames": 1,
    "normal_count_event_id": 1, "swab_over_limit_event_id": 0,
    "fake_wipe_event_id": 0, "max_uses_per_swab": 99,
    "move_threshold": 0.0116, "force_lock_frames": 0,
    "lost_frame_thresh": 2,
    # 本节验证帧数制语义, 显式关掉 v1.4.1 时间制默认值
    "lost_gone_sec": 0, "force_lock_sec": 0,
}


def test_require_label_blocks_count_without_companion(sc):
    """许可标签从未出现: 只移动锚标签 → 不计数不触发事件 (客户反馈场景)."""
    host = _setup(sc, dict(_GATE_CFG))
    _frame(sc, 0, 0.0, [_box("正常产品", 0.5, 0.5)])
    _frame(sc, 0, 0.1, [_box("正常产品", 0.7, 0.5)])
    _frame(sc, 0, 0.2, [_box("正常产品", 0.5, 0.5)])
    assert sc.get_state()["total_products"] == 0
    assert host.events == []


def test_require_label_counts_when_both_present(sc):
    """同帧双类别都在 → 许可解锁 + 位移达标计 1 件."""
    host = _setup(sc, dict(_GATE_CFG))
    _frame(sc, 0, 0.0, [_box("正常产品", 0.5, 0.5), _box("脏污产品", 0.3, 0.3)])
    _frame(sc, 0, 0.1, [_box("正常产品", 0.7, 0.5), _box("脏污产品", 0.3, 0.3)])
    assert sc.get_state()["total_products"] == 1
    assert any(e[1] == 1 for e in host.events)


def test_require_label_alternating_classes_counts(sc):
    """detect9(1) 核心语义: 两类交替出现 (不同帧) 也解锁计数 —
    先见"脏污产品"(许可解锁), 之后锚标签单独移动 → 正常计 1 件."""
    host = _setup(sc, dict(_GATE_CFG))
    _frame(sc, 0, 0.0, [_box("脏污产品", 0.3, 0.3)])          # 许可解锁 (锚不在场)
    _frame(sc, 0, 0.1, [_box("正常产品", 0.5, 0.5)])          # 锚出现建跟踪
    _frame(sc, 0, 0.2, [_box("正常产品", 0.7, 0.5)])          # 位移达标 → 计数
    assert sc.get_state()["total_products"] == 1
    assert any(e[1] == 1 for e in host.events)


def test_require_label_permit_consumed_after_count(sc):
    """计到一件后许可重置: 不再见许可标签, 第二件只动锚标签 → 不计."""
    _setup(sc, dict(_GATE_CFG))
    # 第 1 件: 许可 + 锚移动 → 计数
    _frame(sc, 0, 0.0, [_box("脏污产品", 0.3, 0.3)])
    _frame(sc, 0, 0.1, [_box("正常产品", 0.5, 0.5)])
    _frame(sc, 0, 0.2, [_box("正常产品", 0.7, 0.5)])
    assert sc.get_state()["total_products"] == 1
    # 第 2 件: 许可已被消费, 锚标签再动不计
    _frame(sc, 0, 0.3, [_box("正常产品", 0.4, 0.5)])
    _frame(sc, 0, 0.4, [_box("正常产品", 0.6, 0.5)])
    assert sc.get_state()["total_products"] == 1
    # 许可标签再次出现 → 第 2 件可计
    _frame(sc, 0, 0.5, [_box("脏污产品", 0.3, 0.3), _box("正常产品", 0.4, 0.5)])
    _frame(sc, 0, 0.6, [_box("正常产品", 0.62, 0.5)])
    assert sc.get_state()["total_products"] == 2


def test_require_label_permit_reset_on_tracker_gone(sc):
    """锚跟踪丢失销毁 → 许可同步重置 (detect9(1) tracker 销毁分支)."""
    _setup(sc, dict(_GATE_CFG))
    _frame(sc, 0, 0.0, [_box("脏污产品", 0.3, 0.3)])          # 许可解锁
    _frame(sc, 0, 0.1, [_box("正常产品", 0.5, 0.5)])          # 锚建跟踪
    _frame(sc, 0, 0.2, [])                                     # 丢 1
    _frame(sc, 0, 0.3, [])                                     # 丢 2 → 跟踪销毁+许可重置
    _frame(sc, 0, 0.4, [_box("正常产品", 0.5, 0.5)])          # 新跟踪, 无许可
    _frame(sc, 0, 0.5, [_box("正常产品", 0.7, 0.5)])
    assert sc.get_state()["total_products"] == 0


def test_require_label_empty_keeps_v120_behavior(sc):
    """许可留空 (默认): 仅锚标签即可计数, 与 v1.2.0 零差异."""
    cfg = dict(_GATE_CFG)
    cfg["count_require_label"] = ""
    host = _setup(sc, cfg)
    _frame(sc, 0, 0.0, [_box("正常产品", 0.5, 0.5)])
    _frame(sc, 0, 0.1, [_box("正常产品", 0.7, 0.5)])
    assert sc.get_state()["total_products"] == 1


def test_require_label_config_roundtrip(sc):
    """许可标签经配置保存/回读闭环 (前端 Tab 字段依赖)。
    v1.4.2 出厂默认开启(擦拭产品), 可改可清空(回 v1.2.0 行为)且往返一致."""
    _setup(sc, {})
    assert sc.get_config()["count_require_label"] == "擦拭产品"
    sc.save_config({"count_require_label": "脏污产品"})
    assert sc.get_config()["count_require_label"] == "脏污产品"
    sc.reload_config()
    assert sc.get_config()["count_require_label"] == "脏污产品"
    sc.save_config({"count_require_label": ""})
    assert sc.get_config()["count_require_label"] == ""


# ============================================================
# F. v1.4.0 按标签 ROI 区域过滤
# ============================================================

# 左半屏 ROI (归一化多边形)
_LEFT_ROI = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]


def test_roi_filters_anchor_outside(sc):
    """锚标签 ROI: 框中心在 ROI 外 → 视为不在场, 不计数."""
    cfg = dict(_GATE_CFG)
    cfg["count_require_label"] = ""
    cfg["label_rois"] = {"count_anchor": _LEFT_ROI}
    _setup(sc, cfg)
    # 全程在右半屏移动 (ROI 外)
    _frame(sc, 0, 0.0, [_box("正常产品", 0.7, 0.5)])
    _frame(sc, 0, 0.1, [_box("正常产品", 0.9, 0.5)])
    assert sc.get_state()["total_products"] == 0


def test_roi_counts_anchor_inside(sc):
    """锚标签 ROI: 框中心在 ROI 内移动 → 正常计数."""
    cfg = dict(_GATE_CFG)
    cfg["count_require_label"] = ""
    cfg["label_rois"] = {"count_anchor": _LEFT_ROI}
    _setup(sc, cfg)
    _frame(sc, 0, 0.0, [_box("正常产品", 0.1, 0.5)])
    _frame(sc, 0, 0.1, [_box("正常产品", 0.3, 0.5)])
    assert sc.get_state()["total_products"] == 1


def test_roi_filters_require_label_outside(sc):
    """许可标签 ROI: 许可标签只在 ROI 外出现 → 不解锁, 锚移动不计数."""
    cfg = dict(_GATE_CFG)
    cfg["label_rois"] = {"count_require": _LEFT_ROI}
    _setup(sc, cfg)
    _frame(sc, 0, 0.0, [_box("脏污产品", 0.8, 0.5)])          # ROI 外, 不解锁
    _frame(sc, 0, 0.1, [_box("正常产品", 0.5, 0.5)])
    _frame(sc, 0, 0.2, [_box("正常产品", 0.7, 0.5)])
    assert sc.get_state()["total_products"] == 0
    # ROI 内出现 → 解锁可计
    _frame(sc, 0, 0.3, [_box("脏污产品", 0.2, 0.5)])
    _frame(sc, 0, 0.4, [_box("正常产品", 0.4, 0.5)])
    _frame(sc, 0, 0.5, [_box("正常产品", 0.62, 0.5)])
    assert sc.get_state()["total_products"] == 1


def test_roi_filters_swap_label(sc):
    """换棉签标签 ROI: ROI 外的换棉签动作不解锁清零."""
    cfg = {"swap_channel": 1, "swap_label": "更换棉签",
           "label_rois": {"swap": _LEFT_ROI}}
    _setup(sc, cfg)
    with sc._LOCK:
        sc._state["swab_used"] = 5
    # ROI 外连刷两帧 (稳定窗口本可触发; t 从 10 起避开 lock_time 冷启动窗)
    _frame(sc, 1, 10.0, [_box("更换棉签", 0.8, 0.5)])
    _frame(sc, 1, 10.1, [_box("更换棉签", 0.8, 0.5)])
    assert sc.get_state()["swab_used"] == 5
    # ROI 内 → 正常解锁清零
    _frame(sc, 1, 10.2, [_box("更换棉签", 0.2, 0.5)])
    _frame(sc, 1, 10.35, [_box("更换棉签", 0.2, 0.5)])  # 持续 0.15s >= 默认 0.12s
    assert sc.get_state()["swab_used"] == 0


def test_roi_empty_or_invalid_means_unrestricted(sc):
    """ROI 空/不足3点 = 不限制 (老配置零差异 + 配错不拦停)."""
    cfg = dict(_GATE_CFG)
    cfg["count_require_label"] = ""
    cfg["label_rois"] = {"count_anchor": [[0.1, 0.1]]}   # 只有 1 点, 非法
    _setup(sc, cfg)
    _frame(sc, 0, 0.0, [_box("正常产品", 0.7, 0.5)])
    _frame(sc, 0, 0.1, [_box("正常产品", 0.9, 0.5)])
    assert sc.get_state()["total_products"] == 1


def test_roi_config_roundtrip(sc):
    """label_rois 经配置保存/回读闭环 (前端 ROI 编辑器依赖)."""
    _setup(sc, {})
    assert sc.get_config()["label_rois"] == {}
    sc.save_config({"label_rois": {"count_anchor": _LEFT_ROI}})
    assert sc.get_config()["label_rois"]["count_anchor"] == _LEFT_ROI
    sc.reload_config()
    assert sc.get_config()["label_rois"]["count_anchor"] == _LEFT_ROI


# ============================================================
# G. v1.4.1 时间制离场/强锁 (抗主程序实时丢帧)
# ============================================================

_TIME_CFG = {
    "count_channels": [0], "count_anchor_label": "正常产品",
    "count_require_label": "", "move_confirm_frames": 1,
    "normal_count_event_id": 1, "swab_over_limit_event_id": 0,
    "fake_wipe_event_id": 0, "max_uses_per_swab": 99,
    "move_threshold": 0.0116, "lock_spatial": 0.0145, "lock_time": 0.0,
    "force_lock_frames": 0, "lost_frame_thresh": 99,
    "lost_gone_sec": 0.15, "force_lock_sec": 1.6,
}


def test_time_gone_overrides_frame_thresh(sc):
    """时间制离场: 缺席 0.2s (仅 2 个处理帧) 即销毁跟踪, 不等 99 帧 —
    主程序丢帧场景下帧数制存活过久正是小幅度视频多计的根因."""
    cfg = dict(_TIME_CFG)
    cfg["count_require_label"] = "脏污产品"
    _setup(sc, cfg)
    _frame(sc, 0, 0.0, [_box("脏污产品", 0.3, 0.3)])          # 许可解锁
    _frame(sc, 0, 0.1, [_box("正常产品", 0.5, 0.5)])          # 锚建跟踪
    _frame(sc, 0, 0.2, [])                                     # 缺席开始
    _frame(sc, 0, 0.3, [])                                     # 缺席 0.2s >= 0.15 → 销毁+许可重置
    _frame(sc, 0, 0.4, [_box("正常产品", 0.4, 0.5)])          # 新跟踪 (first 重置), 无许可
    _frame(sc, 0, 0.5, [_box("正常产品", 0.7, 0.5)])
    assert sc.get_state()["total_products"] == 0


def test_time_gone_tolerates_brief_flicker(sc):
    """缺席 < 0.15s 的间歇漏检不销毁跟踪, 位移从原 first 继续累计."""
    _setup(sc, dict(_TIME_CFG))
    _frame(sc, 0, 0.00, [_box("正常产品", 0.5, 0.5)])
    _frame(sc, 0, 0.05, [])                                    # 漏 0.05s < 0.15 → 存活
    _frame(sc, 0, 0.10, [_box("正常产品", 0.7, 0.5)])         # 位移达标 → 计数
    assert sc.get_state()["total_products"] == 1


def test_time_force_lock_blocks_then_releases(sc):
    """时间制强锁: 计数后 1.6s 内一切检测无效, 过期后可再计."""
    _setup(sc, dict(_TIME_CFG))
    _frame(sc, 0, 0.0, [_box("正常产品", 0.5, 0.5)])
    _frame(sc, 0, 0.1, [_box("正常产品", 0.7, 0.5)])          # 第 1 件
    assert sc.get_state()["total_products"] == 1
    _frame(sc, 0, 0.5, [_box("正常产品", 0.3, 0.5)])          # 锁内: 忽略
    _frame(sc, 0, 1.0, [_box("正常产品", 0.6, 0.5)])          # 锁内: 忽略
    assert sc.get_state()["total_products"] == 1
    _frame(sc, 0, 1.8, [_box("正常产品", 0.3, 0.5)])          # 锁过期: 新跟踪
    _frame(sc, 0, 1.9, [_box("正常产品", 0.6, 0.5)])          # 第 2 件
    assert sc.get_state()["total_products"] == 2


def test_min_confidence_floor_filters_low_conf(sc):
    """插件置信度地板: 低于地板的锚/许可检出都不进判定 —
    主程序滑条调低时低置信度误检曾把小幅度视频计到 15 件."""
    cfg = dict(_TIME_CFG)
    cfg.update({"count_require_label": "脏污产品", "min_confidence": 0.7})
    _setup(sc, cfg)
    # 低置信度许可 + 低置信度锚移动 → 全被地板挡掉, 不计
    _frame(sc, 0, 0.0, [_box("脏污产品", 0.3, 0.3, conf=0.4)])
    _frame(sc, 0, 0.1, [_box("正常产品", 0.5, 0.5, conf=0.5)])
    _frame(sc, 0, 0.2, [_box("正常产品", 0.7, 0.5, conf=0.5)])
    assert sc.get_state()["total_products"] == 0
    # 高置信度同剧本 → 正常计 1 件
    _frame(sc, 0, 1.0, [_box("脏污产品", 0.3, 0.3, conf=0.9)])
    _frame(sc, 0, 1.1, [_box("正常产品", 0.5, 0.5, conf=0.9)])
    _frame(sc, 0, 1.2, [_box("正常产品", 0.7, 0.5, conf=0.9)])
    assert sc.get_state()["total_products"] == 1


def test_time_zero_falls_back_to_frame_mode(sc):
    """两个时间参数填 0 → 完全回退帧数制老行为."""
    cfg = dict(_TIME_CFG)
    cfg.update({"lost_gone_sec": 0, "force_lock_sec": 0, "lost_frame_thresh": 2})
    _setup(sc, cfg)
    _frame(sc, 0, 0.0, [_box("正常产品", 0.5, 0.5)])
    _frame(sc, 0, 0.1, [])                                     # 丢 1 (真实时长再长也不算)
    _frame(sc, 0, 9.0, [_box("正常产品", 0.7, 0.5)])          # 跟踪仍在 → 位移达标计数
    assert sc.get_state()["total_products"] == 1


def test_get_state_exposes_absent_enabled(sc):
    """看板状态须带离岗启用态 + 超时秒数 (前端离岗告警条依赖)."""
    _setup(sc, {"operator_absent_enabled": True, "operator_absent_timeout_sec": 45})
    st = sc.get_state()
    assert st["operator_absent_enabled"] is True
    assert st["operator_absent_timeout_sec"] == 45
    assert "absent_remaining" in st
