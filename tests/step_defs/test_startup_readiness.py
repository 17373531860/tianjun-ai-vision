"""冷启动就绪探针 + 加深门槛开关 BDD step 实现 (v3.23.x)。

覆盖两个新端点:
  1. GET /api/v1/system/startup-ready —— 深度就绪探针 (匿名可达, 真查 DB+项目)。
  2. GET/PUT /api/v1/workstations/startup-ready-gate —— 加深就绪门槛开关读写 + 默认值。

这两个端点支撑 Electron "加深启动就绪门槛" 可选项, 以及前端冷启动重试的对照面。
"""
from __future__ import annotations

from pytest_bdd import scenarios, when, then, parsers


scenarios("../features/startup_readiness.feature")


def _to_bool(s: str) -> bool:
    return str(s).strip().lower() in ("true", "1", "yes", "on")


# ============================================================
# 深度就绪探针
# ============================================================
@when("我 GET /api/v1/system/startup-ready")
def when_get_startup_ready(ctx, client):
    ctx["resp"] = client.get("/api/v1/system/startup-ready")


@then("探针返回 ready=true")
def then_probe_ready(ctx):
    body = ctx["resp"].json()
    assert body.get("ready") is True, f"ready={body.get('ready')}, body={body}"


@then("探针返回的 projects 是非负整数")
def then_probe_projects(ctx):
    body = ctx["resp"].json()
    n = body.get("projects")
    assert isinstance(n, int) and n >= 0, f"projects={n!r} 不是非负整数"


# ============================================================
# 加深就绪门槛开关
# ============================================================
@when("我 GET /api/v1/workstations/startup-ready-gate")
def when_get_gate(ctx, client):
    ctx["resp"] = client.get("/api/v1/workstations/startup-ready-gate")


@when(parsers.parse("我 PUT /api/v1/workstations/startup-ready-gate 字段 enabled={val}"))
def when_put_gate(ctx, client, val):
    ctx["resp"] = client.put(
        "/api/v1/workstations/startup-ready-gate",
        json={"enabled": _to_bool(val)},
    )


@then(parsers.parse("门槛返回 enabled={val}"))
def then_inline_gate(ctx, val):
    body = ctx["resp"].json()
    assert body.get("enabled") is _to_bool(val), f"enabled={body.get('enabled')}"


@then(parsers.parse("GET /api/v1/workstations/startup-ready-gate 应返回 enabled={val}"))
def then_get_gate(ctx, client, val):
    resp = client.get("/api/v1/workstations/startup-ready-gate")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("enabled") is _to_bool(val), f"enabled={body.get('enabled')}"


# ============================================================
# 通用
# ============================================================
@then(parsers.parse("就绪响应状态应为 {code:d}"))
def then_status(ctx, code):
    assert ctx["resp"].status_code == code, \
        f"期望 {code}, got {ctx['resp'].status_code}, body={ctx['resp'].text[:300]}"
