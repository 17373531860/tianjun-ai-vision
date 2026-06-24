"""MES 工单接收「最新开工为准」边界 BDD step 实现。

把客户亲定的边界(同产品同工位只留一条任务 / 最新开工顶替 / 顶替必回传完工)
表达成可执行 gherkin, 服务层驱动, 进 CI 无需起服务。
"""
from __future__ import annotations

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from backend.db.database import SessionLocal
from backend.models.models import Project
from backend.models.mes_models import WorkOrder
from backend.services.mes_inbound import MESInbound


scenarios("../features/mes_inbound_supersede.feature")


def _cfg(**over):
    base = {"enabled": True}
    base.update(over)
    return MESInbound._with_defaults(base)


def _get_order(order_no):
    db = SessionLocal()
    try:
        return db.query(WorkOrder).filter(WorkOrder.order_no == order_no).first()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _wipe_bdd_orders():
    """清掉本组建的工单(BDD- 前缀) + 复位激活态, 前后各一次。"""
    def _wipe():
        db = SessionLocal()
        try:
            db.query(WorkOrder).filter(WorkOrder.order_no.like("BDD-%")).delete(
                synchronize_session=False)
            db.query(Project).update({Project.is_active: False})
            db.commit()
        finally:
            db.close()
    _wipe()
    yield
    _wipe()


# ============================================================
# Given — 装配入站配置
# ============================================================
@given("入站已配置开工建单且最新开工为准")
def given_cfg_supersede(ctx):
    ctx["cfg"] = _cfg(create_work_order_on_task=True,
                      supersede_previous_task=True,
                      report_complete_on_supersede=False)


@given("入站已配置开工建单但不顶替")
def given_cfg_no_supersede(ctx):
    ctx["cfg"] = _cfg(create_work_order_on_task=True,
                      supersede_previous_task=False)


@given("入站已配置开工建单顶替并回传完工")
def given_cfg_supersede_report(ctx, monkeypatch):
    calls = []

    class _FakeGW:
        def dispatch(self, event_type, context, channel_id=None):
            calls.append((event_type, context))

    monkeypatch.setattr("backend.services.mes_gateway.get_mes_gateway",
                        lambda: _FakeGW())
    ctx["gw_calls"] = calls
    ctx["cfg"] = _cfg(create_work_order_on_task=True,
                      supersede_previous_task=True,
                      report_complete_on_supersede=True,
                      complete_event_type="task_complete")


# ============================================================
# When — 上游连发开工
# ============================================================
@when(parsers.parse('上游先后发来开工任务 "{a}" 和 "{b}"'))
def when_two_starts(ctx, a, b):
    svc = MESInbound()
    for t in (a, b):
        db = SessionLocal()
        try:
            svc.handle_task_start(db, {"TaskNo": t, "ProductCode": "P1"}, ctx["cfg"])
            db.commit()
        finally:
            db.close()


# ============================================================
# Then — 校验工单状态 / 回传
# ============================================================
@then(parsers.parse('工单 "{order_no}" 状态应为 {status}'))
def then_order_status(order_no, status):
    o = _get_order(order_no)
    assert o is not None, f"工单 {order_no} 不存在"
    assert o.status == status, f"工单 {order_no} 状态={o.status}, 期望 {status}"


@then(parsers.parse('上游应收到 "{order_no}" 的完工回传'))
def then_complete_dispatched(ctx, order_no):
    calls = ctx.get("gw_calls", [])
    assert any(ev == "task_complete" and c["order"]["order_no"] == order_no
               for ev, c in calls), f"未捕获 {order_no} 的完工回传, calls={calls}"
