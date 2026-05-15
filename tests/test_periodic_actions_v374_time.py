"""v3.7.4 周期性强制动作 — 按时间触发 (time_interval_seconds) 集成测试.

业务背景: 客户「检测中、生产停了」场景下需要按时间触发"该做某动作了",
而不是按 cycle 数. 本套测试覆盖:

1. _apply_periodic_actions 正确解析新字段, 校验 interval+time_interval 不能都为 0.
2. _check_periodic_actions_time_only 在时间到期时触发 due / 超期时触发 overdue,
   且 cooldown / once 节流正确.
3. _check_periodic_actions (cycle_end 路径) 完成动作时**同时**重置 counter 和
   last_done_ts.
4. get_periodic_actions_status 返回新增的 time_* 字段, 整体 state 取更严重的.
5. 持久化新格式 (counters + last_done_ts) 与老格式向后兼容.
6. reset_periodic_counter / reset_stats 同步刷新 last_done_ts.
"""
from __future__ import annotations

import json
import time

import pytest

from backend.api.source import VideoSourceManager


def _make_vsm_with_rule(rule, events=None, channel_id=0, project_id=99100):
    vsm = VideoSourceManager(channel_id=channel_id)
    vsm.project_config = {
        "id": project_id + channel_id,
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


def _due_event(eid=370):
    return {"id": eid, "name": "时间到期", "actions": [], "show_notification": True, "toast_id": "ng"}


def _overdue_event(eid=371):
    return {"id": eid, "name": "时间超期", "actions": [], "show_notification": True, "toast_id": "ng"}


# ============================================================
# 1. _apply_periodic_actions 解析与校验
# ============================================================
class TestApplyParsesTimeInterval:
    def test_time_interval_seconds_parsed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        rule = {
            "id": "pa_time_1", "name": "30秒清洁", "enabled": True,
            "trigger_step_ids": ["s_E"], "interval": 0,
            "time_interval_seconds": 30,
            "due_warning_event_id": 370, "overdue_event_id": 371,
        }
        vsm = _make_vsm_with_rule(rule, events=[_due_event(), _overdue_event()])
        assert len(vsm._periodic_actions) == 1
        parsed = vsm._periodic_actions[0]
        assert parsed["interval"] == 0
        assert parsed["time_interval_seconds"] == 30
        # 初始 last_done_ts ≈ now
        assert "pa_time_1" in vsm._periodic_last_done_ts
        assert abs(vsm._periodic_last_done_ts["pa_time_1"] - time.time()) < 1

    def test_both_zero_rule_skipped(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        rule = {
            "id": "pa_zero", "name": "无效规则", "enabled": True,
            "trigger_step_ids": ["s_E"], "interval": 0, "time_interval_seconds": 0,
        }
        vsm = _make_vsm_with_rule(rule)
        assert vsm._periodic_actions == []  # 跳过
        assert "都为 0" in capsys.readouterr().out


# ============================================================
# 2. _check_periodic_actions_time_only — 时间触发主路径
# ============================================================
class TestCheckTimeOnlyTriggers:
    def test_time_due_then_overdue(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        rule = {
            "id": "pa_time_2", "name": "10秒规则",
            "trigger_step_ids": ["s_E"], "interval": 0, "time_interval_seconds": 10,
            "due_warning_event_id": 370, "overdue_event_id": 371,
            "overdue_repeat": "every_cycle",
        }
        vsm = _make_vsm_with_rule(rule, events=[_due_event(), _overdue_event()])

        # t=0 还没到期, 不触发
        vsm._check_periodic_actions_time_only(time.time())
        assert vsm.events_log == []

        # 伪装成"上次完成是 11 秒前" → 已超期 1 秒
        vsm._periodic_last_done_ts["pa_time_2"] = time.time() - 11
        vsm._check_periodic_actions_time_only(time.time())
        assert len(vsm.events_log) == 1
        assert vsm.events_log[0]["event_id"] == "371"  # overdue
        assert vsm.events_log[0]["source"] == "periodic_action"
        assert "超期" in vsm.events_log[0]["reason"]

    def test_cooldown_throttle(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        rule = {
            "id": "pa_time_3", "name": "5秒+冷却10",
            "trigger_step_ids": ["s_E"], "interval": 0, "time_interval_seconds": 5,
            "overdue_event_id": 371, "overdue_repeat": "cooldown:10",
        }
        vsm = _make_vsm_with_rule(rule, events=[_overdue_event()])

        # t = 6 秒（超期 1 秒）→ 第一次触发
        vsm._periodic_last_done_ts["pa_time_3"] = time.time() - 6
        vsm._check_periodic_actions_time_only(time.time())
        assert len(vsm.events_log) == 1

        # t = 8 秒（距上次触发才差 2 秒，cooldown=10 没到）→ 不触发
        vsm._periodic_last_done_ts["pa_time_3"] = time.time() - 8
        vsm._check_periodic_actions_time_only(time.time())
        assert len(vsm.events_log) == 1

        # t = 17 秒（距上次触发差 11 秒，cooldown 满足）→ 再触发
        vsm._periodic_last_done_ts["pa_time_3"] = time.time() - 17
        vsm._check_periodic_actions_time_only(time.time())
        assert len(vsm.events_log) == 2

    def test_disabled_when_time_interval_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        rule = {
            "id": "pa_pure_count", "name": "纯次数",
            "trigger_step_ids": ["s_E"], "interval": 5, "time_interval_seconds": 0,
            "overdue_event_id": 371,
        }
        vsm = _make_vsm_with_rule(rule, events=[_overdue_event()])

        # 时间过去再多, time_only 路径都不应触发
        vsm._periodic_last_done_ts["pa_pure_count"] = time.time() - 99999
        vsm._check_periodic_actions_time_only(time.time())
        assert vsm.events_log == []


# ============================================================
# 3. cycle_end 路径完成动作时同步重置 timestamp
# ============================================================
class TestCycleEndResetsTimestamp:
    def test_trigger_step_resets_both(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        rule = {
            "id": "pa_dual", "name": "10轮+30秒",
            "trigger_step_ids": ["s_E"], "interval": 10, "time_interval_seconds": 30,
            "count_basis": "all", "reset_policy": "always",
        }
        vsm = _make_vsm_with_rule(rule)
        # 模拟已经累积了 5 轮 + 上次完成是 20 秒前
        vsm._periodic_counters["pa_dual"] = 5
        vsm._periodic_last_done_ts["pa_dual"] = time.time() - 20
        # 完成动作 — 含 trigger_step
        vsm._check_periodic_actions(cycle_steps=["A", "E"], is_good=True)
        assert vsm._periodic_counters["pa_dual"] == 0
        # last_done_ts 应该被刷成 ≈ now
        assert abs(vsm._periodic_last_done_ts["pa_dual"] - time.time()) < 1


# ============================================================
# 4. get_periodic_actions_status 返回新字段
# ============================================================
class TestStatusReturnsTimeFields:
    def test_status_has_time_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        rule = {
            "id": "pa_status", "name": "测status",
            "trigger_step_ids": ["s_E"], "interval": 0, "time_interval_seconds": 60,
        }
        vsm = _make_vsm_with_rule(rule)
        # 上次完成是 30 秒前 → 还剩 30 秒 → state=ok
        vsm._periodic_last_done_ts["pa_status"] = time.time() - 30
        st = vsm.get_periodic_actions_status()
        assert len(st) == 1
        s = st[0]
        assert s["time_interval_seconds"] == 60
        assert 28 <= s["time_elapsed_seconds"] <= 32
        assert 28 <= s["time_remaining_seconds"] <= 32
        assert s["time_state"] == "ok"
        assert s["state"] == "ok"  # 整体状态

    def test_status_time_overdue_dominates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        rule = {
            "id": "pa_severe", "name": "严重维度优先",
            "trigger_step_ids": ["s_E"], "interval": 100, "time_interval_seconds": 10,
        }
        vsm = _make_vsm_with_rule(rule)
        # 次数维度 ok (counter=2, interval=100), 时间维度 overdue (gap=15s)
        vsm._periodic_counters["pa_severe"] = 2
        vsm._periodic_last_done_ts["pa_severe"] = time.time() - 15
        st = vsm.get_periodic_actions_status()[0]
        assert st["count_state"] == "ok"
        assert st["time_state"] == "overdue"
        assert st["state"] == "overdue"  # 取更严重


# ============================================================
# 5. 持久化新旧格式兼容
# ============================================================
class TestPersistenceFormat:
    def test_new_format_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        rule = {
            "id": "pa_pers", "name": "持久化",
            "trigger_step_ids": ["s_E"], "interval": 5, "time_interval_seconds": 60,
        }
        vsm = _make_vsm_with_rule(rule)
        vsm._periodic_counters["pa_pers"] = 3
        anchor = time.time() - 25
        vsm._periodic_last_done_ts["pa_pers"] = anchor
        vsm._persist_periodic_counters()

        path = vsm._periodic_counter_path(vsm.project_config["id"])
        data = json.loads(open(path).read())
        assert data == {
            "counters": {"pa_pers": 3},
            "last_done_ts": {"pa_pers": pytest.approx(anchor, abs=0.01)},
        }

        # 重新构造一个 VSM 验证恢复
        vsm2 = _make_vsm_with_rule(rule, project_id=99100)  # 同 project_id
        assert vsm2._periodic_counters["pa_pers"] == 3
        assert abs(vsm2._periodic_last_done_ts["pa_pers"] - anchor) < 0.01

    def test_old_format_back_compat(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        # 先手写老格式 {rule_id: counter}
        project_id = 99100
        old_path = tmp_path / "counters" / f"project_{project_id}_ch0_periodic.json"
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_text(json.dumps({"pa_legacy": 7}))

        rule = {
            "id": "pa_legacy", "name": "老格式",
            "trigger_step_ids": ["s_E"], "interval": 10, "time_interval_seconds": 60,
        }
        vsm = _make_vsm_with_rule(rule, project_id=project_id)
        # counter 应被正确恢复, last_done_ts 用初始值 (≈now)
        assert vsm._periodic_counters["pa_legacy"] == 7
        assert abs(vsm._periodic_last_done_ts["pa_legacy"] - time.time()) < 1


# ============================================================
# 6. reset 系列同步 timestamp
# ============================================================
class TestResetSyncsTimestamp:
    def test_reset_periodic_counter_resets_ts(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.source_periodic_actions_mixin.DATA_DIR", str(tmp_path))
        rule = {
            "id": "pa_r1", "name": "重置",
            "trigger_step_ids": ["s_E"], "interval": 5, "time_interval_seconds": 30,
        }
        vsm = _make_vsm_with_rule(rule)
        vsm._periodic_counters["pa_r1"] = 4
        vsm._periodic_last_done_ts["pa_r1"] = time.time() - 25  # 接近超期

        vsm.reset_periodic_counter("pa_r1")
        assert vsm._periodic_counters["pa_r1"] == 0
        assert abs(vsm._periodic_last_done_ts["pa_r1"] - time.time()) < 1
