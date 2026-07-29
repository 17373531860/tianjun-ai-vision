"""v3.45 事件借用"过程提醒"档 (remind_only) 单测.

2026-07 萍乡现场: 称重投料中的缺料提醒借完整 NG 事件面, 灯/语音/计数/定格
捆在一起 —— 秤读数微抖让提醒 1~2 秒一条, 30 秒把 NG 计数刷了 +16。
新契约: fire_external_event_response(remind_only=True) 只借灯/语音/Toast,
**跳过计数器联动、跳过人工确认定格**; 默认 False 与旧行为零差异。
"""
from __future__ import annotations

import os
os.environ.setdefault("BACKEND_SKIP_INIT", "1")

from backend.api.source import VideoSourceManager


def _make_vsm(require_ack=False):
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99451,
        "name": "remind_only 单测",
        "logic_mode": "sequential",
        "pipeline_config": {},
        "steps_config": [{"id": "s1", "label": "步骤1", "enabled": True}],
        "events_config": [
            {"id": 2, "name": "不良", "require_ack": require_ack,
             "actions": [{"counter_name": "不良总数", "delta": 1}]},
        ],
        "counters_config": [{"name": "不良总数", "initial": 0}],
        "data_config": {},
    })
    vsm.counters["不良总数"] = 0
    return vsm


def test_remind_only_skips_counter_and_ack():
    vsm = _make_vsm(require_ack=True)
    ok = vsm.fire_external_event_response(2, "缺料提醒", source="weighing",
                                          remind_only=True)
    assert ok is True
    assert vsm.counters["不良总数"] == 0, "提醒档不得动计数器"
    assert getattr(vsm, "_pending_ack", False) is False, "提醒档不得触发人工确认定格"
    # Toast/语音日志仍然要出 (提醒的意义所在), 且不带 require_ack
    assert vsm.events_log and vsm.events_log[-1]["reason"] == "缺料提醒"
    assert vsm.events_log[-1]["require_ack"] is False


def test_default_path_still_counts_and_acks():
    """默认 (remind_only=False) 与旧行为零差异: 计数 + require_ack 定格。"""
    vsm = _make_vsm(require_ack=True)
    ok = vsm.fire_external_event_response(2, "离秤结算缺料", source="weighing")
    assert ok is True
    assert vsm.counters["不良总数"] == 1
    assert getattr(vsm, "_pending_ack", False) is True
