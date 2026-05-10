"""会话标识 + session_end 自动导出 BDD step 实现 (v3.6.2)。"""
from __future__ import annotations

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from ._synthetic_helpers import start_synthetic, stop_detection, stop_synthetic


scenarios("../features/session_naming.feature")


# ============================================================
# Background givens (复用)
# ============================================================
@given("后端测试模式已就绪")
def given_test_mode_ready():
    import os
    assert os.environ.get("RUNTIME_MODE") == "test", \
        "conftest 应该设置 RUNTIME_MODE=test"


@given("已停止任何残留检测")
def given_clean_state(client):
    stop_detection(client, channel=0)
    stop_synthetic(client, channel=0)


@pytest.fixture
def naming_ctx():
    return {}


# ============================================================
# 启动检测 + session_name
# ============================================================
def _ensure_synthetic_running(client):
    """让通道 0 有 synthetic 源 + 最小项目，detection/start 才能走完不报"未加载模型"。"""
    r = start_synthetic(
        client, scenario="ok_sequential_cycle.json",
        with_project=True, channel=0,
    )
    return r


@when("客户端用 POST /api/v1/source/detection/start 启动检测但不传 session_name")
def when_start_no_name(client, naming_ctx):
    _ensure_synthetic_running(client)
    naming_ctx["resp"] = client.post(
        "/api/v1/source/detection/start?channel=0",
        json={"conf": 0.25, "iou": 0.45},
    )


@when(parsers.re(r'^客户端用 POST /api/v1/source/detection/start 启动检测并传 session_name "(?P<name>[^"]+)"$'))
def when_start_with_name(client, naming_ctx, name):
    _ensure_synthetic_running(client)
    naming_ctx["resp"] = client.post(
        "/api/v1/source/detection/start?channel=0",
        json={"conf": 0.25, "iou": 0.45, "session_name": name},
    )
    naming_ctx["sent_name"] = name


@then("响应状态应为 200 或 400")
def then_200_or_400(naming_ctx):
    assert naming_ctx["resp"].status_code in (200, 400), \
        f"got {naming_ctx['resp'].status_code} body={naming_ctx['resp'].text[:200]}"


@then("响应状态应为 400")
def then_400(naming_ctx):
    assert naming_ctx["resp"].status_code == 400, \
        f"got {naming_ctx['resp'].status_code} body={naming_ctx['resp'].text[:200]}"


@then("如果创建了会话 它的 name 字段应为 null")
def then_db_name_null(naming_ctx, app):
    if naming_ctx["resp"].status_code != 200:
        return
    sid = naming_ctx["resp"].json().get("session_id")
    if not sid:
        return
    from backend.db.database import SessionLocal
    from backend.models.models import DetectionSession
    db = SessionLocal()
    try:
        s = db.query(DetectionSession).filter(DetectionSession.id == sid).first()
        assert s is not None, f"session_id={sid} 在 DB 找不到"
        assert s.name is None, f"期望 name=None, 实际 {s.name!r}"
    finally:
        db.close()


@then(parsers.re(r'^如果创建了会话 它的 name 字段应为 "(?P<name>[^"]+)"$'))
def then_db_name_equals(naming_ctx, app, name):
    if naming_ctx["resp"].status_code != 200:
        return
    sid = naming_ctx["resp"].json().get("session_id")
    if not sid:
        return
    from backend.db.database import SessionLocal
    from backend.models.models import DetectionSession
    db = SessionLocal()
    try:
        s = db.query(DetectionSession).filter(DetectionSession.id == sid).first()
        assert s is not None
        assert s.name == name, f"期望 name={name!r}, 实际 {s.name!r}"
    finally:
        db.close()


# ============================================================
# 重命名 (PATCH /sessions/{id}/name)
# ============================================================
@given("数据库里至少有一个会话存在")
def given_some_session_exists(app, naming_ctx):
    from backend.db.database import SessionLocal
    from backend.models.models import DetectionSession, Project
    from datetime import datetime
    import uuid as uuid_mod
    db = SessionLocal()
    try:
        s = db.query(DetectionSession).order_by(DetectionSession.id.desc()).first()
        if s is None:
            proj = db.query(Project).first()
            if proj is None:
                proj = Project(name="test_project_for_rename", steps_config=[])
                db.add(proj); db.commit(); db.refresh(proj)
            s = DetectionSession(
                session_uuid=str(uuid_mod.uuid4())[:8],
                project_id=proj.id,
                start_time=datetime.now(),
                status="running",
            )
            db.add(s); db.commit(); db.refresh(s)
        naming_ctx["session_id"] = s.id
    finally:
        db.close()


@when(parsers.re(r'^客户端用 PATCH 给该会话设置标识 "(?P<name>[^"]+)"$'))
def when_patch_set_name(client, naming_ctx, name):
    sid = naming_ctx["session_id"]
    naming_ctx["resp"] = client.patch(
        f"/api/v1/data/sessions/{sid}/name",
        json={"name": name},
    )


@when("客户端用 PATCH 给该会话传 name=null")
def when_patch_null(client, naming_ctx):
    sid = naming_ctx["session_id"]
    naming_ctx["resp"] = client.patch(
        f"/api/v1/data/sessions/{sid}/name",
        json={"name": None},
    )


@when(parsers.re(r'^客户端用 PATCH 给该会话传 name="(?P<name>[^"]+)"$'))
def when_patch_bad_name(client, naming_ctx, name):
    sid = naming_ctx["session_id"]
    naming_ctx["resp"] = client.patch(
        f"/api/v1/data/sessions/{sid}/name",
        json={"name": name},
    )


@then("响应状态应为 200")
def then_200(naming_ctx):
    assert naming_ctx["resp"].status_code == 200, \
        f"got {naming_ctx['resp'].status_code} body={naming_ctx['resp'].text[:200]}"


@then(parsers.re(r'^响应里 name 字段应为 "(?P<name>[^"]+)"$'))
def then_resp_name_equals(naming_ctx, name):
    body = naming_ctx["resp"].json()
    assert body.get("name") == name, f"期望 {name!r}, 实际 {body.get('name')!r}"


@then("响应里 name 字段应为 null")
def then_resp_name_null(naming_ctx):
    body = naming_ctx["resp"].json()
    assert body.get("name") is None, f"期望 None, 实际 {body.get('name')!r}"


# ============================================================
# session_end 实时规则
# ============================================================
@when("客户端用 POST /api/v1/export/realtime-rules 创建一个 trigger_event=session_end 的规则")
def when_create_session_end_rule(client, naming_ctx, app):
    from backend.db.database import SessionLocal
    from backend.models.export_models import ExportTemplate
    db = SessionLocal()
    try:
        tpl = db.query(ExportTemplate).first()
        if tpl is None:
            tpl = ExportTemplate(
                name="bdd_session_end_tpl",
                format="txt",
                content="session_end report id={{ session.id }} name={{ session.name }}\n",
                is_system=False,
            )
            db.add(tpl); db.commit(); db.refresh(tpl)
        tpl_id = tpl.id
    finally:
        db.close()
    naming_ctx["resp"] = client.post(
        "/api/v1/export/realtime-rules",
        json={
            "name": "bdd_session_end_rule",
            "template_id": tpl_id,
            "trigger_event": "session_end",
            "enabled": False,
            "output_dir": "/tmp/bdd_export",
            "filename_template": "{{ session.id }}_{{ session.name or session.session_uuid }}.txt",
        },
    )


@then("响应状态应为 200 或 201")
def then_200_or_201(naming_ctx):
    assert naming_ctx["resp"].status_code in (200, 201), \
        f"got {naming_ctx['resp'].status_code} body={naming_ctx['resp'].text[:200]}"


# ============================================================
# 不存在的 session_id
# ============================================================
@when("客户端用 PATCH /api/v1/data/sessions/999999/name 传任意 name")
def when_patch_nonexistent(client, naming_ctx):
    naming_ctx["resp"] = client.patch(
        "/api/v1/data/sessions/999999/name",
        json={"name": "anything"},
    )


@then("响应状态应为 404")
def then_404(naming_ctx):
    assert naming_ctx["resp"].status_code == 404, \
        f"got {naming_ctx['resp'].status_code} body={naming_ctx['resp'].text[:200]}"


# ============================================================
# v3.6.2 客户反馈: stats.cycles[*].steps + cycle.interval/event 别名
# ============================================================
@when("客户端用 POST /api/v1/export/preview 以 session 范围渲染 jinja 模板")
def when_preview_session_scope(client, naming_ctx):
    sid = naming_ctx["session_id"]
    # 用最简单可渲染的 jinja, 主要是要求 include_context=True 拿到完整 ctx
    naming_ctx["resp"] = client.post(
        "/api/v1/export/preview",
        json={
            "session_id": sid,
            "include_cycles": True,
            "fmt": "txt",
            "template_content": "session.id={{ session.id }}",
            "include_context": True,
        },
    )


def _ctx_cycles(naming_ctx):
    """preview 接口的 context 字段是 JSON 字符串, 解出来返 stats.cycles."""
    import json as _json
    body = naming_ctx["resp"].json()
    raw = body.get("context")
    if isinstance(raw, str):
        ctx = _json.loads(raw)
    elif isinstance(raw, dict):
        ctx = raw
    else:
        return []
    return ((ctx.get("stats") or {}).get("cycles")) or []


@then('响应 context 里 stats.cycles 每条都应含字段 "steps"')
def then_stats_cycles_have_steps(naming_ctx):
    cycles = _ctx_cycles(naming_ctx)
    for i, c in enumerate(cycles):
        assert "steps" in c, f"cycle[{i}] 缺 'steps' 字段, 字段集={sorted(c.keys())}"
        assert isinstance(c["steps"], list), \
            f"cycle[{i}].steps 应为 list, 实际 {type(c['steps']).__name__}"


@then('响应 context 里 stats.cycles 每条都应含字段 "interval"')
def then_stats_cycles_have_interval(naming_ctx):
    cycles = _ctx_cycles(naming_ctx)
    for i, c in enumerate(cycles):
        assert "interval" in c, f"cycle[{i}] 缺 'interval' 别名字段(应等于 interval_to_next)"


@then('响应 context 里 stats.cycles 每条都应含字段 "event"')
def then_stats_cycles_have_event(naming_ctx):
    cycles = _ctx_cycles(naming_ctx)
    for i, c in enumerate(cycles):
        assert "event" in c, f"cycle[{i}] 缺 'event' 别名字段(应等于 event_name)"
