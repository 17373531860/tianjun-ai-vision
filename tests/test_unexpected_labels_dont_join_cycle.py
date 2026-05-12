"""FIX-381 单元测试: 顺序 / 自定义-基于顺序 模式下,

只有"勾进序列"的步骤才进 current_cycle_steps. 未勾选但 enabled=True 的步骤
(典型: 模型识别到的旁路目标, 客户希望显示框但不参与判定) 不会污染 cycle.

客户场景:
  模型能识别 A, B, D, E, F 五种目标. 项目里 steps_config 全启用,
  但 sequence_order 只勾 A→B→D. 检测过程中 E, F 出现, 不应让周期被
  误判 NG ("重复步骤: []" / "多余步骤: [E, F]").

修复前: E, F 入 cycle, 触发 NG.
修复后: E, F 不入 cycle, A→B→D 正常 OK.
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.api.source import VideoSourceManager


_DUMMY_FRAME = np.zeros((10, 10, 3), dtype="uint8")


def _make_vsm_sequential_abd():
    """构造一个顺序模式 VSM: steps_config 含 A B D E F 全 enabled, 序列只勾 A→B→D."""
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99381,
        "name": "FIX-381 单测项目",
        "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": [
            {"id": 1, "label": "A", "enabled": True},
            {"id": 2, "label": "B", "enabled": True},
            {"id": 3, "label": "D", "enabled": True},
            {"id": 4, "label": "E", "enabled": True},
            {"id": 5, "label": "F", "enabled": True},
        ],
        "events_config": [
            {"id": 1, "name": "OK", "actions": [], "show_notification": False},
            {"id": 2, "name": "NG", "actions": [], "show_notification": False},
        ],
        "counters_config": [],
        "pipeline_config": {
            "sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}],
        },
    })
    return vsm


def _make_vsm_custom_sequential_abd():
    """构造一个自定义模式 (基于顺序) VSM: custom_sequence_order = A→B→D."""
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99382,
        "name": "FIX-381 自定义模式单测",
        "task_type": "detection",
        "logic_mode": "custom",
        "steps_config": [
            {"id": 1, "label": "A", "enabled": True},
            {"id": 2, "label": "B", "enabled": True},
            {"id": 3, "label": "D", "enabled": True},
            {"id": 4, "label": "E", "enabled": True},
            {"id": 5, "label": "F", "enabled": True},
        ],
        "events_config": [
            {"id": 1, "name": "OK", "actions": [], "show_notification": False},
            {"id": 2, "name": "NG", "actions": [], "show_notification": False},
        ],
        "counters_config": [],
        "pipeline_config": {
            "custom_based_on": "sequential",
            "custom_sequence_order": [
                {"step_id": 1}, {"step_id": 2}, {"step_id": 3},
            ],
        },
    })
    return vsm


def _feed(vsm, label, t, enabled_labels):
    """直接调底层 _process_single_step, 跳过帧确认门, 模拟"步骤已确认出现"."""
    vsm._step_raw_start.setdefault(label, t)
    vsm._process_single_step(
        label=label,
        current_time=t,
        enabled_labels=enabled_labels,
        is_seq_like=True,
        should_update_screenshot=False,
        original_frame=_DUMMY_FRAME,
        det_info=None,
        just_confirmed_labels=set(),
    )


class TestSequentialMode:
    """顺序模式 (logic_mode='sequential') 行为验证."""

    def test_unexpected_labels_dont_join_cycle(self):
        """A → E → B → F → D 出现, cycle 应该只有 A B D, 没有 E F."""
        vsm = _make_vsm_sequential_abd()
        enabled = {"A", "B", "D", "E", "F"}

        t0 = 1000.0
        _feed(vsm, "A", t0,       enabled)
        _feed(vsm, "E", t0 + 0.5, enabled)
        _feed(vsm, "B", t0 + 1.0, enabled)
        _feed(vsm, "F", t0 + 1.5, enabled)
        _feed(vsm, "D", t0 + 2.0, enabled)

        assert vsm.current_cycle_steps == ["A", "B", "D"], (
            f"E/F 不应进入 cycle, 实际 cycle={vsm.current_cycle_steps}"
        )

    def test_cycle_start_not_triggered_by_unexpected_label(self):
        """E 先出现不应启动 cycle, cycle 必须由序列里的 A 启动."""
        vsm = _make_vsm_sequential_abd()
        enabled = {"A", "B", "D", "E", "F"}

        t0 = 1000.0
        _feed(vsm, "E", t0, enabled)

        assert vsm.current_cycle_steps == [], (
            f"E 不在序列里, 不应启动 cycle, 实际 cycle={vsm.current_cycle_steps}"
        )
        assert vsm.cycle_start_time is None, (
            f"E 不在序列里, 不应设置 cycle_start_time, 实际={vsm.cycle_start_time}"
        )

        _feed(vsm, "A", t0 + 1.0, enabled)
        assert vsm.current_cycle_steps == ["A"]
        assert vsm.cycle_start_time == t0 + 1.0

    def test_only_unexpected_labels_no_ng(self):
        """整个会话只有 E F 出现, 不应触发任何周期 (没 cycle 就没 NG)."""
        vsm = _make_vsm_sequential_abd()
        enabled = {"A", "B", "D", "E", "F"}

        t0 = 1000.0
        for i, lbl in enumerate(["E", "F", "E", "F", "E"]):
            _feed(vsm, lbl, t0 + i * 0.5, enabled)

        assert vsm.current_cycle_steps == []
        assert vsm.cycle_start_time is None


class TestCustomBasedOnSequentialMode:
    """自定义模式-基于顺序 (custom_based_on='sequential') 行为验证."""

    def test_unexpected_labels_dont_join_cycle(self):
        vsm = _make_vsm_custom_sequential_abd()
        enabled = {"A", "B", "D", "E", "F"}

        t0 = 2000.0
        _feed(vsm, "A", t0,       enabled)
        _feed(vsm, "F", t0 + 0.3, enabled)
        _feed(vsm, "E", t0 + 0.6, enabled)
        _feed(vsm, "B", t0 + 1.0, enabled)
        _feed(vsm, "D", t0 + 2.0, enabled)

        assert vsm.current_cycle_steps == ["A", "B", "D"], (
            f"自定义模式下 E/F 也不应进入 cycle, 实际={vsm.current_cycle_steps}"
        )


class TestRegressionUnchangedBehaviors:
    """回归: 修复不应改变其他模式行为."""

    def test_empty_sequence_falls_back_to_old_behavior(self):
        """sequence_order 没配置时, 修复应"安全失效",
        所有 enabled 步骤都能入 cycle (避免空配把所有都挡掉)."""
        vsm = VideoSourceManager(channel_id=0)
        vsm.set_project_config({
            "id": 99383,
            "name": "空序列回归",
            "task_type": "detection",
            "logic_mode": "sequential",
            "steps_config": [
                {"id": 1, "label": "A", "enabled": True},
                {"id": 2, "label": "B", "enabled": True},
            ],
            "events_config": [
                {"id": 1, "name": "OK", "actions": [], "show_notification": False},
                {"id": 2, "name": "NG", "actions": [], "show_notification": False},
            ],
            "counters_config": [],
            "pipeline_config": {"sequence_order": []},
        })

        t0 = 3000.0
        _feed(vsm, "A", t0,       {"A", "B"})
        _feed(vsm, "B", t0 + 0.5, {"A", "B"})

        assert "A" in vsm.current_cycle_steps, (
            f"空序列下应保留老行为 (A 入 cycle), 实际={vsm.current_cycle_steps}"
        )

    def test_detection_mode_unaffected(self):
        """检测模式 (logic_mode='detection') 不应被修复影响 — 仍按 detection_steps 走."""
        vsm = VideoSourceManager(channel_id=0)
        vsm.set_project_config({
            "id": 99384,
            "name": "检测模式回归",
            "task_type": "detection",
            "logic_mode": "detection",
            "steps_config": [
                {"id": 1, "label": "A", "enabled": True},
                {"id": 2, "label": "B", "enabled": True},
                {"id": 3, "label": "E", "enabled": True},
            ],
            "events_config": [
                {"id": 1, "name": "OK", "actions": [], "show_notification": False},
                {"id": 2, "name": "NG", "actions": [], "show_notification": False},
            ],
            "counters_config": [],
            "pipeline_config": {"detection_steps": [1, 2]},
        })

        t0 = 4000.0
        _feed(vsm, "A", t0,       {"A", "B", "E"})
        _feed(vsm, "E", t0 + 0.5, {"A", "B", "E"})
        _feed(vsm, "B", t0 + 1.0, {"A", "B", "E"})

        assert "A" in vsm.current_cycle_steps
        assert "B" in vsm.current_cycle_steps
        assert "E" in vsm.current_cycle_steps, (
            "检测模式下 E 仍应入 cycle (该模式不限制 'unexpected' 标签), "
            f"实际={vsm.current_cycle_steps}"
        )
