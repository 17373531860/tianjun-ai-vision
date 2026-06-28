# ==================== 外部MES按规格自动切项目 BDD ====================
# 虚拟MES = TestClient 直推真实入站路由 /api/v1/mes/inbound/task,
# 走真实 handle_task_start → _switch_project → resolve_project_id_by_spec →
# activate_project_core → 落库 is_active。验证三级匹配(对照表精确/通配符/同名子串)
# + 默认关零差异 + 严格边界, 端到端在真实 ASGI 栈上跑通。
import json

from pytest_bdd import scenarios, given, when, then, parsers

from backend.db.database import SessionLocal
from backend.models.models import Project
from backend.services.mes_inbound import get_mes_inbound

scenarios("../features/spec_project_switch.feature")

_REQUIRED_PROJECT_KWARGS = dict(
    task_type="detection",
    logic_mode="sequential",
    pipeline_config={},
    steps_config=[{"id": 1, "label": "s", "name": "S", "enabled": True}],
    events_config=[],
    counters_config=[],
    alarm_config={},
    detection_config={},
    data_config={},
)


# ---------- 背景 ----------
@given("重置入站对接配置")
def _reset_inbound(ctx):
    ctx["names"] = {}
    db = SessionLocal()
    try:
        get_mes_inbound().save_config(db, {"enabled": False, "switch_project_on_task": False})
        db.commit()
    finally:
        db.close()


# ---------- 前置: 建项目 ----------
@given(parsers.parse('已有检测项目 "{name}"'))
def _make_project(ctx, name):
    db = SessionLocal()
    try:
        p = Project(name=name, is_active=False, **_REQUIRED_PROJECT_KWARGS)
        db.add(p)
        db.commit()
        db.refresh(p)
        ctx.setdefault("names", {})[name] = p.id
    finally:
        db.close()


# ---------- 前置: 配置入站 ----------
@given(parsers.re(
    r'入站开启切项目 按名匹配(?P<name_match>开|关) 严格边界(?P<strict>开|关) 映射 (?P<mapping>.+)'))
def _config_inbound(ctx, name_match, strict, mapping):
    raw = json.loads(mapping)
    # 映射值是项目名 → 解析成项目 id (对照表存的是 id)
    spec_to_id = {}
    for spec, proj_name in raw.items():
        spec_to_id[spec] = ctx["names"][proj_name]
    db = SessionLocal()
    try:
        get_mes_inbound().save_config(db, {
            "enabled": True,
            "switch_project_on_task": True,
            "match_project_by_name": name_match == "开",
            "name_match_strict_boundary": strict == "开",
            "product_project_map": spec_to_id,
        })
        db.commit()
    finally:
        db.close()


# ---------- 动作: 虚拟MES推送 ----------
@when(parsers.parse('虚拟MES推送产品码 "{code}"'))
def _push(ctx, client, code):
    resp = client.post("/api/v1/mes/inbound/task",
                       json={"TaskNo": f"T-{code}", "ProductCode": code})
    ctx["resp_status"] = resp.status_code


# ---------- 断言 ----------
def _active_project_name():
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


@then(parsers.parse('当前激活项目应为 "{name}"'))
def _assert_active(ctx, name):
    assert _active_project_name() == name, \
        f"期望激活 {name}, 实际激活 {_active_project_name()!r}"


@then(parsers.parse('项目 "{name}" 不应被激活'))
def _assert_not_active(ctx, name):
    pid = ctx["names"][name]
    assert not _is_active(pid), f"项目 {name} 不应被激活, 但它被激活了"
