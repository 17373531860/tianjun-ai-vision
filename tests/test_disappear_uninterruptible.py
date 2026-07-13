# -*- coding: utf-8 -*-
"""v3.34 步骤级「消失等待不被其他步骤打断」开关 (disappear_uninterruptible).

背景: v3.9.0 起消失结算带规则 B — 等待期间画面出现其他有意义步骤时,
disappear_delay 立即作废按消失处理。该规则假设步骤在时间上互斥;
工具驻留画面的产线 (吹枪插在工件上、敲击与之并行可见) 不满足此假设,
驻留步骤的闪断会被误判成"消失后重现"→ first_step 模式下提前结算 NG。

本测试覆盖:
  1. 默认 (开关不配置) — 规则 B 原行为零差异: 其他步骤出现立即按消失处理
  2. 开关开启 — 其他步骤在场时消失等待仍按配置走完
  3. 开关开启 — 等待时间真正耗尽后仍正常走消失结算 (不是永不消失)
  4. 配置解析 — steps_config 布尔字段进入 step_time_config

只验证状态机单点行为, 不接触主推理循环 / DB / 报警。
"""
import time

import numpy as np
import pytest


def _make_vsm(uninterruptible: bool):
    """构造两步骤顺序项目: A(等待10s) + B(等待0s)。A 按参数决定是否豁免规则 B。"""
    from backend.api.source import VideoSourceManager

    step_a = {'id': 1, 'label': 'A', 'enabled': True, 'min_frames': 1,
              'disappear_delay': 10}
    if uninterruptible:
        step_a['disappear_uninterruptible'] = True
    steps_config = [
        step_a,
        {'id': 2, 'label': 'B', 'enabled': True, 'min_frames': 1},
    ]
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        'id': 99340,
        'name': '消失等待不打断单测项目',
        'task_type': 'detection',
        'logic_mode': 'sequential',
        'steps_config': steps_config,
        'events_config': [
            {'id': 1, 'name': 'OK', 'actions': [], 'show_notification': False},
            {'id': 2, 'name': 'NG', 'actions': [], 'show_notification': False},
        ],
        'counters_config': [],
        'pipeline_config': {
            'sequence_order': [{'step_id': 1}, {'step_id': 2}],
            'settlement_mode': 'first_step',
        },
    })
    # 隔离录像 / DB / 事件外设
    vsm.start_step_recording = lambda *a, **k: None
    vsm.stop_step_recording = lambda *a, **k: None
    vsm.record_step = lambda *a, **k: None
    vsm.start_cycle = lambda *a, **k: None
    vsm._test_events = []
    vsm._trigger_event = lambda eid, reason='': vsm._test_events.append((eid, reason))
    # 跳过截图分支
    vsm._last_screenshot_time = time.time() + 3600
    return vsm


_FRAME = np.zeros((32, 32, 3), dtype=np.uint8)


def _feed(vsm, labels):
    dets = [{'label': lbl, 'confidence': 0.9,
             'x': 0.1, 'y': 0.1, 'w': 0.2, 'h': 0.2} for lbl in labels]
    vsm._update_step_stats(dets, _FRAME)


def test_default_rule_b_unchanged():
    """默认行为零差异: A 缺席 + B 在场 → A 立即被按消失处理 (规则 B)。"""
    vsm = _make_vsm(uninterruptible=False)
    _feed(vsm, ['A'])
    assert 'A' in vsm.step_last_seen
    # A 刚缺席一帧 (远小于 10s 等待), 但 B 已确认在场 → 规则 B 立即消失
    _feed(vsm, ['B'])
    assert 'A' not in vsm.step_last_seen, "默认行为应保持规则 B: 其他步骤出现立即按消失处理"


def test_uninterruptible_holds_wait():
    """开关开启: A 缺席 + B 在场, 等待时间未耗尽 → A 不被按消失处理。"""
    vsm = _make_vsm(uninterruptible=True)
    _feed(vsm, ['A'])
    assert 'A' in vsm.step_last_seen
    for _ in range(5):
        _feed(vsm, ['B'])
    assert 'A' in vsm.step_last_seen, "开关开启后其他步骤在场不应打断 A 的消失等待"


def test_uninterruptible_still_expires():
    """开关开启只豁免打断, 等待时间真正耗尽后照常消失结算。"""
    vsm = _make_vsm(uninterruptible=True)
    _feed(vsm, ['A'])
    # 把 A 的最后可见时刻拨回 11 秒前 (超过 10s 等待)
    vsm.step_last_seen['A'] = time.time() - 11
    _feed(vsm, ['B'])
    assert 'A' not in vsm.step_last_seen, "等待时间耗尽后应正常按消失处理"
    assert vsm.step_counts.get('A', 0) == 1, "消失结算应给 A 正常计数"


def test_config_parse_bool():
    """配置解析: 字段缺省为 False, 显式 True 进入 step_time_config。"""
    vsm_off = _make_vsm(uninterruptible=False)
    assert vsm_off.step_time_config['A'].get('disappear_uninterruptible') is False
    vsm_on = _make_vsm(uninterruptible=True)
    assert vsm_on.step_time_config['A'].get('disappear_uninterruptible') is True
