# -*- coding: utf-8 -*-
"""v3.57 逐件虚拟步骤 (custom_mix_per_item_virtual_step) 单元测试。

背景 (六和二工位锁螺丝): 工艺新增「开头扫码 → 逐件锁付 → 结尾扫码」, 扫码不计
次数但首尾必须各确认到。方案 = 混合逐件 + 逐件虚拟步骤 (与 v3.49 容器虚拟步骤
全对称的基建扩充):
  - 序列 [扫码, 锁付完成(虚拟), 扫码] — 两个扫码被虚拟步骤隔开, 不是"连续重复",
    disappear_delay 不被强制清零 → 同阶段多次扫码靠消失等待合并为一次出现
  - 全部逐件行完成瞬间, 虚拟标签注入稳定标签流, 走常规序列状态机
  - 末步 [扫码] 期望重复 2 次 — 开头扫码消失不结算 (repeat quota 守门, v3.19 既有),
    结尾扫码消失才结算

覆盖:
  1. 配置解析: 开关+标签生效 / 标签冲突忽略 / 默认关零差异
  2. 全流程 OK: 扫码→锁付→扫码 → last_step 结算 OK
  3. 逐件未完成: 虚拟步骤不注入
  4. 缺结尾扫码: 不结算 (等待), 空闲超时兜底 NG
  5. 缺开头扫码 + 虚拟步骤严格顺序: 虚拟步骤被拦, 补扫后可恢复
  6. 开头扫码消失不提前结算 (末步重复守门)
  7. disappear_delay 合并多次扫码 + 虚拟步骤不当规则 B 打断者

真 VSM 驱动 _update_step_stats 全 tick, 不起后端 / 不接 DB。
"""
import time

import numpy as np

_FRAME = np.zeros((32, 32, 3), dtype=np.uint8)

# 个体中心 x = 0.15 / 0.35 / 0.55
_SLIDER_XS = (0.10, 0.30, 0.50)


def _sliders(n=3):
    return [{"label": "滑块", "confidence": 0.95,
             "x": _SLIDER_XS[i], "y": 0.60, "w": 0.10, "h": 0.10}
            for i in range(n)]


def _action(width=0.60):
    """打螺丝动作框 (width=0.60 盖满 3 个中心)。"""
    return {"label": "打螺丝", "confidence": 0.95,
            "x": 0.05, "y": 0.55, "w": width, "h": 0.20}


def _scan():
    return {"label": "扫码", "confidence": 0.95,
            "x": 0.70, "y": 0.20, "w": 0.12, "h": 0.15}


def _make_vsm(*, virtual_on=True, virtual_label="锁付完成", scan_delay=0,
              strict_virtual=False, idle_timeout=0):
    from backend.api.source import VideoSourceManager

    virtual_row = {"id": 2, "label": virtual_label, "enabled": True,
                   "min_frames": 1, "per_item_virtual": True}
    if strict_virtual:
        virtual_row["strict_order"] = True
    pipeline = {
        "custom_based_on": "sequential",
        "custom_mixed_with": "per_item",
        "custom_sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 1}],
        "settlement_mode": "last_step",
        "idle_timeout_seconds": idle_timeout,
    }
    if virtual_on:
        pipeline["custom_mix_per_item_virtual_step"] = True
        pipeline["custom_mix_per_item_virtual_step_label"] = virtual_label

    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99570,
        "name": "逐件虚拟步骤单测项目",
        "logic_mode": "custom",
        "pipeline_config": pipeline,
        "steps_config": [
            {"id": 1, "label": "扫码", "enabled": True, "min_frames": 1,
             "disappear_delay": scan_delay},
            virtual_row,
            {"id": 3, "label": "打滑块", "enabled": True, "detect_role": "item",
             "per_item": {"item_label": "滑块", "action_label": "打螺丝",
                          "coverage_use_center": True, "sustain_frames": 2,
                          "expected_count": 3}},
        ],
        "events_config": [
            {"id": 1, "name": "OK", "actions": [], "show_notification": False},
            {"id": 2, "name": "NG", "actions": [], "show_notification": False},
        ],
        "counters_config": [], "data_config": {},
    })
    # 隔离录像 / DB / 事件外设
    vsm.start_step_recording = lambda *a, **k: None
    vsm.stop_step_recording = lambda *a, **k: None
    vsm.record_step = lambda *a, **k: None
    vsm.start_cycle = lambda *a, **k: None
    vsm._test_events = []
    vsm._trigger_event = (
        lambda eid, reason='', **k: vsm._test_events.append((eid, reason)))
    vsm._last_screenshot_time = time.time() + 3600
    return vsm


def _feed(vsm, dets):
    vsm._update_step_stats(list(dets), _FRAME)


def _do_lock_all(vsm):
    """锁定 3 个个体 + 动作盖满 (sustain 2 帧) → 逐件行完成。"""
    for _ in range(3):
        _feed(vsm, _sliders(3))
    for _ in range(3):
        _feed(vsm, _sliders(3) + [_action()])


# ==================== 1. 配置解析 ====================

def test_config_virtual_label_set():
    vsm = _make_vsm()
    assert vsm._custom_mix is not None
    assert vsm._custom_mix.mix_type == "per_item"
    assert vsm._custom_mix.virtual_step_label == "锁付完成"


def test_config_default_off_zero_diff():
    vsm = _make_vsm(virtual_on=False)
    assert vsm._custom_mix.virtual_step_label == ""
    # 逐件行完成也不注入
    _do_lock_all(vsm)
    _feed(vsm, [])
    assert "锁付完成" not in vsm.current_cycle_steps


def test_config_label_collision_ignored():
    """虚拟步骤名与个体/动作标签重名 → 忽略 (语义无法两立)。"""
    vsm = _make_vsm(virtual_label="滑块")
    assert vsm._custom_mix.virtual_step_label == ""


# ==================== 2. 全流程 OK ====================

def test_full_flow_scan_lock_scan_ok():
    """扫码 → 逐件全覆盖(虚拟步骤自动入周期) → 扫码 → last_step 结算 OK。"""
    vsm = _make_vsm()
    # 开头扫码 (出现→消失=完成; 末步重复守门: 1/2 不结算)
    _feed(vsm, [_scan()])
    _feed(vsm, [_scan()])
    _feed(vsm, [])
    assert vsm.current_cycle_steps == ["扫码"]
    assert vsm._test_events == [], "开头扫码消失不应提前结算"
    # 逐件锁付 → 虚拟步骤注入入周期
    _do_lock_all(vsm)
    assert vsm._custom_mix.virtual_step_complete()
    _feed(vsm, _sliders(3))          # 注入帧 (个体仍在场, 已被剥离不进步骤侧)
    assert vsm.current_cycle_steps == ["扫码", "锁付完成"]
    _feed(vsm, _sliders(3))          # 入周期后停止注入 → 虚拟标签消失 → 完成
    # 结尾扫码 → 末步凑满 2 次 → 结算
    _feed(vsm, [_scan()])
    assert vsm.current_cycle_steps == ["扫码", "锁付完成", "扫码"]
    _feed(vsm, [])
    assert len(vsm._test_events) == 1, f"应结算一次: {vsm._test_events}"
    eid, reason = vsm._test_events[0]
    assert eid == 1, f"应判 OK: {vsm._test_events}"


def test_full_flow_partial_coverage_ng():
    """逐件只盖到 2/3 → 虚拟步骤不注入; 强行走完扫码也不可能凑齐序列。"""
    vsm = _make_vsm()
    _feed(vsm, [_scan()])
    _feed(vsm, [])
    for _ in range(3):
        _feed(vsm, _sliders(3))
    for _ in range(3):
        _feed(vsm, _sliders(3) + [_action(0.30)])   # 只盖前 2 个
    assert not vsm._custom_mix.virtual_step_complete()
    _feed(vsm, _sliders(3))
    assert "锁付完成" not in vsm.current_cycle_steps, "未全覆盖不得注入虚拟步骤"


# ==================== 3. 缺结尾扫码 ====================

def test_missing_end_scan_waits_then_idle_ng():
    """全覆盖后没有结尾扫码 → 不结算 (等待); 空闲超时兜底 NG。"""
    vsm = _make_vsm(idle_timeout=30)
    _feed(vsm, [_scan()])
    _feed(vsm, [])
    _do_lock_all(vsm)
    _feed(vsm, _sliders(3))
    _feed(vsm, _sliders(3))
    assert vsm.current_cycle_steps == ["扫码", "锁付完成"]
    assert vsm._test_events == [], "缺结尾扫码不得结算"
    # 空闲超时兜底: 把最后入周期时刻和物品侧活动脉冲一起拨回 31s 前
    # (v3.57 覆盖推进会顺推空闲锚点, 只拨锚点会被脉冲顶回来)
    vsm._last_step_added_time = time.time() - 31
    vsm._custom_mix._engine.last_progress_time = time.time() - 31
    _feed(vsm, [])
    assert len(vsm._test_events) == 1
    eid, reason = vsm._test_events[0]
    assert eid == 2, f"空闲超时应判 NG: {vsm._test_events}"


def test_item_progress_defers_idle_timeout():
    """v3.57 物品侧活动脉冲: 逐件覆盖推进视同周期在推进, 空闲超时不强杀干活中
    的周期 (逐件作业动辄几分钟, 步骤序列期间零推进)。"""
    vsm = _make_vsm(idle_timeout=30)
    _feed(vsm, [_scan()])
    _feed(vsm, [])
    for _ in range(3):
        _feed(vsm, _sliders(3))
    for _ in range(3):
        _feed(vsm, _sliders(3) + [_action(0.30)])   # 部分覆盖 (2/3), 账面在推进
    # 步骤侧锚点已"过期", 但物品侧刚有覆盖推进 → 脉冲顺推锚点, 空闲不开火
    vsm._last_step_added_time = time.time() - 31
    _feed(vsm, _sliders(3))
    assert vsm._test_events == [], f"覆盖推进期间不应空闲强杀: {vsm._test_events}"


# ==================== 4. 缺开头扫码 (虚拟步骤严格顺序) ====================

def test_missing_start_scan_strict_virtual_blocks_then_recovers():
    """虚拟步骤开严格顺序: 没扫开头码 → 虚拟步骤被拦不入周期;
    补扫后重新放行 → 全流程仍可 OK (忘扫可补救)。"""
    vsm = _make_vsm(strict_virtual=True)
    _do_lock_all(vsm)
    _feed(vsm, _sliders(3))
    assert "锁付完成" not in vsm.current_cycle_steps, "缺前置扫码应被严格顺序拦下"
    # 工人补扫开头码 → 前置补齐, 虚拟步骤同帧起即可放行入周期
    _feed(vsm, [_scan()])
    _feed(vsm, [])
    _feed(vsm, _sliders(3))
    assert vsm.current_cycle_steps == ["扫码", "锁付完成"]
    _feed(vsm, _sliders(3))
    _feed(vsm, [_scan()])
    _feed(vsm, [])
    assert vsm._test_events and vsm._test_events[0][0] == 1, \
        f"补扫后应正常 OK: {vsm._test_events}"


# ==================== 5. disappear_delay 合并多次扫码 ====================

def test_scan_bursts_merge_via_disappear_delay():
    """扫码配消失等待 8s: 同阶段多次扫码间隔 < 8s → 合并为一次出现;
    锁付动作/个体标签已剥离, 不触发规则 B 提前打断。"""
    vsm = _make_vsm(scan_delay=8)
    # 第一阵扫码
    _feed(vsm, [_scan()])
    # 中断 2 帧 (wall clock 远小于 8s) — 仍在消失等待中
    _feed(vsm, [])
    _feed(vsm, [])
    assert "扫码" in vsm.step_last_seen, "等待期内不应按消失处理"
    # 第二阵扫码 — 接续同一次出现, 周期里仍只有一次扫码
    _feed(vsm, [_scan()])
    assert vsm.current_cycle_steps == ["扫码"]
    # 锁付进行中 (个体+动作在场) — 这些标签被剥离, 不算"其他有意义步骤",
    # 扫码的消失等待不被规则 B 打断
    _feed(vsm, _sliders(3))
    assert "扫码" in vsm.step_last_seen, "个体/动作标签不得打断扫码的消失等待"
    # 等待真正耗尽 → 完成
    vsm.step_last_seen["扫码"] = time.time() - 9
    _do_lock_all(vsm)
    _feed(vsm, _sliders(3))
    assert vsm.current_cycle_steps == ["扫码", "锁付完成"]
    _feed(vsm, _sliders(3))
    # 结尾扫码 (同样合并语义) → 耗尽等待 → 结算
    _feed(vsm, [_scan()])
    assert vsm.current_cycle_steps == ["扫码", "锁付完成", "扫码"]
    vsm.step_last_seen["扫码"] = time.time() - 9
    _feed(vsm, [])
    assert vsm._test_events and vsm._test_events[0][0] == 1, \
        f"合并扫码后应 OK 结算: {vsm._test_events}"


def test_mid_lock_scan_does_not_pollute_cycle():
    """锁付中途 (个体未完成) 工人拿起 PDA 扫了一下 → 不污染周期:
    序列守门拒绝该次扫码入周期 (期望的下一步是虚拟步骤), 后续照常 OK 结算。"""
    vsm = _make_vsm()
    _feed(vsm, [_scan()])
    _feed(vsm, [])
    for _ in range(3):
        _feed(vsm, _sliders(3))
    for _ in range(2):
        _feed(vsm, _sliders(3) + [_action(0.30)])   # 只盖 2/3, 未完成
    _feed(vsm, _sliders(3) + [_scan()])              # 中途扫码
    _feed(vsm, _sliders(3))
    assert vsm.current_cycle_steps == ["扫码"], \
        f"中途扫码不得入周期: {vsm.current_cycle_steps}"
    for _ in range(3):
        _feed(vsm, _sliders(3) + [_action()])        # 补齐覆盖
    _feed(vsm, _sliders(3))
    _feed(vsm, _sliders(3))
    assert vsm.current_cycle_steps == ["扫码", "锁付完成"]
    _feed(vsm, [_scan()])
    _feed(vsm, [])
    assert vsm._test_events and vsm._test_events[0][0] == 1, \
        f"中途扫码后仍应 OK 结算: {vsm._test_events}"


def test_virtual_injection_not_rule_b_interrupter():
    """虚拟步骤注入 (整件达标持续在场) 不作为规则 B 打断者:
    扫码还在消失等待时逐件完成 → 虚拟步骤入周期, 扫码等待不被打断。"""
    vsm = _make_vsm(scan_delay=8)
    _feed(vsm, [_scan()])
    _feed(vsm, [])
    # 扫码在等待中, 完成逐件
    _do_lock_all(vsm)
    _feed(vsm, _sliders(3))
    assert "锁付完成" in vsm.current_cycle_steps
    assert "扫码" in vsm.step_last_seen, "虚拟步骤注入不得打断扫码的消失等待"
