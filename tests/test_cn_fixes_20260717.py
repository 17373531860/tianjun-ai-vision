"""v3.40 川南现场两修复的回归测试 (2026-07-17).

修复 1 (mes_inbound._refresh_reused_order):
  同任务号重复开工走"复用工单"分支时, 工单的项目/工位绑定与四要素留痕
  必须按本次开工报文刷新 — 否则绑定过期的复用单永远挂不上工位,
  监控页四要素不显示、出站推送工单字段全 null。

修复 2 (mes_gateway push_on_result 嵌套结果):
  cycle_end 事件的结果在 cycle.result (嵌套), 老代码只读顶层
  overall_result/result → 周期事件"仅 NG"过滤从未生效, 合格周期
  照样从报警连接推出去 (WarningText="顺序正确完成")。
"""
import pytest

from backend.db.database import SessionLocal
from backend.models.models import Project
from backend.models.mes_models import WorkOrder
from backend.services.mes_inbound import MESInbound
from backend.services.mes_gateway import MESGateway


PREFIX = "CN717-"


# ==================== 公共 fixture ====================
@pytest.fixture
def cleanup():
    """清掉本组测试建的工单/项目 + 复位激活态。"""
    def _wipe():
        db = SessionLocal()
        try:
            db.query(WorkOrder).filter(WorkOrder.order_no.like(f"{PREFIX}%")).delete(
                synchronize_session=False)
            db.query(Project).filter(Project.name.like(f"__cn717_%")).delete(
                synchronize_session=False)
            db.query(Project).update({Project.is_active: False})
            db.commit()
        finally:
            db.close()
    _wipe()
    yield
    _wipe()


def _mk_project(db, name, active=False):
    p = Project(name=name, is_active=active)
    db.add(p)
    db.flush()
    return p


def _cfg(**over):
    base = {"enabled": True, "create_work_order_on_task": True}
    base.update(over)
    return MESInbound._with_defaults(base)


def _get_order(task_no):
    db = SessionLocal()
    try:
        return db.query(WorkOrder).filter(WorkOrder.order_no == task_no).first()
    finally:
        db.close()


# ==================== 修复 1: 复用工单刷新绑定 ====================
def test_reused_order_rebinds_to_current_active_project(cleanup):
    """核心复现: 单子建在项目 A 上, 切到项目 B 后同任务号再开工 → 单子必须改绑 B。"""
    svc = MESInbound()
    task = f"{PREFIX}rebind"
    db = SessionLocal()
    try:
        pa = _mk_project(db, "__cn717_A", active=True)
        pb = _mk_project(db, "__cn717_B", active=False)
        db.commit()

        # 第一次开工: 建单, 绑项目 A
        res = svc.handle_task_start(
            db, {"TaskNo": task, "ProductCode": "rocket"}, _cfg())
        db.commit()
        assert res["ok"] is True
        order = db.query(WorkOrder).filter(WorkOrder.order_no == task).first()
        assert order.project_id == pa.id

        # 现场等价操作: 激活项目切到 B (模拟"按产品切项目"或手工切换)
        db.query(Project).update({Project.is_active: False})
        pb2 = db.query(Project).filter(Project.id == pb.id).first()
        pb2.is_active = True
        db.commit()

        # 第二次开工同任务号 → 复用分支, 老 bug: project_id 停留在 A
        res = svc.handle_task_start(
            db, {"TaskNo": task, "ProductCode": "rocket"}, _cfg())
        db.commit()
        assert res["ok"] is True
        db.expire_all()
        order = db.query(WorkOrder).filter(WorkOrder.order_no == task).first()
        assert order.project_id == pb.id, \
            "复用工单必须重绑当前激活项目, 否则工位挂单匹配永远落空"
        assert order.binding_scope == "project"
    finally:
        db.close()


def test_reused_order_refreshes_four_elements(cleanup):
    """同任务号第二次开工换了操作员/工步 → 四要素留痕必须是最新报文的值。"""
    svc = MESInbound()
    task = f"{PREFIX}fresh"
    db = SessionLocal()
    try:
        _mk_project(db, "__cn717_A", active=True)
        db.commit()
        svc.handle_task_start(
            db, {"TaskNo": task, "ProductCode": "rocket",
                 "StepCode": "1.1", "Operator": "wyf"}, _cfg())
        db.commit()
        svc.handle_task_start(
            db, {"TaskNo": task, "ProductCode": "rocket",
                 "StepCode": "2.5", "Operator": "zhang"}, _cfg())
        db.commit()
        db.expire_all()
        order = db.query(WorkOrder).filter(WorkOrder.order_no == task).first()
        inbound = (order.extra_data or {}).get("inbound") or {}
        assert inbound.get("step_code") == "2.5"
        assert inbound.get("operator") == "zhang"
        assert order.created_by == "zhang"
    finally:
        db.close()


def test_reused_order_channel_binding_refreshed(cleanup):
    """工位路由模式下复用 → 重绑本次报文的工位号。"""
    svc = MESInbound()
    task = f"{PREFIX}chan"
    cfg = _cfg(order_binding="channel", channel_field="channel")
    db = SessionLocal()
    try:
        svc.handle_task_start(
            db, {"TaskNo": task, "ProductCode": "p", "Channel": "0"}, cfg)
        db.commit()
        svc.handle_task_start(
            db, {"TaskNo": task, "ProductCode": "p", "Channel": "2"}, cfg)
        db.commit()
        db.expire_all()
        order = db.query(WorkOrder).filter(WorkOrder.order_no == task).first()
        assert order.binding_scope == "channels"
        assert order.target_channels == [2]
    finally:
        db.close()


def test_reused_order_no_active_project_keeps_old_binding(cleanup):
    """项目路由但当前无激活项目 → 保留旧绑定 (不清空, 不崩)。"""
    svc = MESInbound()
    task = f"{PREFIX}noact"
    db = SessionLocal()
    try:
        pa = _mk_project(db, "__cn717_A", active=True)
        db.commit()
        svc.handle_task_start(db, {"TaskNo": task, "ProductCode": "p"}, _cfg())
        db.commit()
        db.query(Project).update({Project.is_active: False})
        db.commit()
        svc.handle_task_start(db, {"TaskNo": task, "ProductCode": "p"}, _cfg())
        db.commit()
        db.expire_all()
        order = db.query(WorkOrder).filter(WorkOrder.order_no == task).first()
        assert order.project_id == pa.id
    finally:
        db.close()


# ==================== 修复 2: 周期事件按结果过滤 ====================
class _RecordingAdapter:
    def __init__(self):
        self.last_context = None

    def build_payload(self, full_context, config):
        self.last_context = full_context
        return full_context

    def send(self, payload, config):
        return {"status_code": 200, "body": {}, "duration_ms": 1}

    def check_response(self, result, config):
        return True


class _FakeConn:
    def __init__(self, config):
        self.id = 1
        self.name = "cn717-conn"
        self.adapter_type = "rest"
        self.config = config
        self.push_events = ["cycle_end"]
        self.bound_channels = None
        self.retry_count = 0
        self.retry_interval_sec = 1
        self.last_sync_at = None


class _FakeDB:
    def add(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


def _push_cycle(monkeypatch, conn_config, context):
    adapter = _RecordingAdapter()
    monkeypatch.setattr("backend.services.mes_gateway.get_adapter", lambda t: adapter)
    sent = MESGateway()._send_to_connection(
        _FakeDB(), _FakeConn(conn_config), "cycle_end", context, 0)
    return sent, adapter.last_context


def test_ng_filter_blocks_ok_cycle(monkeypatch):
    """客户现场复现: 报警连接勾了"仅 NG", 合格周期 (cycle.result=OK 嵌套) 必须被拦。"""
    sent, ctx = _push_cycle(
        monkeypatch, {"push_on_result": ["NG"]},
        {"cycle": {"result": "OK", "event_name": "顺序正确完成"}})
    assert sent is False
    assert ctx is None, "仅 NG 过滤下, 合格周期不允许推出去"


def test_ng_filter_passes_ng_cycle(monkeypatch):
    """NG 周期照常推送, 不被误拦。"""
    sent, ctx = _push_cycle(
        monkeypatch, {"push_on_result": ["NG"]},
        {"cycle": {"result": "NG", "ng_reason": "缺少 B"}})
    assert sent is True
    assert ctx is not None


def test_no_filter_pushes_both(monkeypatch):
    """不配过滤 → OK/NG 全推 (历史默认行为不变)。"""
    for r in ("OK", "NG"):
        sent, ctx = _push_cycle(monkeypatch, {}, {"cycle": {"result": r}})
        assert sent is True and ctx is not None


def test_top_level_result_still_works(monkeypatch):
    """box_complete 风格顶层 overall_result 的老路径不回归。"""
    sent, ctx = _push_cycle(
        monkeypatch, {"push_on_result": ["NG"]},
        {"overall_result": "OK", "cycle": {}})
    assert sent is False
    sent, ctx = _push_cycle(
        monkeypatch, {"push_on_result": ["NG"]},
        {"overall_result": "NG", "cycle": {}})
    assert sent is True
