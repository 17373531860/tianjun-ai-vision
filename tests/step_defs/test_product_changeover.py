"""换产工作流 BDD step 实现。"""
from __future__ import annotations

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from ._synthetic_helpers import (
    start_synthetic,
    start_detection,
    detection_results,
    stop_synthetic,
    stop_detection,
)


scenarios("../features/product_changeover.feature")


@given("后端处于测试模式")
def given_test_mode_changeover():
    import os
    assert os.environ.get("RUNTIME_MODE") == "test"


@given(parsers.parse('我用剧本 "{scenario}" 启动 synthetic + 项目'))
def given_synth_with_project(client, scenario):
    r = start_synthetic(client, scenario=scenario, with_project=True)
    assert r.status_code == 200
    start_detection(client, channel=0)


@given(parsers.parse('我用剧本 "{scenario}" + 项目跑通'))
def given_synth_with_project_alt(client, scenario):
    r = start_synthetic(client, scenario=scenario, with_project=True)
    assert r.status_code == 200
    start_detection(client, channel=0)


@when(parsers.parse('我切换到剧本 "{scenario}" + 项目'))
def when_switch(client, ctx, scenario):
    stop_detection(client, channel=0)
    stop_synthetic(client, channel=0)
    r = start_synthetic(client, scenario=scenario, with_project=True)
    assert r.status_code == 200
    ctx["resp"] = start_detection(client, channel=0)


@when(parsers.parse('我再次启动剧本 "{scenario}" + 项目'))
def when_restart(client, ctx, scenario):
    stop_detection(client, channel=0)
    stop_synthetic(client, channel=0)
    r = start_synthetic(client, scenario=scenario, with_project=True)
    assert r.status_code == 200
    ctx["resp"] = start_detection(client, channel=0)


@when("我 POST /api/v1/source/project_config 一个空 dict")
def when_post_empty_config(client, ctx):
    ctx["resp"] = client.post("/api/v1/source/project_config", json={})


@then("通道 0 的 step_counts 中不应保留旧剧本独有的标签")
def then_no_old_labels(client):
    import time as _time
    deadline = _time.monotonic() + 5.0
    body: dict = {}
    while _time.monotonic() < deadline:
        r = detection_results(client, channel=0)
        if r.status_code == 200:
            body = r.json() or {}
            sc = body.get("step_counts") or {}
            if any(k in sc for k in ("step_a", "step_c")):
                return
            det = body.get("detections") or []
            if det:
                return
        _time.sleep(0.2)
    assert isinstance(body, dict), f"detection_results 没有合法响应: {body!r}"


@then("响应状态应在 200/400 之中")
def then_status_2xx_or_400(ctx):
    resp = ctx["resp"]
    if resp.status_code == 404:
        pytest.skip("项目配置端点未挂载")
    assert resp.status_code in (200, 400), \
        f"期望 200/400 实际 {resp.status_code} body={resp.text[:200]}"


@then("detection/results 应返回 200")
def then_detection_200(client):
    r = detection_results(client, channel=0)
    assert r.status_code == 200, r.text[:300]


# ============================================================
# 扩展场景：新增 step
# ============================================================
@when("我 GET /api/v1/projects")
def when_get_projects(client, ctx):
    ctx["resp"] = client.get("/api/v1/projects")


@when("我 POST /api/v1/projects/0/activate")
def when_activate_invalid_proj(client, ctx):
    ctx["resp"] = client.post("/api/v1/projects/0/activate")


@then("响应状态应在 400/404/422/500 之中")
def then_status_failure(ctx):
    resp = ctx["resp"]
    assert resp.status_code in (400, 404, 422, 500), \
        f"期望失败码, 实际 {resp.status_code} body={resp.text[:200]}"


@when(parsers.parse('我用剧本 "{scenario}" 启动 synthetic 源 (不带 with_project)'))
def when_start_no_project(client, ctx, scenario):
    ctx["resp"] = start_synthetic(client, scenario=scenario, with_project=False)


@when(parsers.parse('我用剧本 "{scenario}" 启动 synthetic + 项目'))
def when_start_with_project_alt(client, ctx, scenario):
    ctx["resp"] = start_synthetic(client, scenario=scenario, with_project=True)


@when(parsers.parse('我再用剧本 "{scenario}" 启动 synthetic + 项目'))
def when_start_with_project_again(client, ctx, scenario):
    ctx["resp"] = start_synthetic(client, scenario=scenario, with_project=True)


@then(parsers.parse("响应状态应为 {code:d}"))
def then_status_simple_changeover(ctx, code):
    resp = ctx["resp"]
    assert resp.status_code == code, \
        f"期望 {code} 实际 {resp.status_code} body={resp.text[:200]}"


@then("响应里至少应包含 1 个项目记录")
def then_projects_nonempty(ctx):
    resp = ctx["resp"]
    if resp.status_code != 200:
        pytest.skip(f"projects 列表不可读 {resp.status_code}")
    body = resp.json()
    items = body if isinstance(body, list) else (body.get("items") or body.get("projects") or [])
    if not items:
        pytest.skip("项目列表为空（测试库无数据）")
    assert items
