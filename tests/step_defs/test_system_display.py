"""系统显示字段 + License 缓存 BDD step 实现"""
from __future__ import annotations

import pytest
from pytest_bdd import scenarios, given, when, then, parsers


scenarios("../features/system_display.feature")


# ============================================================
# Whens
# ============================================================
@when(parsers.parse(
    '我 PUT /api/v1/system/display 字段 brand_name="{brand}" factory_name="{factory}"'
))
def when_put_display(ctx, client, brand, factory):
    ctx["resp"] = client.put("/api/v1/system/display", json={
        "brand_name": brand,
        "factory_name": factory,
    })


@when(parsers.parse(
    '我 PUT /api/v1/system/license-cache 字段 customer="{customer}" machine_id="{mid}"'
))
def when_put_license(ctx, client, customer, mid):
    ctx["resp"] = client.put("/api/v1/system/license-cache", json={
        "customer": customer,
        "machine_id": mid,
    })


@when("我 PUT /api/v1/system/license-cache 字段为空")
def when_put_license_empty(ctx, client):
    ctx["resp"] = client.put("/api/v1/system/license-cache", json={})


# ============================================================
# Thens
# ============================================================
@then(parsers.parse("响应状态应为 {code:d}"))
def then_status(ctx, code):
    assert ctx["resp"].status_code == code, \
        f"期望 {code}, got {ctx['resp'].status_code}, body={ctx['resp'].text[:300]}"


@then(parsers.parse(
    'GET /api/v1/system/display 应返回 brand_name="{brand}" factory_name="{factory}"'
))
def then_display_get(ctx, client, brand, factory):
    resp = client.get("/api/v1/system/display")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("brand_name") == brand, f"brand_name={body.get('brand_name')}"
    assert body.get("factory_name") == factory, f"factory_name={body.get('factory_name')}"


@then(parsers.parse(
    'GET /api/v1/system/license-cache 应返回 customer="{customer}" machine_id="{mid}"'
))
def then_license_get(ctx, client, customer, mid):
    resp = client.get("/api/v1/system/license-cache")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("customer") == customer, f"customer={body.get('customer')}"
    assert body.get("machine_id") == mid, f"machine_id={body.get('machine_id')}"
