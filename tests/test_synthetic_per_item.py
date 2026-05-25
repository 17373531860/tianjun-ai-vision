"""端到端 pytest: per_item「逐件覆盖」模式 + synthetic 剧本源.

不需要硬件、不需要模型、不需要前端、不需要外部 uvicorn.

剧本走完整 pipeline:
    synthetic capture loop → _synthetic_detections_for_frame_index
    → _update_step_stats 入口分流 → _update_step_stats_per_item
    → _PerItemStep.try_cover → _per_item_settle_cycle
    → _trigger_event(1/2) → counters 累加 / per_item_state.last_ng_detail

测试用例:
    test_synthetic_per_item_ok_all_covered  : 8 颗螺丝全覆盖 + 翻面 → 合格 +1
    test_synthetic_per_item_ng_missing_two  : 8 颗中只覆盖 6 颗 + 翻面 → 不良 +1, 漏 2 件
"""
from __future__ import annotations

import time

import pytest


# ==================== 临时 per_item 项目模板 ====================
# 测试自带的"通用 per_item 项目", 不依赖 DEMO 项目, 便于 CI / 本地隔离运行.
PER_ITEM_PROJECT_TEMPLATE = {
    "name": "__synth_per_item__",
    "task_type": "detect",
    "logic_mode": "per_item",
    "pipeline_config": {
        "per_item": {
            "stability_window_frames": 5,        # 加快周期开始 (默认 10 帧太久)
            "stability_iou_threshold": 0.5,
            "item_timeout_seconds": 60.0,        # 远大于剧本长度, 防止 item 被清理
            "lock_count_on_start": True,
            "finish_label": "翻面",
            "finish_sustain_frames": 3,
        }
    },
    "steps_config": [
        {
            "label": "工序1",
            "threshold": 0.3,
            "min_frames": 1,
            "gap_tolerance": 5,
            "color": "#1976d2",
            "per_item": {
                "item_label": "螺丝",
                "action_label": "打螺丝",
                "item_tracking_iou": 0.3,
                "coverage_iou": 0.3,
                "sustain_frames": 5,
                "completion": "all_covered",
                "min_item_count": "auto",
            },
        },
        {
            "label": "翻面",
            "threshold": 0.3,
            "min_frames": 1,
            "gap_tolerance": 5,
            "color": "#43a047",
        },
    ],
    "events_config": [
        {
            "id": 1,
            "name": "合格(OK)",
            "color": "#10b981",
            "actions": [
                {"counter_name": "合格总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1},
            ],
            "show_notification": False,
        },
        {
            "id": 2,
            "name": "不良(NG)",
            "color": "#ef4444",
            "actions": [
                {"counter_name": "不良总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1},
            ],
            "show_notification": False,
        },
    ],
    "counters_config": [
        {"name": "总产量", "value": 0},
        {"name": "合格总数", "value": 0},
        {"name": "不良总数", "value": 0},
    ],
    "data_config": {},
}


# ==================== 剧本生成器 ====================
def _generate_per_item_scenario(name: str, *, covered_count: int) -> dict:
    """生成逐件覆盖剧本.

    布局: 8 颗螺丝排成 2 行 × 4 列网格. 工序框逐颗覆盖前 covered_count 颗
    (与对应 item 框 bbox 完全重合, IoU=1.0 > coverage_iou=0.3).
    每颗覆盖持续 8 帧 (> sustain_frames=5).
    最后 "翻面" 标签持续 5 帧 (> finish_sustain_frames=3) → 触发结算.

    Args:
        covered_count: 实际覆盖的螺丝颗数 (0-8). 设为 8 走 OK 路径, 小于 8 走 NG 路径.
    """
    positions = [
        (0.05, 0.10), (0.25, 0.10), (0.45, 0.10), (0.65, 0.10),
        (0.05, 0.40), (0.25, 0.40), (0.45, 0.40), (0.65, 0.40),
    ]
    item_w, item_h = 0.12, 0.12
    item_dets = [
        {"label": "螺丝", "confidence": 0.95, "bbox": [x, y, item_w, item_h]}
        for (x, y) in positions
    ]

    timeline: list[dict] = []

    # 帧 0-14: 8 颗螺丝稳定显示 (满足 stability_window=5 + 缓冲)
    timeline.append({"from": 0, "to": 14, "detections": item_dets})

    # 逐颗覆盖 (每颗 8 帧 sustain, > sustain_frames=5)
    cursor = 15
    for idx in range(covered_count):
        x, y = positions[idx]
        action_det = {
            "label": "打螺丝",
            "confidence": 0.92,
            "bbox": [x, y, item_w, item_h],
        }
        timeline.append({
            "from": cursor,
            "to": cursor + 7,
            "detections": item_dets + [action_det],
        })
        cursor += 8

    # 收尾过渡 (8 颗螺丝继续显示 5 帧, 给 state machine 缓冲)
    timeline.append({"from": cursor, "to": cursor + 4, "detections": item_dets})
    cursor += 5

    # 翻面收尾 (持续 5 帧 > finish_sustain_frames=3)
    finish_det = {"label": "翻面", "confidence": 0.93, "bbox": [0.40, 0.70, 0.20, 0.15]}
    timeline.append({
        "from": cursor,
        "to": cursor + 4,
        "detections": [finish_det],
    })
    cursor += 5

    # 收尾后空帧, 让 settle 后的状态稳定
    timeline.append({"from": cursor, "to": cursor + 30, "detections": []})

    return {"name": name, "fps": 60, "timeline": timeline}


# ==================== 辅助函数 ====================
def _delete_project_by_name(client, name: str) -> None:
    """防御性删除上次测试残留的同名项目."""
    r = client.get("/api/v1/projects?limit=200")
    if r.status_code != 200:
        return
    body = r.json()
    items = body.get("items") if isinstance(body, dict) else body
    for p in items or []:
        if p.get("name") == name:
            client.delete(f"/api/v1/projects/{p['id']}")


def _set_project_to_mgr(client, project_id: int, channel: int) -> None:
    """把 DB 项目载入到 mgr.project_config (走 /detection/set-project)."""
    r = client.get(f"/api/v1/projects/{project_id}")
    assert r.status_code == 200, f"读项目 {project_id} 失败: {r.text[:300]}"
    p = r.json()
    payload = {
        "project_id": p["id"],
        "name": p["name"],
        "task_type": p.get("task_type") or "detect",
        "logic_mode": p.get("logic_mode") or "detection",
        "steps_config": p.get("steps_config") or [],
        "pipeline_config": p.get("pipeline_config") or {},
        "events_config": p.get("events_config") or [],
        "counters_config": p.get("counters_config") or [],
        "data_config": p.get("data_config") or {},
    }
    r = client.post(
        f"/api/v1/source/detection/set-project?channel={channel}",
        json=payload,
    )
    assert r.status_code == 200, f"set-project 失败: {r.text[:300]}"


def _stop_all(client, channel: int) -> None:
    """彻底停掉 detection + synthetic, 并等内部线程退出 / session 落盘.

    经验值: 0.5s 足够让推理 / 录制 / mes_hook worker 全部消化完上次的 queue,
    避免上一个测试的 cycle 残留事件污染下一个测试的 counters.
    """
    client.post(f"/api/v1/source/detection/stop?channel={channel}")
    client.post(f"/api/v1/test/synthetic/stop?channel={channel}")
    time.sleep(0.5)


def _start_synth_with_scenario(client, channel: int, spec: dict):
    """启动 synthetic 剧本, with_project=False 保留刚才 set 的真 per_item 项目."""
    return client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": spec,
        "channel": channel,
        "with_project": False,
    })


def _wait_until(predicate, *, timeout_sec=8.0, interval_sec=0.1):
    """轮询直到 predicate() 返回真值或超时. 返回最后一次的 predicate 结果."""
    end = time.time() + timeout_sec
    last = None
    while time.time() < end:
        last = predicate()
        if last:
            return last
        time.sleep(interval_sec)
    return last


# ==================== Fixtures ====================
@pytest.fixture
def per_item_project(client):
    """每个测试创建一个独立的临时 per_item 项目, 测试结束删除."""
    _delete_project_by_name(client, PER_ITEM_PROJECT_TEMPLATE["name"])
    r = client.post("/api/v1/projects", json=PER_ITEM_PROJECT_TEMPLATE)
    assert r.status_code in (200, 201), f"创建临时 per_item 项目失败: {r.text[:300]}"
    pid = r.json()["id"]
    yield pid
    client.delete(f"/api/v1/projects/{pid}")


@pytest.fixture
def fresh_channel(client):
    """每个测试前后清干净通道 0 的 detection + synthetic 状态."""
    _stop_all(client, 0)
    yield 0
    _stop_all(client, 0)


# ==================== 测试用例 ====================
def _read_counters(client, ch: int) -> dict:
    """读 mgr.counters 当前快照 (用作基线 / 验证增量)."""
    r = client.get(f"/api/v1/source/detection/results?channel={ch}")
    if r.status_code != 200:
        return {}
    return r.json().get("counters") or {}


def test_synthetic_per_item_ok_all_covered(client, per_item_project, fresh_channel):
    """OK 剧本: 8 颗螺丝全覆盖 + 翻面 → 合格 +1, 不良 +0, 无 last_ng_detail.

    断言增量而非绝对值, 避免跨测试 mgr 单例状态污染 (counters 在 channel_manager
    单例上累积, set-project 期间偶发清零失败 — 见踩坑笔记).
    """
    ch = fresh_channel
    _set_project_to_mgr(client, per_item_project, ch)
    baseline = _read_counters(client, ch)

    spec = _generate_per_item_scenario("per_item_ok_8_covered", covered_count=8)
    r = _start_synth_with_scenario(client, ch, spec)
    assert r.status_code == 200, f"start synthetic 失败: {r.text[:300]}"

    r = client.post(
        f"/api/v1/source/detection/start?channel={ch}",
        json={"conf": 0.25, "iou": 0.45},
    )
    assert r.status_code == 200, f"detection/start 失败: {r.text[:300]}"

    def _ok_delta_observed():
        r = client.get(f"/api/v1/source/detection/results?channel={ch}")
        if r.status_code != 200:
            return None
        data = r.json()
        c = (data.get("counters") or {}).get("合格总数", 0)
        return data if c >= baseline.get("合格总数", 0) + 1 else None

    data = _wait_until(_ok_delta_observed, timeout_sec=10.0)
    assert data is not None, \
        f"等待 OK 周期超时, 剧本没跑出合格 +1 (基线={baseline})"

    counters = data.get("counters") or {}
    assert counters.get("合格总数", 0) - baseline.get("合格总数", 0) == 1, \
        f"应增 1 次合格 (基线={baseline}, 现在={counters})"
    assert counters.get("不良总数", 0) - baseline.get("不良总数", 0) == 0, \
        f"OK 剧本不应触发不良 (基线={baseline}, 现在={counters})"

    # per_item_state.last_ng_detail 在 OK 流程结算时应被清成 None
    ng = (data.get("per_item_state") or {}).get("last_ng_detail")
    assert ng is None, f"OK 流程结算后 last_ng_detail 应为 None, 实际={ng}"


def test_synthetic_per_item_ng_missing_two(client, per_item_project, fresh_channel):
    """NG 剧本: 8 颗螺丝只覆盖 6 颗 + 翻面 → 不良 +1, 漏 2 件, last_ng_detail 结构化 dict."""
    ch = fresh_channel
    _set_project_to_mgr(client, per_item_project, ch)
    baseline = _read_counters(client, ch)

    spec = _generate_per_item_scenario("per_item_ng_missing_2", covered_count=6)
    r = _start_synth_with_scenario(client, ch, spec)
    assert r.status_code == 200, f"start synthetic 失败: {r.text[:300]}"

    r = client.post(
        f"/api/v1/source/detection/start?channel={ch}",
        json={"conf": 0.25, "iou": 0.45},
    )
    assert r.status_code == 200, f"detection/start 失败: {r.text[:300]}"

    def _ng_delta_observed():
        r = client.get(f"/api/v1/source/detection/results?channel={ch}")
        if r.status_code != 200:
            return None
        data = r.json()
        c = (data.get("counters") or {}).get("不良总数", 0)
        return data if c >= baseline.get("不良总数", 0) + 1 else None

    data = _wait_until(_ng_delta_observed, timeout_sec=10.0)
    assert data is not None, \
        f"等待 NG 周期超时, 剧本没跑出不良 +1 (基线={baseline})"

    counters = data.get("counters") or {}
    assert counters.get("不良总数", 0) - baseline.get("不良总数", 0) == 1, \
        f"应增 1 次不良 (基线={baseline}, 现在={counters})"
    assert counters.get("合格总数", 0) - baseline.get("合格总数", 0) == 0, \
        f"NG 剧本不应触发合格 (基线={baseline}, 现在={counters})"

    # last_ng_detail 应为结构化 dict (v3.8.0+ 升级后格式)
    per_item_state = data.get("per_item_state") or {}
    ng = per_item_state.get("last_ng_detail")
    assert isinstance(ng, dict), \
        f"last_ng_detail 应为 dict, 实际类型={type(ng).__name__}, 值={ng}"
    assert ng.get("missing_total") == 2, \
        f"应漏 2 件, 实际 missing_total={ng.get('missing_total')}, 完整 ng={ng}"
    assert isinstance(ng.get("steps_failed"), list) and len(ng["steps_failed"]) >= 1, \
        f"steps_failed 应是非空 list, 实际={ng.get('steps_failed')}"
    failed = ng["steps_failed"][0]
    assert failed.get("total") == 8 and failed.get("covered_count") == 6, \
        f"工序1 应是 6/8 覆盖, 实际={failed}"
    assert len(failed.get("missing_item_ids") or []) == 2, \
        f"missing_item_ids 应有 2 个, 实际={failed.get('missing_item_ids')}"
    assert isinstance(ng.get("cycle_duration_sec"), (int, float)) and ng["cycle_duration_sec"] > 0, \
        f"cycle_duration_sec 应为正数, 实际={ng.get('cycle_duration_sec')}"
