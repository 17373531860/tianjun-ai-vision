"""包装箱结算 BDD — 客户工作流 (上银包装线, v3.21 M6).

走 client (session TestClient) 调 /api/v1/packaging-flows/scan 驱动扫码,
托盘检测周期用 get_coordinator().on_cycle_settled 直接驱动 (避开 VSM 真实推理),
报警经 mock alarm_sink 捕获到 ctx. 每个 scenario 前清空配置 + 复位协调器单例.
"""
from __future__ import annotations

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

scenarios("../features/packaging_flow.feature")


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
    return {"config_id": None, "alarms": [], "cycle_seq": 0}


@given("启用一条包装结算配置 通道0 每箱2托盘")
def _enable_cfg(client, ctx):
    _cleanup(client)
    r = client.post("/api/v1/packaging-flows", json={
        "name": "__bdd_pkg", "enabled": True, "channel_id": 0,
        "trays_per_box_fixed": 2, "label_match": "strip_hyphen",
        "on_mes_fail": "offline",
    })
    assert r.status_code == 201, r.text
    ctx["config_id"] = r.json()["id"]
    _coord().reload_configs(_new_session())
    _coord().set_alarm_sink(lambda c, k, m: ctx["alarms"].append(k))
    _coord().set_mes_fetcher(lambda c, o: {"dispatch_qty": ctx.get("box_total", 0)})


@given(parsers.parse("工单应做箱数为 {n:d}"))
def _set_box_total(ctx, n):
    ctx["box_total"] = n


@given(parsers.parse("工单标签长度应为 {n:d} 位"))
def _set_label_len(client, ctx, n):
    client.put(f"/api/v1/packaging-flows/{ctx['config_id']}", json={"label_len": n})
    _coord().reload_configs(_new_session())
    # PUT 触发的 reload 会重建 cfg, 报警 sink/fetcher 仍是单例上注入的, 无需重设


@when(parsers.parse('扫码 "{code}"'))
def _scan(client, code):
    r = client.post("/api/v1/packaging-flows/scan",
                    json={"code": code, "channel_id": 0})
    assert r.status_code == 200, r.text


@when(parsers.parse("完成 {n:d} 个合格托盘"))
def _good_trays(ctx, n):
    db = _new_session()
    for _ in range(n):
        ctx["cycle_seq"] += 1
        _coord().on_cycle_settled(0, ctx["cycle_seq"], True, db)


@when(parsers.parse("完成 {n:d} 个不达标托盘"))
def _ng_trays(ctx, n):
    db = _new_session()
    for _ in range(n):
        ctx["cycle_seq"] += 1
        _coord().on_cycle_settled(0, ctx["cycle_seq"], False, db)


@then(parsers.parse('工单 "{order}" 最终结果应为 "{result}"'))
def _assert_final(order, result):
    from backend.models.mes_models import PackagingFlowRun
    db = _new_session()
    row = db.query(PackagingFlowRun).filter_by(order_no=order).first()
    assert row is not None, f"工单 {order} 无落库记录"
    assert row.final_result == result, f"final_result={row.final_result} 期望 {result}"


@then(parsers.parse('应触发 "{kind}" 报警'))
def _assert_alarm(ctx, kind):
    assert kind in ctx["alarms"], f"未触发 {kind}, 实际={ctx['alarms']}"


@then(parsers.parse('不应触发 "{kind}" 报警'))
def _assert_no_alarm(ctx, kind):
    assert kind not in ctx["alarms"], f"不应触发 {kind}, 实际={ctx['alarms']}"
