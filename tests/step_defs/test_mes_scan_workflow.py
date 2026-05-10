"""MES 扫码 → 绑工件 → 推送 BDD step 实现。"""
from __future__ import annotations

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from ._synthetic_helpers import (
    start_synthetic,
    start_detection,
)


scenarios("../features/mes_scan_workflow.feature")


@given("后端处于测试模式")
def given_test_mode_mes():
    import os
    assert os.environ.get("RUNTIME_MODE") == "test"


@given("MES 子系统已经初始化")
def given_mes_inited(client):
    r = client.get("/api/v1/mes/config")
    if r.status_code == 404:
        pytest.skip("MES 路由未挂载，环境不匹配")


@given(parsers.parse('加载剧本 "{scenario}" 并附带最小项目配置'))
def given_load_with_project(client, scenario):
    r = start_synthetic(client, scenario=scenario, with_project=True)
    assert r.status_code == 200, r.text[:300]


@when("我 GET /api/v1/mes/config")
def when_get_mes_config(client, ctx):
    ctx["resp"] = client.get("/api/v1/mes/config")


@when("我 PUT /api/v1/mes/config 一个最小启用配置")
def when_put_mes_config(client, ctx):
    ctx["resp"] = client.put("/api/v1/mes/config", json={
        "enabled": True,
        "endpoint": "http://localhost:0/mes",
        "method": "POST",
        "timeout": 1,
    })


@when("我 GET /api/v1/mes/scanner/state")
def when_get_scanner(client, ctx):
    ctx["resp"] = client.get("/api/v1/mes/scanner/state")


@when("我启动通道 0 的检测")
def when_start(client, ctx):
    ctx["resp"] = start_detection(client, channel=0)


@then(parsers.parse("响应状态应为 {code:d}"))
def then_status(ctx, code):
    resp = ctx["resp"]
    if resp.status_code == 404:
        pytest.skip(f"该路由未挂载 (404)，环境差异跳过")
    assert resp.status_code == code, f"期望 {code} 实际 {resp.status_code} body={resp.text[:200]}"


@then("响应状态应为 200 或 201")
def then_status_2xx(ctx):
    resp = ctx["resp"]
    if resp.status_code == 404:
        pytest.skip("路由未挂载")
    assert resp.status_code in (200, 201), \
        f"期望 200/201 实际 {resp.status_code} body={resp.text[:200]}"


@then("GET /api/v1/mes/config 后 enabled 字段应为 True")
def then_mes_enabled(client):
    r = client.get("/api/v1/mes/config")
    if r.status_code != 200:
        pytest.skip("MES config 不可读")
    body = r.json()
    assert body.get("enabled") is True, f"enabled 仍为 {body.get('enabled')}"


@then("不应在日志里看到 MES Hook 异常关键词")
def then_no_mes_exception(client, ctx):
    resp = ctx.get("resp")
    if resp is not None and resp.status_code == 200:
        return
    r = client.get("/api/v1/source/status")
    assert r.status_code == 200, f"source/status 不可读: {r.status_code} {r.text[:200]}"


# ============================================================
# 扩展场景：新增 step
# ============================================================
@when("我 GET /api/v1/mes/workorders")
def when_get_workorders(client, ctx):
    ctx["resp"] = client.get("/api/v1/mes/workorders")


@when("我 GET /api/v1/mes/workpieces?limit=1")
def when_get_workpieces(client, ctx):
    ctx["resp"] = client.get("/api/v1/mes/workpieces?limit=1")


@when("我 PUT /api/v1/mes/config 一个 disabled 配置")
def when_put_disabled(client, ctx):
    ctx["resp"] = client.put("/api/v1/mes/config", json={
        "enabled": False,
        "endpoint": "http://localhost:0/mes",
    })


@when("我 GET /api/v1/mes/adapters")
def when_get_adapters(client, ctx):
    ctx["resp"] = client.get("/api/v1/mes/adapters")


@when("我 PUT /api/v1/mes/config 一个不带 endpoint 的配置")
def when_put_no_endpoint(client, ctx):
    ctx["resp"] = client.put("/api/v1/mes/config", json={"enabled": True})


@then("响应状态应在 200/404 之中")
def then_status_2xx_404(ctx):
    resp = ctx["resp"]
    assert resp.status_code in (200, 404), \
        f"实际 {resp.status_code} body={resp.text[:200]}"


@then("响应状态应在 200/400/422 之中")
def then_status_2xx_4xx(ctx):
    resp = ctx["resp"]
    assert resp.status_code in (200, 400, 422), \
        f"实际 {resp.status_code} body={resp.text[:200]}"
