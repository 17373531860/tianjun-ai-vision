"""v3.44.2 人工确认定格 = 推理整体停摆 单测.

客户诉求 (2026-07-23, 上银 SY3): 确认框弹出到工人点确认前, 工人要执行
"取出错盘 / 放回"等整改动作 — 这些动作绝不能被识别成放托盘 / 收尾步骤。
老实现只冻结状态机 (帧仍推理 + 出框); 新契约: 定格期间跳过模型推理并
清空已发布检测框 (推理循环见 source_inference_loop_mixin._ack_freeze_active)。

本文件锁定:
  A. _ack_freeze_active(): 步骤状态机路径 + _pending_ack → True;
     tracking / 区域事件模式 / 无定格 → False (行为零差异)。
  B. 定格中 _update_step_stats 阻塞门兜底: 即便有检测喂进来也寸步不进
     (混合台账 / 周期均不变) — 双保险中的第二道。
"""
from __future__ import annotations

import os
os.environ.setdefault("BACKEND_SKIP_INIT", "1")

from backend.api.source import VideoSourceManager


def _make_mix_vsm():
    """真 VSM + custom(顺序) + 容器混合跟踪, 与 SY3 同构 (动作确认进箱)."""
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99442,
        "name": "确认定格推理停摆单测",
        "logic_mode": "custom",
        "pipeline_config": {
            "custom_based_on": "sequential",
            "custom_mixed_with": "tracking",
            "custom_sequence_order": [{"step_id": "s1"}, {"step_id": "s2"}],
            "custom_mix_container_enabled": True,
            "custom_mix_container_label": "托盘",
            "custom_mix_container_count_mode": "items_total",
            "custom_mix_container_items_total": 96,
            "custom_mix_container_confirm_by_action": True,
            "custom_mix_container_action_label": "放托盘",
        },
        "steps_config": [
            {"id": "s1", "label": "贴标", "enabled": True},
            {"id": "s2", "label": "封箱", "enabled": True},
            {"id": "s3", "label": "滑块", "enabled": True, "detect_role": "item",
             "expected_count": 24},
        ],
        "events_config": [], "counters_config": [], "data_config": {},
    })
    assert vsm._custom_mix is not None
    return vsm


def test_ack_freeze_active_only_when_pending():
    vsm = _make_mix_vsm()
    assert vsm._ack_freeze_active() is False, "无定格时不得跳过推理"
    vsm._pending_ack = True
    assert vsm._ack_freeze_active() is True, "定格中步骤状态机路径应跳过推理"


def test_ack_freeze_inactive_for_tracking_and_region_modes():
    vsm = _make_mix_vsm()
    vsm._pending_ack = True
    # tracking 模式没有 require_ack 定格语义 → 不跳过 (零差异)
    vsm.project_config = dict(vsm.project_config or {}, logic_mode="tracking")
    assert vsm._ack_freeze_active() is False
    # 区域事件模式同理
    vsm.project_config = dict(vsm.project_config, logic_mode="custom")
    vsm._region_event_engine = object()
    assert vsm._ack_freeze_active() is False


def test_step_stats_gate_blocks_everything_during_freeze():
    """第二道保险: 定格中即便检测喂进来, 状态机 / 混合台账寸步不进."""
    vsm = _make_mix_vsm()
    vsm._pending_ack = True
    vsm._pending_ack_timeout_sec = 0   # 不超时, 一直定格

    cont = vsm._custom_mix._engine._container
    booked_before = len(cont._done)
    cycle_before = list(vsm.current_cycle_steps)

    dets = [
        {"label": "放托盘", "x": 10, "y": 10, "w": 50, "h": 50, "confidence": 0.9},
        {"label": "托盘", "x": 100, "y": 100, "w": 200, "h": 150, "confidence": 0.9},
        {"label": "贴标", "x": 300, "y": 100, "w": 60, "h": 60, "confidence": 0.9},
    ]
    for _ in range(30):
        vsm._update_step_stats(dets, None)

    assert len(cont._done) == booked_before, "定格中不得进箱记账"
    assert list(vsm.current_cycle_steps) == cycle_before, "定格中不得推进周期"
    assert vsm._pending_ack is True
