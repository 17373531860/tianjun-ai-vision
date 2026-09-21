# -*- coding: utf-8 -*-
"""v3.60.2 六和二工位现场补丁单测。

背景 (2026-09-20 现场): 混合逐件方案里, 动作标签「螺丝锁付-已完成」单开一行
并停用 (避免它进步骤序列刷噪音) 后:
  1. runner 出口按启用步骤标签过滤把它丢弃 → 覆盖记账永远 0/N、虚拟步骤永不
     注入 → 每周期只能空闲超时 NG ("识别不到打螺丝了")
  2. 扫码后摆 32 颗螺丝的阶段 (个体陆续入账、覆盖零推进) 超过空闲超时 →
     干活中被判 NG ("放螺丝的时间段报了一次NG")

修复:
  1. _get_enabled_labels 豁免逐件配对的 item_label / action_label
     (与 v3.59.0a 容器标签豁免同构, 支持字符串与数组 OR 两种形态)
  2. _PerItemMixEngine 账面个体数推进也刷新 last_progress_time (摆件脉冲)
"""
import time

import numpy as np

_FRAME = np.zeros((32, 32, 3), dtype=np.uint8)


def _mk_vsm(steps_config, pipeline_extra=None):
    from backend.api.source import VideoSourceManager
    pipeline = {
        "custom_based_on": "sequential",
        "custom_mixed_with": "per_item",
        "custom_sequence_order": [{"step_id": 1}],
    }
    pipeline.update(pipeline_extra or {})
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99602, "name": "标签豁免单测", "logic_mode": "custom",
        "pipeline_config": pipeline, "steps_config": steps_config,
        "events_config": [
            {"id": 1, "name": "OK", "actions": [], "show_notification": False},
            {"id": 2, "name": "NG", "actions": [], "show_notification": False},
        ],
        "counters_config": [], "data_config": {},
    })
    vsm.start_step_recording = lambda *a, **k: None
    vsm.stop_step_recording = lambda *a, **k: None
    vsm.record_step = lambda *a, **k: None
    vsm.start_cycle = lambda *a, **k: None
    vsm._test_events = []
    vsm._trigger_event = (
        lambda eid, reason="", **k: vsm._test_events.append((eid, reason)))
    vsm._last_screenshot_time = time.time() + 3600
    return vsm


# ==================== 1. 过滤豁免 ====================

def test_停用的动作标签仍在启用标签集合内():
    """动作标签行 enabled=False → 仍必须过 runner 过滤 (配对引擎数据源)。"""
    vsm = _mk_vsm([
        {"id": 1, "label": "扫码", "enabled": True, "min_frames": 1},
        {"id": 2, "label": "位置", "enabled": True, "detect_role": "item",
         "per_item": {"item_label": "位置", "action_label": "已完成",
                      "coverage_use_center": True, "sustain_frames": 2,
                      "expected_count": 3}},
        # 现场做法: 动作标签单开一行并停用
        {"id": 3, "label": "已完成", "enabled": False, "threshold": 35},
    ])
    labels = vsm._get_enabled_labels()
    assert "已完成" in labels, "停用的动作标签被 runner 过滤丢弃 → 覆盖记账 0/N"
    assert "位置" in labels
    assert "扫码" in labels


def test_数组OR形态的标签同样豁免():
    vsm = _mk_vsm([
        {"id": 1, "label": "扫码", "enabled": True, "min_frames": 1},
        {"id": 2, "label": "产品组", "enabled": True, "detect_role": "item",
         "per_item": {"item_label": ["产品1", "产品2"],
                      "action_label": ["打钉A", "打钉B"],
                      "expected_count": 2}},
    ])
    labels = vsm._get_enabled_labels()
    for lbl in ("产品1", "产品2", "打钉A", "打钉B"):
        assert lbl in labels, f"{lbl} 应被豁免"


def test_无逐件配对时不豁免任何额外标签():
    """零差异守门: 普通顺序项目, 停用行仍被过滤。"""
    from backend.api.source import VideoSourceManager
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99603, "name": "零差异", "logic_mode": "sequential",
        "pipeline_config": {},
        "steps_config": [
            {"id": 1, "label": "步骤A", "enabled": True},
            {"id": 2, "label": "步骤B", "enabled": False},
        ],
        "events_config": [], "counters_config": [], "data_config": {},
    })
    labels = vsm._get_enabled_labels()
    assert "步骤A" in labels
    assert "步骤B" not in labels


# ==================== 2. 摆件脉冲 ====================

def _items(n, xs=(0.10, 0.30, 0.50)):
    return [{"label": "位置", "confidence": 0.95,
             "x": xs[i], "y": 0.60, "w": 0.10, "h": 0.10} for i in range(n)]


def test_摆件入账刷新活动脉冲():
    """个体逐个入账 (无覆盖推进) 也应刷新 last_progress_time。"""
    vsm = _mk_vsm([
        {"id": 1, "label": "扫码", "enabled": True, "min_frames": 1,
         "disappear_delay": 0},
        {"id": 2, "label": "位置行", "enabled": True, "detect_role": "item",
         "per_item": {"item_label": "位置", "action_label": "已完成",
                      "coverage_use_center": True, "sustain_frames": 2,
                      "expected_count": 3}},
    ])
    eng = vsm._custom_mix._engine
    # 首帧 1 件: 建立基线 (不算活动)
    vsm._update_step_stats(_items(1), _FRAME)
    assert eng.last_progress_time == 0.0, "首帧基线不算活动"
    # 第 2 件入账 → 脉冲
    vsm._update_step_stats(_items(2), _FRAME)
    t2 = eng.last_progress_time
    assert t2 > 0.0, "第 2 件入账应刷新活动脉冲"
    # 数量不变 → 不刷新
    vsm._update_step_stats(_items(2), _FRAME)
    assert eng.last_progress_time == t2, "账面无推进不该刷新"
    # 第 3 件入账 → 再刷新
    time.sleep(0.01)
    vsm._update_step_stats(_items(3), _FRAME)
    assert eng.last_progress_time > t2, "第 3 件入账应再次刷新"


# ==================== 3. 停用行阈值回填 ====================

def test_停用动作标签行的阈值回填到守门表():
    """停用行的检测被豁免放行后, 该行显式阈值必须继续生效 —
    否则守门退化到模型 0.25 下限, 弱误报直灌覆盖记账。"""
    vsm = _mk_vsm([
        {"id": 1, "label": "扫码", "enabled": True, "min_frames": 1},
        {"id": 2, "label": "位置", "enabled": True, "detect_role": "item",
         "threshold": 50,
         "per_item": {"item_label": "位置", "action_label": "已完成",
                      "expected_count": 3}},
        {"id": 3, "label": "已完成", "enabled": False, "threshold": 35},
    ])
    assert vsm.step_conf_thresholds.get("已完成") == 0.35, \
        f"停用行阈值未回填: {vsm.step_conf_thresholds}"
    # 启用行不受影响
    assert vsm.step_conf_thresholds.get("位置") == 0.5


def test_启用行阈值不被回填覆盖():
    """启用行的阈值以 _apply_steps_config 为准, 回填不碰。"""
    vsm = _mk_vsm([
        {"id": 1, "label": "扫码", "enabled": True, "min_frames": 1},
        {"id": 2, "label": "位置", "enabled": True, "detect_role": "item",
         "per_item": {"item_label": "位置", "action_label": "已完成",
                      "expected_count": 3}},
        {"id": 3, "label": "已完成", "enabled": True, "threshold": 40},
    ])
    assert vsm.step_conf_thresholds.get("已完成") == 0.40
