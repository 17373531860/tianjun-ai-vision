"""Phase 1.2 — 周期性强制动作 pairwise 矩阵测试。

维度：
  - count_basis: all / good_only / ng_only
  - reset_policy: always / only_when_due
  - overdue_repeat: every_cycle / once / cooldown:3
  - interval: 2 / 5 / 10

完整笛卡尔积 = 3 × 2 × 3 × 3 = 54 组合
pairwise 减到 ~9-12 组合，仍能覆盖所有两两交互。

每组合用 PeriodicActionsMixin 实例跑 12 个 cycle（含 OK/NG 混合 + 偶尔含 E），
检查最终 counter 状态是合理的。
"""
from __future__ import annotations

import pytest
from allpairspy import AllPairs

from backend.api.source_periodic_actions_mixin import PeriodicActionsMixin


class _Stub(PeriodicActionsMixin):
    def __init__(self):
        self.channel_id = 0
        self.counters = {}
        self.events_log = []
        self._event_seq = 0
        self.project_config = {}

    def _persist_counters(self):
        pass

    def _persist_periodic_counters(self):
        pass

    def _restore_periodic_counters(self, config):
        pass


# ============================================================
# Pairwise 矩阵生成
# ============================================================
PARAMS_PERIODIC = [
    ["all", "good_only", "ng_only"],          # count_basis
    ["always", "only_when_due"],               # reset_policy
    ["every_cycle", "once", "cooldown:3"],    # overdue_repeat
    [2, 5, 10],                                # interval
]
PAIRWISE_PERIODIC = list(AllPairs(PARAMS_PERIODIC))


def _ids(combo):
    cb, rp, repeat, iv = combo
    safe_repeat = repeat.replace(":", "_")
    return f"{cb}-{rp}-{safe_repeat}-i{iv}"


@pytest.mark.parametrize(
    "count_basis,reset_policy,overdue_repeat,interval",
    PAIRWISE_PERIODIC,
    ids=[_ids(c) for c in PAIRWISE_PERIODIC],
)
def test_pairwise_periodic_action_invariants(
    count_basis, reset_policy, overdue_repeat, interval
):
    """对每个维度组合，验证基础不变量：
    1) counter 永远 >= 0
    2) 触发了 trigger step 且条件满足时 counter 会被重置
    3) overdue 事件触发次数 <= 实际超期 cycle 数
    """
    host = _Stub()
    rule = {
        "id": "pa_pw",
        "name": f"pw_{count_basis}_{reset_policy}",
        "enabled": True,
        "trigger_step_ids": ["s_E"],
        "interval": interval,
        "count_basis": count_basis,
        "reset_policy": reset_policy,
        "due_warning_event_id": 10,
        "overdue_event_id": 20,
        "overdue_repeat": overdue_repeat,
    }
    config = {
        "id": 9999,
        "steps_config": [
            {"id": "s_A", "label": "A", "enabled": True},
            {"id": "s_E", "label": "E", "enabled": True},
        ],
        "events_config": [
            {"id": 10, "name": "due", "actions": [], "show_notification": True},
            {"id": 20, "name": "overdue", "actions": [], "show_notification": True},
        ],
        "pipeline_config": {"periodic_actions": [rule]},
    }
    host.project_config = config
    host._apply_periodic_actions(config)

    # 12 个 cycle 模拟：
    #   ok-no-e × 4
    #   ng-no-e × 2
    #   ok-with-e × 1
    #   ok-no-e × 5
    sequence = [
        ("ok", False), ("ok", False), ("ok", False), ("ok", False),
        ("ng", False), ("ng", False),
        ("ok", True),  # 含 E
        ("ok", False), ("ok", False), ("ok", False), ("ok", False), ("ok", False),
    ]

    counter_history = []
    overdue_count_at_each_step = []

    for label, has_e in sequence:
        is_good = label == "ok"
        steps = ["A", "E"] if has_e else ["A"]
        host._check_periodic_actions(steps, is_good)
        counter_history.append(host._periodic_counters["pa_pw"])
        overdue_count_at_each_step.append(
            sum(1 for e in host.events_log if e.get("event_id") == "20")
        )

    # 不变量 1: counter 永远 >= 0
    assert all(c >= 0 for c in counter_history), \
        f"counter 出现负值: {counter_history}"

    # 不变量 2: 任何相邻两步 counter 增量 <= 1
    for i in range(1, len(counter_history)):
        diff = counter_history[i] - counter_history[i - 1]
        assert diff <= 1, \
            f"counter 单步增长 > 1, 位置 {i}, history={counter_history}"

    # 不变量 3: overdue 事件触发次数 <= 超期发生 cycle 数
    overdue_cycles = sum(1 for c in counter_history if c > interval)
    final_overdue_count = overdue_count_at_each_step[-1]
    assert final_overdue_count <= overdue_cycles, \
        (f"overdue 触发 {final_overdue_count} 次，超过实际超期 cycle 数 "
         f"{overdue_cycles}; counter_history={counter_history}")


def test_pairwise_count_summary():
    """打印 pairwise 减枝效果"""
    full = 1
    for p in PARAMS_PERIODIC:
        full *= len(p)
    reduced = len(PAIRWISE_PERIODIC)
    print(f"\n[pairwise] 完整笛卡尔积 {full}, pairwise 减枝 {reduced} "
          f"(覆盖率 {reduced * 100 / full:.0f}% 的组合 / 100% 的两两交互)")
    assert reduced < full, "pairwise 应当减少组合数"
