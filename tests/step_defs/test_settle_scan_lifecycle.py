# -*- coding: utf-8 -*-
"""v3.50 齐件即结算 + 扫码器生命周期 (捷昌二期) BDD step 实现。

面向功能的验收口径 (与 feature 文件一一对应):
  A: 项目配置面 — tracking_settle_on_complete 开关 + 步骤级
     settle_confirm_frames 经 /projects API 落库读回不失真
  B: 设备配置面 — scanner_devices 三个新字段 (resume_on /
     rearm_forget_last / strict_ok_dedup) 默认值 = 现状, 显式配置读回不失真
  B: 运行时 — ok_only NG 灭灯阻塞 → POST /scanner/resume 人工出口;
     cycle_end NG 照常恢复 (现状零差异)
"""
from __future__ import annotations

import uuid

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

scenarios("../features/settle_scan_lifecycle.feature")


@given("后端处于测试模式")
def given_test_mode():
    import os
    assert os.environ.get("RUNTIME_MODE") == "test"


# ============================================================
# A: 齐件即结算 — 项目配置面
# ============================================================

def _create_tracking_project(client, ctx, with_settle: bool):
    name = f"bdd_soc_{uuid.uuid4().hex[:8]}"
    pipeline = {
        "logic_mode": "tracking",
        "tracking_cycle_strategy": "roi_exit",
    }
    steps = [{
        "id": 1, "label": "螺丝", "displayLabel": "螺丝", "enabled": True,
        "threshold": 50, "count_mode": "track", "expected_count": 2,
    }]
    if with_settle:
        pipeline["tracking_settle_on_complete"] = True
        steps[0]["settle_confirm_frames"] = 5
    r = client.post("/api/v1/projects", json={
        "name": name,
        "pipeline_config": pipeline,
        "steps_config": steps,
    })
    assert r.status_code in (200, 201), r.text[:300]
    ctx["proj_id"] = r.json()["id"]


@when("我创建一个 ROI离开 跟踪项目并开启齐件即结算, 螺丝步骤确认帧数设为 5")
def when_create_soc_project(client, ctx):
    _create_tracking_project(client, ctx, with_settle=True)


@when("我创建一个 ROI离开 跟踪项目且不带齐件即结算配置")
def when_create_plain_project(client, ctx):
    _create_tracking_project(client, ctx, with_settle=False)


def _read_project(client, ctx):
    r = client.get(f"/api/v1/projects/{ctx['proj_id']}")
    assert r.status_code == 200, r.text[:300]
    return r.json()


@then("读回项目 pipeline_config.tracking_settle_on_complete 应为 True")
def then_soc_on(client, ctx):
    pc = _read_project(client, ctx).get("pipeline_config") or {}
    assert pc.get("tracking_settle_on_complete") is True, pc


@then("读回项目螺丝步骤的 settle_confirm_frames 应为 5")
def then_confirm_frames(client, ctx):
    steps = _read_project(client, ctx).get("steps_config") or []
    step = next((s for s in steps if s.get("label") == "螺丝"), None)
    assert step is not None, steps
    assert int(step.get("settle_confirm_frames") or 0) == 5, step


@then("读回项目 pipeline_config.tracking_settle_on_complete 不应为 True")
def then_soc_off(client, ctx):
    pc = _read_project(client, ctx).get("pipeline_config") or {}
    assert pc.get("tracking_settle_on_complete") is not True, pc


# ============================================================
# B: 扫码器生命周期 — 设备配置面
# ============================================================

def _create_scanner(client, ctx, extra: dict):
    body = {
        "name": f"bdd_枪_{uuid.uuid4().hex[:6]}",
        # 每台随机 IP 避开"同 IP+port 去重"拦截
        "ip": f"127.9.{uuid.uuid4().int % 250 + 1}.{uuid.uuid4().int % 250 + 1}",
        "port": 55256,
        "device_type": "text_lon",
        "scan_mode": "once_per_cycle",
        "enabled": False,  # 不真连
    }
    body.update(extra)
    r = client.post("/api/v1/scanner/devices", json=body)
    assert r.status_code in (200, 201), r.text[:300]
    ctx["dev"] = r.json().get("device") or r.json()


@when("我 POST 一台最小配置的 LON 扫码器")
def when_create_minimal_scanner(client, ctx):
    _create_scanner(client, ctx, extra={})


@when("我 POST 一台 resume_on=ok_only 且开作废旧码和强制去重的 LON 扫码器")
def when_create_lifecycle_scanner(client, ctx):
    _create_scanner(client, ctx, extra={
        "resume_on": "ok_only",
        "rearm_forget_last": True,
        "strict_ok_dedup": True,
    })


def _read_device(client, ctx):
    dev_id = ctx["dev"]["id"]
    r = client.get("/api/v1/scanner/devices")
    assert r.status_code == 200, r.text[:300]
    dev = next((d for d in r.json() if d.get("id") == dev_id), None)
    assert dev is not None, f"设备 {dev_id} 应在列表里"
    return dev


@then(parsers.parse('读回设备 resume_on 应为 "{expected}"'))
def then_resume_on(client, ctx, expected):
    assert _read_device(client, ctx)["resume_on"] == expected


@then(parsers.parse("读回设备 rearm_forget_last 应为 {expected}"))
def then_rearm(client, ctx, expected):
    assert _read_device(client, ctx)["rearm_forget_last"] is (expected == "True")


@then(parsers.parse("读回设备 strict_ok_dedup 应为 {expected}"))
def then_strict(client, ctx, expected):
    assert _read_device(client, ctx)["strict_ok_dedup"] is (expected == "True")


# ============================================================
# B: 扫码器生命周期 — 运行时 (全局单例 ScannerService 上挂假连接)
# ============================================================

_BDD_CONN_ID = 99050


@pytest.fixture
def _runtime_conn_cleanup():
    yield
    from backend.services.scanner import get_scanner_service
    get_scanner_service()._connections.pop(_BDD_CONN_ID, None)


@given(parsers.parse(
    "工位 0 挂着一台 resume_on={resume_on} 且等待周期恢复的扫码器连接"))
def given_runtime_conn(ctx, _runtime_conn_cleanup, resume_on):
    from backend.services.scanner import get_scanner_service, ScannerConnection
    svc = get_scanner_service()
    conn = ScannerConnection(
        device_id=_BDD_CONN_ID, name="BDD枪", ip="127.0.0.1", port=55256,
        channel_id=0, enabled=True, device_type="text_lon",
        scan_mode="once_per_cycle", resume_on=resume_on)
    conn._wait_cycle_resume = True
    svc._connections[_BDD_CONN_ID] = conn
    ctx["svc"] = svc
    ctx["conn"] = conn


@when("周期以 NG 结算触发扫码器恢复")
def when_ng_settle(ctx):
    ctx["resumed"] = ctx["svc"].resume_after_cycle(0, is_good=False)


@when("我 POST /api/v1/scanner/resume?channel_id=0")
def when_post_resume(client, ctx):
    ctx["resp"] = client.post("/api/v1/scanner/resume",
                              params={"channel_id": 0})


@then("该扫码器应处于恢复阻塞状态")
def then_conn_blocked(ctx):
    conn = ctx["conn"]
    assert conn._wait_cycle_resume is True, "NG 后必须保持灭灯"
    assert conn._resume_blocked is True


@then("该扫码器不应处于恢复阻塞状态")
def then_conn_not_blocked(ctx):
    conn = ctx["conn"]
    assert conn._wait_cycle_resume is False, "cycle_end 模式 NG 也应照常恢复"
    assert conn._resume_blocked is False


@then("工位 0 的 is_resume_blocked 应为 True")
def then_blocked_true(ctx):
    assert ctx["svc"].is_resume_blocked(0) is True


@then("工位 0 的 is_resume_blocked 应为 False")
def then_blocked_false(ctx):
    assert ctx["svc"].is_resume_blocked(0) is False


@then(parsers.parse("响应状态应为 {code:d}"))
def then_status(ctx, code):
    resp = ctx["resp"]
    assert resp.status_code == code, \
        f"期望 {code} 实际 {resp.status_code} body={resp.text[:200]}"


@then("恢复响应 resumed 列表应包含该扫码器")
def then_resumed_contains(ctx):
    data = ctx["resp"].json()
    assert data["success"] is True
    assert "BDD枪" in (data.get("resumed") or []), data


@then("恢复响应 resumed 列表应为空")
def then_resumed_empty(ctx):
    data = ctx["resp"].json()
    assert data["success"] is True
    assert data.get("resumed") == [], data
