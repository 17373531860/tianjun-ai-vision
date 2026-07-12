"""包装箱结算 复合条码取段 + 工单号识别规则 BDD (v3.30.1, 上银正式产线).

现场叙事: 上银正式产线箱标签同时印 JOB工单条形码 / 数量条形码(80.00) / 复合二维码
(订单|工单|数量|校验串). 工人先扫工单纸(枪丢'-')开单, 再扫箱上任意工单码 —
三种工单码取段+补符号后必须认成同一张工单; 数量码有在途工单时走"标签不符"阻断,
没有在途工单时由"工单号识别规则"挡住不开垃圾工单.

走 client (session TestClient) 调 /api/v1/packaging-flows/scan 驱动扫码 (HTTP 全链路),
MES 拉单用可控 fetcher, 逐箱结算用 on_cycle_settled 直接驱动, 报警经 mock sink 捕获.
所有扫码串都来自 2026-07-10 现场扫码枪真实捕获 (见 uat_20260710_composite_label.py).
"""
from __future__ import annotations

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

scenarios("../features/packaging_composite_label.feature")


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
    return {"config_id": None, "alarms": [], "cycle_seq": 0, "orders": {}}


def _enable(client, ctx, *, composite=True, prefix="JOB", pick_mode="prefix",
            pick_index=1, hyphen_pos=12, pattern="", items_per_box=120):
    """建一条上银式配置: sliders 口径 + insert_char 补符号 + 复合取段 + 识别规则."""
    _cleanup(client)
    r = client.post("/api/v1/packaging-flows", json={
        "name": "__bdd_composite", "enabled": True, "channel_id": 0,
        "count_unit": "sliders", "items_per_box_source": "config",
        "items_per_box_fixed": items_per_box, "slider_total_field": "dispatch_qty",
        "on_mes_fail": "offline", "on_label_mismatch": "block",
        "label_match": "insert_char", "hyphen_template": "-", "hyphen_pos": hyphen_pos,
        "composite_label_enabled": composite, "composite_delimiter": "|",
        "composite_pick_mode": pick_mode, "composite_prefix": prefix,
        "composite_index": pick_index,
        "order_code_pattern": pattern or None,
    })
    assert r.status_code == 201, r.text
    ctx["config_id"] = r.json()["id"]
    coord = _coord()
    coord.reload_configs(_new_session())
    coord.set_alarm_sink(lambda c, k, m: ctx["alarms"].append(k))
    coord.set_mes_fetcher(lambda c, o: ctx["orders"].get(o))
    coord.set_paper_order_probe(lambda c, l: True)


@given(parsers.parse('启用复合取段包装配置 前缀 "{prefix}" 补符号位 {pos:d}'))
def _enable_prefix(client, ctx, prefix, pos):
    _enable(client, ctx, prefix=prefix, hyphen_pos=pos)


@given(parsers.parse('启用复合取段包装配置 前缀 "{prefix}" 补符号位 {pos:d} 但取段关闭'))
def _enable_prefix_off(client, ctx, prefix, pos):
    _enable(client, ctx, composite=False, prefix=prefix, hyphen_pos=pos)


@given(parsers.parse('启用复合取段包装配置 前缀 "{prefix}" 补符号位 {pos:d} 识别规则 "{pattern}"'))
def _enable_prefix_pattern(client, ctx, prefix, pos, pattern):
    _enable(client, ctx, prefix=prefix, hyphen_pos=pos, pattern=pattern)


@given(parsers.parse('启用复合取段包装配置 前缀 "{prefix}" 补符号位 {pos:d} 识别规则 "{pattern}" 每箱{n:d}滑块'))
def _enable_prefix_pattern_box(client, ctx, prefix, pos, pattern, n):
    _enable(client, ctx, prefix=prefix, hyphen_pos=pos, pattern=pattern,
            items_per_box=n)


@given(parsers.parse("启用复合取段包装配置 取第 {idx:d} 段"))
def _enable_index(client, ctx, idx):
    # index 模式场景用无符号短工单号, 不需要补符号
    _enable(client, ctx, pick_mode="index", pick_index=idx, prefix="",
            hyphen_pos=0)


@given(parsers.parse('MES 工单 "{order}" 滑块总数 {qty:d}'))
def _set_order(ctx, order, qty):
    ctx["orders"][order] = {"dispatch_qty": qty, "job_no": order}


@when(parsers.parse('扫码 "{code}"'))
def _scan(client, code):
    r = client.post("/api/v1/packaging-flows/scan",
                    json={"code": code, "channel_id": 0})
    assert r.status_code == 200, r.text


@when(parsers.parse('逐箱完成滑块 "{csv}"'))
def _settle_boxes(ctx, csv):
    db = _new_session()
    for part in csv.split(","):
        ctx["cycle_seq"] += 1
        _coord().on_cycle_settled(0, ctx["cycle_seq"], True, db,
                                  slider_count=int(part.strip()))


@then(parsers.parse('在途工单应为 "{order}"'))
def _assert_run_order(ctx, order):
    state = _coord().get_state(ctx["config_id"])
    assert state is not None, "无在途工单"
    assert state["order_no"] == order, f"order_no={state['order_no']} 期望 {order}"


@then("不应有在途工单")
def _assert_no_run(ctx):
    assert _coord().get_state(ctx["config_id"]) is None


@then(parsers.parse('应触发 "{kind}" 报警'))
def _assert_alarm(ctx, kind):
    assert kind in ctx["alarms"], f"未触发 {kind}, 实际={ctx['alarms']}"


@then(parsers.parse('不应触发 "{kind}" 报警'))
def _assert_no_alarm(ctx, kind):
    assert kind not in ctx["alarms"], f"不该触发 {kind}, 实际={ctx['alarms']}"


def _latest_run(order):
    """多场景复用同一工单号会留多条 run, 断言只看本场景 (最新) 那条."""
    from backend.models.mes_models import PackagingFlowRun
    db = _new_session()
    return (db.query(PackagingFlowRun).filter_by(order_no=order)
            .order_by(PackagingFlowRun.id.desc()).first())


@then(parsers.parse('工单 "{order}" 最终结果应为 "{result}"'))
def _assert_final(order, result):
    row = _latest_run(order)
    assert row is not None, f"工单 {order} 无落库记录"
    assert row.final_result == result, f"final_result={row.final_result} 期望 {result}"


@then(parsers.parse('工单 "{order}" 已结算箱数应为 {n:d}'))
def _assert_box_done(order, n):
    row = _latest_run(order)
    assert row is not None, f"工单 {order} 无落库记录"
    assert row.box_done == n, f"box_done={row.box_done} 期望 {n}"
