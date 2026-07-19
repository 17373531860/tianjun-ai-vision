"""包装箱结算 滑块口径 BDD — 上银 MES 闭环 (v3.22).

走 client (session TestClient) 调 /api/v1/packaging-flows/scan 驱动扫工单,
MES 拉单用可控 fetcher (按工单号给滑块总数+规格), 逐箱检测周期用
get_coordinator().on_cycle_settled(slider_count=...) 直接驱动 (避开 VSM 真实推理),
报警经 mock alarm_sink 捕获. 每个 scenario 前清空配置 + 复位单例.

托盘只是容器、不锁托盘数: 这里全程不配每箱托盘数, 只按每箱滑块总数判满.
"""
from __future__ import annotations

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

scenarios("../features/packaging_flow_sliders.feature")


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


def _enable(client, ctx, items_per_box, on_mes_fail="offline"):
    _cleanup(client)
    r = client.post("/api/v1/packaging-flows", json={
        "name": "__bdd_sliders", "enabled": True, "channel_id": 0,
        "count_unit": "sliders", "items_per_box_source": "config",
        "items_per_box_fixed": items_per_box, "slider_total_field": "dispatch_qty",
        "on_mes_fail": on_mes_fail,
    })
    assert r.status_code == 201, r.text
    ctx["config_id"] = r.json()["id"]
    coord = _coord()
    coord.reload_configs(_new_session())
    coord.set_alarm_sink(lambda c, k, m: ctx["alarms"].append(k))
    # fetcher: 按工单号查 ctx["orders"]; 查不到返回 None → on_mes_fail
    coord.set_mes_fetcher(lambda c, o: ctx["orders"].get(o))
    # 尾箱塞工单探测: 默认 True (不阻断); 单独场景再覆盖成 False
    coord.set_paper_order_probe(lambda c, l: True)


@given(parsers.parse("启用滑块口径包装配置 通道0 每箱{n:d}滑块"))
def _enable_cfg(client, ctx, n):
    _enable(client, ctx, n)


@given(parsers.parse("启用滑块口径包装配置 通道0 每箱{n:d}滑块 阻断"))
def _enable_cfg_block(client, ctx, n):
    _enable(client, ctx, n, on_mes_fail="block")


@given("开启尾箱必须塞工单")
def _enable_paper_gate(client, ctx):
    client.put(f"/api/v1/packaging-flows/{ctx['config_id']}",
               json={"tail_paper_order_required": True, "tail_paper_step_label": "put_paper"})
    _coord().reload_configs(_new_session())
    # 默认探测放行, 待 "尾箱在未塞工单下完成滑块" 步骤里临时切成 False


@given("开启放工单收尾动作模式")
def _enable_paper_close_action(client, ctx):
    # v3.43 子开关: 箱归周期结算 (尾箱当场落账), 放工单归工单收尾, 默认关=老行为
    client.put(f"/api/v1/packaging-flows/{ctx['config_id']}",
               json={"tail_paper_as_close_action": True})
    _coord().reload_configs(_new_session())


@given(parsers.parse("缺工单判定方式为时限 {n:d} 秒"))
def _set_timeout_judge_mode(client, ctx, n):
    # v3.43 判定方式二选一: 时限模式 = 扫新单报警关 + 时限秒数 > 0
    client.put(f"/api/v1/packaging-flows/{ctx['config_id']}",
               json={"tail_paper_scan_alarm": False, "tail_paper_timeout_s": n})
    _coord().reload_configs(_new_session())
    ctx["paper_timeout_s"] = n


@when("等待时限到点")
def _wait_timeout(ctx):
    import time as _t
    _t.sleep((ctx.get("paper_timeout_s") or 1) + 0.5)


@given("开启完成工单重扫拦截")
def _enable_rescan_block(client, ctx):
    # v3.42.1: 完成且 OK 的工单再扫同号 → 报警提示且不重新录入 (默认关=老行为)
    client.put(f"/api/v1/packaging-flows/{ctx['config_id']}",
               json={"block_completed_order_rescan": True})
    _coord().reload_configs(_new_session())


@given(parsers.parse('MES 工单 "{order}" 滑块总数 {qty:d} 规格 "{spec}"'))
def _set_order(ctx, order, qty, spec):
    ctx["orders"][order] = {"dispatch_qty": qty, "spec": spec, "job_no": order}


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


@when(parsers.parse("尾箱在未塞工单下完成滑块 {n:d}"))
def _settle_tail_no_paper(ctx, n):
    _coord().set_paper_order_probe(lambda c, l: False)  # 没检测到放工单动作
    ctx["cycle_seq"] += 1
    _coord().on_cycle_settled(0, ctx["cycle_seq"], True, _new_session(), slider_count=n)


@when("随后检测到放工单动作再来一个周期")
def _settle_paper_cycle(ctx):
    # 工人补放工单: 探测转为可见, 下一个检测周期结算时完成工单收尾
    _coord().set_paper_order_probe(lambda c, l: True)
    ctx["cycle_seq"] += 1
    _coord().on_cycle_settled(0, ctx["cycle_seq"], True, _new_session(), slider_count=0)


@when("现场已放工单")
def _paper_now_visible(ctx):
    # 只放工单不等新周期结算 (扫码时刻由探测钩子看到实时步骤)
    _coord().set_paper_order_probe(lambda c, l: True)


@then(parsers.parse('在途工单应为 "{order}"'))
def _assert_running_order(ctx, order):
    state = _coord().get_state(ctx["config_id"])
    norm = order.replace("-", "").upper()
    assert state is not None, "无进行中工单"
    assert state["order_no"] == norm, f"在途={state['order_no']} 期望 {norm}"


@then(parsers.parse('工单 "{order}" 应做箱数 {boxes:d} 尾箱目标 {tail:d}'))
def _assert_plan(ctx, order, boxes, tail):
    state = _coord().get_state(ctx["config_id"])
    assert state is not None, "无进行中工单状态"
    assert state["box_total"] == boxes, f"box_total={state['box_total']} 期望 {boxes}"
    assert state["tail_target"] == tail, f"tail_target={state['tail_target']} 期望 {tail}"
    assert state["count_unit"] == "sliders"


@then(parsers.parse('工单 "{order}" 最终结果应为 "{result}"'))
def _assert_final(order, result):
    from backend.models.mes_models import PackagingFlowRun
    db = _new_session()
    norm = order.replace("-", "").upper()
    row = db.query(PackagingFlowRun).filter_by(order_no=norm).first()
    assert row is not None, f"工单 {norm} 无落库记录"
    assert row.final_result == result, f"final_result={row.final_result} 期望 {result}"


@then(parsers.parse('工单 "{order}" 已结算箱数应为 {n:d}'))
def _assert_box_done(order, n):
    from backend.models.mes_models import PackagingFlowRun
    db = _new_session()
    norm = order.replace("-", "").upper()
    row = db.query(PackagingFlowRun).filter_by(order_no=norm).first()
    assert row is not None, f"工单 {norm} 无落库记录"
    assert row.box_done == n, f"box_done={row.box_done} 期望 {n}"


@then(parsers.parse('应触发 "{kind}" 报警'))
def _assert_alarm(ctx, kind):
    assert kind in ctx["alarms"], f"未触发 {kind}, 实际={ctx['alarms']}"


@then(parsers.parse('不应触发 "{kind}" 报警'))
def _assert_no_alarm(ctx, kind):
    assert kind not in ctx["alarms"], f"不该触发 {kind}, 实际={ctx['alarms']}"


@then("工单状态应为等放工单收尾")
def _assert_awaiting_paper(ctx):
    state = _coord().get_state(ctx["config_id"])
    assert state is not None, "无进行中工单"
    assert state.get("status") == "awaiting_paper", f"status={state.get('status')}"


@then(parsers.parse('工单 "{order}" 不应有进行中记录'))
def _assert_no_run(ctx, order):
    assert _coord().get_state(ctx["config_id"]) is None


@then(parsers.parse('工单 "{order}" 落库运行记录应只有 {n:d} 条'))
def _assert_run_count(order, n):
    from backend.models.mes_models import PackagingFlowRun
    db = _new_session()
    norm = order.replace("-", "").upper()
    cnt = db.query(PackagingFlowRun).filter_by(order_no=norm).count()
    assert cnt == n, f"运行记录 {cnt} 条, 期望 {n} (重扫拦截应阻止新记录)"
