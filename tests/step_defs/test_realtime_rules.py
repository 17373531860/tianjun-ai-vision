"""实时规则 BDD step 实现"""
from __future__ import annotations

import os

import pytest
from pytest_bdd import scenarios, given, when, then, parsers


scenarios("../features/realtime_rules.feature")


# ============================================================
# Background
# ============================================================
@given(parsers.parse(
    '已有一个自建模板用于实时规则 名称="{name}" format={fmt} content="{content}"'
))
def given_template_for_rule(ctx, client, name, fmt, content):
    resp = client.post("/api/v1/export/templates", json={
        "name": name,
        "format": fmt,
        "scope": "realtime",
        "content": content,
    })
    assert resp.status_code == 200, resp.text
    ctx["template"] = resp.json()


# ============================================================
# Givens
# ============================================================
@given(parsers.parse('已经创建一条实时规则 名称="{name}"'), target_fixture="rule_id")
def given_rule_created(ctx, client, tmp_output_dir, name):
    tpl_id = ctx["template"]["id"]
    resp = client.post("/api/v1/export/realtime-rules", json={
        "name": name,
        "enabled": True,
        "template_id": tpl_id,
        "output_dir": tmp_output_dir,
        "filename_template": "{{ cycle.id or 'no-cycle' }}.txt",
        "trigger_event": "cycle_end",
    })
    assert resp.status_code == 200, resp.text
    ctx["rule"] = resp.json()
    return ctx["rule"]["id"]


# ============================================================
# Whens
# ============================================================
@when(parsers.parse('我创建实时规则 名称="{name}"'))
def when_create_rule(ctx, client, tmp_output_dir, name):
    tpl_id = ctx["template"]["id"]
    ctx["resp"] = client.post("/api/v1/export/realtime-rules", json={
        "name": name,
        "enabled": True,
        "template_id": tpl_id,
        "output_dir": tmp_output_dir,
        "filename_template": "{{ cycle.id or 'manual' }}.txt",
        "trigger_event": "cycle_end",
    })


@when("我请求 GET /api/v1/export/realtime-rules")
def when_list_rules(ctx, client):
    ctx["resp"] = client.get("/api/v1/export/realtime-rules")


@when("我对该规则执行 toggle")
def when_toggle(ctx, client):
    rid = ctx["rule"]["id"]
    ctx["resp"] = client.post(f"/api/v1/export/realtime-rules/{rid}/toggle")


@when("我删除该实时规则")
def when_delete_rule(ctx, client):
    rid = ctx["rule"]["id"]
    ctx["deleted_id"] = rid
    ctx["resp"] = client.delete(f"/api/v1/export/realtime-rules/{rid}")


@when("我对该规则执行 test-run")
def when_test_run(ctx, client):
    rid = ctx["rule"]["id"]
    ctx["resp"] = client.post(
        f"/api/v1/export/realtime-rules/{rid}/test-run", json={}
    )


# ============================================================
# Thens
# ============================================================
@then(parsers.parse("响应状态应为 {code:d}"))
def then_status(ctx, code):
    assert ctx["resp"].status_code == code, \
        f"期望 {code}, got {ctx['resp'].status_code}, body={ctx['resp'].text[:300]}"


@then(parsers.parse("规则 enabled 应为 {val}"))
def then_enabled(ctx, val):
    expected = val.lower() == "true"
    body = ctx["resp"].json()
    assert body.get("enabled") is expected, f"enabled={body.get('enabled')}"
    if "id" in body:
        ctx["rule"] = body  # 更新缓存的 rule 数据


@then(parsers.parse("规则 trigger_event 应为 {val}"))
def then_trigger_event(ctx, val):
    body = ctx["resp"].json()
    assert body.get("trigger_event") == val, f"got {body.get('trigger_event')}"


@then(parsers.parse('规则列表应包含名称="{name}"'))
def then_list_contains(ctx, name):
    body = ctx["resp"].json()
    items = body.get("items", [])
    names = [r.get("name") for r in items]
    assert name in names, f"未找到 {name}; got {names}"


@then("该规则再次 GET 应返回 404")
def then_rule_404(ctx, client):
    rid = ctx["deleted_id"]
    resp = client.get(f"/api/v1/export/realtime-rules/{rid}")
    assert resp.status_code == 404


@then(parsers.parse("test-run 结果 status 应为 {expected}"))
def then_testrun_status(ctx, expected):
    body = ctx["resp"].json()
    assert body.get("status") == expected, f"status={body.get('status')}, body={body}"


@then(parsers.parse("该规则的 logs 应至少有 {n:d} 条"))
def then_logs_count(ctx, client, n):
    rid = ctx["rule"]["id"]
    resp = client.get(f"/api/v1/export/realtime-rules/{rid}/logs")
    assert resp.status_code == 200
    items = resp.json().get("items", [])
    assert len(items) >= n, f"期望至少 {n} 条日志, got {len(items)}"
