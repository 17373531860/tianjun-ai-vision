"""M4 开工任务 → 工单生命周期接入 测试 (带真实 DB)。"""
import pytest

from backend.db.database import SessionLocal
from backend.models.models import Project
from backend.models.mes_models import WorkOrder
from backend.services.mes_inbound import MESInbound


def _seed_project_id():
    db = SessionLocal()
    try:
        p = db.query(Project).filter(Project.name == "__bdd_seed_project__").first()
        return p.id if p else None
    finally:
        db.close()


@pytest.fixture
def cleanup():
    """清掉本组测试建的工单 (order_no 以 M4- 开头) + 复位激活态。"""
    def _wipe():
        db = SessionLocal()
        try:
            db.query(WorkOrder).filter(WorkOrder.order_no.like("M4-%")).delete(
                synchronize_session=False)
            db.query(Project).update({Project.is_active: False})
            db.commit()
        finally:
            db.close()
    _wipe()
    yield
    _wipe()


def _cfg(**over):
    base = {"enabled": True}
    base.update(over)
    return MESInbound._with_defaults(base)


def _get_order(task_no):
    db = SessionLocal()
    try:
        return db.query(WorkOrder).filter(WorkOrder.order_no == task_no).first()
    finally:
        db.close()


def test_default_off_no_order_created(cleanup):
    svc = MESInbound()
    db = SessionLocal()
    try:
        res = svc.handle_task_start(
            db, {"TaskNo": "M4-NOOP", "ProductCode": "P1"}, _cfg())
        db.commit()
        assert res["ok"] is True
    finally:
        db.close()
    assert _get_order("M4-NOOP") is None


def test_create_work_order_in_progress(cleanup):
    svc = MESInbound()
    db = SessionLocal()
    try:
        res = svc.handle_task_start(
            db, {"TaskNo": "M4-T1", "ProductCode": "PC9", "Operator": "王五"},
            _cfg(create_work_order_on_task=True))
        db.commit()
        assert res["ok"] is True
    finally:
        db.close()
    order = _get_order("M4-T1")
    assert order is not None
    assert order.status == "in_progress"
    assert order.product_code == "PC9"
    assert order.source == "external"


def test_create_work_order_idempotent(cleanup):
    svc = MESInbound()
    cfg = _cfg(create_work_order_on_task=True)
    for _ in range(2):
        db = SessionLocal()
        try:
            svc.handle_task_start(db, {"TaskNo": "M4-T2", "ProductCode": "P1"}, cfg)
            db.commit()
        finally:
            db.close()
    db = SessionLocal()
    try:
        rows = db.query(WorkOrder).filter(WorkOrder.order_no == "M4-T2").all()
        assert len(rows) == 1
        assert rows[0].status == "in_progress"
    finally:
        db.close()


def test_complete_signal_closes_order(cleanup):
    svc = MESInbound()
    create_cfg = _cfg(create_work_order_on_task=True)
    complete_cfg = _cfg(complete_field="is_complete")
    # 先开工建单
    db = SessionLocal()
    try:
        svc.handle_task_start(db, {"TaskNo": "M4-T3", "ProductCode": "P1"}, create_cfg)
        db.commit()
    finally:
        db.close()
    assert _get_order("M4-T3").status == "in_progress"
    # 再发完工信号
    db = SessionLocal()
    try:
        res = svc.handle_task_start(
            db, {"TaskNo": "M4-T3", "ProductCode": "P1", "IsComplete": True}, complete_cfg)
        db.commit()
        assert res["ok"] is True
    finally:
        db.close()
    assert _get_order("M4-T3").status == "completed"


def test_complete_signal_unknown_order_passes(cleanup):
    svc = MESInbound()
    db = SessionLocal()
    try:
        res = svc.handle_task_start(
            db, {"TaskNo": "M4-NOPE", "ProductCode": "P1", "IsComplete": "true"},
            _cfg(complete_field="is_complete"))
        db.commit()
        assert res["ok"] is True  # 工单不存在静默放行
    finally:
        db.close()


def test_mapped_extra_persisted(cleanup):
    """开工建工单时, 操作员/开工时间等映射字段落进 extra_data['inbound'] 留痕。"""
    svc = MESInbound()
    db = SessionLocal()
    try:
        svc.handle_task_start(db, {
            "TaskNo": "M4-EX", "ProductCode": "PCX",
            "Operator": "王五", "BeginTime": "2026-06-24 08:00",
        }, _cfg(create_work_order_on_task=True))
        db.commit()
    finally:
        db.close()
    order = _get_order("M4-EX")
    assert order is not None
    inbound = (order.extra_data or {}).get("inbound") or {}
    assert inbound.get("operator") == "王五"
    assert inbound.get("begin_time") == "2026-06-24 08:00"


def test_mapped_extra_off(cleanup):
    svc = MESInbound()
    db = SessionLocal()
    try:
        svc.handle_task_start(db, {"TaskNo": "M4-EX2", "ProductCode": "P", "Operator": "x"},
                              _cfg(create_work_order_on_task=True, store_mapped_extra=False))
        db.commit()
    finally:
        db.close()
    order = _get_order("M4-EX2")
    assert order is not None
    assert (order.extra_data or {}).get("inbound") is None


def test_supersede_completes_previous_task(cleanup):
    """最新开工为准: B 开工顶替 A → A 完成, B 进行中。"""
    svc = MESInbound()
    cfg = _cfg(create_work_order_on_task=True, supersede_previous_task=True,
               report_complete_on_supersede=False)
    for t in ("M4-A", "M4-B"):
        db = SessionLocal()
        try:
            svc.handle_task_start(db, {"TaskNo": t, "ProductCode": "P1"}, cfg)
            db.commit()
        finally:
            db.close()
    assert _get_order("M4-A").status == "completed"
    assert _get_order("M4-B").status == "in_progress"


def test_supersede_off_keeps_both(cleanup):
    svc = MESInbound()
    cfg = _cfg(create_work_order_on_task=True, supersede_previous_task=False)
    for t in ("M4-C", "M4-D"):
        db = SessionLocal()
        try:
            svc.handle_task_start(db, {"TaskNo": t, "ProductCode": "P1"}, cfg)
            db.commit()
        finally:
            db.close()
    assert _get_order("M4-C").status == "in_progress"
    assert _get_order("M4-D").status == "in_progress"


def test_supersede_reports_complete(cleanup, monkeypatch):
    """顶替时对被顶替工单回传完工出站事件。"""
    calls = []

    class _FakeGW:
        def dispatch(self, event_type, context, channel_id=None):
            calls.append((event_type, context))

    monkeypatch.setattr("backend.services.mes_gateway.get_mes_gateway", lambda: _FakeGW())
    svc = MESInbound()
    cfg = _cfg(create_work_order_on_task=True, supersede_previous_task=True,
               report_complete_on_supersede=True, complete_event_type="task_complete")
    for t in ("M4-E", "M4-F"):
        db = SessionLocal()
        try:
            svc.handle_task_start(db, {"TaskNo": t, "ProductCode": "P1"}, cfg)
            db.commit()
        finally:
            db.close()
    assert _get_order("M4-E").status == "completed"
    assert any(ev == "task_complete" and c["order"]["order_no"] == "M4-E"
               for ev, c in calls)


def test_order_binding_channel(cleanup):
    """工位路由: 工单绑到报文里的工位号 (binding_scope=channels)。"""
    svc = MESInbound()
    cfg = _cfg(create_work_order_on_task=True, order_binding="channel", channel_field="channel")
    db = SessionLocal()
    try:
        svc.handle_task_start(db, {"TaskNo": "M4-CHB", "ProductCode": "P1", "Channel": "2"}, cfg)
        db.commit()
    finally:
        db.close()
    o = _get_order("M4-CHB")
    assert o is not None
    assert o.binding_scope == "channels"
    assert o.target_channels == [2]
    assert o.status == "in_progress"


def test_supersede_same_channel(cleanup):
    """同工位顶替: ch1 新开工只顶替 ch1 旧单, 不动 ch2。"""
    svc = MESInbound()
    cfg = _cfg(create_work_order_on_task=True, order_binding="channel", channel_field="channel",
               supersede_previous_task=True, supersede_scope="same_channel",
               report_complete_on_supersede=False)
    for t, ch in [("M4-S1", "1"), ("M4-S2", "2"), ("M4-S3", "1")]:
        db = SessionLocal()
        try:
            svc.handle_task_start(db, {"TaskNo": t, "ProductCode": "P", "Channel": ch}, cfg)
            db.commit()
        finally:
            db.close()
    assert _get_order("M4-S1").status == "completed"      # ch1 旧单被顶
    assert _get_order("M4-S2").status == "in_progress"    # ch2 不受影响
    assert _get_order("M4-S3").status == "in_progress"    # ch1 新单


def test_reject_duplicate_task(cleanup):
    """开启拒绝重复: 同任务号第二次开工回 duplicate, 不重复处理。"""
    svc = MESInbound()
    cfg = _cfg(create_work_order_on_task=True, reject_duplicate_task=True)
    db = SessionLocal()
    try:
        r1 = svc.handle_task_start(db, {"TaskNo": "M4-DUP", "ProductCode": "P1"}, cfg)
        db.commit()
        assert r1["ok"] is True
    finally:
        db.close()
    db = SessionLocal()
    try:
        r2 = svc.handle_task_start(db, {"TaskNo": "M4-DUP", "ProductCode": "P1"}, cfg)
        db.commit()
        assert r2["ok"] is False
        assert r2["code_key"] == "duplicate"
    finally:
        db.close()


def test_reject_duplicate_off_is_idempotent(cleanup):
    svc = MESInbound()
    cfg = _cfg(create_work_order_on_task=True, reject_duplicate_task=False)
    for _ in range(2):
        db = SessionLocal()
        try:
            r = svc.handle_task_start(db, {"TaskNo": "M4-DUP2", "ProductCode": "P1"}, cfg)
            db.commit()
            assert r["ok"] is True
        finally:
            db.close()


def test_complete_match_by_product_code(cleanup):
    """完工报文只带产品码 (无任务号) 也能按产品码找到最新在产单收工。"""
    svc = MESInbound()
    cfg = _cfg(create_work_order_on_task=True, complete_field="is_complete",
               complete_match_column="product_code", complete_match_field="product_code",
               required_fields=["product_code"])
    # 开工建单 (产品码 PZ)
    db = SessionLocal()
    try:
        svc.handle_task_start(db, {"TaskNo": "M4-PZ", "ProductCode": "PZ"}, cfg)
        db.commit()
    finally:
        db.close()
    assert _get_order("M4-PZ").status == "in_progress"
    # 完工: 只带产品码 + 完工标志, 无任务号
    db = SessionLocal()
    try:
        svc.handle_task_start(db, {"ProductCode": "PZ", "IsComplete": "1"}, cfg)
        db.commit()
    finally:
        db.close()
    assert _get_order("M4-PZ").status == "completed"


def _make_channel_order(order_no):
    """直接建一张工位绑定的外部在产工单 (用于验证顶替范围过滤)。"""
    from backend.services.work_order import WorkOrderService
    svc = WorkOrderService()
    db = SessionLocal()
    try:
        o = svc.create_order(db, {
            "order_no": order_no, "product_name": "X", "source": "external",
            "status": "pending", "binding_scope": "channels", "target_channels": [0],
        })
        svc.change_status(db, o.id, "in_progress")
        db.commit()
    finally:
        db.close()


def test_supersede_scope_project_skips_channel_order(cleanup):
    """scope=project (默认): 工位绑定的外部在产单不被顶替。"""
    _make_channel_order("M4-CH1")
    svc = MESInbound()
    db = SessionLocal()
    try:
        svc.handle_task_start(db, {"TaskNo": "M4-PJ1", "ProductCode": "P1"},
                              _cfg(create_work_order_on_task=True,
                                   supersede_previous_task=True,
                                   report_complete_on_supersede=False,
                                   supersede_scope="project"))
        db.commit()
    finally:
        db.close()
    assert _get_order("M4-CH1").status == "in_progress"   # 工位单幸存
    assert _get_order("M4-PJ1").status == "in_progress"


def test_supersede_scope_external_catches_channel_order(cleanup):
    """scope=external: 连工位绑定的外部在产单也一并顶替。"""
    _make_channel_order("M4-CH2")
    svc = MESInbound()
    db = SessionLocal()
    try:
        svc.handle_task_start(db, {"TaskNo": "M4-PJ2", "ProductCode": "P1"},
                              _cfg(create_work_order_on_task=True,
                                   supersede_previous_task=True,
                                   report_complete_on_supersede=False,
                                   supersede_scope="external"))
        db.commit()
    finally:
        db.close()
    assert _get_order("M4-CH2").status == "completed"     # 工位单被顶替
    assert _get_order("M4-PJ2").status == "in_progress"


def test_switch_project_and_create_order_combined(cleanup):
    pid = _seed_project_id()
    svc = MESInbound()
    cfg = _cfg(switch_project_on_task=True, create_work_order_on_task=True,
               product_project_map={"PC-COMBO": pid})
    db = SessionLocal()
    try:
        res = svc.handle_task_start(
            db, {"TaskNo": "M4-COMBO", "ProductCode": "PC-COMBO"}, cfg)
        db.commit()
        assert res["ok"] is True
    finally:
        db.close()
    # 项目被激活 + 工单 in_progress 且绑到该项目
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.id == pid).first()
        assert proj.is_active is True
        order = db.query(WorkOrder).filter(WorkOrder.order_no == "M4-COMBO").first()
        assert order.status == "in_progress"
        assert order.project_id == pid
    finally:
        db.close()
