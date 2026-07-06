"""同标签区域拆分 —— synthetic 剧本走真实 pipeline 的端到端回归.

不需要硬件/模型/前端: synthetic 注入「打螺丝」在四个象限先后出现,
拆分层改写成 螺丝1~4 虚拟步骤, 顺序模式状态机正常计步 + 结算 OK 周期;
同帧持续出现的「前罩」驱动就位提示 in_position=True。

这条剧本同时锁定三个关键集成点:
  1. runner/synthetic 出口 → _apply_label_splits 改写 (标签变虚拟步骤)
  2. 虚拟步骤作为普通步骤进状态机 (step_counts / 周期结算零改动工作)
  3. /detection/results 暴露 placement_guide 运行态
"""
from __future__ import annotations

import time

import pytest


CH = 0

# 四象限区域
Q1 = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
Q2 = [[0.5, 0.0], [1.0, 0.0], [1.0, 0.5], [0.5, 0.5]]
Q3 = [[0.0, 0.5], [0.5, 0.5], [0.5, 1.0], [0.0, 1.0]]
Q4 = [[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]]

# 引导框: 画面中央大区域, 前罩 (中心 0.5,0.5) 始终在框内
GUIDE_POLY = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]

ANCHOR_DET = {"label": "前罩", "confidence": 0.97, "bbox": [0.2, 0.2, 0.6, 0.6]}


def _screw(cx, cy):
    return {"label": "打螺丝", "confidence": 0.92,
            "bbox": [cx - 0.05, cy - 0.05, 0.1, 0.1]}


def _scenario():
    """打螺丝按 象限1→2→3→4 各出现 30 帧(每步间隔 10 帧空档), 之后回到象限1
    再打一轮首步 → first_step 结算模式收掉上一周期 (OK)。前罩全程在场。"""
    tl = []
    quads = [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)]
    f = 10
    for cx, cy in quads:
        tl.append({"from": f, "to": f + 29,
                   "detections": [ANCHOR_DET, _screw(cx, cy)]})
        tl.append({"from": f + 30, "to": f + 39, "detections": [ANCHOR_DET]})
        f += 40
    # 第二个工件的第一颗螺丝 → 触发 first_step 结算上一周期
    tl.append({"from": f, "to": f + 29,
               "detections": [ANCHOR_DET, _screw(0.25, 0.25)]})
    tl.append({"from": f + 30, "to": f + 600, "detections": [ANCHOR_DET]})
    # 开头空档也保持前罩在场 (就位提示从第一帧就该亮)
    tl.insert(0, {"from": 0, "to": 9, "detections": [ANCHOR_DET]})
    return {"name": "label_split_4_screws", "fps": 60, "timeline": tl}


def _project_config():
    steps = [
        {"id": f"vs-{i}", "label": f"螺丝{i}", "threshold": 0.3,
         "min_frames": 1, "color": "#1976d2", "split_origin": "ls_screws"}
        for i in range(1, 5)
    ]
    return {
        "project_id": -1,
        "name": "__label_split_pipeline__",
        "task_type": "detect",
        "logic_mode": "sequential",
        "steps_config": steps,
        "pipeline_config": {
            "sequence_order": [{"step_id": s["id"]} for s in steps],
            "settlement_mode": "first_step",
            "settle_dedup": False,
            "label_splits": [{
                "id": "ls_screws",
                "enabled": True,
                "source_label": "打螺丝",
                "mode": "fixed",
                "unmatched": "drop",
                "regions": [
                    {"name": "螺丝1", "polygon": Q1},
                    {"name": "螺丝2", "polygon": Q2},
                    {"name": "螺丝3", "polygon": Q3},
                    {"name": "螺丝4", "polygon": Q4},
                ],
            }],
            "placement_guide": {
                "enabled": True,
                "anchor_label": "前罩",
                "polygon": GUIDE_POLY,
                "mode": "hint",
            },
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [
                {"counter_name": "合格总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [
                {"counter_name": "不良总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1}]},
        ],
        "counters_config": [
            {"name": "合格总数", "value": 0},
            {"name": "不良总数", "value": 0},
            {"name": "总产量", "value": 0},
        ],
    }


@pytest.fixture
def split_channel(client):
    """启 synthetic + 下发含拆分规则的项目配置, 结束后清干净。"""
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")

    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": _scenario(), "channel": CH, "with_project": False,
    })
    assert r.status_code == 200, r.text[:300]

    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}",
                    json=_project_config())
    assert r.status_code == 200, r.text[:300]

    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]

    yield CH

    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")


def _poll(client, ch, predicate, timeout=20.0):
    last = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/v1/source/detection/results?channel={ch}")
        if r.status_code == 200:
            last = r.json()
            if predicate(last):
                return last
        time.sleep(0.2)
    return last


def test_virtual_labels_reach_frontend(client, split_channel):
    """检测框标签应是改写后的虚拟步骤名（前端画框直接可见 螺丝N）。"""
    body = _poll(client, split_channel, lambda b: any(
        str(d.get("label", "")).startswith("螺丝")
        for d in (b.get("detections") or [])))
    assert body is not None, "/detection/results 无返回"
    labels = {d.get("label") for d in (body.get("detections") or [])}
    assert any(str(l).startswith("螺丝") for l in labels), \
        f"应出现虚拟步骤标签, 实际: {labels}"
    # 原始标签「打螺丝」不应以原名出现 (unmatched=drop + 全部区域覆盖)
    assert "打螺丝" not in labels


def test_four_virtual_steps_counted_and_cycle_ok(client, split_channel):
    """四个虚拟步骤都被计到 + 顺序模式结算出 1 个 OK 周期。"""
    def _all_counted(b):
        sc = b.get("step_counts") or {}
        counters = b.get("counters") or {}
        return (all(sc.get(f"螺丝{i}", 0) >= 1 for i in range(1, 5))
                and counters.get("合格总数", 0) >= 1)

    body = _poll(client, split_channel, _all_counted, timeout=25.0)
    assert body is not None, "/detection/results 无返回"
    sc = body.get("step_counts") or {}
    counters = body.get("counters") or {}
    for i in range(1, 5):
        assert sc.get(f"螺丝{i}", 0) >= 1, f"螺丝{i} 未计步: step_counts={sc}"
    assert counters.get("合格总数", 0) >= 1, f"OK 周期未结算: counters={counters}"
    assert counters.get("不良总数", 0) == 0, f"不应出 NG: counters={counters}"


def test_placement_guide_exposed_and_in_position(client, split_channel):
    """就位提示运行态透出 + 前罩在引导框内 → in_position=True。"""
    body = _poll(client, split_channel, lambda b: (
        (b.get("placement_guide") or {}).get("in_position") is True))
    assert body is not None, "/detection/results 无返回"
    pg = body.get("placement_guide")
    assert pg is not None, "placement_guide 字段缺失"
    assert pg["enabled"] is True
    assert pg["anchor_label"] == "前罩"
    assert pg["in_position"] is True
    assert pg["anchor_visible"] is True


# ============================================================
# 多轮次 (v3.32): 前罩/后罩两轮打同样四个位置 → 8 个虚拟步骤
# ============================================================

COVER_DET = {"label": "盖罩", "confidence": 0.96, "bbox": [0.3, 0.3, 0.4, 0.4]}
QUADS = [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)]


def _rounds_scenario():
    """两轮各打四颗(位置完全相同), 轮次靠「盖罩」重新出现切换:
    盖罩#1→前罩螺丝1~4 → 盖罩离场1s重现(#2)→后罩螺丝1~4
    → 盖罩离场1s重现(#3, 回绕第1轮)→打象限1 → first_step 结算上一周期 OK。"""
    tl = [{"from": 0, "to": 9, "detections": [COVER_DET]}]

    def _one_round(f_start):
        f = f_start
        for cx, cy in QUADS:
            tl.append({"from": f, "to": f + 29,
                       "detections": [COVER_DET, _screw(cx, cy)]})
            tl.append({"from": f + 30, "to": f + 39, "detections": [COVER_DET]})
            f += 40
        return f

    f = _one_round(10)                       # 第1轮: 前罩螺丝1~4
    tl.append({"from": f, "to": f + 59, "detections": []})          # 盖罩离场 1s
    tl.append({"from": f + 60, "to": f + 89, "detections": [COVER_DET]})  # 重现 → 第2轮
    f = _one_round(f + 90)                   # 第2轮: 后罩螺丝1~4
    tl.append({"from": f, "to": f + 59, "detections": []})          # 工件下线
    tl.append({"from": f + 60, "to": f + 89, "detections": [COVER_DET]})  # 下一工件 → 回绕第1轮
    tl.append({"from": f + 90, "to": f + 119,
               "detections": [COVER_DET, _screw(0.25, 0.25)]})      # 前罩螺丝1 → 结算
    tl.append({"from": f + 120, "to": f + 800, "detections": [COVER_DET]})
    return {"name": "label_split_two_rounds", "fps": 60, "timeline": tl}


def _rounds_project_config():
    labels = [f"{p}螺丝{i}" for p in ("前罩", "后罩") for i in range(1, 5)]
    steps = [
        {"id": f"vs-{k}", "label": lbl, "threshold": 0.3, "min_frames": 1,
         "color": "#1976d2", "split_origin": "ls_screws"}
        for k, lbl in enumerate(labels)
    ]
    cfg = _project_config()
    cfg["name"] = "__label_split_rounds_pipeline__"
    cfg["steps_config"] = steps
    cfg["pipeline_config"]["sequence_order"] = [{"step_id": s["id"]} for s in steps]
    cfg["pipeline_config"]["label_splits"][0]["rounds"] = {
        "enabled": True, "trigger_label": "盖罩", "count": 2,
        "prefixes": ["前罩", "后罩"], "trigger_gap_seconds": 0.5,
    }
    cfg["pipeline_config"].pop("placement_guide", None)
    return cfg


@pytest.fixture
def rounds_channel(client):
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": _rounds_scenario(), "channel": CH, "with_project": False,
    })
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}",
                    json=_rounds_project_config())
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]
    yield CH
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")


def test_rounds_eight_virtual_steps_and_cycle_ok(client, rounds_channel):
    """两轮8个虚拟步骤全部计到 + 顺序结算 OK + 轮次运行态透出。"""
    expected = [f"{p}螺丝{i}" for p in ("前罩", "后罩") for i in range(1, 5)]

    def _done(b):
        sc = b.get("step_counts") or {}
        counters = b.get("counters") or {}
        return (all(sc.get(lbl, 0) >= 1 for lbl in expected)
                and counters.get("合格总数", 0) >= 1)

    body = _poll(client, rounds_channel, _done, timeout=40.0)
    assert body is not None, "/detection/results 无返回"
    sc = body.get("step_counts") or {}
    counters = body.get("counters") or {}
    for lbl in expected:
        assert sc.get(lbl, 0) >= 1, f"{lbl} 未计步: step_counts={sc}"
    assert counters.get("合格总数", 0) >= 1, f"OK 周期未结算: counters={counters}"
    assert counters.get("不良总数", 0) == 0, f"不应出 NG: counters={counters}"
    rounds = body.get("label_split_rounds") or {}
    assert "打螺丝" in rounds, f"轮次运行态未透出: {rounds}"
    assert rounds["打螺丝"]["count"] == 2


# ============================================================
# 严格顺序违序即时事件 (v3.32)
# ============================================================

def _violation_scenario():
    """直接打象限2 (跳过螺丝1) → 严格顺序守门拦下 + 当场触发违序事件。"""
    return {"name": "strict_violation", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 129, "detections": [_screw(0.75, 0.25)]},   # 螺丝2 越序 2s
        {"from": 130, "to": 700, "detections": []},
    ]}


def _violation_project_config():
    steps = [
        {"id": f"vs-{i}", "label": f"螺丝{i}", "threshold": 0.3, "min_frames": 1,
         "color": "#1976d2", "split_origin": "ls_screws", "strict_order": True}
        for i in range(1, 5)
    ]
    cfg = _project_config()
    cfg["name"] = "__strict_violation_pipeline__"
    cfg["steps_config"] = steps
    cfg["pipeline_config"]["sequence_order"] = [{"step_id": s["id"]} for s in steps]
    cfg["pipeline_config"]["strict_order_violation_event_id"] = 3
    cfg["pipeline_config"].pop("placement_guide", None)
    cfg["events_config"].append(
        {"id": 3, "name": "违序警告", "actions": [{"counter_name": "违序次数", "delta": 1}]})
    cfg["counters_config"].append({"name": "违序次数", "value": 0})
    return cfg


@pytest.fixture
def violation_channel(client):
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": _violation_scenario(), "channel": CH, "with_project": False,
    })
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}",
                    json=_violation_project_config())
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]
    yield CH
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")


def _violation_project_config_no_event():
    """同一越序剧本, 但不配 strict_order_violation_event_id → 零差异对照组。"""
    cfg = _violation_project_config()
    cfg["name"] = "__strict_violation_zero_diff__"
    cfg["pipeline_config"].pop("strict_order_violation_event_id", None)
    return cfg


@pytest.fixture
def violation_channel_no_event(client):
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": _violation_scenario(), "channel": CH, "with_project": False,
    })
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}",
                    json=_violation_project_config_no_event())
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]
    yield CH
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")


def test_zero_diff_strict_order_without_event_only_blocks(client, violation_channel_no_event):
    """零差异对照: 未配违序事件时, 守门只拦不报 —— 与 v3.32 之前行为一致。

    同一越序剧本跑 6 秒: 螺丝2 不计步(拦截语义不变), 违序计数器不涨,
    OK/NG 相对基线不变。
    """
    base = _poll(client, violation_channel_no_event,
                 lambda b: isinstance(b.get("counters"), dict), timeout=10.0)
    assert base is not None, "/detection/results 无返回"
    base_screw2 = (base.get("step_counts") or {}).get("螺丝2", 0)
    base_viol = (base.get("counters") or {}).get("违序次数", 0)
    base_ok = (base.get("counters") or {}).get("合格总数", 0)
    base_ng = (base.get("counters") or {}).get("不良总数", 0)

    # 剧本 10~130 帧(60fps)持续越序 2s, 等它整段播完
    time.sleep(6.0)
    r = client.get(f"/api/v1/source/detection/results?channel={CH}")
    assert r.status_code == 200
    body = r.json()
    sc = body.get("step_counts") or {}
    counters = body.get("counters") or {}
    assert sc.get("螺丝2", 0) == base_screw2, f"越序步骤不该计步: {sc}"
    assert counters.get("违序次数", 0) == base_viol, \
        f"未配置事件时违序计数器不该涨: {counters}"
    assert counters.get("合格总数", 0) == base_ok
    assert counters.get("不良总数", 0) == base_ng


def test_strict_order_violation_fires_event_immediately(client, violation_channel):
    """越序打螺丝2: 步骤照旧被拦不计入, 但违序事件当场触发(计数器+1)。

    步骤计数/OK/NG 计数器在同通道跨测试持久化, 断言全部用"相对基线增量"。
    """
    base = _poll(client, violation_channel,
                 lambda b: isinstance(b.get("counters"), dict), timeout=10.0)
    assert base is not None, "/detection/results 无返回"
    base_screw2 = (base.get("step_counts") or {}).get("螺丝2", 0)
    base_ok = (base.get("counters") or {}).get("合格总数", 0)
    base_ng = (base.get("counters") or {}).get("不良总数", 0)

    body = _poll(client, violation_channel, lambda b: (
        (b.get("counters") or {}).get("违序次数", 0) >= 1), timeout=20.0)
    counters = body.get("counters") or {}
    assert counters.get("违序次数", 0) >= 1, f"违序事件未触发: counters={counters}"
    # 被拦截的步骤不应计入周期 (拦截语义不变)
    sc = body.get("step_counts") or {}
    assert sc.get("螺丝2", 0) == base_screw2, f"越序步骤不该计步: step_counts={sc}"
    # 本剧本内不该有周期结算 → OK/NG 相对基线不变
    assert counters.get("合格总数", 0) == base_ok
    assert counters.get("不良总数", 0) == base_ng
