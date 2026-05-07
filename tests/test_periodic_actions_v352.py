"""v3.5.2 周期性强制动作 — reset_stats / run_on_start 集成测试.

直接构造真实 VideoSourceManager 实例 (绕过摄像头/推理), 验证:
1. reset_stats() 调用后 _periodic_counters 全部归零, 且 cooldown 标志重置.
2. _run_periodic_actions_on_start() 把 run_on_start=True 规则的 counter 推到
   interval **并把规则 ID 加入 _run_on_start_pending**, 不立即触发事件.
3. _check_periodic_actions_on_first_step(label) — 开机后**第一个步骤完成**:
   - label ∈ trigger_labels → 静默 reset, pending 移除
   - label ∉ trigger_labels → emit overdue 事件, pending 移除
4. _emit_periodic_notification 写 events_log 时永远带 should_warn_no_barcode=False,
   不污染前端"未绑码"toast.
"""
from __future__ import annotations

import pytest

from backend.api.source import VideoSourceManager


# ----------------------------------------------------------------
# 工具: 不开摄像头, 直接构造 VSM 并塞配置
# ----------------------------------------------------------------
def _make_vsm_with_rule(rule, events=None, channel_id=0):
    """构造 VSM 实例 + 加载 periodic_actions 规则 (不启动任何线程)."""
    vsm = VideoSourceManager(channel_id=channel_id)
    vsm.project_config = {
        "id": 99000 + channel_id,
        "steps_config": [
            {"id": "s_A", "label": "A", "enabled": True},
            {"id": "s_E", "label": "E", "enabled": True},
        ],
        "events_config": events or [],
        "pipeline_config": {"periodic_actions": [rule]},
    }
    vsm.counters = {"合格总数": 0, "不良总数": 0, "总产量": 0, "NG步骤": 0}
    vsm._apply_periodic_actions(vsm.project_config)
    return vsm


def _due_event(eid=300):
    return {
        "id": eid,
        "name": "保养到期",
        "actions": [],
        "show_notification": True,
        "toast_id": "ng",
    }


# ----------------------------------------------------------------
# Tests
# ----------------------------------------------------------------
class TestResetStatsClearsPeriodicCounter:
    """v3.5.2 需求1: 清零计数器 → periodic counter 同步归零."""

    def test_reset_stats_zeros_periodic_counters(self, tmp_path, monkeypatch):
        # 隔离 counter 持久化文件到 tmp 目录
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_1",
            "name": "清洁治具",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 5,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": None,
            "overdue_event_id": None,
            "overdue_repeat": "every_cycle",
            "channel_filter": None,
            "run_on_start": False,
        }
        vsm = _make_vsm_with_rule(rule)

        # 走 3 个不含 E 的 cycle, counter 应该到 3
        for _ in range(3):
            vsm._check_periodic_actions(["A"], True)
        assert vsm._periodic_counters["pa_test_1"] == 3

        # 调用 reset_stats — 应该把 counter 清回 0
        vsm.reset_stats()
        assert vsm._periodic_counters["pa_test_1"] == 0

    def test_reset_stats_clears_cooldown_flag(self, tmp_path, monkeypatch):
        """reset 后超期 cooldown 状态也得清, 否则 once 模式无法再次触发."""
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_2",
            "name": "保养",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 2,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": None,
            "overdue_event_id": 200,
            "overdue_repeat": "once",
            "channel_filter": None,
            "run_on_start": False,
        }
        vsm = _make_vsm_with_rule(rule, events=[_due_event(eid=200)])

        for _ in range(4):
            vsm._check_periodic_actions(["A"], True)
        # last_overdue_count 已被设
        assert vsm._periodic_actions[0]["last_overdue_count"] >= 0

        vsm.reset_stats()
        assert vsm._periodic_actions[0]["last_overdue_count"] == -1


class TestRunOnStart:
    """v3.5.2 需求2: run_on_start = 静默推 counter 到 interval, 等第一轮自然判定."""

    def test_run_on_start_pushes_counter_to_interval(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_3",
            "name": "首检",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 10,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": 300,
            "overdue_event_id": None,
            "overdue_repeat": "every_cycle",
            "channel_filter": None,
            "run_on_start": True,
        }
        vsm = _make_vsm_with_rule(rule, events=[_due_event(eid=300)])

        assert vsm._periodic_counters["pa_test_3"] == 0

        vsm._run_periodic_actions_on_start()
        assert vsm._periodic_counters["pa_test_3"] == 10

    def test_run_on_start_does_not_emit_event_immediately(self, tmp_path, monkeypatch):
        """v3.5.2 调整: run_on_start 不立刻 emit toast 事件, 等第一轮判定."""
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_3b",
            "name": "首检静默",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 10,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": 300,
            "overdue_event_id": 400,
            "overdue_repeat": "every_cycle",
            "channel_filter": None,
            "run_on_start": True,
        }
        vsm = _make_vsm_with_rule(
            rule,
            events=[
                _due_event(eid=300),
                {"id": 400, "name": "超期", "actions": [], "show_notification": True, "toast_id": "ng"},
            ],
        )

        vsm._run_periodic_actions_on_start()
        eids = [str(e.get("event_id")) for e in vsm.events_log]
        assert "300" not in eids, f"run_on_start 不应立即 emit due 事件, events_log={eids}"
        assert "400" not in eids, f"run_on_start 不应立即 emit overdue 事件, events_log={eids}"

    def test_run_on_start_pending_set_populated(self, tmp_path, monkeypatch):
        """run_on_start=True 规则启动后被加入 _run_on_start_pending 集合."""
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_3p",
            "name": "首件等待",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 5,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": 300,
            "overdue_event_id": 400,
            "overdue_repeat": "every_cycle",
            "channel_filter": None,
            "run_on_start": True,
        }
        vsm = _make_vsm_with_rule(
            rule,
            events=[
                _due_event(eid=300),
                {"id": 400, "name": "超期", "actions": [], "show_notification": True, "toast_id": "ng"},
            ],
        )

        vsm._run_periodic_actions_on_start()
        assert "pa_test_3p" in vsm._run_on_start_pending, (
            f"run_on_start 应该把 rule_id 加入 pending, got {vsm._run_on_start_pending}"
        )

    def test_first_step_is_trigger_silent_reset(self, tmp_path, monkeypatch):
        """开机首检 + 首个步骤完成 = trigger_step → 静默 reset, 不触发事件."""
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_3c",
            "name": "首件_有做",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 5,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": 300,
            "overdue_event_id": 400,
            "overdue_repeat": "every_cycle",
            "channel_filter": None,
            "run_on_start": True,
        }
        vsm = _make_vsm_with_rule(
            rule,
            events=[
                _due_event(eid=300),
                {"id": 400, "name": "超期", "actions": [], "show_notification": True, "toast_id": "ng"},
            ],
        )

        vsm._run_periodic_actions_on_start()
        assert vsm._periodic_counters["pa_test_3c"] == 5
        assert "pa_test_3c" in vsm._run_on_start_pending

        # 第一个完成的步骤就是 trigger E → 静默 reset
        vsm._check_periodic_actions_on_first_step("E")
        assert vsm._periodic_counters["pa_test_3c"] == 0
        assert "pa_test_3c" not in vsm._run_on_start_pending, "判定后应从 pending 移除"
        eids = [str(e.get("event_id")) for e in vsm.events_log]
        assert "300" not in eids and "400" not in eids, (
            f"首个步骤就是 trigger 不应触发任何 periodic 事件, got {eids}"
        )

    def test_first_step_not_trigger_emits_event(self, tmp_path, monkeypatch):
        """开机首检 + 首个步骤完成 != trigger_step → 立即 emit overdue 事件."""
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_3d",
            "name": "首件_没做",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 5,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": None,
            "overdue_event_id": 400,
            "overdue_repeat": "every_cycle",
            "channel_filter": None,
            "run_on_start": True,
        }
        vsm = _make_vsm_with_rule(
            rule,
            events=[{"id": 400, "name": "超期", "actions": [], "show_notification": True, "toast_id": "ng"}],
        )

        vsm._run_periodic_actions_on_start()

        # 第一个完成的步骤是 A (不是 trigger E) → emit 事件
        vsm._check_periodic_actions_on_first_step("A")
        assert "pa_test_3d" not in vsm._run_on_start_pending, "判定后应从 pending 移除"
        # counter 未变 (仍为 interval), 等周期结束按正常累加规则走
        assert vsm._periodic_counters["pa_test_3d"] == 5
        eids = [str(e.get("event_id")) for e in vsm.events_log]
        assert "400" in eids, f"首个步骤不是 trigger 应触发事件, got {eids}"

    def test_first_step_judgement_only_runs_once(self, tmp_path, monkeypatch):
        """开机首检判定一次性 — 第一次判定后 pending 移除, 后续步骤不再清算."""
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_3o",
            "name": "一次性",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 5,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": None,
            "overdue_event_id": 400,
            "overdue_repeat": "every_cycle",
            "channel_filter": None,
            "run_on_start": True,
        }
        vsm = _make_vsm_with_rule(
            rule,
            events=[{"id": 400, "name": "超期", "actions": [], "show_notification": True, "toast_id": "ng"}],
        )

        vsm._run_periodic_actions_on_start()
        # 第一个步骤 A — emit 事件
        vsm._check_periodic_actions_on_first_step("A")
        first_count = sum(1 for e in vsm.events_log if str(e.get("event_id")) == "400")
        assert first_count == 1

        # 第二个步骤 B — 不应再 emit (pending 已空)
        vsm._check_periodic_actions_on_first_step("B")
        second_count = sum(1 for e in vsm.events_log if str(e.get("event_id")) == "400")
        assert second_count == 1, "首检判定一次性, 第二个步骤不应再次 emit"

    def test_emit_periodic_notification_marks_should_warn_no_barcode_false(
        self, tmp_path, monkeypatch
    ):
        """周期性强制动作事件不应弹"未绑码"toast — 后端显式标 False."""
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_3e",
            "name": "标志位",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 1,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": 300,
            "overdue_event_id": None,
            "overdue_repeat": "every_cycle",
            "channel_filter": None,
            "run_on_start": False,
        }
        vsm = _make_vsm_with_rule(rule, events=[_due_event(eid=300)])

        # 走一个不含 E 的 cycle, 触发 due
        vsm._check_periodic_actions(["A"], True)
        # 找出由 periodic_action 写的事件
        pa_events = [e for e in vsm.events_log if e.get("source") == "periodic_action"]
        assert pa_events, "应至少有一条 periodic_action 事件"
        for e in pa_events:
            assert e.get("should_warn_no_barcode") is False, (
                f"周期性动作事件应显式标 should_warn_no_barcode=False, got {e}"
            )

    def test_run_on_start_false_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_4",
            "name": "regular",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 10,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": 300,
            "overdue_event_id": None,
            "overdue_repeat": "every_cycle",
            "channel_filter": None,
            "run_on_start": False,
        }
        vsm = _make_vsm_with_rule(rule, events=[_due_event(eid=300)])

        vsm._run_periodic_actions_on_start()
        assert vsm._periodic_counters["pa_test_4"] == 0
        # 不应该往 events_log 写
        eids = [str(e.get("event_id")) for e in vsm.events_log]
        assert "300" not in eids

    def test_run_on_start_idempotent_on_counter(self, tmp_path, monkeypatch):
        """连续调多次 — counter 不会越过 interval."""
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_6",
            "name": "idem",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 5,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": 300,
            "overdue_event_id": None,
            "overdue_repeat": "every_cycle",
            "channel_filter": None,
            "run_on_start": True,
        }
        vsm = _make_vsm_with_rule(rule, events=[_due_event(eid=300)])

        vsm._run_periodic_actions_on_start()
        vsm._run_periodic_actions_on_start()
        vsm._run_periodic_actions_on_start()

        assert vsm._periodic_counters["pa_test_6"] == 5

    def test_run_on_start_skips_other_channels(self, tmp_path, monkeypatch):
        """channel_filter 限定其他通道时, 当前通道 run_on_start 不动."""
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = {
            "id": "pa_test_7",
            "name": "only_ch1",
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": 5,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": 300,
            "overdue_event_id": None,
            "overdue_repeat": "every_cycle",
            "channel_filter": [1, 2],  # 只对 ch1/ch2 生效, 我们是 ch0
            "run_on_start": True,
        }
        vsm = _make_vsm_with_rule(rule, events=[_due_event(eid=300)], channel_id=0)

        vsm._run_periodic_actions_on_start()
        assert vsm._periodic_counters["pa_test_7"] == 0
