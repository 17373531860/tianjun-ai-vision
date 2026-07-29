# -*- coding: utf-8 -*-
"""包装工单镜像进工单管理 (v3.45.1) — 同步链路护栏.

现场诉求 (2026-07-28): 扫码开工的工单只存包装运行记录, 工单管理页看不见、
没处清理。本组用例固化四条契约:
  1. 扫码开工 → work_orders 出现镜像单 (source=packaging, 生产中)
  2. 工单完成 → 镜像单推"已完成" + 箱口径数量回填
  3. 工单作废 → 镜像单推"已取消"
  4. 开关关 / 同号已存在 → 不建新单 (同号只刷状态, 原 source 保留)
"""
import pytest

from backend.services.packaging_flow_coordinator import get_coordinator
from backend.db.database import SessionLocal
from backend.models.mes_models import (
    PackagingFlowConfig,
    PackagingFlowRun,
    WorkOrder,
)


@pytest.fixture(autouse=True)
def _clean(client):
    def _wipe():
        db = SessionLocal()
        db.query(PackagingFlowRun).delete()
        db.query(PackagingFlowConfig).delete()
        db.query(WorkOrder).filter(
            WorkOrder.order_no.like("__wo_sync_%")).delete(
            synchronize_session=False)
        db.commit()
        db.close()

    coord = get_coordinator()
    coord.cleanup_for_testing()
    _wipe()
    yield
    coord.cleanup_for_testing()
    _wipe()


def _setup_flow(client, **cfg_over):
    payload = {
        "name": "__test_pkg_wo_sync",
        "enabled": True,
        "channel_id": 0,
        "trays_per_box_fixed": 2,
        "on_mes_fail": "offline",
    }
    payload.update(cfg_over)
    r = client.post("/api/v1/packaging-flows", json=payload)
    assert r.status_code == 201, r.text
    coord = get_coordinator()
    coord.reload_configs(SessionLocal())
    return coord, r.json()["id"]


def _wo(db, order_no):
    return db.query(WorkOrder).filter(WorkOrder.order_no == order_no).first()


def test_scan_creates_mirror_in_progress(client):
    """扫码开工 → 工单表出现镜像单: 来源=packaging, 状态=生产中, 计划箱数回填."""
    coord, _ = _setup_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2, "spec": "HGH15CA"})
    db = SessionLocal()

    coord.on_scan("__wo_sync_A", db, channel_id=0)

    wo = _wo(db, "__wo_sync_A")
    assert wo is not None, "扫码开工应在工单表建镜像单"
    assert wo.source == "packaging"
    assert wo.status == "in_progress"
    assert wo.planned_qty == 2
    assert wo.actual_start is not None
    assert (wo.extra_data or {}).get("packaging", {}).get("qty_unit") == "box"


def test_complete_pushes_mirror_completed_with_counts(client):
    """做满收尾 → 镜像单推'已完成', completed/good/ng 按箱口径回填."""
    coord, _ = _setup_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    db = SessionLocal()

    coord.on_scan("__wo_sync_B", db, channel_id=0)
    coord.on_scan("__wo_sync_B", db, channel_id=0)   # 开箱1
    coord.on_cycle_settled(0, 1, True, db)
    coord.on_cycle_settled(0, 2, True, db)           # 箱1 满
    coord.on_scan("__wo_sync_B", db, channel_id=0)   # 结算箱1 + 开箱2
    coord.on_cycle_settled(0, 3, True, db)
    coord.on_cycle_settled(0, 4, True, db)           # 箱2 满
    coord.on_scan("__wo_sync_B2", db, channel_id=0)  # 换单 → 完成 B

    db.expire_all()
    wo = _wo(db, "__wo_sync_B")
    assert wo.status == "completed"
    assert wo.completed_qty == 2 and wo.good_qty == 2 and wo.ng_qty == 0
    assert wo.actual_end is not None
    assert (wo.extra_data or {}).get("packaging", {}).get("final_result") == "OK"
    # 换到的新单也应有镜像 (生产中)
    wo2 = _wo(db, "__wo_sync_B2")
    assert wo2 is not None and wo2.status == "in_progress"


def test_abort_pushes_mirror_cancelled(client):
    """漏箱 void 作废 → 镜像单推'已取消'."""
    coord, _ = _setup_flow(client, on_short_box="void")
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 3})
    coord.set_alarm_sink(lambda c, k, m: None)
    db = SessionLocal()

    coord.on_scan("__wo_sync_C", db, channel_id=0)
    coord.on_scan("__wo_sync_C", db, channel_id=0)
    coord.on_cycle_settled(0, 1, True, db)
    coord.on_cycle_settled(0, 2, True, db)
    coord.on_scan("__wo_sync_C2", db, channel_id=0)  # 漏箱 → void 作废

    db.expire_all()
    wo = _wo(db, "__wo_sync_C")
    assert wo.status == "cancelled"
    assert wo.actual_end is not None


def test_switch_off_no_mirror(client):
    """开关关 → 老行为, 工单表不出现镜像单."""
    coord, _ = _setup_flow(client, sync_work_orders=False)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    db = SessionLocal()

    coord.on_scan("__wo_sync_D", db, channel_id=0)

    assert _wo(db, "__wo_sync_D") is None
    # 包装运行记录不受影响 (原有链路零差异)
    run = db.query(PackagingFlowRun).filter_by(order_no="__wo_sync_D").first()
    assert run is not None


def test_existing_order_updated_not_recreated(client):
    """同号单已存在 (如外部 MES 推送) → 不重建, 只刷状态数量, source 保留."""
    db = SessionLocal()
    db.add(WorkOrder(order_no="__wo_sync_E", product_name="外部单",
                     planned_qty=0, status="pending", source="external"))
    db.commit()

    coord, _ = _setup_flow(client)
    coord.set_mes_fetcher(lambda c, o: {"dispatch_qty": 2})
    coord.on_scan("__wo_sync_E", db, channel_id=0)

    db.expire_all()
    rows = db.query(WorkOrder).filter(
        WorkOrder.order_no == "__wo_sync_E").all()
    assert len(rows) == 1, "同号不应重建第二行"
    assert rows[0].source == "external", "原 source 应保留"
    assert rows[0].status == "in_progress"
    assert rows[0].planned_qty == 2
