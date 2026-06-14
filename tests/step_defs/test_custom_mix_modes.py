"""自定义混合模式 (custom_mixed_with) BDD step 实现.

四组合 (sequential/detection × per_item/tracking) × OK/NG 全矩阵 + 零差异对照.
与 tests/test_synthetic_custom_mix.py (E2E) 共用 tests/custom_mix_helpers.py
的项目模板和剧本生成器, 保证两层验证同一套客户场景.
"""
from __future__ import annotations

import os

from pytest_bdd import scenarios, given, when, then, parsers

from tests.custom_mix_helpers import (
    PROJECT_NAME,
    build_custom_mix_project,
    build_mix_scenario,
    delete_project_by_name,
    read_counters,
    set_project_to_mgr,
    start_synth_with_scenario,
    stop_all,
    wait_until,
)

scenarios("../features/custom_mix_modes.feature")


# ============================================================
# 背景
# ============================================================
@given("后端处于测试模式 (RUNTIME_MODE=test)")
def given_test_mode():
    assert os.environ.get("RUNTIME_MODE") == "test", "conftest 应该设置 RUNTIME_MODE=test"


@given("通道 0 的混合模式测试环境是干净的")
def given_clean_channel(client):
    stop_all(client, 0)


# ============================================================
# Given: 项目
# ============================================================
def _create_and_load(client, request, ctx, based_on: str, mixed_with):
    delete_project_by_name(client, PROJECT_NAME)
    proj = build_custom_mix_project(based_on, mixed_with or "per_item")
    if mixed_with is None:
        proj["pipeline_config"]["custom_mixed_with"] = None  # 零差异对照: 关掉混合
    r = client.post("/api/v1/projects", json=proj)
    assert r.status_code in (200, 201), f"创建项目失败: {r.text[:300]}"
    pid = r.json()["id"]
    request.addfinalizer(lambda: client.delete(f"/api/v1/projects/{pid}"))
    request.addfinalizer(lambda: stop_all(client, 0))
    set_project_to_mgr(client, pid, 0)
    ctx["mixed_with"] = mixed_with or "per_item"
    ctx["based_on"] = based_on


@given(parsers.parse('一个基于 "{based_on}" 并混合 "{mixed_with}" 的自定义项目已载入通道 0'))
def given_mix_project(client, request, ctx, based_on, mixed_with):
    _create_and_load(client, request, ctx, based_on, mixed_with)


@given(parsers.parse('一个基于 "{based_on}" 但未混合的自定义项目已载入通道 0'))
def given_no_mix_project(client, request, ctx, based_on):
    _create_and_load(client, request, ctx, based_on, None)


# ============================================================
# When: 跑剧本
# ============================================================
def _run_scenario(client, ctx, *, item_ok: bool):
    ctx["baseline"] = read_counters(client, 0)
    spec = build_mix_scenario(
        f"bdd_mix_{ctx['based_on']}_{ctx['mixed_with']}_{'ok' if item_ok else 'ng'}",
        mixed_with=ctx["mixed_with"], item_ok=item_ok)
    r = start_synth_with_scenario(client, 0, spec)
    assert r.status_code == 200, f"start synthetic 失败: {r.text[:300]}"
    r = client.post("/api/v1/source/detection/start?channel=0",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, f"detection/start 失败: {r.text[:300]}"


@when("我跑一个物品达标的混合周期剧本")
def when_run_item_ok(client, ctx):
    _run_scenario(client, ctx, item_ok=True)


@when("我跑一个物品不达标的混合周期剧本")
def when_run_item_ng(client, ctx):
    _run_scenario(client, ctx, item_ok=False)


# ============================================================
# Then: 计数器断言
# ============================================================
def _assert_counter_delta(client, ctx, inc_name: str, flat_name: str):
    baseline = ctx["baseline"]

    def _observed():
        counters = read_counters(client, 0)
        return counters if counters.get(inc_name, 0) >= baseline.get(inc_name, 0) + 1 else None

    counters = wait_until(_observed)
    assert counters is not None, \
        f"等待 {inc_name} +1 超时 (基线={baseline}, 现在={read_counters(client, 0)})"
    assert counters.get(inc_name, 0) - baseline.get(inc_name, 0) == 1, \
        f"{inc_name} 应 +1 (基线={baseline}, 现在={counters})"
    assert counters.get(flat_name, 0) - baseline.get(flat_name, 0) == 0, \
        f"{flat_name} 不应变化 (基线={baseline}, 现在={counters})"


@then("合格计数应增加 1 且不良计数不变")
def then_ok_plus_one(client, ctx):
    _assert_counter_delta(client, ctx, "合格总数", "不良总数")


@then("不良计数应增加 1 且合格计数不变")
def then_ng_plus_one(client, ctx):
    _assert_counter_delta(client, ctx, "不良总数", "合格总数")
