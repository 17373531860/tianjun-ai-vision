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


class TestManualResetPeriodicCounter:
    """v3.7.3: Monitor"重置"按钮 → reset_periodic_counter(rule_id) 行为."""

    def _rule(self, rid="pa_manual", interval=10):
        return {
            "id": rid,
            "name": rid,
            "enabled": True,
            "trigger_step_ids": ["s_E"],
            "interval": interval,
            "count_basis": "all",
            "reset_policy": "always",
            "due_warning_event_id": 300,
            "overdue_event_id": 301,
            "overdue_repeat": "every_cycle",
            "run_on_start": False,
        }

    def test_reset_single_zeros_only_target(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        vsm = VideoSourceManager(channel_id=0)
        vsm.project_config = {
            "id": 99100,
            "steps_config": [
                {"id": "s_A", "label": "A", "enabled": True},
                {"id": "s_E", "label": "E", "enabled": True},
            ],
            "events_config": [_due_event(eid=300), _due_event(eid=301)],
            "pipeline_config": {
                "periodic_actions": [self._rule("pa_a"), self._rule("pa_b")],
            },
        }
        vsm.counters = {"合格总数": 0, "不良总数": 0, "总产量": 0, "NG步骤": 0}
        vsm._apply_periodic_actions(vsm.project_config)

        vsm._periodic_counters["pa_a"] = 7
        vsm._periodic_counters["pa_b"] = 4
        # 模拟有一条规则已在 overdue cooldown 中
        for r in vsm._periodic_actions:
            if r["id"] == "pa_a":
                r["last_overdue_count"] = 15

        result = vsm.reset_periodic_counter("pa_a")
        assert result == {"reset": ["pa_a"]}
        assert vsm._periodic_counters["pa_a"] == 0
        assert vsm._periodic_counters["pa_b"] == 4  # 未动
        pa_a_rule = next(r for r in vsm._periodic_actions if r["id"] == "pa_a")
        assert pa_a_rule["last_overdue_count"] == -1  # cooldown 已解

    def test_reset_all_when_rule_id_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        vsm = VideoSourceManager(channel_id=0)
        vsm.project_config = {
            "id": 99101,
            "steps_config": [{"id": "s_E", "label": "E", "enabled": True}],
            "events_config": [_due_event(eid=300), _due_event(eid=301)],
            "pipeline_config": {
                "periodic_actions": [self._rule("pa_x"), self._rule("pa_y")],
            },
        }
        vsm.counters = {"合格总数": 0, "不良总数": 0, "总产量": 0, "NG步骤": 0}
        vsm._apply_periodic_actions(vsm.project_config)

        vsm._periodic_counters["pa_x"] = 11
        vsm._periodic_counters["pa_y"] = 99

        result = vsm.reset_periodic_counter(None)
        assert set(result["reset"]) == {"pa_x", "pa_y"}
        assert vsm._periodic_counters == {"pa_x": 0, "pa_y": 0}

    def test_reset_unknown_rule_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        vsm = _make_vsm_with_rule(self._rule("pa_real"))
        vsm._periodic_counters["pa_real"] = 3

        result = vsm.reset_periodic_counter("pa_ghost")
        assert result == {"reset": []}
        assert vsm._periodic_counters["pa_real"] == 3  # 未动

    def test_reset_persists_to_disk(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        vsm = _make_vsm_with_rule(self._rule("pa_disk"))
        vsm._periodic_counters["pa_disk"] = 8
        vsm._persist_periodic_counters()

        vsm.reset_periodic_counter("pa_disk")
        path = vsm._periodic_counter_path(vsm.project_config["id"])
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # v3.7.4: 持久化升级为 {"counters": {...}, "last_done_ts": {...}}.
        # 老格式 ({rule_id: counter}) 仅在读取时兼容, 写出统一新格式.
        assert saved["counters"]["pa_disk"] == 0
        assert "last_done_ts" in saved

    def test_reset_clears_run_on_start_pending(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path)
        )
        rule = self._rule("pa_rs")
        rule["run_on_start"] = True
        vsm = _make_vsm_with_rule(rule, events=[_due_event(eid=300)])
        vsm._run_periodic_actions_on_start()
        assert "pa_rs" in vsm._run_on_start_pending

        vsm.reset_periodic_counter("pa_rs")
        assert "pa_rs" not in vsm._run_on_start_pending
        assert vsm._periodic_counters["pa_rs"] == 0


# ============================================================================
# v3.7.5: 旁路保养观察账本 — 顺序模式下 FIX-381 拦截 trigger_step 后仍能清零
# ============================================================================
class TestPeriodicTriggerObservedBypassesSequentialFilter:
    """v3.7.5: _observe_periodic_trigger + _periodic_triggers_observed 旁路账本.

    背景: v3.7.2 (FIX-381) 在 process_step_detection 顺序模式分支拦截了"非
    expected_seq 内的步骤"return, 导致保养类 trigger_step (本来就在主序列外)
    永远进不了 cycle.step_sequence, _check_periodic_actions 永远看不到 -> 永远
    清不了零. 修法: 早于 FIX-381 拦截先记一笔到旁路账本, 周期判定时合并算 trigger.
    """

    def _rule(self, rid="pa_obs", trigger="E", interval=5):
        return {
            "id": rid,
            "name": f"保养-{rid}",
            "interval": interval,
            "trigger_step": trigger,
            # _apply_periodic_actions 读 trigger_step_labels (兼容前端直传 labels);
            # v3.7.5 测试历史上写成 trigger_labels (没 _step 前缀), 与生产代码字段
            # 名不一致 → _apply 解析后 rule 列表为空 → _observe 永远不写账本.
            "trigger_step_labels": [trigger],
            "due_event_id": 300,
            "overdue_event_id": 301,
            "due_message": "{name} 该做了 ({counter}/{interval})",
            "overdue_message": "{name} 已超 {overdue} 轮!",
            "reset_policy": "always",
            "count_basis": "all",
            "channel_filter": [],
            "run_on_start": False,
            "time_interval_seconds": 0,
            "time_due_message": "",
            "time_overdue_message": "",
        }

    def test_observe_records_trigger_into_book(self):
        """observe 应把 trigger_labels 里的 label 加进 _periodic_triggers_observed."""
        vsm = _make_vsm_with_rule(self._rule(trigger="E"))
        assert vsm._periodic_triggers_observed == set()

        vsm._observe_periodic_trigger("E")
        assert "E" in vsm._periodic_triggers_observed

    def test_observe_ignores_irrelevant_label(self):
        """非 trigger_labels 里的 label 不应进账本."""
        vsm = _make_vsm_with_rule(self._rule(trigger="E"))
        vsm._observe_periodic_trigger("A")
        vsm._observe_periodic_trigger("D")
        assert vsm._periodic_triggers_observed == set()

    def test_observe_dedupes_same_label(self):
        """同一轮多次观察到同一 label, 账本去重 (set)."""
        vsm = _make_vsm_with_rule(self._rule(trigger="E"))
        vsm._observe_periodic_trigger("E")
        vsm._observe_periodic_trigger("E")
        vsm._observe_periodic_trigger("E")
        assert vsm._periodic_triggers_observed == {"E"}

    def test_check_resets_when_only_observed_has_trigger(self):
        """核心场景: cycle_steps 里没有 E (被 FIX-381 拦截了), 但旁路账本有 ->
        _check_periodic_actions 仍应识别为 did_trigger 并清零."""
        rule = self._rule(trigger="E", interval=5)
        vsm = _make_vsm_with_rule(rule)
        vsm._periodic_counters["pa_obs"] = 3

        vsm._observe_periodic_trigger("E")
        vsm._check_periodic_actions(cycle_steps=["A", "B", "D"], is_good=True)

        assert vsm._periodic_counters["pa_obs"] == 0, (
            "旁路账本里有 trigger 时, did_trigger 应被识别 -> reset_policy=always 清零"
        )

    def test_observed_book_cleared_after_check(self):
        """_check_periodic_actions 判定完应清空旁路账本, 不串到下一轮."""
        rule = self._rule(trigger="E")
        vsm = _make_vsm_with_rule(rule)
        vsm._observe_periodic_trigger("E")
        assert "E" in vsm._periodic_triggers_observed

        vsm._check_periodic_actions(cycle_steps=["A"], is_good=True)
        assert vsm._periodic_triggers_observed == set(), "判定完应清空"

    def test_reset_stats_clears_observed_book(self):
        """reset_stats 应连同账本一起清, 防 reset 后还残留."""
        rule = self._rule(trigger="E")
        vsm = _make_vsm_with_rule(rule)
        vsm._observe_periodic_trigger("E")
        assert "E" in vsm._periodic_triggers_observed

        vsm.reset_stats()
        assert vsm._periodic_triggers_observed == set(), "reset_stats 应清空账本"

    def test_channel_filter_respected_in_observe(self):
        """observe 时应遵守 rule.channel_filter, 别工位的 trigger 不能错记到本工位."""
        rule = self._rule(trigger="E")
        rule["channel_filter"] = [1, 2]  # 仅 channel 1/2 关心
        vsm = _make_vsm_with_rule(rule, channel_id=0)  # 本工位是 0
        vsm._observe_periodic_trigger("E")
        assert vsm._periodic_triggers_observed == set(), "channel 0 不在 filter 里, 不应记账"

    def test_time_also_resets_via_observed_path(self):
        """时间维度 (last_done_ts) 也应通过旁路账本路径同步重置."""
        import time
        rule = self._rule(trigger="E", interval=5)
        rule["time_interval_seconds"] = 600  # 同时启用时间维度
        vsm = _make_vsm_with_rule(rule)
        vsm._periodic_counters["pa_obs"] = 3
        # 把 last_done_ts 倒推, 制造已超期场景
        vsm._periodic_last_done_ts["pa_obs"] = time.time() - 1000

        vsm._observe_periodic_trigger("E")
        vsm._check_periodic_actions(cycle_steps=["A"], is_good=True)

        assert vsm._periodic_counters["pa_obs"] == 0
        # last_done_ts 应被刷到现在 (允许 1 秒误差)
        assert abs(vsm._periodic_last_done_ts["pa_obs"] - time.time()) < 1.0
