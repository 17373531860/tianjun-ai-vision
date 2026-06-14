"""端到端 pytest: 自定义模式混合子状态机 × synthetic 剧本源 (四组合全矩阵).

不需要硬件、不需要模型、不需要前端。剧本走完整 pipeline:
    synthetic capture loop → _update_step_stats (物品标签分流 → CustomMixMachine.feed)
    → 步骤侧周期/结算 → compose_settle_event 合成裁决
    → _trigger_event(1/2) → counters 累加

组合矩阵 (用户要求每种组合都有面向功能验证):
    基于顺序 × 混合逐件    : OK (动作盖满 3 个个体) + NG (只盖到 2 个, 步骤侧正确仍 NG)
    基于顺序 × 混合跟踪    : OK (3 唯一个体) + NG (2 唯一个体)
    基于检测 × 混合逐件    : OK + NG
    基于检测 × 混合跟踪    : OK + NG
"""
from __future__ import annotations

import pytest

from tests.custom_mix_helpers import (
    PROJECT_NAME,
    build_custom_mix_project,
    delete_project_by_name,
    run_mix_cycle,
    stop_all,
)


@pytest.fixture
def fresh_channel(client):
    stop_all(client, 0)
    yield 0
    stop_all(client, 0)


def _make_project(client, based_on: str, mixed_with: str) -> int:
    delete_project_by_name(client, PROJECT_NAME)
    r = client.post("/api/v1/projects", json=build_custom_mix_project(based_on, mixed_with))
    assert r.status_code in (200, 201), f"创建混合模式项目失败: {r.text[:300]}"
    return r.json()["id"]


@pytest.fixture
def mix_project(client, request):
    based_on, mixed_with = request.param
    pid = _make_project(client, based_on, mixed_with)
    yield pid, based_on, mixed_with
    client.delete(f"/api/v1/projects/{pid}")


_MATRIX = [
    ("sequential", "per_item"),
    ("sequential", "tracking"),
    ("detection", "per_item"),
    ("detection", "tracking"),
]
_IDS = [f"{b}+{m}" for b, m in _MATRIX]


@pytest.mark.parametrize("mix_project", _MATRIX, indirect=True, ids=_IDS)
def test_custom_mix_ok(client, mix_project, fresh_channel):
    """OK 路径: 步骤侧正确 + 物品侧达标 → 合格 +1."""
    pid, based_on, mixed_with = mix_project
    run_mix_cycle(
        client, fresh_channel, pid,
        mixed_with=mixed_with, item_ok=True, expect_event="ok",
        scenario_name=f"mix_ok_{based_on}_{mixed_with}",
    )


@pytest.mark.parametrize("mix_project", _MATRIX, indirect=True, ids=_IDS)
def test_custom_mix_item_ng_downgrades(client, mix_project, fresh_channel):
    """NG 路径 (核心断言): 步骤序列完全正确, 但物品侧不达标
    (per_item 覆盖 2/3 / tracking 唯一个体 2/3) → 周期被降级为不良 +1.

    这是混合模式的存在意义: 没有混合时同样的剧本步骤侧会判 OK。
    """
    pid, based_on, mixed_with = mix_project
    run_mix_cycle(
        client, fresh_channel, pid,
        mixed_with=mixed_with, item_ok=False, expect_event="ng",
        scenario_name=f"mix_ng_{based_on}_{mixed_with}",
    )


def test_custom_mix_state_exposed(client, fresh_channel):
    """detection/results 应透出 custom_mix_state (物品计数快照, 给前端 Monitor)."""
    pid = _make_project(client, "sequential", "per_item")
    try:
        data = run_mix_cycle(
            client, fresh_channel, pid,
            mixed_with="per_item", item_ok=True, expect_event="ok",
            scenario_name="mix_state_probe",
        )
        state = data.get("custom_mix_state")
        assert isinstance(state, dict), f"custom_mix_state 应为 dict, 实际={state}"
        assert state.get("mix_type") == "per_item"
        items = state.get("items") or []
        assert len(items) == 1 and items[0].get("label") == "打滑块"
        assert items[0].get("role") == "pair"
        assert items[0].get("expected_count") == 3
        # 真逐件引擎: steps 透出与独立模式 per_item_state.steps 同形快照
        steps = state.get("steps") or []
        assert len(steps) == 1 and steps[0].get("action_label") == "打螺丝"
    finally:
        client.delete(f"/api/v1/projects/{pid}")


def test_no_mix_same_scenario_is_ok(client, fresh_channel):
    """对照组 (零差异验证): 同样的 NG 物品剧本, 不开混合 → 步骤侧判 OK.

    证明 NG 降级真的来自混合子状态机, 而不是物品标签干扰了步骤序列
    (物品标签被剥离 / 物品行不进序列)。
    """
    proj = build_custom_mix_project("sequential", "per_item")
    proj["pipeline_config"]["custom_mixed_with"] = None  # 关掉混合, 其余配置不动
    delete_project_by_name(client, PROJECT_NAME)
    r = client.post("/api/v1/projects", json=proj)
    assert r.status_code in (200, 201), r.text[:300]
    pid = r.json()["id"]
    try:
        # item_ok=False 的剧本: 第 3 个个体未被覆盖 — 不混合时应不影响判定 → OK
        run_mix_cycle(
            client, fresh_channel, pid,
            mixed_with="per_item", item_ok=False, expect_event="ok",
            scenario_name="mix_off_control_group",
        )
    finally:
        client.delete(f"/api/v1/projects/{pid}")
