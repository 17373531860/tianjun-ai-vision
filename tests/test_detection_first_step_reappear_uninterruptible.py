# -*- coding: utf-8 -*-
"""v3.60.1 检测模式首步重现结算尊重「消失等待不被打断」(disappear_uninterruptible).

背景: v3.34 给顺序模式 first_step 结算加了 _held_visible 豁免 — 首步开
disappear_uninterruptible 且上一次出现尚未走完消失结算 (old_last_seen 非 None,
等待桥接中) 时, 本次出现是同一次放置的闪断回位, 不触发首步重现结算。
检测模式的首步重现结算块漏掉了同款豁免 → 六和组装工位「放下层母排 → 拿起
扫码 2~4s → 放回」被切成两个 NG 周期, 即使消失等待+不被打断都已配置。

本测试覆盖:
  1. 默认 (开关不配置) — 零差异: 首步消失结算后重现 → 正常触发周期结算
  2. 开关开启 — 消失等待桥接中的重现 (old_last_seen 非 None) 不触发结算
  3. 开关开启 — 真正走完消失结算后的重现仍正常触发 (不是永不结算)

只验证状态机单点行为, 不接触主推理循环 / DB / 报警。
"""
import time

import numpy as np


def _make_vsm(uninterruptible: bool, disappear_delay: float = 10):
    """构造两步骤检测模式项目: A(首步) + B。A 按参数决定是否豁免规则 B。"""
    from backend.api.source import VideoSourceManager

    step_a = {'id': 1, 'label': 'A', 'enabled': True, 'min_frames': 1,
              'disappear_delay': disappear_delay}
    if uninterruptible:
        step_a['disappear_uninterruptible'] = True
    steps_config = [
        step_a,
        {'id': 2, 'label': 'B', 'enabled': True, 'min_frames': 1},
    ]
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        'id': 99591,
        'name': '检测模式首步重现豁免单测项目',
        'task_type': 'detection',
        'logic_mode': 'detection',
        'steps_config': steps_config,
        'events_config': [
            {'id': 1, 'name': 'OK', 'actions': [], 'show_notification': False},
            {'id': 2, 'name': 'NG', 'actions': [], 'show_notification': False},
        ],
        'counters_config': [],
        'pipeline_config': {},
    })
    # 隔离录像 / DB / 事件外设
    vsm.start_step_recording = lambda *a, **k: None
    vsm.stop_step_recording = lambda *a, **k: None
    vsm.record_step = lambda *a, **k: None
    vsm.start_cycle = lambda *a, **k: None
    vsm._test_events = []
    vsm._trigger_event = lambda eid, reason='': vsm._test_events.append((eid, reason))
    # 记录检测模式周期结算调用
    vsm._settle_calls = []
    _orig_settle = vsm._settle_detection_cycle

    def _spy_settle(*a, **k):
        vsm._settle_calls.append(list(vsm.current_cycle_steps))
        return _orig_settle(*a, **k)

    vsm._settle_detection_cycle = _spy_settle
    # 跳过截图分支
    vsm._last_screenshot_time = time.time() + 3600
    return vsm


_FRAME = np.zeros((32, 32, 3), dtype=np.uint8)


def _feed(vsm, labels):
    dets = [{'label': lbl, 'confidence': 0.9,
             'x': 0.1, 'y': 0.1, 'w': 0.2, 'h': 0.2} for lbl in labels]
    vsm._update_step_stats(dets, _FRAME)


def test_default_reappear_settles():
    """默认零差异: 首步被规则 B 消失结算后重现 → 触发周期结算。"""
    vsm = _make_vsm(uninterruptible=False)
    _feed(vsm, ['A'])
    _feed(vsm, ['B'])          # 规则 B: A 立即按消失处理, step_last_seen 清掉
    assert 'A' not in vsm.step_last_seen
    assert vsm.current_cycle_steps == ['A', 'B']
    _feed(vsm, ['A'])          # 首步重现 → 结算
    assert len(vsm._settle_calls) == 1, "默认行为应保持: 首步消失后重现触发检测模式周期结算"
    assert vsm._settle_calls[0] == ['A', 'B']


def test_uninterruptible_bridged_reappear_no_settle():
    """开关开启: 消失等待桥接中的首步重现 (扫码闪断回位) 不触发周期结算。"""
    vsm = _make_vsm(uninterruptible=True)
    _feed(vsm, ['A'])
    _feed(vsm, ['B'])          # 不被打断: A 的 step_last_seen 保留 (等待桥接中)
    assert 'A' in vsm.step_last_seen
    assert vsm.current_cycle_steps == ['A', 'B']
    _feed(vsm, ['A'])          # 闪断回位: 不应切周期
    assert vsm._settle_calls == [], \
        "消失等待桥接中的首步重现不应触发结算 (v3.60.1 检测模式豁免)"
    assert vsm.current_cycle_steps == ['A', 'B'], "周期应保持, 首步不应重复入账"


def test_uninterruptible_true_disappear_then_reappear_settles():
    """开关开启但真正走完消失结算后重现 → 仍正常触发 (不是永不结算)。"""
    vsm = _make_vsm(uninterruptible=True, disappear_delay=0.2)
    _feed(vsm, ['A'])
    _feed(vsm, ['B'])
    time.sleep(0.35)           # 超过 disappear_delay, 等待真正耗尽
    _feed(vsm, ['B'])          # 触发 A 的消失结算
    assert 'A' not in vsm.step_last_seen, "等待耗尽后 A 应走完消失结算"
    _feed(vsm, ['A'])          # 真实新工件边界 → 正常结算
    assert len(vsm._settle_calls) == 1, "真正消失后的首步重现必须仍触发周期结算"
