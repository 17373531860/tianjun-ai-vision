"""v3.14 RFC 11 串行流水线 BDD 集成测试.

走端到端真实 REST + Coordinator, 验证 4 个核心场景:
  1. TimeWindowTrigger demo - 三工位全 OK 合格 (福建金龙现场场景)
  2. short_circuit_on_ng - 任一工位 NG 立即结束
  3. enabled 状态下禁止删除
  4. 与工位组通道互斥

step 通过 client (session-level TestClient) 直接调 /api/v1/workpiece-flows API,
通过 get_coordinator() 直接驱动 cycle 事件 (避开 VSM 真实推理).
"""
from __future__ import annotations

import os
import time
import pytest
from pytest_bdd import scenarios, given, when, then, parsers

scenarios("../features/workpiece_flow_v314.feature")


# ============================================================
# 测试上下文 (跨 step 共享状态)
# ============================================================
@pytest.fixture
def ctx():
    """每个 scenario 一个干净的 context."""
    return {
        "flow_id": None,
        "flow_name": None,
        "last_response": None,
        "stations": [],
        "created_group_id": None,
    }


def _coord():
    from backend.services.workpiece_flow_coordinator import get_coordinator
    return get_coordinator()


def _new_session():
    """每次拿一个新的 SessionLocal — 保证落库可见."""
    from backend.db.database import SessionLocal
    return SessionLocal()


def _cleanup_all_flows(client):
    """清空全部已有 workpiece_flow_configs (跨 scenario 隔离)."""
    try:
        resp = client.get("/api/v1/workpiece-flows")
        for f in (resp.json().get("items") or []):
            if f.get("enabled"):
                client.put(f"/api/v1/workpiece-flows/{f['id']}", json={"enabled": False})
            client.delete(f"/api/v1/workpiece-flows/{f['id']}")
    except Exception:
        pass


def _cleanup_all_groups(client):
    """清空全部已有 channel_groups (跨 scenario 隔离)."""
    try:
        resp = client.get("/api/v1/channel-groups")
        items = resp.json().get("items") if isinstance(resp.json(), dict) else resp.json()
        for g in (items or []):
            client.delete(f"/api/v1/channel-groups/{g['id']}")
    except Exception:
        pass


# ============================================================
# Background
# ============================================================
@given("后端处于测试模式 (RUNTIME_MODE=test)")
def _bg_test_mode():
    assert os.environ.get("RUNTIME_MODE") == "test"


@given("当前工位已无任何 in-flight 工件")
def _bg_clean(client):
    _cleanup_all_flows(client)
    _cleanup_all_groups(client)
    # 清空 Coordinator 单例状态防止跨 scenario 污染
    coord = _coord()
    try:
        coord.cleanup_for_testing()
    except Exception:
        pass


# ============================================================
# 创建 + 启用
# ============================================================
@given(parsers.parse('我创建名为 "{name}" 的流水线, 工位顺序 "{stations_csv}", 时间窗 FIFO 模式'))
def _create_flow_time_window(client, ctx, name, stations_csv):
    stations = [int(s) for s in stations_csv.split(",") if s.strip()]
    payload = {
        "name": name,
        "station_channel_ids": stations,
        "trigger_mode": "time_window",
        "fifo_max_in_flight": 3,
        "cycle_to_cycle_window_ms": 15000,
        "short_circuit_on_ng": True,
        "workpiece_timeout_ms": 60000,
        "timeout_action": "force_ng",
        "settle_strategy": "all_ok_required",
        "enabled": False,
    }
    resp = client.post("/api/v1/workpiece-flows", json=payload)
    assert resp.status_code in (200, 201), f"创建失败: {resp.status_code} {resp.text}"
    data = resp.json()
    ctx["flow_id"] = data["id"]
    ctx["flow_name"] = name
    ctx["stations"] = stations


@given("我启用该流水线")
def _enable_flow(client, ctx):
    resp = client.put(f"/api/v1/workpiece-flows/{ctx['flow_id']}", json={"enabled": True})
    assert resp.status_code == 200, f"启用失败: {resp.status_code} {resp.text}"


# ============================================================
# Cycle 事件驱动
# ============================================================
@when(parsers.parse("工位 {ch:d} 开始一个 cycle 序号 {cycle_id:d}"))
def _cycle_started(ch, cycle_id):
    db = _new_session()
    try:
        _coord().on_cycle_started(channel_id=ch, cycle_id=cycle_id, db=db)
    finally:
        db.close()


@when(parsers.parse("工位 {ch:d} 结算 cycle {cycle_id:d} OK"))
def _cycle_settled_ok(ch, cycle_id):
    db = _new_session()
    try:
        _coord().on_cycle_settled(channel_id=ch, cycle_id=cycle_id, is_good=True, db=db)
    finally:
        db.close()


@when(parsers.parse("工位 {ch:d} 结算 cycle {cycle_id:d} NG"))
def _cycle_settled_ng(ch, cycle_id):
    db = _new_session()
    try:
        _coord().on_cycle_settled(channel_id=ch, cycle_id=cycle_id, is_good=False, db=db)
    finally:
        db.close()


# ============================================================
# 状态断言
# ============================================================
@then(parsers.parse("该流水线的 in-flight 工件数应该为 {n:d}"))
def _check_in_flight(ctx, n):
    in_flight = _coord().list_in_flight(ctx["flow_id"])
    assert len(in_flight) == n, f"in-flight={len(in_flight)} 期望={n}"


@then("该流水线的最近一次 run 应该是 COMPLETED_OK")
def _check_last_run_ok(client, ctx):
    resp = client.get(f"/api/v1/workpiece-flows/{ctx['flow_id']}/runs", params={"limit": 5})
    assert resp.status_code == 200, f"查询 runs 失败: {resp.text}"
    items = resp.json().get("items") or []
    assert len(items) >= 1, "应至少有 1 条 run 记录"
    last = items[0]
    assert last["status"] == "completed", f"run.status={last['status']} 期望=completed"
    assert last["final_result"] == "OK", f"final_result={last['final_result']} 期望=OK"


@then("该流水线的最近一次 run 应该是 SHORT_CIRCUITED")
def _check_last_run_short(client, ctx):
    resp = client.get(f"/api/v1/workpiece-flows/{ctx['flow_id']}/runs", params={"limit": 5})
    assert resp.status_code == 200
    items = resp.json().get("items") or []
    assert len(items) >= 1
    last = items[0]
    assert last["status"] == "short_circuited", f"run.status={last['status']} 期望=short_circuited"
    assert last["final_result"] == "NG"


# ============================================================
# 删除前必须禁用
# ============================================================
@when("我尝试通过 API 删除该流水线")
def _try_delete_enabled(client, ctx):
    ctx["last_response"] = client.delete(f"/api/v1/workpiece-flows/{ctx['flow_id']}")


@then(parsers.parse("应该收到 {code:d} 错误响应"))
def _check_error_code(ctx, code):
    assert ctx["last_response"] is not None
    assert ctx["last_response"].status_code == code, (
        f"{ctx['last_response'].status_code} {ctx['last_response'].text}"
    )


# ============================================================
# 工位组互斥
# ============================================================
@given(parsers.parse('已存在一个工位组占用通道 "{channels_csv}"'))
def _create_channel_group(client, ctx, channels_csv):
    channels = [int(c) for c in channels_csv.split(",") if c.strip()]
    # 用一个唯一 name 防 unique 冲突
    name = f"wfc-bdd-group-{int(time.time() * 1000)}"
    payload = {
        "name": name,
        "member_channel_ids": channels,
        "logic_mode": "all_pass",
        "enabled": True,
    }
    resp = client.post("/api/v1/channel-groups", json=payload)
    assert resp.status_code in (200, 201), f"工位组创建失败: {resp.status_code} {resp.text}"
    ctx["created_group_id"] = resp.json()["id"]


@when(parsers.parse('我尝试创建工位顺序为 "{stations_csv}" 的流水线'))
def _try_create_conflict_flow(client, ctx, stations_csv):
    stations = [int(s) for s in stations_csv.split(",") if s.strip()]
    payload = {
        "name": f"wfc-bdd-conflict-{int(time.time() * 1000)}",
        "station_channel_ids": stations,
        "trigger_mode": "time_window",
        "short_circuit_on_ng": True,
        "enabled": True,  # 启用状态才会触发与 channel_group 互斥检查
    }
    ctx["last_response"] = client.post("/api/v1/workpiece-flows", json=payload)


@then(parsers.parse('应该收到 {code:d} 错误响应并携带提示 "{token}"'))
def _check_error_with_token(ctx, code, token):
    assert ctx["last_response"].status_code == code, (
        f"{ctx['last_response'].status_code} {ctx['last_response'].text}"
    )
    body = ctx["last_response"].text
    assert token in body, f'返回 body 应包含 "{token}", 实际={body[:200]}'
