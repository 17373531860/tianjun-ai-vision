"""自定义导出 BDD step 实现 — 走真实 HTTP API"""
from __future__ import annotations

import json

import pytest
from pytest_bdd import scenarios, given, when, then, parsers


scenarios("../features/custom_export.feature")


# ============================================================
# Givens
# ============================================================
@given(parsers.parse('已有一个自建模板 名称="{name}" format={fmt}'))
def given_user_template(ctx, client, name, fmt):
    resp = client.post("/api/v1/export/templates", json={
        "name": name,
        "format": fmt,
        "scope": "both",
        "content": "test content",
    })
    assert resp.status_code == 200, resp.text
    ctx["last_template"] = resp.json()


@given("系统预设模板已 seed")
def given_system_seeded(ctx, client):
    """main.py 启动时已自动调 seed_builtin_templates"""
    pass


# ============================================================
# Whens
# ============================================================
@when("我请求 GET /api/v1/export/fields")
def when_get_fields(ctx, client):
    ctx["resp"] = client.get("/api/v1/export/fields")


@when(parsers.parse(
    '我创建模板 名称="{name}" format={fmt} scope={scope} content="{content}"'
))
def when_create_template(ctx, client, name, fmt, scope, content):
    ctx["resp"] = client.post("/api/v1/export/templates", json={
        "name": name,
        "format": fmt,
        "scope": scope,
        "content": content,
    })


@when("我请求 GET /api/v1/export/templates")
def when_list_templates(ctx, client):
    ctx["resp"] = client.get("/api/v1/export/templates")


@when("我删除该模板")
def when_delete_template(ctx, client):
    tpl_id = ctx["last_template"]["id"]
    ctx["deleted_id"] = tpl_id
    ctx["resp"] = client.delete(f"/api/v1/export/templates/{tpl_id}")


@when(parsers.parse('我尝试删除 builtin_id="{bid}" 对应的系统模板'))
def when_delete_builtin(ctx, client, bid):
    list_resp = client.get("/api/v1/export/templates")
    items = list_resp.json().get("items", [])
    target = next((t for t in items if t.get("builtin_id") == bid), None)
    assert target, f'未找到 builtin_id="{bid}" 的系统模板; items={[t.get("builtin_id") for t in items]}'
    ctx["resp"] = client.delete(f"/api/v1/export/templates/{target['id']}")


@when(parsers.parse(
    '我用 template_content="{tpl}" 调用 POST /api/v1/export/preview'
))
def when_preview_simple(ctx, client, tpl):
    ctx["resp"] = client.post("/api/v1/export/preview", json={
        "template_content": tpl,
        "fmt": "txt",
    })


@when(parsers.parse(
    '我用 template_content="{tpl}" 和 license_payload={lp} 调用 preview'
))
def when_preview_with_license(ctx, client, tpl, lp):
    license_payload = json.loads(lp)
    ctx["resp"] = client.post("/api/v1/export/preview", json={
        "template_content": tpl,
        "fmt": "txt",
        "license_payload": license_payload,
    })


# ============================================================
# Thens
# ============================================================
@then(parsers.parse("响应状态应为 {code:d}"))
def then_status(ctx, code):
    assert ctx["resp"].status_code == code, \
        f"期望 {code}, 实际 {ctx['resp'].status_code}, body={ctx['resp'].text[:300]}"


@then("响应应包含字段组列表")
def then_resp_has_groups(ctx):
    body = ctx["resp"].json()
    assert "groups" in body or "items" in body, f"unexpected body: {body}"


@then("字段组应至少包含 cycle 和 system")
def then_resp_has_cycle_system(ctx):
    body = ctx["resp"].json()
    groups = body.get("groups") or body.get("items") or []
    keys = {g.get("key") or g.get("group") or g.get("name") for g in groups}
    assert "cycle" in keys, f"缺 cycle 组; got {keys}"
    assert "system" in keys, f"缺 system 组; got {keys}"


@then("模板 id 应被分配")
def then_id_assigned(ctx):
    body = ctx["resp"].json()
    assert isinstance(body.get("id"), int) and body["id"] > 0
    ctx["last_template"] = body


@then("模板 is_system 应为 false")
def then_not_system(ctx):
    body = ctx["resp"].json()
    assert body.get("is_system") is False, f"is_system={body.get('is_system')}"


@then(parsers.parse('模板列表应包含名称="{name}"'))
def then_list_contains(ctx, name):
    body = ctx["resp"].json()
    items = body.get("items", [])
    names = [t.get("name") for t in items]
    assert name in names, f"未找到 {name}; got {names}"


@then("该模板再次 GET 应返回 404")
def then_template_not_found(ctx, client):
    tpl_id = ctx["deleted_id"]
    resp = client.get(f"/api/v1/export/templates/{tpl_id}")
    assert resp.status_code == 404, f"期望 404, got {resp.status_code}"


@then(parsers.parse('rendered 字段应包含 "{substr}"'))
def then_rendered_contains(ctx, substr):
    body = ctx["resp"].json()
    rendered = body.get("rendered", "")
    assert substr in rendered, f"'{substr}' not in rendered={rendered!r}"


@then(parsers.parse('rendered 字段应等于 "{val}"'))
def then_rendered_equals(ctx, val):
    body = ctx["resp"].json()
    assert body.get("rendered") == val, f"got {body.get('rendered')!r}"


@then("error 字段应为 null")
def then_no_error(ctx):
    body = ctx["resp"].json()
    assert body.get("error") is None, f"error={body.get('error')}"
