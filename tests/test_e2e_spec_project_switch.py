# ==================== 规格→项目 自动切换 端到端功能测试 ====================
# 覆盖共享匹配器的两个消费方, 证明口径一致 (这是本次重构的核心):
#   ① MES 入站: 虚拟MES 经真实 ASGI 栈 POST /api/v1/mes/inbound/task → 切项目落库
#   ② 上银包装: 协调器 _real_project_activator(spec, cfg) → 同一匹配器 → 切项目落库
# 另含一条护栏: 协调器可正常 import (上一会话曾把切项目函数改出缩进语法错, 整模块 import 失败)。
from backend.db.database import SessionLocal
from backend.models.models import Project
from backend.services.mes_inbound import get_mes_inbound

_PROJ_KW = dict(
    task_type="detection", logic_mode="sequential",
    pipeline_config={}, steps_config=[{"id": 1, "label": "s", "name": "S", "enabled": True}],
    events_config=[], counters_config=[], alarm_config={}, detection_config={}, data_config={},
)


def _mk(name):
    db = SessionLocal()
    try:
        p = Project(name=name, is_active=False, **_PROJ_KW)
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


def _active_name():
    db = SessionLocal()
    try:
        p = db.query(Project).filter(Project.is_active == True).first()  # noqa: E712
        return p.name if p else None
    finally:
        db.close()


def _is_active(pid):
    db = SessionLocal()
    try:
        p = db.query(Project).filter(Project.id == pid).first()
        return bool(p and p.is_active)
    finally:
        db.close()


def _set_inbound(**over):
    db = SessionLocal()
    try:
        cfg = {"enabled": True, "switch_project_on_task": True,
               "match_project_by_name": False, "name_match_strict_boundary": False,
               "product_project_map": {}}
        cfg.update(over)
        get_mes_inbound().save_config(db, cfg)
        db.commit()
    finally:
        db.close()


# ---------- 消费方①: MES 入站, 真实 HTTP/ASGI 全栈 ----------
def test_e2e_mes_inbound_wildcard_switch(client):
    pid = _mk("E2EWILD")
    _set_inbound(product_project_map={"E2EWILD-*": pid})
    r = client.post("/api/v1/mes/inbound/task",
                    json={"TaskNo": "T1", "ProductCode": "E2EWILD-20260628"})
    assert r.status_code in (200, 201)
    assert _active_name() == "E2EWILD"


def test_e2e_mes_inbound_name_substring_switch(client):
    _mk("E2ESUB")
    _set_inbound(match_project_by_name=True)
    r = client.post("/api/v1/mes/inbound/task",
                    json={"TaskNo": "T2", "ProductCode": "E2ESUB-99"})
    assert r.status_code in (200, 201)
    assert _active_name() == "E2ESUB"


def test_e2e_mes_inbound_default_off_no_switch(client):
    pid = _mk("E2EINOFF")
    _set_inbound(match_project_by_name=False)  # 默认关 + 对照表无此规格
    client.post("/api/v1/mes/inbound/task",
                json={"TaskNo": "T3", "ProductCode": "E2EINOFF-1"})
    assert not _is_active(pid), "按名匹配默认关时不应切到同名项目"


# ---------- 消费方②: 上银包装协调器, 真实 DB 激活 ----------
def test_e2e_packaging_name_substring_switch():
    from backend.services.packaging_flow_coordinator import _real_project_activator
    _mk("E2EPKG")
    ok = _real_project_activator("E2EPKG-7", {
        "spec_to_project": {}, "match_project_by_name": True,
        "name_match_strict_boundary": False})
    assert ok is True
    assert _active_name() == "E2EPKG"


def test_e2e_packaging_wildcard_switch():
    from backend.services.packaging_flow_coordinator import _real_project_activator
    pid = _mk("E2EPKGW")
    ok = _real_project_activator("L-E2EPKGW-R", {
        "spec_to_project": {"*E2EPKGW*": pid}, "match_project_by_name": False})
    assert ok is True
    assert _active_name() == "E2EPKGW"


def test_e2e_packaging_default_off_no_switch():
    from backend.services.packaging_flow_coordinator import _real_project_activator
    pid = _mk("E2EPKGOFF")
    ok = _real_project_activator("E2EPKGOFF-1", {
        "spec_to_project": {}, "match_project_by_name": False})
    assert ok is False
    assert not _is_active(pid)


# ---------- 护栏: 协调器可正常 import (防缩进语法错复活) ----------
def test_e2e_coordinator_module_imports():
    from backend.services import packaging_flow_coordinator as pfc
    assert hasattr(pfc, "_real_project_activator")
