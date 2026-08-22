"""区域事件模式组合矩阵 BDD step 实现 (v3.32).

组合覆盖: 其他模式零差异 / 空规则退回步骤统计 / overlap 满帧确认 /
划过不足帧不误报 / 漏检中断容忍 / 常驻工件排除 / 每类置信度过滤 /
顺序校验开=记乱序 + 关=零差异。全部走 synthetic 剧本源经真实 pipeline
(引擎→周期/步骤/计数器结算), 不 mock 中间环节。

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


scenarios("../features/region_events_matrix.feature")

CH = 0
FPS = 60

# 简化标定: 台面操作区 (右下), 下料出口区 (右缘)
AB_RECT = [[0.5, 0.4], [0.9, 0.4], [0.9, 1.0], [0.5, 1.0]]
C_RECT = [[0.85, 0.0], [1.0, 0.0], [1.0, 1.0], [0.85, 1.0]]

WORK_ON_TABLE = {"label": "工件", "confidence": 0.9, "bbox": [0.6, 0.6, 0.2, 0.2]}
PEN_ON_WORK = {"label": "测硬度笔", "confidence": 0.8, "bbox": [0.68, 0.68, 0.04, 0.04]}
PEN_LOW_CONF = {"label": "测硬度笔", "confidence": 0.3, "bbox": [0.68, 0.68, 0.04, 0.04]}
GUN_ON_WORK = {"label": "扫码枪", "confidence": 0.8, "bbox": [0.62, 0.55, 0.1, 0.1]}
WORK_IN_C = {"label": "工件", "confidence": 0.9, "bbox": [0.88, 0.44, 0.1, 0.12]}


# ============================================================
# 项目配置构造
# ============================================================

def _events_and_counters():
    return {
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [
                {"counter_name": "合格总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [
                {"counter_name": "不良总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1}]},
            {"id": 3, "name": "乱序警告", "actions": [
                {"counter_name": "乱序次数", "delta": 1}]},
        ],
        "counters_config": [
            {"name": "合格总数", "value": 0}, {"name": "不良总数", "value": 0},
            {"name": "总产量", "value": 0}, {"name": "乱序次数", "value": 0},
        ],
    }


def _rules(n1=15, n2=10):
    return [
        {"id": "r1", "name": "测硬度", "type": "overlap",
         "subject_label": "测硬度笔", "object_label": "工件",
         "region": AB_RECT, "region_mode": "and", "min_frames": n1},
        {"id": "r2", "name": "扫码", "type": "overlap",
         "subject_label": "扫码枪", "object_label": "工件",
         "region": AB_RECT, "region_mode": "or", "min_frames": n2},
        {"id": "r3", "name": "下工件", "type": "region_exit",
         "subject_label": "工件", "region": C_RECT,
         "min_frames": 3, "gone_frames": 8},
    ]


def _project(name, *, logic_mode="region_events", region_events=None,
             steps_config=None):
    proj = {
        "project_id": -1, "name": name, "task_type": "detect",
        "logic_mode": logic_mode,
        "steps_config": steps_config or [
            {"id": "s1", "label": "测硬度笔", "threshold": 0.3, "min_frames": 1},
            {"id": "s2", "label": "工件", "threshold": 0.3, "min_frames": 1},
        ],
        "pipeline_config": {},
        **_events_and_counters(),
    }
    if region_events is not None:
        proj["pipeline_config"]["region_events"] = region_events
    return proj


def _standard_re(*, class_conf=None, gap=3, seq_enabled=False, n1=15, n2=10,
                 settlement=False, extra_rules=None):
    re_cfg = {
        "enabled": True,
        "class_conf": class_conf if class_conf is not None else
        {"扫码枪": 0.45, "测硬度笔": 0.25, "工件": 0.5},
        "gap_tolerance_frames": gap,
        "rules": _rules(n1=n1, n2=n2) + list(extra_rules or []),
    }
    if seq_enabled:
        re_cfg["sequence_check"] = {"enabled": True,
                                    "order": ["测硬度", "扫码", "下工件"],
                                    "event_id": 3}
    if settlement:
        # 顺序即优先级: 标准序→合格; 缺测硬度→NG; 重复扫码→NG
        re_cfg["settlement_rules"] = [
            {"match": "exact", "sequence": ["测硬度", "扫码", "下工件"],
             "event_id": 1},
            {"match": "missing", "target": "测硬度", "event_id": 2},
            {"match": "repeated", "target": "扫码", "min_count": 2,
             "event_id": 2},
        ]
    return re_cfg


# ============================================================
# 剧本构造 (fps=60)
# ============================================================

def _full_cycle_scenario(scan_first=False):
    """测硬度(30帧) → 扫码(20帧) → 工件进下料区(10帧) → 消失结算。"""
    hardness = [WORK_ON_TABLE, PEN_ON_WORK]
    scan = [WORK_ON_TABLE, GUN_ON_WORK]
    first, second = (scan, hardness) if scan_first else (hardness, scan)
    return {"name": "re_bdd_full_cycle", "fps": FPS, "timeline": [
        {"from": 0, "to": 9, "detections": [WORK_ON_TABLE]},
        {"from": 10, "to": 39, "detections": first},
        {"from": 40, "to": 49, "detections": [WORK_ON_TABLE]},
        {"from": 50, "to": 69, "detections": second},
        {"from": 70, "to": 79, "detections": [WORK_ON_TABLE]},
        {"from": 80, "to": 89, "detections": [WORK_IN_C]},
        {"from": 90, "to": 900, "detections": []},
    ]}


def _tool_overlap_scenario(pen=PEN_ON_WORK):
    """工具持续重叠工件 3 秒 (不下料, 只看事件确认/过滤)。"""
    return {"name": "re_bdd_overlap", "fps": FPS, "timeline": [
        {"from": 0, "to": 9, "detections": [WORK_ON_TABLE]},
        {"from": 10, "to": 189, "detections": [WORK_ON_TABLE, pen]},
        {"from": 190, "to": 900, "detections": [WORK_ON_TABLE]},
    ]}


def _brief_pass_scenario():
    """工具只划过一瞬 (5 帧 ≈ 0.08s, 远小于 min_frames=15)。"""
    return {"name": "re_bdd_brief", "fps": FPS, "timeline": [
        {"from": 0, "to": 9, "detections": [WORK_ON_TABLE]},
        {"from": 10, "to": 14, "detections": [WORK_ON_TABLE, PEN_ON_WORK]},
        {"from": 15, "to": 600, "detections": [WORK_ON_TABLE]},
    ]}


def _flaky_overlap_scenario():
    """重叠中途反复漏检 (亮 6 帧 / 灭 3 帧交替), 容忍足够大时应确认。"""
    tl = [{"from": 0, "to": 9, "detections": [WORK_ON_TABLE]}]
    f = 10
    for _ in range(12):
        tl.append({"from": f, "to": f + 5,
                   "detections": [WORK_ON_TABLE, PEN_ON_WORK]})
        tl.append({"from": f + 6, "to": f + 8, "detections": [WORK_ON_TABLE]})
        f += 9
    tl.append({"from": f, "to": 900, "detections": [WORK_ON_TABLE]})
    return {"name": "re_bdd_flaky", "fps": FPS, "timeline": tl}


def _resident_vanish_scenario():
    """台面常驻工件 (从未进下料区) 直接消失 → 不应触发结算。"""
    return {"name": "re_bdd_resident", "fps": FPS, "timeline": [
        {"from": 0, "to": 179, "detections": [WORK_ON_TABLE]},
        {"from": 180, "to": 900, "detections": []},
    ]}


def _skip_hardness_scenario():
    """缺测硬度: 只扫码就下料 → 结算判定 missing 规则应判 NG。"""
    scan = [WORK_ON_TABLE, GUN_ON_WORK]
    return {"name": "re_bdd_skip_hardness", "fps": FPS, "timeline": [
        {"from": 0, "to": 9, "detections": [WORK_ON_TABLE]},
        {"from": 10, "to": 29, "detections": scan},
        {"from": 30, "to": 39, "detections": [WORK_ON_TABLE]},
        {"from": 40, "to": 49, "detections": [WORK_IN_C]},
        {"from": 50, "to": 900, "detections": []},
    ]}


def _double_scan_scenario():
    """扫码两次 (中间隔 1s 让 episode 闭合) → repeated 规则应判 NG。"""
    hardness = [WORK_ON_TABLE, PEN_ON_WORK]
    scan = [WORK_ON_TABLE, GUN_ON_WORK]
    return {"name": "re_bdd_double_scan", "fps": FPS, "timeline": [
        {"from": 0, "to": 9, "detections": [WORK_ON_TABLE]},
        {"from": 10, "to": 39, "detections": hardness},
        {"from": 40, "to": 49, "detections": [WORK_ON_TABLE]},
        {"from": 50, "to": 69, "detections": scan},
        {"from": 70, "to": 129, "detections": [WORK_ON_TABLE]},
        {"from": 130, "to": 149, "detections": scan},
        {"from": 150, "to": 159, "detections": [WORK_ON_TABLE]},
        {"from": 160, "to": 169, "detections": [WORK_IN_C]},
        {"from": 170, "to": 900, "detections": []},
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


def _launch(client, ctx, scenario_dict):
    r = start_synthetic(client, scenario_dict=scenario_dict, channel=CH,
                        with_project=False)
    assert r.status_code == 200, f"启动 synthetic 失败: {r.text[:300]}"
    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}",
                    json=ctx["project"])
    assert r.status_code == 200, f"下发项目失败: {r.text[:300]}"
    r = start_detection(client, channel=CH, conf=0.2)
    assert r.status_code == 200, f"启动检测失败: {r.text[:300]}"
    base = _poll(client, lambda b: isinstance(b.get("counters"), dict),
                 timeout=10.0)
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


@given("一个顺序模式项目, 但配置里残留了区域事件规则")
def given_sequential_with_re(ctx):
    ctx["project"] = _project("__re_bdd_seq_zero_diff__",
                              logic_mode="sequential",
                              region_events=_standard_re())


@given("一个区域事件模式项目, 但未配置任何规则")
def given_re_no_rules(ctx):
    ctx["project"] = _project("__re_bdd_no_rules__",
                              region_events={"enabled": True, "rules": []})


@given("一个标准三规则的区域事件项目")
def given_standard(ctx):
    ctx["project"] = _project("__re_bdd_standard__",
                              region_events=_standard_re())


@given("一个中断容忍很大的区域事件项目")
def given_big_gap(ctx):
    # 亮6灭3交替: 容忍给 30 帧, 即使推理循环把灭段全看见也不清零
    ctx["project"] = _project("__re_bdd_gap__",
                              region_events=_standard_re(gap=30))


@given("一个测硬度笔置信度门限较高的区域事件项目")
def given_high_conf(ctx):
    ctx["project"] = _project(
        "__re_bdd_conf__",
        region_events=_standard_re(
            class_conf={"扫码枪": 0.45, "测硬度笔": 0.6, "工件": 0.5}))


@given("一个开启顺序校验的区域事件项目")
def given_seq_on(ctx):
    ctx["project"] = _project("__re_bdd_seq_on__",
                              region_events=_standard_re(seq_enabled=True))


@given("一个关闭顺序校验的区域事件项目")
def given_seq_off(ctx):
    ctx["project"] = _project("__re_bdd_seq_off__",
                              region_events=_standard_re(seq_enabled=False))


@given("一个配置了结算判定的区域事件项目")
def given_settlement(ctx):
    # 重复扫码场景是"连续同名两次": 关掉连续同动作去重 (默认开会静默吸收
    # 第二次), 让 repeated 判定按次数抓 —— 去重语义本身由引擎单测覆盖
    re_cfg = _standard_re(settlement=True)
    re_cfg["dedup_consecutive"] = False
    ctx["project"] = _project("__re_bdd_settlement__", region_events=re_cfg)


@given("一个含进区规则的区域事件项目")
def given_enter_rule(ctx):
    enter = {"id": "r4", "name": "上料", "type": "region_enter",
             "subject_label": "工件", "region": AB_RECT, "min_frames": 3}
    ctx["project"] = _project("__re_bdd_enter__",
                              region_events=_standard_re(extra_rules=[enter]))


# ============================================================
# When
# ============================================================

@when("注入工具持续重叠工件的剧本并启动检测")
def when_tool_overlap(client, ctx):
    _launch(client, ctx, _tool_overlap_scenario())


@when("注入完整工位循环剧本并启动检测")
def when_full_cycle(client, ctx):
    _launch(client, ctx, _full_cycle_scenario())


@when("注入工具只划过一瞬的剧本并启动检测")
def when_brief_pass(client, ctx):
    _launch(client, ctx, _brief_pass_scenario())


@when("注入重叠中途反复漏检的剧本并启动检测")
def when_flaky(client, ctx):
    _launch(client, ctx, _flaky_overlap_scenario())


@when("注入台面常驻工件直接消失的剧本并启动检测")
def when_resident_vanish(client, ctx):
    _launch(client, ctx, _resident_vanish_scenario())


@when("注入低置信度工具重叠工件的剧本并启动检测")
def when_low_conf(client, ctx):
    _launch(client, ctx, _tool_overlap_scenario(pen=PEN_LOW_CONF))


@when("注入先扫码后测硬度的乱序剧本并启动检测")
def when_out_of_order(client, ctx):
    _launch(client, ctx, _full_cycle_scenario(scan_first=True))


@when("注入缺测硬度直接扫码下料的剧本并启动检测")
def when_skip_hardness(client, ctx):
    _launch(client, ctx, _skip_hardness_scenario())


@when("注入扫码两次的完整循环剧本并启动检测")
def when_double_scan(client, ctx):
    _launch(client, ctx, _double_scan_scenario())


# ============================================================
# Then
# ============================================================

@then(parsers.parse('原始标签"{label}"应作为普通步骤计数'))
def then_raw_label_counted(client, ctx, label):
    body = _poll(client, lambda b: _step_delta(ctx, b, label) >= 1)
    assert body is not None and _step_delta(ctx, body, label) >= 1, \
        f"原始标签未计步: {body and body.get('step_counts')}"
    ctx["last_body"] = body


@then(parsers.parse('不应出现事件步骤"{name}"'))
def then_no_event_step(client, ctx, name):
    # 剧本重叠段最长 3s, 等它整段播完再断言, 防"还没发生"假绿
    body = ctx.get("last_body")
    if body is None:
        time.sleep(5.0)
        r = detection_results(client, channel=CH)
        assert r.status_code == 200
        body = r.json()
        ctx["last_body"] = body
    assert _step_delta(ctx, body, name) == 0, \
        f"不该出现事件步骤 {name}: {body.get('step_counts')}"


@then(parsers.parse('事件步骤计数应包含 "{names}"'))
def then_event_steps_counted(client, ctx, names):
    expected = [s.strip() for s in names.split(",") if s.strip()]
    body = _poll(client, lambda b: all(
        _step_delta(ctx, b, n) >= 1 for n in expected), timeout=30.0)
    assert body is not None, "/detection/results 无返回"
    for n in expected:
        assert _step_delta(ctx, body, n) >= 1, \
            f"{n} 未计数: {body.get('step_counts')}"
    ctx["last_body"] = body


@then(parsers.parse('results 载荷规则清单应为 "{names}"'))
def then_results_rule_names(client, ctx, names):
    """v3.54.1 多工位 SOP 建卡契约: 前端消费 results.project_config.pipeline_config.region_events.rules[].name"""
    expected = [s.strip() for s in names.split(",") if s.strip()]
    body = ctx.get("last_body")
    if body is None:
        r = detection_results(client, channel=CH)
        assert r.status_code == 200
        body = r.json()
        ctx["last_body"] = body
    rules = (((body.get("project_config") or {})
              .get("pipeline_config") or {})
             .get("region_events") or {}).get("rules") or []
    got = [r.get("name") for r in rules if r.get("name")]
    assert got == expected, f"规则清单 {got} != {expected}"


@then("周期应结算为合格")
def then_cycle_ok(client, ctx):
    body = _poll(client, lambda b: _counter_delta(ctx, b, "合格总数") >= 1,
                 timeout=30.0)
    assert body is not None and _counter_delta(ctx, body, "合格总数") >= 1, \
        f"OK 周期未结算: {body and body.get('counters')}"
    assert _counter_delta(ctx, body, "不良总数") == 0, \
        f"不应出 NG: {body.get('counters')}"
    ctx["last_body"] = body


@then("周期应结算为不合格")
def then_cycle_ng(client, ctx):
    body = _poll(client, lambda b: _counter_delta(ctx, b, "不良总数") >= 1,
                 timeout=30.0)
    assert body is not None and _counter_delta(ctx, body, "不良总数") >= 1, \
        f"NG 周期未结算: {body and body.get('counters')}"
    assert _counter_delta(ctx, body, "合格总数") == 0, \
        f"结算判定命中 NG 时不该出 OK: {body.get('counters')}"
    ctx["last_body"] = body


@then("合格计数应保持不变")
def then_ok_counter_unchanged(client, ctx):
    # 等剧本关键段播完 (常驻工件消失确认 / 划过段结束) 再断言
    time.sleep(5.0)
    r = detection_results(client, channel=CH)
    assert r.status_code == 200
    body = r.json()
    assert _counter_delta(ctx, body, "合格总数") == 0, \
        f"不该结算合格周期: {body.get('counters')}"
    ctx["last_body"] = body


@then(parsers.parse("乱序计数应至少增加 {n:d}"))
def then_violation_counter(client, ctx, n):
    body = _poll(client, lambda b: _counter_delta(ctx, b, "乱序次数") >= n,
                 timeout=25.0)
    assert body is not None and _counter_delta(ctx, body, "乱序次数") >= n, \
        f"乱序事件未触发: {body and body.get('counters')}"
    ctx["last_body"] = body


@then("乱序计数应保持不变")
def then_violation_counter_unchanged(client, ctx):
    body = ctx.get("last_body")
    assert body is not None, "前置断言未留快照"
    assert _counter_delta(ctx, body, "乱序次数") == 0, \
        f"顺序校验关闭时乱序计数不该涨: {body.get('counters')}"


# ============================================================
# 清理
# ============================================================

@pytest.fixture(autouse=True)
def _cleanup(client):
    yield
    stop_detection(client, channel=CH)
    stop_synthetic(client, channel=CH)
