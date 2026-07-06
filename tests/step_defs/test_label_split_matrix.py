"""同标签区域拆分组合矩阵 BDD step 实现 (v3.32).

组合覆盖: 无规则零差异 / 固定画面 / 锚点跟随 / 多轮次 / 每轮独立区域 /
违序即时事件(配置=报 + 未配置=零差异只拦)。全部走 synthetic 剧本源
经真实 pipeline (改写层→状态机→结算→计数器), 不 mock 中间环节。

同通道 step_counts / counters 跨 scenario 持久化, 断言一律用相对基线增量。
"""
from __future__ import annotations

import time

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from ._synthetic_helpers import (
    start_synthetic,
    stop_synthetic,
    start_detection,
    stop_detection,
    detection_results,
)


scenarios("../features/label_split_matrix.feature")

CH = 0
FPS = 60

Q1 = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
Q2 = [[0.5, 0.0], [1.0, 0.0], [1.0, 0.5], [0.5, 0.5]]
Q3 = [[0.0, 0.5], [0.5, 0.5], [0.5, 1.0], [0.0, 1.0]]
Q4 = [[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]]
QUAD_CENTERS = [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)]
# 第2轮独立区域: 螺丝1 整体右移 0.25 (翻面后位置偏移)
SHIFTED_Q1 = [[0.25, 0.0], [0.75, 0.0], [0.75, 0.5], [0.25, 0.5]]

# 锚点跟随: 标定时锚点框 vs 运行时整体平移 (+0.1, +0.05), 尺寸不变
ANCHOR_REF = {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6}
ANCHOR_RUNTIME = {"label": "前罩", "confidence": 0.97,
                  "bbox": [0.3, 0.25, 0.6, 0.6]}
SHIFT = (0.1, 0.05)

COVER = {"label": "盖罩", "confidence": 0.96, "bbox": [0.3, 0.3, 0.4, 0.4]}


def _screw(cx, cy):
    return {"label": "打螺丝", "confidence": 0.92,
            "bbox": [cx - 0.05, cy - 0.05, 0.1, 0.1]}


# ============================================================
# 项目配置构造
# ============================================================

def _base_project(name, step_labels, *, strict=False):
    steps = [
        {"id": f"vs-{k}", "label": lbl, "threshold": 0.3, "min_frames": 1,
         "color": "#1976d2", **({"strict_order": True} if strict else {})}
        for k, lbl in enumerate(step_labels)
    ]
    return {
        "project_id": -1, "name": name, "task_type": "detect",
        "logic_mode": "sequential",
        "steps_config": steps,
        "pipeline_config": {
            "sequence_order": [{"step_id": s["id"]} for s in steps],
            "settlement_mode": "first_step",
            "settle_dedup": False,
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [
                {"counter_name": "合格总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [
                {"counter_name": "不良总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1}]},
            {"id": 3, "name": "违序警告", "actions": [
                {"counter_name": "违序次数", "delta": 1}]},
        ],
        "counters_config": [
            {"name": "合格总数", "value": 0}, {"name": "不良总数", "value": 0},
            {"name": "总产量", "value": 0}, {"name": "违序次数", "value": 0},
        ],
    }


def _split_rule(mode="fixed"):
    rule = {
        "id": "ls_bdd", "enabled": True, "source_label": "打螺丝",
        "mode": mode, "unmatched": "drop",
        "regions": [{"name": f"螺丝{i + 1}", "polygon": poly}
                    for i, poly in enumerate([Q1, Q2, Q3, Q4])],
    }
    if mode == "anchor":
        rule["anchor_label"] = "前罩"
        rule["anchor_ref"] = dict(ANCHOR_REF)
        rule["anchor_hold_seconds"] = 3.0
    return rule


# ============================================================
# 剧本构造 (fps=60, 开头留 1s 空档给基线采样)
# ============================================================

def _four_screws_scenario(centers, extra_det=None):
    """四颗螺丝依序打完 + 第二工件首颗触发 first_step 结算。"""
    always = [extra_det] if extra_det else []
    tl = [{"from": 0, "to": 59, "detections": list(always)}]
    f = 60
    for cx, cy in centers:
        tl.append({"from": f, "to": f + 29, "detections": always + [_screw(cx, cy)]})
        tl.append({"from": f + 30, "to": f + 39, "detections": list(always)})
        f += 40
    tl.append({"from": f, "to": f + 29, "detections": always + [_screw(*centers[0])]})
    tl.append({"from": f + 30, "to": f + 900, "detections": list(always)})
    return {"name": "bdd_four_screws", "fps": FPS, "timeline": tl}


def _rounds_scenario():
    """盖罩两轮各打四颗(同位置) → 8 虚拟步骤 → 回绕结算。"""
    tl = [{"from": 0, "to": 59, "detections": []},
          {"from": 60, "to": 89, "detections": [COVER]}]

    def _round(f):
        for cx, cy in QUAD_CENTERS:
            tl.append({"from": f, "to": f + 29, "detections": [COVER, _screw(cx, cy)]})
            tl.append({"from": f + 30, "to": f + 39, "detections": [COVER]})
            f += 40
        return f

    f = _round(90)
    tl.append({"from": f, "to": f + 59, "detections": []})            # 离场 1s
    tl.append({"from": f + 60, "to": f + 89, "detections": [COVER]})  # 重现 → 第2轮
    f = _round(f + 90)
    tl.append({"from": f, "to": f + 59, "detections": []})            # 工件下线
    tl.append({"from": f + 60, "to": f + 89, "detections": [COVER]})  # 下一工件 → 回绕
    tl.append({"from": f + 90, "to": f + 119,
               "detections": [COVER, _screw(*QUAD_CENTERS[0])]})      # 触发结算
    tl.append({"from": f + 120, "to": f + 1200, "detections": [COVER]})
    return {"name": "bdd_two_rounds", "fps": FPS, "timeline": tl}


def _override_scenario():
    """第1轮打共享区域老位置; 第2轮先打老位置(应被丢弃)再打右移新位置(应命中)。

    关键帧: 老位置螺丝 240~269 → 帧 280 后采中途快照; 新位置螺丝 360~389。
    """
    return {"name": "bdd_round_override", "fps": FPS, "timeline": [
        {"from": 0, "to": 59, "detections": []},
        {"from": 60, "to": 89, "detections": [COVER]},                       # → 第1轮
        {"from": 90, "to": 119, "detections": [COVER, _screw(0.25, 0.25)]},  # 前罩螺丝1
        {"from": 120, "to": 149, "detections": [COVER]},
        {"from": 150, "to": 209, "detections": []},                          # 离场 1s
        {"from": 210, "to": 239, "detections": [COVER]},                     # → 第2轮
        {"from": 240, "to": 269, "detections": [COVER, _screw(0.15, 0.25)]},  # 老位置 → 丢
        {"from": 270, "to": 359, "detections": [COVER]},
        {"from": 360, "to": 389, "detections": [COVER, _screw(0.5, 0.25)]},  # 新位置 → 命中
        {"from": 390, "to": 1200, "detections": [COVER]},
    ]}


def _violation_scenario():
    """直接越序打第二象限螺丝 2 秒。"""
    return {"name": "bdd_strict_violation", "fps": FPS, "timeline": [
        {"from": 0, "to": 59, "detections": []},
        {"from": 60, "to": 179, "detections": [_screw(0.75, 0.25)]},
        {"from": 180, "to": 900, "detections": []},
    ]}


def _passthrough_scenario():
    return {"name": "bdd_passthrough", "fps": FPS, "timeline": [
        {"from": 0, "to": 59, "detections": []},
        {"from": 60, "to": 179, "detections": [_screw(0.25, 0.25)]},
        {"from": 180, "to": 900, "detections": []},
    ]}


# ============================================================
# 运行时 helper
# ============================================================

def _poll(client, predicate, timeout=25.0):
    last = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = detection_results(client, channel=CH)
        if r.status_code == 200:
            last = r.json()
            if predicate(last):
                return last
        time.sleep(0.2)
    return last


def _frame_seq(client):
    r = client.get(f"/api/v1/test/synthetic/state?channel={CH}")
    if r.status_code != 200:
        return -1
    return (r.json() or {}).get("frame_seq", -1)


def _launch(client, ctx, scenario_dict):
    r = start_synthetic(client, scenario_dict=scenario_dict, channel=CH,
                        with_project=False)
    assert r.status_code == 200, f"启动 synthetic 失败: {r.text[:300]}"
    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}",
                    json=ctx["project"])
    assert r.status_code == 200, f"下发项目失败: {r.text[:300]}"
    r = start_detection(client, channel=CH)
    assert r.status_code == 200, f"启动检测失败: {r.text[:300]}"
    base = _poll(client, lambda b: isinstance(b.get("counters"), dict), timeout=10.0)
    assert base is not None, "启动后 /detection/results 无返回"
    ctx["base_steps"] = dict(base.get("step_counts") or {})
    ctx["base_counters"] = dict(base.get("counters") or {})


def _step_delta(ctx, body, label):
    return (body.get("step_counts") or {}).get(label, 0) \
        - ctx["base_steps"].get(label, 0)


def _counter_delta(ctx, body, name):
    return (body.get("counters") or {}).get(name, 0) \
        - ctx["base_counters"].get(name, 0)


# ============================================================
# Given
# ============================================================

@given("通道 0 检测器与虚拟剧本源处于停止状态")
def given_stopped(client):
    stop_detection(client, channel=CH)
    stop_synthetic(client, channel=CH)


@given('一个只认"打螺丝"标签且未配置拆分规则的项目')
def given_no_split_project(ctx):
    ctx["project"] = _base_project("__bdd_ls_passthrough__", ["打螺丝"])


@given(parsers.parse('一个四象限拆分项目, 区域定位方式为"{mode_cn}"'))
def given_quadrant_project(ctx, mode_cn):
    mode = "anchor" if mode_cn == "锚点跟随" else "fixed"
    proj = _base_project(f"__bdd_ls_{mode}__", [f"螺丝{i}" for i in range(1, 5)])
    proj["pipeline_config"]["label_splits"] = [_split_rule(mode)]
    ctx["project"] = proj


@given(parsers.parse("该项目启用多轮次拆分, 盖罩切换共 {count:d} 轮"))
def given_rounds(ctx, count):
    labels = [f"{p}螺丝{i}" for p in ("前罩", "后罩") for i in range(1, 5)]
    proj = _base_project("__bdd_ls_rounds__", labels)
    proj["pipeline_config"]["label_splits"] = [_split_rule("fixed")]
    proj["pipeline_config"]["label_splits"][0]["rounds"] = {
        "enabled": True, "trigger_label": "盖罩", "count": count,
        "prefixes": ["前罩", "后罩"], "trigger_gap_seconds": 0.5,
    }
    ctx["project"] = proj


@given(parsers.parse("一个单区域拆分项目并启用多轮次, 第 {rnd:d} 轮使用整体右移的独立区域"))
def given_override_project(ctx, rnd):
    proj = _base_project("__bdd_ls_override__", ["前罩螺丝1", "后罩螺丝1"])
    rule = _split_rule("fixed")
    rule["regions"] = [{"name": "螺丝1", "polygon": Q1}]
    rule["rounds"] = {
        "enabled": True, "trigger_label": "盖罩", "count": 2,
        "prefixes": ["前罩", "后罩"], "trigger_gap_seconds": 0.5,
        "region_overrides": {str(rnd): [{"name": "螺丝1", "polygon": SHIFTED_Q1}]},
    }
    proj["pipeline_config"]["label_splits"] = [rule]
    ctx["project"] = proj


@given("一个严格顺序四象限拆分项目且已配置违序即时事件")
def given_strict_with_event(ctx):
    proj = _base_project("__bdd_ls_strict_event__",
                         [f"螺丝{i}" for i in range(1, 5)], strict=True)
    proj["pipeline_config"]["label_splits"] = [_split_rule("fixed")]
    proj["pipeline_config"]["strict_order_violation_event_id"] = 3
    ctx["project"] = proj


@given("一个严格顺序四象限拆分项目但未配置违序即时事件")
def given_strict_without_event(ctx):
    proj = _base_project("__bdd_ls_strict_zero_diff__",
                         [f"螺丝{i}" for i in range(1, 5)], strict=True)
    proj["pipeline_config"]["label_splits"] = [_split_rule("fixed")]
    ctx["project"] = proj


# ============================================================
# When
# ============================================================

@when("注入持续打螺丝的剧本并启动检测")
def when_passthrough(client, ctx):
    _launch(client, ctx, _passthrough_scenario())


@when("注入按对角顺序打完四颗螺丝的剧本并启动检测")
def when_four_screws(client, ctx):
    _launch(client, ctx, _four_screws_scenario(QUAD_CENTERS))


@when("注入工件整体偏移后打完四颗螺丝的剧本并启动检测")
def when_four_screws_shifted(client, ctx):
    shifted = [(cx + SHIFT[0], cy + SHIFT[1]) for cx, cy in QUAD_CENTERS]
    _launch(client, ctx, _four_screws_scenario(shifted, extra_det=ANCHOR_RUNTIME))


@when("注入两轮各打四颗螺丝的剧本并启动检测")
def when_two_rounds(client, ctx):
    _launch(client, ctx, _rounds_scenario())


@when("注入第二轮在右移位置打螺丝的剧本并启动检测")
def when_override(client, ctx):
    _launch(client, ctx, _override_scenario())
    # 等老位置螺丝段播完 (帧 240~269), 在新位置螺丝出现前 (帧 360) 采中途快照
    deadline = time.time() + 20.0
    while time.time() < deadline:
        fs = _frame_seq(client)
        if fs >= 285:
            break
        time.sleep(0.1)
    assert 285 <= _frame_seq(client) < 355, \
        f"中途快照时机漂移: frame_seq={_frame_seq(client)}"
    r = detection_results(client, channel=CH)
    assert r.status_code == 200
    ctx["mid_body"] = r.json()


@when("注入直接越序打第二颗螺丝的剧本并启动检测")
def when_violation(client, ctx):
    _launch(client, ctx, _violation_scenario())


# ============================================================
# Then
# ============================================================

@then('原始标签"打螺丝"应作为普通步骤计数')
def then_passthrough_counted(client, ctx):
    body = _poll(client, lambda b: _step_delta(ctx, b, "打螺丝") >= 1)
    assert body is not None and _step_delta(ctx, body, "打螺丝") >= 1, \
        f"原始标签未计步: {body and body.get('step_counts')}"
    ctx["last_body"] = body


@then('不应出现虚拟步骤"螺丝1"')
def then_no_virtual(client, ctx):
    body = ctx.get("last_body") or {}
    assert _step_delta(ctx, body, "螺丝1") == 0, \
        f"不该出现虚拟步骤: {body.get('step_counts')}"


@then(parsers.parse('步骤计数应包含 "{labels}"'))
def then_steps_counted(client, ctx, labels):
    expected = [s.strip() for s in labels.split(",") if s.strip()]
    body = _poll(client, lambda b: all(
        _step_delta(ctx, b, lbl) >= 1 for lbl in expected), timeout=40.0)
    assert body is not None, "/detection/results 无返回"
    for lbl in expected:
        assert _step_delta(ctx, body, lbl) >= 1, \
            f"{lbl} 未计步: {body.get('step_counts')}"
    ctx["last_body"] = body


@then("周期应结算为合格")
def then_cycle_ok(client, ctx):
    body = _poll(client, lambda b: _counter_delta(ctx, b, "合格总数") >= 1,
                 timeout=30.0)
    assert body is not None and _counter_delta(ctx, body, "合格总数") >= 1, \
        f"OK 周期未结算: {body and body.get('counters')}"
    assert _counter_delta(ctx, body, "不良总数") == 0, \
        f"不应出 NG: {body.get('counters')}"


@then("第二轮打在老位置的螺丝不应被计入任何步骤")
def then_old_position_dropped(ctx):
    mid = ctx["mid_body"]
    # 中途快照: 第1轮的 前罩螺丝1 已计, 第2轮老位置螺丝已播完但 后罩螺丝1 仍为 0
    assert _step_delta(ctx, mid, "前罩螺丝1") >= 1, \
        f"第1轮共享区域应命中: {mid.get('step_counts')}"
    assert _step_delta(ctx, mid, "后罩螺丝1") == 0, \
        f"老位置螺丝不该命中第2轮独立区域: {mid.get('step_counts')}"


@then(parsers.parse("违序计数器应至少增加 {n:d}"))
def then_violation_counter(client, ctx, n):
    body = _poll(client, lambda b: _counter_delta(ctx, b, "违序次数") >= n,
                 timeout=20.0)
    assert body is not None and _counter_delta(ctx, body, "违序次数") >= n, \
        f"违序事件未触发: {body and body.get('counters')}"
    ctx["last_body"] = body


@then("违序计数器应保持不变")
def then_violation_counter_unchanged(client, ctx):
    # 越序段 60~179 帧(2s) + 缓冲, 等它整段播完再断言
    time.sleep(5.0)
    r = detection_results(client, channel=CH)
    assert r.status_code == 200
    body = r.json()
    assert _counter_delta(ctx, body, "违序次数") == 0, \
        f"未配置事件时违序计数器不该涨: {body.get('counters')}"
    ctx["last_body"] = body


@then('越序的"螺丝2"不应计步')
def then_screw2_not_counted(ctx):
    body = ctx.get("last_body") or {}
    assert _step_delta(ctx, body, "螺丝2") == 0, \
        f"越序步骤不该计步: {body.get('step_counts')}"


# ============================================================
# 清理
# ============================================================

@pytest.fixture(autouse=True)
def _cleanup(client):
    yield
    stop_detection(client, channel=CH)
    stop_synthetic(client, channel=CH)
