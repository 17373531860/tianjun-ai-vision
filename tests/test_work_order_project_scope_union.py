"""Task D：按项目筛工单时仍应包含项目无关的工位/集群工单。"""
import uuid

import pytest

from backend.models.mes_models import WorkOrder
from backend.models.models import Project
from backend.services.work_order import WorkOrderService


@pytest.fixture(autouse=True)
def _runtime_work_order_hotfix():
    """成品不替换产品源码；测试真实运行时重绑并在结束后还原。"""
    from backend import hotfix

    original = WorkOrderService.list_orders
    hotfix._WORK_ORDER_PATCHED = False
    hotfix._patch_work_order_scope()
    try:
        yield
    finally:
        WorkOrderService.list_orders = original
        hotfix._WORK_ORDER_PATCHED = False


def _project(name):
    return Project(
        name=name,
        task_type="detection",
        logic_mode="sequential",
        pipeline_config={},
        steps_config=[],
        events_config=[],
        counters_config=[],
        alarm_config={},
        detection_config={},
        data_config={},
        is_active=False,
    )


def _order(order_no, *, scope, project_id=None, status="draft"):
    return WorkOrder(
        order_no=order_no,
        product_name="Task D 工单",
        planned_qty=1,
        status=status,
        binding_scope=scope,
        project_id=project_id,
        target_channels=[0, 1] if scope == "channels" else None,
        target_stations=["main"] if scope == "cluster" else None,
    )


def test_project_filter_unions_channel_and_cluster_scopes_with_correct_total(db_session):
    marker = f"__task_d_{uuid.uuid4().hex[:10]}"
    wanted_project = _project(f"{marker}_wanted")
    other_project = _project(f"{marker}_other")
    db_session.add_all([wanted_project, other_project])
    db_session.flush()

    expected_nos = {
        f"{marker}_project",
        f"{marker}_channels",
        f"{marker}_cluster",
    }
    db_session.add_all([
        _order(f"{marker}_project", scope="project", project_id=wanted_project.id),
        _order(f"{marker}_channels", scope="channels"),
        _order(f"{marker}_cluster", scope="cluster"),
        _order(f"{marker}_other_project", scope="project", project_id=other_project.id),
        _order(f"{marker}_unbound", scope="project", project_id=None),
        _order(f"{marker}_completed_channels", scope="channels", status="completed"),
    ])
    db_session.flush()

    service = WorkOrderService()
    items, total = service.list_orders(
        db_session,
        project_id=wanted_project.id,
        keyword=marker,
        status="draft",
        date_from="2000-01-01",
        date_to="2100-01-01",
        skip=0,
        limit=20,
    )

    assert total == 3
    assert {item.order_no for item in items} == expected_nos

    first_page, first_total = service.list_orders(
        db_session,
        project_id=wanted_project.id,
        keyword=marker,
        status="draft",
        skip=0,
        limit=2,
    )
    second_page, second_total = service.list_orders(
        db_session,
        project_id=wanted_project.id,
        keyword=marker,
        status="draft",
        skip=2,
        limit=2,
    )

    assert first_total == second_total == 3
    assert len(first_page) == 2
    assert len(second_page) == 1
    assert {item.order_no for item in first_page + second_page} == expected_nos
