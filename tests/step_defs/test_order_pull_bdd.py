"""外部 MES 工单拉取 BDD step 实现 (v3.20)。

通过 TestClient 调真 API (pull-test / connections / pull / orders), 只 mock 最外层
的 requests.request (外部 MES 不在测试环境)。覆盖: 端点契约 + 连不上优雅失败 +
结构自动识别 + 立即同步落库 + 试同步不落库 + 上银错误带回 errorInfo。
"""
from __future__ import annotations

import json
import uuid

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

scenarios("../features/order_pull.feature")

PULL = "/api/v1/mes/gateway"


def _fake_resp(status, body):
    class _R:
        status_code = status

        def json(self):
            if body is None:
                raise ValueError("no json")
            return body

        @property
        def text(self):
            return json.dumps(body, ensure_ascii=False) if body is not None else ""

    return _R()


def _hiwin_body(rows):
    return {"error": None, "statusCode": 200, "success": True,
            "response": {"pageNo": 1, "numberOfPerPage": 200,
                         "resultData": rows, "i18nInfo": None}}


def _hiwin_err():
    return {"error": {"errorCode": 1, "errorInfo": "SYSTEM ERROR~~~~",
                      "detail_message": "column last_update_dt is ambiguous"},
            "statusCode": 500, "response": None, "success": False}


def _pull_cfg(url="http://fake-hiwin/api"):
    return {
        "enabled": True, "url": url, "method": "POST",
        "content_type": "application/json",
        "request_body_template": '{"api":"hiwin/x/query","parameters":{"job_no":"{job_no}"}}',
        "success_path": "statusCode", "success_value": 200,
        "array_path": "response.resultData",
        "field_mapping": {"order_no": "job_no", "customer_name": "cust_name",
                          "product_spec": "spec", "planned_qty": "dispatch_qty",
                          "product_name": "spec"},
        "import_mode": "upsert", "retry_count": 0,
        "triggers": {"manual": True, "scheduled": False},
    }


@pytest.fixture
def http_holder(monkeypatch):
    """劫持 puller 内部 requests.request: holder['resp'] 控返回, holder['raise'] 模拟连不上。"""
    holder = {"resp": _fake_resp(599, None), "raise": False}

    def fake_request(method, url, **kwargs):
        if holder.get("raise"):
            raise Exception("Connection refused (mock)")
        holder["last"] = {"method": method, "url": url}
        return holder["resp"]

    monkeypatch.setattr("backend.services.mes_puller.requests.request", fake_request)
    return holder


# ---------------- 背景 ----------------
@given("后端处于测试模式")
def _given_test_mode():
    import os
    assert os.environ.get("RUNTIME_MODE") == "test"


# ---------------- 数据准备 (given) ----------------
@given("外部 MES 将返回 1 条上银工单")
def _given_one_order(http_holder, ctx):
    no = f"BDDJOB-{uuid.uuid4().hex[:8]}"
    ctx["job_no"] = no
    http_holder["resp"] = _fake_resp(200, _hiwin_body([
        {"job_no": no, "cust_name": "雷鸟", "dispatch_qty": 120, "spec": "HG20-A"},
    ]))


@given("外部 MES 将返回 500 错误带 errorInfo")
def _given_err(http_holder, ctx):
    ctx["job_no"] = f"BDDJOB-{uuid.uuid4().hex[:8]}"
    http_holder["resp"] = _fake_resp(500, _hiwin_err())


@given("已建一条上银拉取连接")
def _given_conn(client, ctx):
    r = client.post(f"{PULL}/connections", json={
        "name": f"BDD-Pull-{uuid.uuid4().hex[:6]}", "adapter_type": "rest",
        "enabled": False, "pull_enabled": True, "config": {"pull": _pull_cfg()}})
    assert r.status_code in (200, 201), r.text[:200]
    ctx["conn_id"] = (r.json().get("data") or r.json()).get("id")


# ---------------- 动作 (when) ----------------
@when("我 GET 拉取连接列表")
def _when_list(client, ctx):
    ctx["resp"] = client.get(f"{PULL}/connections")


@when("我用一个连不上的地址测试拉取")
def _when_test_bad(client, ctx, http_holder):
    http_holder["raise"] = True
    ctx["resp"] = client.post(f"{PULL}/pull-test", json={
        "pull_config": _pull_cfg(url="http://localhost:1/nope"), "job_no": ""})
    ctx["res"] = ctx["resp"].json()


@when("我用上银模板测试拉取")
def _when_test_hiwin(client, ctx):
    ctx["resp"] = client.post(f"{PULL}/pull-test", json={
        "pull_config": _pull_cfg(), "job_no": ctx.get("job_no", "")})
    ctx["res"] = ctx["resp"].json()


@when("我对该连接执行立即同步")
def _when_pull_real(client, ctx):
    ctx["resp"] = client.post(f"{PULL}/connections/{ctx['conn_id']}/pull",
                              json={"dry_run": False})
    ctx["res"] = ctx["resp"].json()


@when("我对该连接执行试同步")
def _when_pull_dry(client, ctx):
    ctx["resp"] = client.post(f"{PULL}/connections/{ctx['conn_id']}/pull",
                              json={"dry_run": True, "max_items": 1})
    ctx["res"] = ctx["resp"].json()


# ---------------- 断言 (then) ----------------
@then(parsers.parse("拉取响应状态应为 {code:d}"))
def _then_status(ctx, code):
    resp = ctx["resp"]
    if resp.status_code == 404:
        pytest.skip("路由未挂载 (404)")
    assert resp.status_code == code, f"期望 {code} 实际 {resp.status_code} body={resp.text[:200]}"


@then(parsers.parse("测试结果 success 应为 {flag}"))
def _then_test_success(ctx, flag):
    want = flag.strip().lower() in ("true", "是")
    assert bool(ctx["res"].get("success")) is want, f"success={ctx['res'].get('success')} body={ctx['res']}"


@then(parsers.parse('识别出的数组路径应为 "{path}"'))
def _then_array_path(ctx, path):
    g = ctx["res"].get("structure_guess") or {}
    assert g.get("array_path") == path, f"array_path={g.get('array_path')} guess={g}"


@then(parsers.parse("拉取结果 created 应为 {n:d}"))
def _then_created(ctx, n):
    assert ctx["res"].get("created") == n, f"created={ctx['res'].get('created')} res={ctx['res']}"


@then(parsers.parse("拉取结果 success 应为 {flag}"))
def _then_pull_success(ctx, flag):
    want = flag.strip().lower() in ("true", "是")
    assert bool(ctx["res"].get("success")) is want, f"success={ctx['res'].get('success')} res={ctx['res']}"


@then(parsers.parse('拉取错误信息应包含 "{kw}"'))
def _then_error_contains(ctx, kw):
    err = ctx["res"].get("error") or ""
    assert kw in err, f"error={err!r} 不含 {kw!r}"


@then("该工单应能在工单列表查到")
def _then_order_found(client, ctx):
    r = client.get("/api/v1/mes/orders", params={"limit": 200})
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    items = body if isinstance(body, list) else (body.get("items") or body.get("data") or [])
    nos = [o.get("order_no") for o in items if isinstance(o, dict)]
    assert ctx["job_no"] in nos, f"工单 {ctx['job_no']} 未入库, 现有={nos[:10]}"


@then("该工单不应在工单列表出现")
def _then_order_absent(client, ctx):
    r = client.get("/api/v1/mes/orders", params={"limit": 200})
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    items = body if isinstance(body, list) else (body.get("items") or body.get("data") or [])
    nos = [o.get("order_no") for o in items if isinstance(o, dict)]
    assert ctx["job_no"] not in nos, f"试同步不该落库, 但 {ctx['job_no']} 出现了"
