"""包装强制结案 + 待机收尾策略 BDD (v3.23).

走 client (session TestClient) 调:
  - /api/v1/packaging-flows/scan          扫工单开单
  - /api/v1/packaging-flows/{id}/force-settle  管理员强制结案 (鉴权关 → 隐式超管放行)
逐箱用 get_coordinator().on_cycle_settled(slider_count=...) 直接驱动 (避开真实推理);
待机/停止收尾用 on_forced_settle_by_channel(is_standby=...) 直接驱动 (无对应 HTTP 端点).
每个 scenario 前清空配置 + 复位单例.
"""
from __future__ import annotations

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

scenarios("../features/packaging_force_settle.feature")


def _coord():
    from backend.services.packaging_flow_coordinator import get_coordinator
    return get_coordinator()


def _new_session():
    from backend.db.database import SessionLocal
    return SessionLocal()


def _cleanup(client):
    coord = _coord()
    coord.cleanup_for_testing()
    resp = client.get("/api/v1/packaging-flows")
    for c in (resp.json() or {}).get("items", []):
        if c.get("enabled"):
            client.put(f"/api/v1/packaging-flows/{c['id']}", json={"enabled": False})
        client.delete(f"/api/v1/packaging-flows/{c['id']}")


@pytest.fixture
def ctx():
    return {"config_id": None, "cycle_seq": 0, "orders": {}, "fs_resp": None}


def _enable(client, ctx, items_per_box, standby_settle=True):
    _cleanup(client)
    r = client.post("/api/v1/packaging-flows", json={
        "name": "__bdd_force_settle", "enabled": True, "channel_id": 0,
        "count_unit": "sliders", "items_per_box_source": "config",
        "items_per_box_fixed": items_per_box, "slider_total_field": "dispatch_qty",
        "on_mes_fail": "offline", "on_forced_stop": "settle",
        "forced_settle_on_standby": standby_settle,
    })
    assert r.status_code == 201, r.text
    ctx["config_id"] = r.json()["id"]
    coord = _coord()
    coord.reload_configs(_new_session())
    coord.set_alarm_sink(lambda c, k, m: None)
    coord.set_mes_fetcher(lambda c, o: ctx["orders"].get(o))
    coord.set_paper_order_probe(lambda c, l: True)


@given(parsers.parse("启用滑块强制结案配置 通道0 每箱{n:d}滑块"))
def _enable_cfg(client, ctx, n):
    _enable(client, ctx, n, standby_settle=True)


@given(parsers.parse("启用滑块强制结案配置 通道0 每箱{n:d}滑块 待机不收尾"))
def _enable_cfg_no_standby(client, ctx, n):
    _enable(client, ctx, n, standby_settle=False)


@given(parsers.parse("启用滑块强制结案配置 通道0 每箱{n:d}滑块 待机也收尾"))
def _enable_cfg_standby(client, ctx, n):
    _enable(client, ctx, n, standby_settle=True)


@given(parsers.parse('MES工单 "{order}" 滑块总数 {qty:d}'))
def _set_order(ctx, order, qty):
    ctx["orders"][order] = {"dispatch_qty": qty, "job_no": order}


@when(parsers.parse('扫工单 "{code}"'))
def _scan(client, code):
    r = client.post("/api/v1/packaging-flows/scan",
                    json={"code": code, "channel_id": 0})
    assert r.status_code == 200, r.text


@when(parsers.parse("完成一箱滑块 {n:d}"))
def _settle_one_box(ctx, n):
    ctx["cycle_seq"] += 1
    _coord().on_cycle_settled(0, ctx["cycle_seq"], True, _new_session(), slider_count=n)


@when(parsers.parse('强制结案 理由 "{reason}"'))
def _force_settle(client, ctx, reason):
    ctx["fs_resp"] = client.post(
        f"/api/v1/packaging-flows/{ctx['config_id']}/force-settle",
        json={"reason": reason})


@when("触发待机收尾")
def _standby_settle(ctx):
    _coord().on_forced_settle_by_channel(0, _new_session(), is_standby=True)


@when("触发停止收尾")
def _stop_settle(ctx):
    _coord().on_forced_settle_by_channel(0, _new_session(), is_standby=False)


@then("强制结案接口应返回成功")
def _assert_fs_ok(ctx):
    r = ctx["fs_resp"]
    assert r is not None and r.status_code == 200, getattr(r, "text", r)
    assert r.json().get("success") is True


@then(parsers.parse("强制结案接口应返回状态码 {code:d}"))
def _assert_fs_code(ctx, code):
    r = ctx["fs_resp"]
    assert r is not None and r.status_code == code, f"实得 {getattr(r,'status_code',None)}: {getattr(r,'text','')}"


def _row(order):
    from backend.models.mes_models import PackagingFlowRun
    db = _new_session()
    norm = order.replace("-", "").upper()
    return db.query(PackagingFlowRun).filter_by(order_no=norm).first()


@then(parsers.parse('工单 "{order}" 状态应为已完成'))
def _assert_completed(order):
    row = _row(order)
    assert row is not None, f"工单 {order} 无落库记录"
    assert row.status == "completed", f"status={row.status}"


@then(parsers.parse('工单 "{order}" 强制结案理由应为 "{reason}"'))
def _assert_reason(order, reason):
    row = _row(order)
    assert row.forced_reason == reason, f"forced_reason={row.forced_reason!r}"


@then(parsers.parse('工单 "{order}" 强制结案授权人应非空'))
def _assert_operator(order):
    row = _row(order)
    assert row.forced_by, f"forced_by={row.forced_by!r} 应非空"


@then(parsers.parse('工单 "{order}" 仍在进行中'))
def _assert_running(ctx, order):
    assert _coord().get_state(ctx["config_id"]) is not None, "工单已不在进行中"


@then(parsers.parse('工单 "{order}" 不再进行中'))
def _assert_not_running(ctx, order):
    assert _coord().get_state(ctx["config_id"]) is None, "工单仍在进行中"
