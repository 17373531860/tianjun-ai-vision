"""视频源连接与切换 BDD step 实现。"""
from __future__ import annotations

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from ._synthetic_helpers import (
    start_synthetic,
    stop_synthetic,
    synthetic_state,
)


scenarios("../features/source_connection.feature")


@given("后端处于测试模式 (RUNTIME_MODE=test)")
def given_test_mode_source():
    import os
    assert os.environ.get("RUNTIME_MODE") == "test"


@given(parsers.parse('我用剧本 "{scenario}" 启动 synthetic 源'))
def given_synthetic_running(client, scenario):
    r = start_synthetic(client, scenario=scenario)
    assert r.status_code == 200, r.text[:300]


@when(parsers.parse('我用剧本 "{scenario}" 启动 synthetic 源'))
def when_start_with_scenario(client, ctx, scenario):
    ctx["resp"] = start_synthetic(client, scenario=scenario)


@when(parsers.parse('我再次用剧本 "{scenario}" 启动 synthetic 源'))
def when_start_again(client, ctx, scenario):
    ctx["resp"] = start_synthetic(client, scenario=scenario)
    ctx["last_scenario_name"] = scenario.replace(".json", "")


@when("我用内联 JSON 启动 synthetic 源")
def when_start_inline(client, ctx):
    spec = {
        "name": "inline-adhoc",
        "fps": 60,
        "timeline": [
            {
                "from": 0,
                "to": 60,
                "detections": [
                    {"label": "X", "confidence": 0.9, "bbox": [0.2, 0.2, 0.2, 0.2]}
                ],
            }
        ],
    }
    ctx["resp"] = start_synthetic(client, scenario_dict=spec)
    ctx["last_scenario_name"] = "inline-adhoc"


@when("我停止 synthetic 源")
def when_stop_synth(client, ctx):
    ctx["resp"] = stop_synthetic(client)


@then("GET /api/v1/test/synthetic/state 的 frame_seq 应该 >= 0")
def then_state_seq(client):
    r = synthetic_state(client)
    assert r.status_code == 200
    seq = r.json().get("frame_seq", 0)
    assert seq >= 0, f"frame_seq={seq} 不合预期"


@then(parsers.parse('synthetic 调试信息里的剧本名应为 "{name}"'))
def then_debug_name(client, name):
    r = synthetic_state(client)
    body = r.json() if r.status_code == 200 else {}
    debug_name = body.get("scenario_name") or body.get("name") or ""
    assert debug_name == name or name in debug_name, \
        f"期望剧本名 {name}, 实际={debug_name}, body={body}"


@then(parsers.parse("响应状态应为 {code:d}"))
def then_status_simple(ctx, code):
    resp = ctx["resp"]
    assert resp.status_code == code, f"期望 {code} 实际 {resp.status_code} body={resp.text[:200]}"


@then("GET /api/v1/test/synthetic/state 应返回 200")
def then_state_200(client):
    r = synthetic_state(client)
    assert r.status_code == 200
