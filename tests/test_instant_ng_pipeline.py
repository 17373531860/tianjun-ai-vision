"""v3.43 实时NG (违规即时结算) —— synthetic 剧本走真实 pipeline 的端到端回归.

剧本: 顺序模式 步骤A→B→C 全部严格顺序。工人先做 A (周期开), 然后直接做 C
(跳过 B = 违序/缺前置的最早可证明时刻) → 严格守门拦下 C 的同时:

  1. 斩立决档: instant_ng_on_violation=True + NG事件无需人工确认
     → 当场触发 NG 事件2 结算 (不良总数+1), 清运行时开新周期
  2. 零差异档: 开关关 (默认) → 只拦不报, NG 计数不动 (老行为)
  3. 提示档:   开关开 + NG事件2 配 require_ack
     → NG 计数+1 且进入人工确认定格 (pending_ack.active=true)
  4. 空周期守门: 周期未开 (第一动作就是违序) → 不落 NG,
     退回违序即时提示事件 (若配了)
"""
from __future__ import annotations

import time

import pytest


CH = 0


def _det(label):
    return {"label": label, "confidence": 0.92, "bbox": [0.4, 0.4, 0.2, 0.2]}


def _scenario_a_then_c():
    """A 出现 1s → 空档 → C 越序出现 2s → 空到剧本尾。60fps。"""
    return {"name": "instant_ng_a_then_c", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 69, "detections": [_det("步骤A")]},
        {"from": 70, "to": 99, "detections": []},
        {"from": 100, "to": 219, "detections": [_det("步骤C")]},
        {"from": 220, "to": 900, "detections": []},
    ]}


def _scenario_c_first():
    """周期未开时第一动作就是越序 C: C 直接出现 2s → 空到剧本尾。"""
    return {"name": "instant_ng_c_first", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 129, "detections": [_det("步骤C")]},
        {"from": 130, "to": 900, "detections": []},
    ]}


def _scenario_regression():
    """A→B→C 正常推进后 B 再次出现 (非法重复=回退): 期望 A,B,C,D 未做完。"""
    return {"name": "instant_ng_regression", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 69, "detections": [_det("步骤A")]},
        {"from": 70, "to": 99, "detections": []},
        {"from": 100, "to": 159, "detections": [_det("步骤B")]},
        {"from": 160, "to": 189, "detections": []},
        {"from": 190, "to": 249, "detections": [_det("步骤C")]},
        {"from": 250, "to": 279, "detections": []},
        {"from": 280, "to": 399, "detections": [_det("步骤B")]},
        {"from": 400, "to": 900, "detections": []},
    ]}


def _project_config(name, instant_ng=False, ng_require_ack=False,
                    violation_event=None, labels="ABC", strict=True,
                    logic_mode="sequential"):
    steps = [
        {"id": f"ing-{i}", "label": f"步骤{ch}", "threshold": 0.3, "min_frames": 1,
         "color": "#1976d2", "strict_order": bool(strict)}
        for i, ch in enumerate(labels, start=1)
    ]
    ng_event = {"id": 2, "name": "不合格(NG)", "actions": [
        {"counter_name": "不良总数", "delta": 1},
        {"counter_name": "总产量", "delta": 1}]}
    if ng_require_ack:
        ng_event["require_ack"] = True
        ng_event["ack_timeout_sec"] = 0
    events = [
        {"id": 1, "name": "合格(OK)", "actions": [
            {"counter_name": "合格总数", "delta": 1},
            {"counter_name": "总产量", "delta": 1}]},
        ng_event,
    ]
    counters = [
        {"name": "合格总数", "value": 0},
        {"name": "不良总数", "value": 0},
        {"name": "总产量", "value": 0},
    ]
    pipeline = {
        "settlement_mode": "first_step",
        "settle_dedup": False,
        "instant_ng_on_violation": bool(instant_ng),
    }
    if logic_mode == "sequential":
        pipeline["sequence_order"] = [{"step_id": s["id"]} for s in steps]
    if violation_event:
        pipeline["strict_order_violation_event_id"] = violation_event
        events.append({"id": violation_event, "name": "违序警告", "actions": [
            {"counter_name": "违序次数", "delta": 1}]})
        counters.append({"name": "违序次数", "value": 0})
    return {
        "project_id": -1,
        "name": name,
        "task_type": "detect",
        "logic_mode": logic_mode,
        "steps_config": steps,
        "pipeline_config": pipeline,
        "events_config": events,
        "counters_config": counters,
    }


def _start(client, scenario, cfg):
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": scenario, "channel": CH, "with_project": False,
    })
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}", json=cfg)
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]


def _cleanup(client):
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")


def _poll(client, pred, timeout=20.0):
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        r = client.get(f"/api/v1/source/detection/results?channel={CH}")
        if r.status_code == 200:
            body = r.json()
            try:
                if pred(body):
                    return body
            except Exception:
                pass
        time.sleep(0.3)
    return body


def _baseline(client):
    body = _poll(client, lambda b: isinstance(b.get("counters"), dict), timeout=10.0)
    assert body is not None, "/detection/results 无返回"
    c = body.get("counters") or {}
    return c.get("不良总数", 0), c.get("合格总数", 0), c.get("违序次数", 0)


def test_instant_ng_settles_cycle_immediately(client):
    """斩立决: A 入周期后 C 越序 → 当场 NG 结算 (不良+1), 周期序列被清。"""
    _start(client, _scenario_a_then_c(),
           _project_config("__instant_ng_kill__", instant_ng=True))
    try:
        base_ng, base_ok, _ = _baseline(client)
        body = _poll(client, lambda b: (
            (b.get("counters") or {}).get("不良总数", 0) >= base_ng + 1))
        counters = body.get("counters") or {}
        assert counters.get("不良总数", 0) == base_ng + 1, \
            f"实时NG未触发或多触发: {counters}"
        assert counters.get("合格总数", 0) == base_ok, f"不该出OK: {counters}"
        # 斩立决后运行时被清: 当前周期序列为空 (C 仍在画面也不接续旧周期)
        body2 = _poll(client, lambda b: not (b.get("current_cycle_steps") or []),
                      timeout=5.0)
        assert not (body2.get("current_cycle_steps") or []), \
            f"斩立决后周期序列应清空: {body2.get('current_cycle_steps')}"
        # C 持续在画面且周期已空 → 不再重复落 NG (空周期守门)
        time.sleep(3.0)
        r = client.get(f"/api/v1/source/detection/results?channel={CH}")
        assert r.json().get("counters", {}).get("不良总数", 0) == base_ng + 1, \
            "空周期守门失效: NG 重复累加"
    finally:
        _cleanup(client)


def test_instant_ng_off_zero_diff(client):
    """零差异: 开关关 (默认) → 同一剧本只拦不报, NG 计数不动。"""
    _start(client, _scenario_a_then_c(),
           _project_config("__instant_ng_off__", instant_ng=False))
    try:
        base_ng, _, _ = _baseline(client)
        # C 越序段 100~220 帧 (60fps ≈ 1.7~3.7s), 等它整段播完
        time.sleep(6.0)
        r = client.get(f"/api/v1/source/detection/results?channel={CH}")
        counters = r.json().get("counters") or {}
        assert counters.get("不良总数", 0) == base_ng, \
            f"开关关时不该实时NG: {counters}"
    finally:
        _cleanup(client)


def test_instant_ng_with_require_ack_freezes(client):
    """提示档: NG事件2 配需人工确认 → NG+1 且进入定格 (pending_ack.active)。"""
    _start(client, _scenario_a_then_c(),
           _project_config("__instant_ng_ack__", instant_ng=True,
                           ng_require_ack=True))
    try:
        base_ng, _, _ = _baseline(client)
        body = _poll(client, lambda b: (
            (b.get("pending_ack") or {}).get("active") is True))
        pa = body.get("pending_ack") or {}
        assert pa.get("active") is True, f"提示档未进入人工确认定格: {pa}"
        assert str(pa.get("event_id")) == "2", f"定格事件应为NG事件2: {pa}"
        counters = body.get("counters") or {}
        assert counters.get("不良总数", 0) == base_ng + 1, \
            f"提示档 NG 计数应+1: {counters}"
        # 定格期间不再叠加事件
        time.sleep(2.0)
        r = client.get(f"/api/v1/source/detection/results?channel={CH}")
        assert r.json().get("counters", {}).get("不良总数", 0) == base_ng + 1
    finally:
        _cleanup(client)


def test_instant_ng_regression_settles_immediately(client):
    """回退档 (v3.43 二期): 不勾严格顺序, A→B→C 后 B 非法重复入账 → 当场 NG。

    可证明性准绳: 回退标记一旦立起, 顺序型结算必判 NG —— 提前结不改判定。
    """
    _start(client, _scenario_regression(),
           _project_config("__instant_ng_regression__", instant_ng=True,
                           labels="ABCD", strict=False))
    try:
        base_ng, base_ok, _ = _baseline(client)
        body = _poll(client, lambda b: (
            (b.get("counters") or {}).get("不良总数", 0) >= base_ng + 1))
        counters = body.get("counters") or {}
        assert counters.get("不良总数", 0) == base_ng + 1, \
            f"回退实时NG未触发或多触发: {counters}"
        assert counters.get("合格总数", 0) == base_ok, f"不该出OK: {counters}"
        # 结算原因应带回退语义 (近 30s 事件日志里查)
        evs = body.get("recent_events") or []
        ng_evs = [e for e in evs if str(e.get("event_id")) == "2"]
        assert any("回退" in (e.get("reason") or "") for e in ng_evs), \
            f"NG原因应含回退: {[e.get('reason') for e in ng_evs]}"
    finally:
        _cleanup(client)


def test_instant_ng_regression_off_zero_diff(client):
    """回退零差异: 开关关, 同一回退剧本 → 周期中途 NG 计数不动 (等结算)。"""
    _start(client, _scenario_regression(),
           _project_config("__instant_ng_reg_off__", instant_ng=False,
                           labels="ABCD", strict=False))
    try:
        base_ng, _, _ = _baseline(client)
        # 回退发生在 280~400 帧 (60fps ≈ 4.7~6.7s), 等它播完
        time.sleep(8.0)
        r = client.get(f"/api/v1/source/detection/results?channel={CH}")
        counters = r.json().get("counters") or {}
        assert counters.get("不良总数", 0) == base_ng, \
            f"开关关时回退不该实时NG: {counters}"
    finally:
        _cleanup(client)


def _scenario_detection_dup():
    """检测模式: A (首步开周期) → B → B 再次出现 (超期望次数)。"""
    return {"name": "instant_ng_det_dup", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 69, "detections": [_det("步骤A")]},
        {"from": 70, "to": 99, "detections": []},
        {"from": 100, "to": 159, "detections": [_det("步骤B")]},
        {"from": 160, "to": 189, "detections": []},
        {"from": 190, "to": 309, "detections": [_det("步骤B")]},
        {"from": 310, "to": 900, "detections": []},
    ]}


def test_instant_ng_detection_duplicate_settles_immediately(client):
    """检测模式重复超次 (v3.43 二期): B 第二次入账 → 当场 NG (重复步骤)。"""
    _start(client, _scenario_detection_dup(),
           _project_config("__instant_ng_det_dup__", instant_ng=True,
                           strict=False, logic_mode="detection"))
    try:
        base_ng, base_ok, _ = _baseline(client)
        body = _poll(client, lambda b: (
            (b.get("counters") or {}).get("不良总数", 0) >= base_ng + 1))
        counters = body.get("counters") or {}
        assert counters.get("不良总数", 0) == base_ng + 1, \
            f"检测重复实时NG未触发或多触发: {counters}"
        assert counters.get("合格总数", 0) == base_ok, f"不该出OK: {counters}"
        evs = body.get("recent_events") or []
        ng_evs = [e for e in evs if str(e.get("event_id")) == "2"]
        assert any("重复步骤" in (e.get("reason") or "") for e in ng_evs), \
            f"NG原因应含重复步骤: {[e.get('reason') for e in ng_evs]}"
    finally:
        _cleanup(client)


def test_instant_ng_detection_duplicate_off_zero_diff(client):
    """检测模式零差异: 开关关, 同一重复剧本 → 周期中途 NG 计数不动。"""
    _start(client, _scenario_detection_dup(),
           _project_config("__instant_ng_det_off__", instant_ng=False,
                           strict=False, logic_mode="detection"))
    try:
        base_ng, _, _ = _baseline(client)
        # 重复发生在 190~310 帧 (60fps ≈ 3.2~5.2s), 等它播完
        time.sleep(7.0)
        r = client.get(f"/api/v1/source/detection/results?channel={CH}")
        counters = r.json().get("counters") or {}
        assert counters.get("不良总数", 0) == base_ng, \
            f"开关关时不该实时NG: {counters}"
    finally:
        _cleanup(client)


def test_instant_ng_empty_cycle_falls_back_to_hint(client):
    """空周期守门: 周期未开时第一动作就是越序 → 不落 NG, 退回违序提示事件。"""
    _start(client, _scenario_c_first(),
           _project_config("__instant_ng_empty__", instant_ng=True,
                           violation_event=5))
    try:
        base_ng, _, base_viol = _baseline(client)
        body = _poll(client, lambda b: (
            (b.get("counters") or {}).get("违序次数", 0) >= base_viol + 1))
        counters = body.get("counters") or {}
        assert counters.get("违序次数", 0) >= base_viol + 1, \
            f"空周期时应退回提示事件: {counters}"
        assert counters.get("不良总数", 0) == base_ng, \
            f"空周期不该落 NG: {counters}"
    finally:
        _cleanup(client)
