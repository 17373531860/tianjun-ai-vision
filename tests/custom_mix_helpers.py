"""自定义混合模式 (custom_mixed_with) 测试共享件: 项目模板 + 剧本生成器.

被 test_synthetic_custom_mix.py (E2E) 与 step_defs/test_custom_mix_modes.py (BDD)
共用, 保证两层测试跑的是同一套客户场景。

客户场景 (包装线简化版):
  步骤: 贴标 → 封箱 (顺序两步 / 检测模式无序两步)
  物品 (原生独立模式词汇):
    per_item 混合: 配对行 "打滑块" — 个体=滑块×3 (expected_count=3),
        动作=打螺丝 (中心点判定, 持续 3 帧). OK=动作盖满 3 个个体;
        NG=只盖到 2 个 (第 3 个个体未覆盖 → 周期降级 NG)
    tracking 混合: 滑块行 count_mode=track, 周期内唯一个体累积 == 3
"""
from __future__ import annotations

import time

# ==================== 项目模板 ====================
PROJECT_NAME = "__synth_custom_mix__"

_EVENTS = [
    {
        "id": 1, "name": "合格(OK)", "color": "#10b981",
        "actions": [
            {"counter_name": "合格总数", "delta": 1},
            {"counter_name": "总产量", "delta": 1},
        ],
        "show_notification": False,
    },
    {
        "id": 2, "name": "不良(NG)", "color": "#ef4444",
        "actions": [
            {"counter_name": "不良总数", "delta": 1},
            {"counter_name": "总产量", "delta": 1},
        ],
        "show_notification": False,
    },
]

_COUNTERS = [
    {"name": "总产量", "value": 0},
    {"name": "合格总数", "value": 0},
    {"name": "不良总数", "value": 0},
]


def build_custom_mix_project(based_on: str, mixed_with: str) -> dict:
    """构建 custom + 混合模式项目配置 (based_on: sequential/detection,
    mixed_with: per_item/tracking)。物品行使用各自独立模式的原生词汇。"""
    if mixed_with == "per_item":
        item_row = {
            "id": "s3", "label": "打滑块", "enabled": True, "threshold": 0.3,
            "min_frames": 1, "disappear_delay": 0,
            "detect_role": "item",
            "per_item": {"item_label": "滑块", "action_label": "打螺丝",
                         "coverage_use_center": True, "sustain_frames": 3,
                         "expected_count": 3},
        }
    else:
        item_row = {
            "id": "s3", "label": "滑块", "enabled": True, "threshold": 0.3,
            "min_frames": 1, "disappear_delay": 0,
            "detect_role": "item",
            "count_mode": "track", "expected_count": 3,
        }
    steps = [
        {"id": "s1", "label": "贴标", "enabled": True, "threshold": 0.3,
         "min_frames": 1, "disappear_delay": 0},
        {"id": "s2", "label": "封箱", "enabled": True, "threshold": 0.3,
         "min_frames": 1, "disappear_delay": 0},
        item_row,
    ]
    pipeline = {
        "custom_based_on": based_on,
        "custom_mixed_with": mixed_with,
        "custom_conditions": [],
        # 末步消失结算 (first_step 需要首步重现, 剧本控制更繁琐)
        "settlement_mode": "last_step",
    }
    if based_on == "sequential":
        pipeline["custom_sequence_order"] = [{"step_id": "s1"}, {"step_id": "s2"}]
    else:
        pipeline["custom_detection_steps"] = ["s1", "s2"]
    return {
        "name": PROJECT_NAME,
        "task_type": "detect",
        "logic_mode": "custom",
        "pipeline_config": pipeline,
        "steps_config": steps,
        "events_config": _EVENTS,
        "counters_config": _COUNTERS,
        "data_config": {},
    }


# ==================== 剧本生成器 ====================
_SLIDER_POS = [(0.10, 0.60), (0.30, 0.60), (0.50, 0.60)]


def _slider_dets(count: int, with_track_ids: bool):
    dets = []
    for i in range(count):
        x, y = _SLIDER_POS[i]
        d = {"label": "滑块", "confidence": 0.95, "bbox": [x, y, 0.10, 0.10]}
        if with_track_ids:
            d["track_id"] = 11 + i
        dets.append(d)
    return dets


def build_mix_scenario(name: str, *, mixed_with: str, item_ok: bool) -> dict:
    """生成混合模式剧本.

    时间轴 (fps=60):
      0-9    贴标出现 (周期开始) → 第 10 帧消失 (步骤1完成)
      12-21  滑块 3 个出现 (per_item=锁定个体表 / tracking=唯一个体累积起步)
      24-33  per_item: 打螺丝动作出现 —
               item_ok=True 大动作框盖住 3 个个体中心 (全覆盖 → 步骤完成)
               item_ok=False 窄动作框只盖住前 2 个 (第 3 个未覆盖 → NG)
             tracking: item_ok=True 第 3 个唯一个体出现 (3/3) / False 不出现 (2/3)
      46-55  封箱出现 → 第 56 帧消失 (末步完成 → last_step 结算)
      56-99  空帧, 让结算/事件落地
    """
    label_det = {"label": "贴标", "confidence": 0.95, "bbox": [0.10, 0.10, 0.15, 0.15]}
    seal_det = {"label": "封箱", "confidence": 0.95, "bbox": [0.60, 0.10, 0.20, 0.18]}

    timeline = [
        {"from": 0, "to": 9, "detections": [label_det]},
        {"from": 10, "to": 11, "detections": []},
    ]

    if mixed_with == "per_item":
        # 个体登场: 3 个滑块 (固定数量模式吸收锁定 3/3)
        sliders = _slider_dets(3, False)
        timeline.append({"from": 12, "to": 21, "detections": sliders})
        # 动作覆盖: 中心点判定 — OK 框罩住 3 个中心, NG 框只罩住前 2 个
        # (滑块中心 x = 0.15 / 0.35 / 0.55)
        action_w = 0.60 if item_ok else 0.30
        action_det = {"label": "打螺丝", "confidence": 0.95,
                      "bbox": [0.05, 0.55, action_w, 0.20]}
        timeline.append({"from": 24, "to": 33, "detections": sliders + [action_det]})
    else:
        # tracking: 周期内唯一个体累积. OK=3 个, NG=只有 2 个.
        timeline.append({"from": 12, "to": 21, "detections": _slider_dets(2, True)})
        if item_ok:
            timeline.append({"from": 34, "to": 43, "detections": _slider_dets(3, True)})

    timeline.append({"from": 46, "to": 55, "detections": [seal_det]})
    timeline.append({"from": 56, "to": 99, "detections": []})
    return {"name": name, "fps": 60, "timeline": timeline}


# ==================== HTTP 工具 (与 test_synthetic_per_item 同款姿势) ====================
def delete_project_by_name(client, name: str) -> None:
    r = client.get("/api/v1/projects?limit=200")
    if r.status_code != 200:
        return
    body = r.json()
    items = body.get("items") if isinstance(body, dict) else body
    for p in items or []:
        if p.get("name") == name:
            client.delete(f"/api/v1/projects/{p['id']}")


def set_project_to_mgr(client, project_id: int, channel: int) -> None:
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
    r = client.post(f"/api/v1/source/detection/set-project?channel={channel}", json=payload)
    assert r.status_code == 200, f"set-project 失败: {r.text[:300]}"


def stop_all(client, channel: int) -> None:
    client.post(f"/api/v1/source/detection/stop?channel={channel}")
    client.post(f"/api/v1/test/synthetic/stop?channel={channel}")
    time.sleep(0.5)


def start_synth_with_scenario(client, channel: int, spec: dict):
    return client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": spec,
        "channel": channel,
        "with_project": False,
    })


def read_counters(client, ch: int) -> dict:
    r = client.get(f"/api/v1/source/detection/results?channel={ch}")
    if r.status_code != 200:
        return {}
    return r.json().get("counters") or {}


def wait_until(predicate, *, timeout_sec=12.0, interval_sec=0.1):
    end = time.time() + timeout_sec
    last = None
    while time.time() < end:
        last = predicate()
        if last:
            return last
        time.sleep(interval_sec)
    return last


def run_mix_cycle(client, ch: int, project_id: int, *, mixed_with: str, item_ok: bool,
                  expect_event: str, scenario_name: str) -> dict:
    """跑一个混合模式周期并等待预期计数器增量. 返回最终 results JSON."""
    set_project_to_mgr(client, project_id, ch)
    baseline = read_counters(client, ch)

    spec = build_mix_scenario(scenario_name, mixed_with=mixed_with, item_ok=item_ok)
    r = start_synth_with_scenario(client, ch, spec)
    assert r.status_code == 200, f"start synthetic 失败: {r.text[:300]}"
    r = client.post(f"/api/v1/source/detection/start?channel={ch}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, f"detection/start 失败: {r.text[:300]}"

    counter_name = "合格总数" if expect_event == "ok" else "不良总数"
    other_name = "不良总数" if expect_event == "ok" else "合格总数"

    def _delta_observed():
        rr = client.get(f"/api/v1/source/detection/results?channel={ch}")
        if rr.status_code != 200:
            return None
        data = rr.json()
        c = (data.get("counters") or {}).get(counter_name, 0)
        return data if c >= baseline.get(counter_name, 0) + 1 else None

    data = wait_until(_delta_observed)
    assert data is not None, \
        f"等待 {counter_name} +1 超时 (剧本={scenario_name}, 基线={baseline})"
    counters = data.get("counters") or {}
    assert counters.get(other_name, 0) - baseline.get(other_name, 0) == 0, \
        f"不应触发 {other_name} (基线={baseline}, 现在={counters})"
    return data
