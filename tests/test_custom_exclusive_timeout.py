"""纯 custom 条件分支互斥与空闲超时结算回归。"""


def _make_vsm(*, custom_based_on=None, idle_timeout_seconds=0,
              idle_timeout_event_id=None, custom_conditions=None):
    from backend.api.source import VideoSourceManager

    pipeline_config = {
        "custom_based_on": custom_based_on,
        "custom_conditions": custom_conditions if custom_conditions is not None else [
            {"id": 1, "priority": 1, "sequence": [1, 2], "event_id": 1},
            {"id": 2, "priority": 2, "sequence": [1, 3], "event_id": 5},
        ],
        "idle_timeout_seconds": idle_timeout_seconds,
        "idle_timeout_event_id": idle_timeout_event_id,
    }
    if custom_based_on == "sequential":
        pipeline_config["custom_sequence_order"] = [
            {"step_id": 1}, {"step_id": 2}, {"step_id": 3},
        ]

    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99201,
        "name": "纯 custom 三路互斥测试",
        "task_type": "detection",
        "logic_mode": "custom",
        "pipeline_config": pipeline_config,
        "steps_config": [
            {"id": 1, "label": "A", "enabled": True, "min_frames": 1},
            {"id": 2, "label": "B", "enabled": True, "min_frames": 1},
            {"id": 3, "label": "C", "enabled": True, "min_frames": 1},
        ],
        "events_config": [
            {"id": 1, "name": "合格", "actions": [], "show_notification": False},
            {"id": 2, "name": "不良", "actions": [], "show_notification": False},
            {"id": 4, "name": "行为不良", "actions": [], "show_notification": False},
            {"id": 5, "name": "产品不良", "actions": [], "show_notification": False},
        ],
        "counters_config": [],
    })
    vsm.start_cycle = lambda: None
    vsm._test_events = []
    vsm._trigger_event = (
        lambda event_id, reason="": vsm._test_events.append((event_id, reason)) or True
    )
    return vsm


def _appear(vsm, label, when):
    enabled = set(vsm.step_conf_thresholds)
    pipeline = vsm.project_config.get("pipeline_config", {})
    is_seq_like = (
        vsm.project_config.get("logic_mode") == "sequential"
        or (
            vsm.project_config.get("logic_mode") == "custom"
            and pipeline.get("custom_based_on") == "sequential"
        )
    )
    vsm._process_single_step(
        label, when, enabled, is_seq_like, False, None, None,
    )


def _detection(label, x=0.1):
    return {
        "label": label,
        "confidence": 0.9,
        "x": x,
        "y": 0.1,
        "w": 0.1,
        "h": 0.1,
    }


def test_first_completed_branch_locks_out_the_other_branch():
    vsm = _make_vsm()

    _appear(vsm, "A", 1.0)
    _appear(vsm, "B", 2.0)
    _appear(vsm, "C", 3.0)

    assert vsm.current_cycle_steps == ["A", "B"]
    assert vsm._cycle_regression is False


def test_second_branch_can_lock_first_and_reject_the_other_branch():
    vsm = _make_vsm()

    _appear(vsm, "A", 1.0)
    _appear(vsm, "C", 2.0)
    _appear(vsm, "B", 3.0)

    assert vsm.current_cycle_steps == ["A", "C"]
    assert vsm._cycle_regression is False


def test_non_prefix_label_cannot_open_an_empty_cycle():
    vsm = _make_vsm()
    cycle_starts = []
    strict_violations = []
    closing_guard_calls = []
    vsm.start_cycle = lambda: cycle_starts.append(True)
    vsm.step_strict_order["B"] = True
    vsm._fire_strict_order_violation = (
        lambda label, reason: strict_violations.append((label, reason))
    )
    vsm._closing_guard_blocks = (
        lambda label: closing_guard_calls.append(label) or False
    )

    _appear(vsm, "B", 1.0)

    assert vsm.current_cycle_steps == []
    assert cycle_starts == []
    assert vsm.cycle_start_time is None
    assert vsm._last_step_added_time is None
    assert "B" not in vsm.step_last_seen
    assert vsm.step_counts.get("B", 0) == 0
    assert strict_violations == []
    assert closing_guard_calls == []


def test_same_frame_branches_use_custom_condition_priority():
    import numpy as np

    vsm = _make_vsm()
    _appear(vsm, "A", 1.0)
    # 刻意让同时组先输出低优先级 C，证明分支选择不依赖检测框/集合顺序。
    vsm._simultaneous_groups = [{
        "enabled": True,
        "labels": ["B", "C"],
        "priority_order": ["C", "B"],
        "time_window": 1.0,
    }]
    detections = [
        {"label": "A", "confidence": 0.9, "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1},
        {"label": "C", "confidence": 0.9, "x": 0.3, "y": 0.1, "w": 0.1, "h": 0.1},
        {"label": "B", "confidence": 0.9, "x": 0.5, "y": 0.1, "w": 0.1, "h": 0.1},
    ]

    vsm._update_step_stats(detections, np.zeros((40, 60, 3), dtype=np.uint8))

    assert vsm.current_cycle_steps == ["A", "B"]
    assert vsm.step_last_seen["A"] > 1.0, "持续可见的前序标签仍应刷新 last_seen"
    first_refresh = vsm.step_last_seen["A"]
    vsm._update_step_stats(detections, np.zeros((40, 60, 3), dtype=np.uint8))
    assert vsm.current_cycle_steps == ["A", "B"]
    assert vsm.step_last_seen["A"] > first_refresh


def test_condition_without_event_cannot_win_same_frame_branch():
    import numpy as np

    vsm = _make_vsm(custom_conditions=[
        {"id": 1, "priority": 1, "sequence": [1, 2], "event_id": None},
        {"id": 2, "priority": 2, "sequence": [1, 3], "event_id": 5},
    ])
    _appear(vsm, "A", 1.0)

    vsm._update_step_stats(
        [_detection("A"), _detection("B", 0.3), _detection("C", 0.5)],
        np.zeros((40, 60, 3), dtype=np.uint8),
    )

    assert vsm.current_cycle_steps == ["A", "C"]
    assert vsm._cycle_regression is False


def test_pure_custom_cross_cycle_group_cannot_bypass_branch_prefix():
    import numpy as np

    vsm = _make_vsm()
    _appear(vsm, "A", 1.0)
    _appear(vsm, "B", 2.0)
    vsm._simultaneous_groups = [{
        "enabled": True,
        "cross_cycle": True,
        "labels": ["C", "A"],
        "prev_cycle_labels": ["C"],
        "next_cycle_labels": ["A"],
        "priority_order": ["C", "A"],
        "time_window": 1.0,
    }]

    vsm._update_step_stats(
        [_detection("A"), _detection("B", 0.3), _detection("C", 0.5)],
        np.zeros((40, 60, 3), dtype=np.uint8),
    )

    assert vsm.current_cycle_steps == ["A", "B"]
    assert vsm._test_events == []


def test_incomplete_branch_idle_timeout_uses_configured_event():
    import time

    import numpy as np

    vsm = _make_vsm(idle_timeout_seconds=1, idle_timeout_event_id=4)
    _appear(vsm, "A", 1.0)
    vsm._last_step_added_time = time.time() - 2

    # A 持续可见，避免把“步骤消失结算”误当成 idle timeout 的证据。
    vsm._update_step_stats(
        [_detection("A")], np.zeros((40, 60, 3), dtype=np.uint8)
    )

    assert len(vsm._test_events) == 1
    event_id, reason = vsm._test_events[0]
    assert event_id == 4
    assert "空闲超时" in reason
    assert "A" in reason
    assert vsm.current_cycle_steps == []

    # 结算时仍在画面的标签必须等到真实离场后才能再次开启周期，避免同一工件
    # 每隔 idle_timeout 重复上报一次中断事件。
    vsm._update_step_stats(
        [_detection("A")], np.zeros((40, 60, 3), dtype=np.uint8)
    )
    assert vsm.current_cycle_steps == []
    assert [event_id for event_id, _ in vsm._test_events] == [4]

    vsm._update_step_stats([], np.zeros((40, 60, 3), dtype=np.uint8))
    vsm._update_step_stats(
        [_detection("A")], np.zeros((40, 60, 3), dtype=np.uint8)
    )
    assert vsm.current_cycle_steps == ["A"]


def test_completed_condition_wins_over_prefix_then_uses_condition_event():
    import time

    import numpy as np

    vsm = _make_vsm(
        idle_timeout_seconds=1,
        idle_timeout_event_id=4,
        custom_conditions=[
            {"id": 1, "priority": 1, "sequence": [1, 2, 3], "event_id": 5},
            {"id": 2, "priority": 2, "sequence": [1, 3], "event_id": 1},
        ],
    )
    _appear(vsm, "A", 1.0)
    vsm._simultaneous_groups = [{
        "enabled": True,
        "labels": ["B", "C"],
        "priority_order": ["B", "C"],
        "time_window": 1.0,
    }]
    vsm._update_step_stats(
        [_detection("A"), _detection("B", 0.3), _detection("C", 0.5)],
        np.zeros((40, 60, 3), dtype=np.uint8),
    )
    assert vsm.current_cycle_steps == ["A", "C"]

    vsm._last_step_added_time = time.time() - 2
    vsm._update_step_stats(
        [_detection("A"), _detection("C", 0.5)],
        np.zeros((40, 60, 3), dtype=np.uint8),
    )

    assert [event_id for event_id, _ in vsm._test_events] == [1]


def test_idle_timeout_without_event_config_falls_back_to_event_2():
    import time

    import numpy as np

    vsm = _make_vsm(idle_timeout_seconds=1, idle_timeout_event_id=None)
    _appear(vsm, "A", 1.0)
    vsm._last_step_added_time = time.time() - 2

    vsm._update_step_stats(
        [_detection("A")], np.zeros((40, 60, 3), dtype=np.uint8)
    )

    assert [event_id for event_id, _ in vsm._test_events] == [2]


def test_invalid_idle_timeout_event_config_is_normalized_to_fallback():
    import time

    import numpy as np

    vsm = _make_vsm(idle_timeout_seconds=1, idle_timeout_event_id=999)
    assert vsm.idle_timeout_event_id is None
    _appear(vsm, "A", 1.0)
    vsm._last_step_added_time = time.time() - 2

    vsm._update_step_stats(
        [_detection("A")], np.zeros((40, 60, 3), dtype=np.uint8)
    )

    assert [event_id for event_id, _ in vsm._test_events] == [2]


def test_idle_timeout_keeps_cycle_when_interrupt_event_is_unavailable():
    import time

    import numpy as np

    vsm = _make_vsm(idle_timeout_seconds=1, idle_timeout_event_id=4)
    _appear(vsm, "A", 1.0)
    vsm._last_step_added_time = time.time() - 2
    vsm.project_config["events_config"] = [
        event for event in vsm.project_config["events_config"]
        if event["id"] != 4
    ]
    vsm._trigger_event = lambda event_id, reason="": (_ for _ in ()).throw(
        AssertionError("不存在的中断事件不应进入 dispatch")
    )

    vsm._update_step_stats(
        [_detection("A")], np.zeros((40, 60, 3), dtype=np.uint8)
    )

    assert vsm.current_cycle_steps == ["A"]
    assert vsm._pure_custom_settle_latched_labels == {}


def test_complete_condition_wins_when_interrupt_event_becomes_unavailable():
    import time

    import numpy as np

    vsm = _make_vsm(idle_timeout_seconds=1, idle_timeout_event_id=4)
    _appear(vsm, "A", 1.0)
    _appear(vsm, "B", 2.0)
    vsm._last_step_added_time = time.time() - 2
    vsm.project_config["events_config"] = [
        event for event in vsm.project_config["events_config"]
        if event["id"] != 4
    ]

    vsm._update_step_stats(
        [_detection("A"), _detection("B", 0.3)],
        np.zeros((40, 60, 3), dtype=np.uint8),
    )

    assert [event_id for event_id, _ in vsm._test_events] == [1]


def test_completed_default_branch_timeout_uses_condition_event_not_interrupt():
    import time

    import numpy as np

    vsm = _make_vsm(idle_timeout_seconds=1, idle_timeout_event_id=4)
    _appear(vsm, "A", 1.0)
    _appear(vsm, "B", 2.0)
    vsm._last_step_added_time = time.time() - 2

    vsm._update_step_stats(
        [_detection("A"), _detection("B", 0.3)],
        np.zeros((40, 60, 3), dtype=np.uint8),
    )

    assert [event_id for event_id, _ in vsm._test_events] == [1]


def test_pure_custom_settle_keeps_condition_order_over_simultaneous_priority():
    import time

    import numpy as np

    vsm = _make_vsm(idle_timeout_seconds=1, idle_timeout_event_id=4)
    _appear(vsm, "A", 1.0)
    _appear(vsm, "B", 2.0)
    vsm._simultaneous_groups = [{
        "enabled": True,
        "labels": ["A", "B"],
        "priority_order": ["B", "A"],
        "time_window": 1.0,
    }]
    vsm._last_step_added_time = time.time() - 2

    vsm._update_step_stats(
        [_detection("A"), _detection("B", 0.3)],
        np.zeros((40, 60, 3), dtype=np.uint8),
    )

    assert [event_id for event_id, _ in vsm._test_events] == [1]


def test_same_frame_static_branches_trigger_only_selected_condition_once():
    import numpy as np

    vsm = _make_vsm()
    _appear(vsm, "A", 1.0)
    for label in ("B", "C"):
        vsm.step_detection_type[label] = "static"
        vsm.step_static_config[label] = {
            "trigger_frames": 1,
            "join_cycle": True,
            "trigger_event": None,
        }
        vsm.step_static_triggered[label] = False

    vsm._update_step_stats(
        [_detection("A"), _detection("C", 0.3), _detection("B", 0.5)],
        np.zeros((40, 60, 3), dtype=np.uint8),
    )

    assert vsm.current_cycle_steps == []
    assert [event_id for event_id, _ in vsm._test_events] == [1]
    assert "['A', 'B']" in vsm._test_events[0][1]

    # static 标签持续留在画面时，已结算的同一工件不能再次打开并重复触发。
    for _ in range(2):
        vsm._update_step_stats(
            [_detection("A"), _detection("C", 0.3), _detection("B", 0.5)],
            np.zeros((40, 60, 3), dtype=np.uint8),
        )
    assert vsm.current_cycle_steps == []
    assert [event_id for event_id, _ in vsm._test_events] == [1]


def test_static_terminal_can_match_condition_without_joining_cycle():
    """保留 static join_cycle=False 仍可作为自定义条件终点的既有能力。"""
    import numpy as np

    vsm = _make_vsm()
    _appear(vsm, "A", 1.0)
    vsm.step_detection_type["B"] = "static"
    vsm.step_static_config["B"] = {
        "trigger_frames": 1,
        "join_cycle": False,
        "trigger_event": None,
    }
    vsm.step_static_triggered["B"] = False

    vsm._update_step_stats(
        [_detection("A"), _detection("B", 0.3)],
        np.zeros((40, 60, 3), dtype=np.uint8),
    )

    assert vsm.current_cycle_steps == []
    assert [event_id for event_id, _ in vsm._test_events] == [1]
    assert "['A', 'B']" in vsm._test_events[0][1]


def test_static_nonjoining_branch_stays_locked_until_trigger_frames():
    """不入周期的 static 分支累计期间也必须维持同帧 priority 决定。"""
    import numpy as np

    vsm = _make_vsm()
    _appear(vsm, "A", 1.0)
    vsm.step_detection_type["B"] = "static"
    vsm.step_static_config["B"] = {
        "trigger_frames": 2,
        "join_cycle": False,
        "trigger_event": None,
    }
    vsm.step_static_triggered["B"] = False
    frame = [_detection("A"), _detection("B", 0.3), _detection("C", 0.5)]

    vsm._update_step_stats(frame, np.zeros((40, 60, 3), dtype=np.uint8))
    assert vsm.current_cycle_steps == ["A"]
    assert vsm._test_events == []

    vsm._update_step_stats(frame, np.zeros((40, 60, 3), dtype=np.uint8))

    assert vsm.current_cycle_steps == []
    assert [event_id for event_id, _ in vsm._test_events] == [1]
    assert "['A', 'B']" in vsm._test_events[0][1]


def test_idle_timeout_zero_preserves_open_cycle():
    import time

    import numpy as np

    vsm = _make_vsm(idle_timeout_seconds=0, idle_timeout_event_id=4)
    _appear(vsm, "A", 1.0)
    vsm._last_step_added_time = time.time() - 3600

    vsm._update_step_stats(
        [_detection("A")], np.zeros((40, 60, 3), dtype=np.uint8)
    )

    assert vsm.current_cycle_steps == ["A"]
    assert vsm._test_events == []


def test_custom_based_on_sequential_keeps_existing_non_prefix_flow():
    vsm = _make_vsm(custom_based_on="sequential")

    _appear(vsm, "A", 1.0)
    _appear(vsm, "B", 2.0)
    _appear(vsm, "C", 3.0)

    assert vsm.current_cycle_steps == ["A", "B", "C"]


def test_pure_custom_allows_configured_repeat_after_real_disappearance():
    vsm = _make_vsm(custom_conditions=[
        {"id": 1, "priority": 1, "sequence": [1, 2, 1], "event_id": 1},
    ])

    _appear(vsm, "A", 1.0)
    _appear(vsm, "B", 2.0)
    vsm.step_last_seen.pop("A", None)
    vsm.step_start_time.pop("A", None)
    vsm.step_frame_confirmed["A"] = False
    vsm.step_consecutive_frames["A"] = 0
    _appear(vsm, "A", 3.0)

    assert vsm.current_cycle_steps == ["A", "B", "A"]
    assert vsm._cycle_regression is False


def test_pure_custom_allows_configured_consecutive_repeat_after_disappearance():
    vsm = _make_vsm(custom_conditions=[
        {"id": 1, "priority": 1, "sequence": [1, 1], "event_id": 1},
    ])

    _appear(vsm, "A", 1.0)
    vsm.step_last_seen.pop("A", None)
    vsm.step_start_time.pop("A", None)
    vsm.step_frame_confirmed["A"] = False
    vsm.step_consecutive_frames["A"] = 0
    _appear(vsm, "A", 2.0)

    assert vsm.current_cycle_steps == ["A", "A"]
    assert vsm._cycle_regression is False


def test_dynamic_condition_settle_latches_visible_and_completed_labels():
    """动态条件结算后，常驻/短闪标签都不能直接污染下一周期。"""
    import numpy as np

    vsm = _make_vsm()
    for label in ("A", "B"):
        vsm.step_time_config[label]["disappear_delay"] = 60.0
    settle_calls = []
    original_settle = vsm._settle_custom_cycle

    def _settle_spy(*args, **kwargs):
        settle_calls.append((args, kwargs))
        return original_settle(*args, **kwargs)

    vsm._settle_custom_cycle = _settle_spy
    frame = np.zeros((40, 60, 3), dtype=np.uint8)

    vsm._update_step_stats([_detection("A")], frame)
    vsm._update_step_stats([_detection("A"), _detection("B", 0.3)], frame)
    # A 仍常驻；B 被其它有效步骤按规则 B 提前判定完成并触发动态条件结算。
    vsm.step_last_seen["B"] -= 1.0
    vsm._update_step_stats([_detection("A")], frame)

    assert [event_id for event_id, _ in vsm._test_events] == [1]
    assert vsm.current_cycle_steps == []
    assert set(vsm._pure_custom_settle_latched_labels) == {"A", "B"}
    assert len(settle_calls) == 1

    # B 短闪重现，随后 A 也短暂闪断；两者都未满足各自 disappear_delay，
    # 不得重新开周期或上报第二个事件。
    vsm._update_step_stats(
        [_detection("A"), _detection("B", 0.3)], frame,
    )
    vsm._update_step_stats([_detection("A")], frame)
    vsm._update_step_stats([], frame)
    vsm._update_step_stats([_detection("A")], frame)

    assert vsm.current_cycle_steps == []
    assert [event_id for event_id, _ in vsm._test_events] == [1]
    assert set(vsm._pure_custom_settle_latched_labels) == {"A", "B"}
    assert len(settle_calls) == 1


def test_dynamic_condition_rearms_after_each_label_really_disappears():
    """所有条件标签分别真离场后，下一件仍可走同一动态条件结算。"""
    import time

    import numpy as np

    vsm = _make_vsm()
    vsm.step_time_config["A"]["disappear_delay"] = 1.0
    vsm.step_time_config["B"]["disappear_delay"] = 60.0
    frame = np.zeros((40, 60, 3), dtype=np.uint8)

    vsm._update_step_stats([_detection("A")], frame)
    vsm._update_step_stats([_detection("A"), _detection("B", 0.3)], frame)
    vsm.step_last_seen["B"] = time.time() - 61.0
    vsm._update_step_stats([_detection("A")], frame)
    assert set(vsm._pure_custom_settle_latched_labels) == {"A", "B"}

    # 每个标签独立复用自己的 disappear_delay；A 已真离场时 B 仍保持锁存。
    now = time.time()
    vsm._pure_custom_settle_latched_labels["A"] = now - 2.0
    vsm._pure_custom_settle_latched_labels["B"] = now
    vsm._update_step_stats([], frame)
    assert set(vsm._pure_custom_settle_latched_labels) == {"B"}

    vsm._pure_custom_settle_latched_labels["B"] = time.time() - 61.0
    vsm._update_step_stats([], frame)
    assert vsm._pure_custom_settle_latched_labels == {}

    # 两者均完成真离场后，下一件 A -> B 仍应正常结算。
    vsm._update_step_stats([_detection("A")], frame)
    vsm._update_step_stats([_detection("A"), _detection("B", 0.3)], frame)
    vsm.step_last_seen["B"] = time.time() - 61.0
    vsm._update_step_stats([_detection("A")], frame)

    assert [event_id for event_id, _ in vsm._test_events] == [1, 1]
    assert vsm.current_cycle_steps == []


def test_based_custom_exact_match_does_not_use_pure_custom_settle_path():
    """custom+sequential/detection 的完整条件仍保留既有直接事件路径。"""
    import pytest

    for custom_based_on in ("sequential", "detection"):
        vsm = _make_vsm(custom_based_on=custom_based_on)
        vsm.current_cycle_steps = ["A", "B"]
        vsm._settle_custom_cycle = lambda *args, **kwargs: pytest.fail(
            f"custom+{custom_based_on} 不应进入 pure custom 统一结算路径"
        )

        vsm._check_events("B")

        assert [event_id for event_id, _ in vsm._test_events] == [1]
        assert vsm.current_cycle_steps == []


def test_reset_stats_clears_pure_custom_post_settle_latch():
    vsm = _make_vsm()
    vsm._pure_custom_settle_latched_labels = {"A": 1.0}
    vsm._persist_counters = lambda: None

    vsm.reset_stats()

    assert vsm._pure_custom_settle_latched_labels == {}


def test_legacy_custom_settle_without_unmatched_event_stays_silent():
    vsm = _make_vsm()
    _appear(vsm, "A", 1.0)

    vsm._settle_custom_cycle()

    assert vsm._test_events == []
    assert vsm.current_cycle_steps == []
