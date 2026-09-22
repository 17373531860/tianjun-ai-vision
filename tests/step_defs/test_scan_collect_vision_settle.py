"""多码采集随视觉周期结算 BDD step 实现 (v3.60.1c 六和现场链)。

链路与 tests/test_scan_collect_vision_settle_e2e.py 同源 (复用其 helper):
真项目(detection+last_step) → 真激活 → synthetic 视觉周期 → 真扫码 API
→ 视觉末步结算 → 锚定位组 → 异步收口。本文件用客户语言锁定业务行为。
"""
from __future__ import annotations

import uuid

import pytest
from pytest_bdd import scenarios, given, when, then

from tests.test_scan_collect_vision_settle_e2e import (
    CH,
    SCENARIO,
    _project_payload,
    _put_scan_collect,
    _scan,
    _state,
    _wait_settled,
    _wait_settled_group,
)

scenarios("../features/scan_collect_vision_settle.feature")


@pytest.fixture
def ctx():
    return {}


@given("后端处于测试模式")
def given_test_mode():
    import os
    assert os.environ.get("RUNTIME_MODE") == "test"


@given("一个检测模式末步结算项目已激活且开启随视觉周期结算")
def given_project(client, ctx):
    name = f"__bdd_vsettle_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/v1/projects/", json=_project_payload(name))
    assert r.status_code in (200, 201), r.text[:300]
    pid = r.json()["id"]
    r = client.post(f"/api/v1/projects/{pid}/activate")
    assert r.status_code == 200, r.text[:300]
    _put_scan_collect(client, pid)
    client.post("/api/v1/scan-collect/clear", json={"channel_id": CH})
    ctx["pid"] = pid
    yield
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")
    client.post("/api/v1/scan-collect/clear", json={"channel_id": CH})
    client.delete(f"/api/v1/projects/{pid}")


@given("视觉周期剧本已启动")
def given_chain(client):
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": SCENARIO, "channel": CH, "with_project": False,
    })
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]


@when("我只扫入 1 个芯子码")
def when_scan_one(client, ctx):
    _scan(client, "9260000000001")
    st = _state(client)
    got = {s["key"]: s["got"] for s in st.get("slots", [])}
    assert got.get("chip") == 1, f"扫码未进组: {st}"
    ctx["gid"] = st.get("group_id")


@when("我扫满 2 个芯子码并扫入工装收尾码")
def when_scan_full(client, ctx):
    _scan(client, "9260000000001")
    _scan(client, "9260000000002")
    _scan(client, "H-C035-527-5")
    ctx["gid"] = _state(client).get("group_id")


@then("组应仍在采集中 未被收尾码抢跑结算")
def then_group_still_open(client, ctx):
    st = _state(client)
    assert st.get("group_id") == ctx["gid"] and ctx["gid"], \
        f"closing 抢跑结算了 (a 版回归): {st}"
    got = {s["key"]: s["got"] for s in st.get("slots", [])}
    assert got == {"chip": 2, "fixture": 1}, st


@when("等待视觉末步结算收口")
def when_wait_settle(client, ctx):
    if ctx.get("gid"):
        ctx["settled"] = _wait_settled_group(client, ctx["gid"])
    else:
        ctx["settled"] = _wait_settled(client)


@then("本组判定应为 NG 且原因包含缺码")
def then_ng(ctx):
    settled = ctx["settled"]
    assert settled["is_good"] is False, settled
    assert "缺" in (settled.get("reason") or ""), settled


@then("本组判定应为 OK 且工件码为工装码")
def then_ok(ctx):
    settled = ctx["settled"]
    assert settled["result"] == "ok", settled
    assert settled.get("workpiece_sn") == "H-C035-527-5", settled


@then("面板应已翻篇等待下一件")
def then_turned_page(client):
    st = _state(client)
    assert not st.get("group_id"), f"组未翻篇: {st}"
