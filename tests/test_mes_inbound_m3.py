"""M3 入站按产品代号切检测项目 测试 (带真实 DB 激活)。"""
import pytest

from backend.db.database import SessionLocal
from backend.models.models import Project
from backend.services.mes_inbound import MESInbound


def _seed_project_id():
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.name == "__bdd_seed_project__").first()
        return proj.id if proj else None
    finally:
        db.close()


@pytest.fixture
def restore_active():
    """测试结束把所有项目激活态复位为 False, 避免污染其他测试。"""
    yield
    db = SessionLocal()
    try:
        db.query(Project).update({Project.is_active: False})
        db.commit()
    finally:
        db.close()


def _cfg(**over):
    # 默认开"切项目" (这组测试就是验切项目路径; 全局默认已改为关)
    base = {"enabled": True, "switch_project_on_task": True}
    base.update(over)
    return MESInbound._with_defaults(base)


def test_switch_off_passes_through():
    svc = MESInbound()
    ok, key, _ = svc._apply_task_action(None, {"product_code": "ANY"},
                                        _cfg(switch_project_on_task=False))
    assert ok is True and key is None


def test_unknown_product_not_in_map():
    svc = MESInbound()
    db = SessionLocal()
    try:
        ok, key, _ = svc._apply_task_action(
            db, {"product_code": "NOPE"},
            _cfg(product_project_map={"P1": 99999}))
        assert ok is False and key == "unknown_product"
    finally:
        db.close()


def test_missing_product_code_when_switch_on():
    svc = MESInbound()
    db = SessionLocal()
    try:
        ok, key, _ = svc._apply_task_action(db, {}, _cfg(product_project_map={"P1": 1}))
        assert ok is False and key == "unknown_product"
    finally:
        db.close()


def test_mapped_to_nonexistent_project():
    svc = MESInbound()
    db = SessionLocal()
    try:
        ok, key, _ = svc._apply_task_action(
            db, {"product_code": "PX"},
            _cfg(product_project_map={"PX": 987654}))
        assert ok is False and key == "unknown_product"
    finally:
        db.close()


def test_mapped_to_real_project_activates(restore_active):
    pid = _seed_project_id()
    assert pid is not None
    svc = MESInbound()
    db = SessionLocal()
    try:
        ok, key, msg = svc._apply_task_action(
            db, {"product_code": "P-OK"},
            _cfg(product_project_map={"P-OK": pid}))
        assert ok is True, msg
    finally:
        db.close()
    # 验证落库激活态
    db2 = SessionLocal()
    try:
        proj = db2.query(Project).filter(Project.id == pid).first()
        assert proj.is_active is True
    finally:
        db2.close()


def test_already_active_skips_reload(restore_active):
    pid = _seed_project_id()
    svc = MESInbound()
    cfg = _cfg(product_project_map={"P-OK": pid})
    db = SessionLocal()
    try:
        svc._apply_task_action(db, {"product_code": "P-OK"}, cfg)  # 首次激活
        ok, key, msg = svc._apply_task_action(db, {"product_code": "P-OK"}, cfg)  # 再次
        assert ok is True
        assert "已激活" in msg
    finally:
        db.close()


def test_end_to_end_inbound_switches_project(restore_active):
    """完整入站: handle_task_start → 切项目 → 成功响应码。"""
    pid = _seed_project_id()
    svc = MESInbound()
    cfg = _cfg(product_project_map={"P-OK": pid})
    db = SessionLocal()
    try:
        res = svc.handle_task_start(
            db, {"TaskNo": "T-M3", "ProductCode": "P-OK"}, cfg)
        assert res["ok"] is True
        assert res["response"]["code"] == 0
    finally:
        db.close()
