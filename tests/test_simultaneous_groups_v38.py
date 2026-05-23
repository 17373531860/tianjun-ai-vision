"""v3.8.x 同时出现组重构单元测试

覆盖范围:
  类一 (非跨周期):
    - 缓冲提前释放: 等待全员到齐期间出现"非组内有意义步骤" → 立即释放已收集成员
    - 结算前重排: cycle_steps 内成员按优先顺序兜底回写
  类二 (跨周期):
    - 上周期成员先到 → 进 cycle_steps + 等待状态
    - 下周期成员先到 (周期非空) → 不入 cycle_steps + 等待状态
    - 等待中另一侧到达 → 结算上周期 + 启动下周期 + 屏蔽集合
    - 等待中非组内步骤到达 → 立即结算上周期 + 屏蔽集合, 让非组内步骤走正常路径
    - 等待超时 → 自动结算 + 屏蔽集合
    - 屏蔽集合解除: 出现组外有意义步骤时一次性清空

测试策略:
  直接对 VideoSourceManager 的两个新函数 _process_cross_cycle_groups /
  _handle_blocked_labels_release / _reorder_simultaneous_groups_in_cycle 做白盒测试,
  绕过 _detect_only / 帧确认门, 用更精确的状态注入验证状态机迁移.
"""
from __future__ import annotations

import time
import pytest

from backend.api.source import VideoSourceManager


# ============================================================
# 辅助: 构造一个最小可用的 VSM 实例 (无推理链)
# ============================================================
def _make_vsm(simultaneous_groups, logic_mode="sequential", expected_seq=None):
    """构造 VSM, 注入同时出现组配置, 不启动实际推理.

    expected_seq: 期望的 sequence_order step_ids 列表 (默认 [1,2,3,4,5] 对应 A-B-C-D-E)
    """
    vsm = VideoSourceManager(channel_id=0)
    if expected_seq is None:
        expected_seq = [1, 2, 3, 4, 5]
    vsm.set_project_config({
        "id": 99388,
        "name": "v3.8 同时出现组单测",
        "task_type": "detection",
        "logic_mode": logic_mode,
        "steps_config": [
            {"id": 1, "label": "A", "enabled": True, "min_frames": 1},
            {"id": 2, "label": "B", "enabled": True, "min_frames": 1},
            {"id": 3, "label": "C", "enabled": True, "min_frames": 1},
            {"id": 4, "label": "D", "enabled": True, "min_frames": 1},
            {"id": 5, "label": "E", "enabled": True, "min_frames": 1},
        ],
        "events_config": [
            {"id": 1, "name": "OK", "actions": [], "show_notification": False},
            {"id": 2, "name": "NG", "actions": [], "show_notification": False},
        ],
        "counters_config": [],
        "pipeline_config": {
            "sequence_order": [{"step_id": sid} for sid in expected_seq],
            "simultaneous_groups": simultaneous_groups,
            "settlement_mode": "first_step",
        },
    })
    return vsm


# ============================================================
# 类一 (非跨周期): 缓冲提前释放
# ============================================================
class TestType1BufferEarlyRelease:
    def test_buffer_release_on_other_meaningful_step(self):
        """缓冲中 B 已挂起, 出现 D (非组内) → B 提前释放进 ready_ordered."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": False,
            "labels": ["B", "C"],
            "priority_order": ["B", "C"],
            "time_window": 5.0,
        }])

        # 前置: 周期内已有 A
        vsm.current_cycle_steps = ["A"]
        # B 还没在 step_last_seen 里 (潜在新出现)

        # 第 1 帧: B 出现 → 进入缓冲挂起
        t0 = time.time()
        pending, ready = vsm._process_simultaneous_groups(
            frame_detected_labels={"B"}, detected_labels={"B"}, current_time=t0
        )
        assert "B" in pending, "B 应该被挂起到 pending"
        assert ready == [], "全员未到齐, ready_ordered 应为空"
        assert vsm._sim_group_buffers[0]["collecting"] is True

        # 第 2 帧: D 出现 (D 已通过帧确认), C 还没来 → 缓冲被 D 打断, B 提前释放
        pending2, ready2 = vsm._process_simultaneous_groups(
            frame_detected_labels={"B", "D"}, detected_labels={"B", "D"}, current_time=t0 + 0.5
        )
        assert ready2 == ["B"], f"B 应被立即释放到 ready_ordered, 实际 {ready2}"
        assert vsm._sim_group_buffers[0]["collecting"] is False, "缓冲应已关闭"

    def test_buffer_full_members_in_window(self):
        """缓冲中 C 后续到达 → 全员到齐, 按优先顺序输出 [B, C]."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": False,
            "labels": ["B", "C"],
            "priority_order": ["B", "C"],
            "time_window": 5.0,
        }])
        vsm.current_cycle_steps = ["A"]

        t0 = time.time()
        vsm._process_simultaneous_groups(
            frame_detected_labels={"B"}, detected_labels={"B"}, current_time=t0
        )
        _, ready = vsm._process_simultaneous_groups(
            frame_detected_labels={"B", "C"}, detected_labels={"B", "C"}, current_time=t0 + 0.5
        )
        assert ready == ["B", "C"], f"全员到齐应按优先顺序输出 [B, C], 实际 {ready}"

    def test_buffer_timeout(self):
        """缓冲时间窗超过 → 已收集的按优先顺序输出, 缺失的归结算缺步骤."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": False,
            "labels": ["B", "C"],
            "priority_order": ["B", "C"],
            "time_window": 1.0,
        }])
        vsm.current_cycle_steps = ["A"]

        t0 = time.time()
        vsm._process_simultaneous_groups(
            frame_detected_labels={"B"}, detected_labels={"B"}, current_time=t0
        )
        _, ready = vsm._process_simultaneous_groups(
            frame_detected_labels={"B"}, detected_labels={"B"}, current_time=t0 + 1.5
        )
        assert ready == ["B"], f"超时应输出已收集 [B], 实际 {ready}"


# ============================================================
# 类一 (非跨周期): 结算前重排
# ============================================================
class TestType1ReorderInCycle:
    def test_reorder_inverted_pair(self):
        """cycle_steps = [A, C, B, D] → 重排为 [A, B, C, D]."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": False,
            "labels": ["B", "C"],
            "priority_order": ["B", "C"],
            "time_window": 5.0,
        }])
        vsm.current_cycle_steps = ["A", "C", "B", "D"]
        vsm._reorder_simultaneous_groups_in_cycle()
        assert vsm.current_cycle_steps == ["A", "B", "C", "D"], \
            f"重排后应为 [A,B,C,D], 实际 {vsm.current_cycle_steps}"

    def test_reorder_skips_single_member(self):
        """cycle_steps 里组内只有一个成员 → 不重排."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": False,
            "labels": ["B", "C"],
            "priority_order": ["B", "C"],
            "time_window": 5.0,
        }])
        vsm.current_cycle_steps = ["A", "B", "D"]
        vsm._reorder_simultaneous_groups_in_cycle()
        assert vsm.current_cycle_steps == ["A", "B", "D"]

    def test_reorder_already_correct(self):
        """cycle_steps 已经是优先顺序 → 不变."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": False,
            "labels": ["B", "C"],
            "priority_order": ["B", "C"],
            "time_window": 5.0,
        }])
        vsm.current_cycle_steps = ["A", "B", "C", "D"]
        vsm._reorder_simultaneous_groups_in_cycle()
        assert vsm.current_cycle_steps == ["A", "B", "C", "D"]

    def test_reorder_skips_cross_cycle(self):
        """跨周期组不参与类一重排 (由类二处理)."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": True,
            "labels": ["E", "A"],
            "priority_order": ["E", "A"],
            "prev_cycle_labels": ["E"],
            "next_cycle_labels": ["A"],
            "time_window": 3.0,
        }])
        vsm.current_cycle_steps = ["A", "E"]  # 假设运行时出现这种异常顺序
        vsm._reorder_simultaneous_groups_in_cycle()
        # 跨周期组应跳过, 顺序保持
        assert vsm.current_cycle_steps == ["A", "E"]


# ============================================================
# 类二 (跨周期): 状态机
# ============================================================
class TestType2CrossCycleStateMachine:
    def _make_cross_vsm(self, time_window=3.0):
        return _make_vsm([{
            "enabled": True,
            "cross_cycle": True,
            "labels": ["E", "A"],
            "priority_order": ["E", "A"],
            "prev_cycle_labels": ["E"],
            "next_cycle_labels": ["A"],
            "time_window": time_window,
        }], expected_seq=[1, 2, 3, 4, 5])

    def test_prev_member_first_enters_cycle_steps(self):
        """E (上周期成员) 先到 → 加入 current_cycle_steps + 进入等待."""
        vsm = self._make_cross_vsm()
        # 模拟上周期已经做了 A-B-C-D
        vsm.current_cycle_steps = ["A", "B", "C", "D"]
        t0 = time.time()

        consumed = vsm._process_cross_cycle_groups(
            frame_detected_labels={"E"}, detected_labels={"E"}, current_time=t0
        )
        assert "E" in vsm.current_cycle_steps, "E 应被加入 cycle_steps (情况乙)"
        assert "E" in consumed, "E 应被消费"
        assert 0 in vsm._cross_cycle_waiting, "等待状态应建立"
        assert vsm._cross_cycle_waiting[0]["phase"] == "waiting"
        assert vsm._cross_cycle_waiting[0]["first_role"] == "prev"

    def test_next_member_first_does_not_enter_cycle(self):
        """A (下周期首步) 先到 + 上周期已完成 (含 prev 成员 E) → 不进 cycle_steps, 进入等待.

        真实客户场景: A→B→C→D→E 已完整, E 残影还在; A 再次出现属于下周期首步,
        应进入跨周期等待 (等 E 这一侧"残影时间"窗口结束才结算上周期).
        """
        vsm = self._make_cross_vsm()
        # cycle_steps 必须含 prev 成员 (E), 才是真正的跨周期场景
        vsm.current_cycle_steps = ["A", "B", "C", "D", "E"]
        original_steps = list(vsm.current_cycle_steps)
        t0 = time.time()

        consumed = vsm._process_cross_cycle_groups(
            frame_detected_labels={"A"}, detected_labels={"A"}, current_time=t0
        )
        assert vsm.current_cycle_steps == original_steps, "A 不应改变 cycle_steps"
        assert "A" in consumed
        assert vsm._cross_cycle_waiting[0]["first_role"] == "next"

    def test_next_member_first_no_prev_member_in_cycle_falls_through(self):
        """A 先到 + cycle_steps 里没有任何 prev 成员 (例如 [A] 刚开始) → 不进等待, 让主循环处理.

        防御场景: A 是项目首步, 第一次出现时 cycle_steps 还没有 E,
        不应被跨周期路由误判成"下周期首步先到".
        """
        vsm = self._make_cross_vsm()
        vsm.current_cycle_steps = ["A"]  # 只有 A, 没有 E
        t0 = time.time()

        consumed = vsm._process_cross_cycle_groups(
            frame_detected_labels={"A"}, detected_labels={"A"}, current_time=t0
        )
        # 没进入等待状态, A 也不被 consume (让主循环正常处理)
        assert 0 not in vsm._cross_cycle_waiting

    # ─── next 先到方向的 3 种终止路径 (对称补全) ───

    def test_next_first_then_prev_arrives_settles(self):
        """A 先到等待 → E 到达 → 路径①: 结算上周期 + 启动下周期 + 屏蔽组员."""
        vsm = self._make_cross_vsm()
        vsm.current_cycle_steps = ["A", "B", "C", "D", "E"]  # 上周期已完整含 E
        t0 = time.time()

        # A 先到, 进入 next 等待
        vsm._process_cross_cycle_groups(
            frame_detected_labels={"A"}, detected_labels={"A"}, current_time=t0
        )
        assert vsm._cross_cycle_waiting[0]["first_role"] == "next"

        # 0.5s 后 E 残影到达 (实际场景: 上周期 E 还没消失)
        vsm._process_cross_cycle_groups(
            frame_detected_labels={"E"}, detected_labels={"E"}, current_time=t0 + 0.5
        )
        # 新周期应启动 [A]
        assert vsm.current_cycle_steps == ["A"], \
            f"新周期应只含 A, 实际 {vsm.current_cycle_steps}"
        assert {"E", "A"} <= vsm._blocked_labels
        assert 0 not in vsm._cross_cycle_waiting

    def test_next_first_then_timeout_auto_settles(self):
        """A 先到等待 → 等待超时 → 路径③: 自动结算 + 屏蔽."""
        vsm = self._make_cross_vsm(time_window=1.0)
        vsm.current_cycle_steps = ["A", "B", "C", "D", "E"]
        t0 = time.time()

        vsm._process_cross_cycle_groups(
            frame_detected_labels={"A"}, detected_labels={"A"}, current_time=t0
        )
        assert 0 in vsm._cross_cycle_waiting

        # 超时后没有任何标签到来
        vsm._process_cross_cycle_groups(
            frame_detected_labels=set(), detected_labels=set(), current_time=t0 + 1.5
        )
        assert {"E", "A"} <= vsm._blocked_labels
        assert 0 not in vsm._cross_cycle_waiting

    def test_next_first_then_other_meaningful_step(self):
        """A 先到等待 → B (组外) 到达 → 路径②: 立即结算 + 屏蔽, B 不被消费."""
        vsm = self._make_cross_vsm()
        vsm.current_cycle_steps = ["A", "B", "C", "D", "E"]
        t0 = time.time()

        vsm._process_cross_cycle_groups(
            frame_detected_labels={"A"}, detected_labels={"A"}, current_time=t0
        )

        consumed = vsm._process_cross_cycle_groups(
            frame_detected_labels={"B"}, detected_labels={"B"}, current_time=t0 + 0.3
        )
        assert "B" not in consumed, "B 应让主循环处理"
        assert {"E", "A"} <= vsm._blocked_labels
        assert 0 not in vsm._cross_cycle_waiting

    # ─── prev 先到方向 4 种终止路径 ───

    def test_other_side_arrives_triggers_settlement(self):
        """E 在等待, A 到来 → 结算上周期 + 启动新周期 + 屏蔽集合."""
        vsm = self._make_cross_vsm()
        vsm.current_cycle_steps = ["A", "B", "C", "D"]
        t0 = time.time()

        vsm._process_cross_cycle_groups(
            frame_detected_labels={"E"}, detected_labels={"E"}, current_time=t0
        )
        assert "E" in vsm.current_cycle_steps

        vsm._process_cross_cycle_groups(
            frame_detected_labels={"A"}, detected_labels={"A"}, current_time=t0 + 0.5
        )
        assert vsm.current_cycle_steps == ["A"], \
            f"新周期应只含 A, 实际 {vsm.current_cycle_steps}"
        assert {"E", "A"} <= vsm._blocked_labels, \
            f"组员应被加入屏蔽集合, 实际 {vsm._blocked_labels}"
        assert 0 not in vsm._cross_cycle_waiting, "等待状态应清空"

    def test_waiting_timeout_auto_settles(self):
        """等待超时 → 自动结算 + 屏蔽集合."""
        vsm = self._make_cross_vsm(time_window=1.0)
        vsm.current_cycle_steps = ["A", "B", "C", "D"]
        t0 = time.time()

        vsm._process_cross_cycle_groups(
            frame_detected_labels={"E"}, detected_labels={"E"}, current_time=t0
        )
        assert 0 in vsm._cross_cycle_waiting

        vsm._process_cross_cycle_groups(
            frame_detected_labels=set(), detected_labels=set(), current_time=t0 + 1.5
        )
        assert {"E", "A"} <= vsm._blocked_labels
        assert 0 not in vsm._cross_cycle_waiting

    def test_other_meaningful_step_interrupts_waiting(self):
        """E 在等待, B 到来 (非组内有意义) → 立即结算 + 屏蔽, B 不被消费."""
        vsm = self._make_cross_vsm()
        vsm.current_cycle_steps = ["A", "B", "C", "D"]
        t0 = time.time()

        vsm._process_cross_cycle_groups(
            frame_detected_labels={"E"}, detected_labels={"E"}, current_time=t0
        )

        consumed = vsm._process_cross_cycle_groups(
            frame_detected_labels={"B"}, detected_labels={"B"}, current_time=t0 + 0.5
        )
        assert "B" not in consumed
        assert {"E", "A"} <= vsm._blocked_labels
        assert 0 not in vsm._cross_cycle_waiting

    def test_residue_E_blocked_during_waiting(self):
        """等待中 E 残影持续被识别 → 被 consume, 不写入 cycle_steps 额外副本."""
        vsm = self._make_cross_vsm()
        vsm.current_cycle_steps = ["A", "B", "C", "D"]
        t0 = time.time()

        vsm._process_cross_cycle_groups(
            frame_detected_labels={"E"}, detected_labels={"E"}, current_time=t0
        )
        initial_count = vsm.current_cycle_steps.count("E")
        assert initial_count == 1

        consumed = vsm._process_cross_cycle_groups(
            frame_detected_labels={"E"}, detected_labels={"E"}, current_time=t0 + 0.2
        )
        assert "E" in consumed
        assert vsm.current_cycle_steps.count("E") == initial_count


# ============================================================
# 类二: 多成员组 (3+ 成员)
# ============================================================
class TestType2MultiMemberGroup:
    """澄清点 9: 客户明确要求支持成员数 > 2 的跨周期组."""

    def _make_3member_vsm(self, time_window=3.0):
        """3 成员组: prev=[D, E], next=[A]. 模拟 '上周期收尾 2 步 + 下周期 1 步'."""
        return _make_vsm([{
            "enabled": True,
            "cross_cycle": True,
            "labels": ["D", "E", "A"],
            "priority_order": ["D", "E", "A"],
            "prev_cycle_labels": ["D", "E"],
            "next_cycle_labels": ["A"],
            "time_window": time_window,
        }], expected_seq=[1, 2, 3, 4, 5])

    def test_3member_prev_e_first_waits_for_a(self):
        """3 成员组: E (prev) 先到 → 等 A (next), A 到达后结算."""
        vsm = self._make_3member_vsm()
        # 上周期已含前驱 D, E 出现是合法收尾
        vsm.current_cycle_steps = ["A", "B", "C", "D"]
        t0 = time.time()

        vsm._process_cross_cycle_groups(
            frame_detected_labels={"E"}, detected_labels={"E"}, current_time=t0
        )
        assert "E" in vsm.current_cycle_steps
        assert vsm._cross_cycle_waiting[0]["first_role"] == "prev"

        # A 到达 → 全员到齐
        vsm._process_cross_cycle_groups(
            frame_detected_labels={"A"}, detected_labels={"A"}, current_time=t0 + 0.5
        )
        assert vsm.current_cycle_steps == ["A"]
        # 屏蔽集合含全 3 个成员
        assert {"D", "E", "A"} <= vsm._blocked_labels

    def test_3member_prev_d_falls_through_when_predecessors_missing(self):
        """3 成员组: D (prev) 出现但前驱 A/B/C 缺 → 让主循环按回退处理."""
        vsm = self._make_3member_vsm()
        vsm.current_cycle_steps = ["A"]  # 缺 B, C
        t0 = time.time()

        vsm._process_cross_cycle_groups(
            frame_detected_labels={"D"}, detected_labels={"D"}, current_time=t0
        )
        # D 不应被视为跨周期 prev 到达
        assert 0 not in vsm._cross_cycle_waiting
        assert "D" not in vsm.current_cycle_steps


# ============================================================
# 场景癸: 切项目 / 启停状态清空
# ============================================================
class TestProjectSwitchClearsState:
    def test_apply_project_config_clears_cross_cycle_state(self):
        """切项目 (重新 set_project_config) 时应清空 _blocked_labels 与 _cross_cycle_waiting."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": True,
            "labels": ["E", "A"],
            "priority_order": ["E", "A"],
            "prev_cycle_labels": ["E"],
            "next_cycle_labels": ["A"],
            "time_window": 3.0,
        }])
        # 注入残留状态
        vsm._blocked_labels = {"E", "A"}
        vsm._cross_cycle_waiting[0] = {"phase": "waiting", "first_member": "E"}
        vsm.current_cycle_steps = ["A", "B"]

        # 切到一个新项目 (不含跨周期组)
        vsm.set_project_config({
            "id": 99999,
            "name": "新项目",
            "task_type": "detection",
            "logic_mode": "sequential",
            "steps_config": [{"id": 1, "label": "X", "enabled": True, "min_frames": 1}],
            "events_config": [],
            "counters_config": [],
            "pipeline_config": {
                "sequence_order": [{"step_id": 1}],
            },
        })

        # 所有 v3.8.x 跨周期状态应被清空
        assert vsm._blocked_labels == set()
        assert vsm._cross_cycle_waiting == {}
        assert vsm.current_cycle_steps == []


# ============================================================
# 场景戊: detection 模式下跨周期路由也能 settle
# ============================================================
class TestDetectionModeCrossCycle:
    def test_settle_for_cross_cycle_routes_to_detection_settle(self):
        """logic_mode=detection 时, _settle_for_cross_cycle 应路由到 _settle_detection_cycle."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": True,
            "labels": ["E", "A"],
            "priority_order": ["E", "A"],
            "prev_cycle_labels": ["E"],
            "next_cycle_labels": ["A"],
            "time_window": 3.0,
        }], logic_mode="detection")

        # 注入一个 cycle_steps 让 _settle 不被空判跳过
        vsm.current_cycle_steps = ["A", "B", "C", "D", "E"]

        # 触发跨周期路由: E 先到, A 后到 → 应该走 detection 结算
        t0 = time.time()
        vsm._process_cross_cycle_groups(
            frame_detected_labels={"E"}, detected_labels={"E"}, current_time=t0
        )
        vsm._process_cross_cycle_groups(
            frame_detected_labels={"A"}, detected_labels={"A"}, current_time=t0 + 0.5
        )
        # 结算后新周期已开始
        assert vsm.current_cycle_steps == ["A"]
        assert {"E", "A"} <= vsm._blocked_labels


# ============================================================
# 类二: 被屏蔽集合的解除
# ============================================================
class TestBlockedLabelsRelease:
    def test_release_on_out_of_group_step(self):
        """已屏蔽 {E, A}, 本帧出现 B (组外) → 屏蔽清空."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": True,
            "labels": ["E", "A"],
            "priority_order": ["E", "A"],
            "prev_cycle_labels": ["E"],
            "next_cycle_labels": ["A"],
            "time_window": 3.0,
        }])
        vsm._blocked_labels = {"E", "A"}
        vsm._handle_blocked_labels_release(detected_labels={"B"})
        assert vsm._blocked_labels == set(), "出现组外步骤应清空屏蔽"

    def test_no_release_if_only_group_members(self):
        """已屏蔽 {E, A}, 本帧只有 E → 不解除."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": True,
            "labels": ["E", "A"],
            "priority_order": ["E", "A"],
            "prev_cycle_labels": ["E"],
            "next_cycle_labels": ["A"],
            "time_window": 3.0,
        }])
        vsm._blocked_labels = {"E", "A"}
        vsm._handle_blocked_labels_release(detected_labels={"E"})
        assert vsm._blocked_labels == {"E", "A"}

    def test_no_release_if_empty_frame(self):
        """空帧 → 不解除."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": True,
            "labels": ["E", "A"],
            "priority_order": ["E", "A"],
            "prev_cycle_labels": ["E"],
            "next_cycle_labels": ["A"],
            "time_window": 3.0,
        }])
        vsm._blocked_labels = {"E", "A"}
        vsm._handle_blocked_labels_release(detected_labels=set())
        assert vsm._blocked_labels == {"E", "A"}


# ============================================================
# 类二: 清空机制覆盖
# ============================================================
class TestType2StateClearing:
    def test_clear_state_resets_new_fields(self):
        """_clear_step_runtime_state 应清空 _blocked_labels 和 _cross_cycle_waiting."""
        vsm = _make_vsm([{
            "enabled": True,
            "cross_cycle": True,
            "labels": ["E", "A"],
            "priority_order": ["E", "A"],
            "prev_cycle_labels": ["E"],
            "next_cycle_labels": ["A"],
            "time_window": 3.0,
        }])
        # 注入一些状态
        vsm._blocked_labels = {"E", "A"}
        vsm._cross_cycle_waiting[0] = {"phase": "waiting", "first_member": "E"}

        vsm._clear_step_runtime_state()

        assert vsm._blocked_labels == set(), "屏蔽集合应被清空"
        assert vsm._cross_cycle_waiting == {}, "等待状态应被清空"
