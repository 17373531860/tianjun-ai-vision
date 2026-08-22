"""检测主页自定义布局 (v3.54) BDD step 实现

面向功能视角：产线组长按形态保存/恢复检测主页排版，
后端 SystemConfig KV 落库（升级/备份不丢），坏数据拒收护住主页渲染。
细粒度校验矩阵见单测 tests/test_monitor_layout_api.py。
"""
from __future__ import annotations

from pytest_bdd import scenarios, given, when, then, parsers


scenarios("../features/monitor_layout.feature")

BASE = "/api/v1/system/monitor-layouts"


def _layout(video_rect: dict) -> dict:
    return {"version": 1, "snap": True, "slots": {"video": video_rect}}


# ============================================================
# Givens
# ============================================================
@given(parsers.parse('形态 "{fk1}" 与 "{fk2}" 均已保存自定义布局'))
def given_two_layouts(client, fk1, fk2):
    for fk in (fk1, fk2):
        resp = client.put(f"{BASE}/{fk}", json=_layout(
            {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}))
        assert resp.status_code == 200, resp.text


# ============================================================
# Whens
# ============================================================
@when(parsers.parse('我保存形态 "{fk}" 的布局且视频区块位于 x={x:g} y={y:g}'))
def when_put_layout(ctx, client, fk, x, y):
    ctx["resp"] = client.put(f"{BASE}/{fk}", json=_layout(
        {"x": x, "y": y, "w": 0.5, "h": 0.4}))


@when(parsers.parse('我保存形态 "{fk}" 的无版本号布局'))
def when_put_layout_no_version(ctx, client, fk):
    ctx["resp"] = client.put(f"{BASE}/{fk}", json={
        "slots": {"video": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}}})


@when(parsers.parse('我用非法形态键 "{fk}" 保存布局'))
def when_put_layout_bad_key(ctx, client, fk):
    ctx["resp"] = client.put(f"{BASE}/{fk}", json=_layout(
        {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}))


@when(parsers.parse('我保存形态 "{fk}" 的布局且视频区块宽高均为 0'))
def when_put_layout_zero_size(ctx, client, fk):
    ctx["resp"] = client.put(f"{BASE}/{fk}", json=_layout(
        {"x": 0.2, "y": 0.2, "w": 0, "h": 0}))


@when(parsers.parse('我保存形态 "{fk}" 的布局且区块 "{slot}" 位于 x={x:g} y={y:g}'))
def when_put_layout_named_slot(ctx, client, fk, slot, x, y):
    ctx["resp"] = client.put(f"{BASE}/{fk}", json={
        "version": 1, "snap": True,
        "slots": {slot: {"x": x, "y": y, "w": 0.96, "h": 0.22}},
    })


@when(parsers.parse('我删除形态 "{fk}" 的布局'))
def when_delete_layout(ctx, client, fk):
    ctx["resp"] = client.delete(f"{BASE}/{fk}")


@when("我删除全部自定义布局")
def when_delete_all_layouts(ctx, client):
    ctx["resp"] = client.delete(BASE)


# ============================================================
# Thens
# ============================================================
@then(parsers.parse("布局响应状态应为 {code:d}"))
def then_layout_status(ctx, code):
    assert ctx["resp"].status_code == code, \
        f"期望 {code}, got {ctx['resp'].status_code}, body={ctx['resp'].text[:300]}"


def _get_layouts(client) -> dict:
    resp = client.get(BASE)
    assert resp.status_code == 200, resp.text
    return resp.json().get("layouts", {})


@then(parsers.parse('读取全部布局应包含形态 "{fk}" 且视频区块 x={x:g}'))
def then_layout_contains_video_x(client, fk, x):
    layouts = _get_layouts(client)
    assert fk in layouts, f"缺形态 {fk}, 现有 {list(layouts)}"
    assert abs(layouts[fk]["slots"]["video"]["x"] - x) < 1e-6, \
        f"video.x={layouts[fk]['slots']['video']['x']}"


@then(parsers.parse('读取全部布局应包含形态 "{fk}" 且区块 "{slot}" x={x:g}'))
def then_layout_contains_slot_x(client, fk, slot, x):
    layouts = _get_layouts(client)
    assert fk in layouts, f"缺形态 {fk}, 现有 {list(layouts)}"
    slots = layouts[fk].get("slots") or {}
    assert slot in slots, f"缺槽位 {slot}, 现有 {list(slots)}"
    assert abs(slots[slot]["x"] - x) < 1e-6, f"{slot}.x={slots[slot]['x']}"


@then(parsers.parse('读回形态 "{fk}" 的视频区块坐标应全部落在 0 到 1 之间'))
def then_layout_video_clamped(client, fk):
    rect = _get_layouts(client)[fk]["slots"]["video"]
    for f in ("x", "y", "w", "h"):
        assert 0.0 <= rect[f] <= 1.0, f"{f}={rect[f]} 越界"


@then(parsers.parse('读回形态 "{fk}" 的视频区块宽高应不小于 {mn:g}'))
def then_layout_video_min_size(client, fk, mn):
    rect = _get_layouts(client)[fk]["slots"]["video"]
    assert rect["w"] >= mn and rect["h"] >= mn, \
        f"w={rect['w']} h={rect['h']} 低于最小尺寸 {mn}"


@then(parsers.parse('读取全部布局应不包含形态 "{fk}"'))
def then_layouts_not_contains(client, fk):
    layouts = _get_layouts(client)
    assert fk not in layouts, f"不应存在 {fk}: {layouts.get(fk)}"


@then(parsers.parse('读取全部布局应包含形态 "{fk1}" 且不包含形态 "{fk2}"'))
def then_layouts_contains_and_not(client, fk1, fk2):
    layouts = _get_layouts(client)
    assert fk1 in layouts, f"缺形态 {fk1}, 现有 {list(layouts)}"
    assert fk2 not in layouts, f"不应存在 {fk2}"


@then("读取全部布局应为空")
def then_layouts_empty(client):
    layouts = _get_layouts(client)
    assert layouts == {}, f"应为空, 实际 {list(layouts)}"
