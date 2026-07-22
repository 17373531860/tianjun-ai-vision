# -*- coding: utf-8 -*-
"""托盘进箱动作不应期 (v3.43.1) BDD step 实现.

客户现场事故 (2026-07-20 上银客户机): TRT 推理下"放托盘"置信度贴阈值抖动,
动作中途断检超过消失确认帧 → 一次动作被拆成两个脉冲, 同一盘 24 支记成 48 支.
本组场景走完整 pipeline (synthetic 剧本源 → 推理线程 → 混合跟踪引擎 → 容器累加器),
断言进箱账本 (custom_mix_state.container.trays_done), 与前端 Monitor 混合清点面板同源.

时间轴设计 (fps=60, 动作步骤 min_frames=2 / 消失确认帧走缺省 8):
  0-299    托盘 + 24 滑块在场 (峰值锁 24)
  300-311  放托盘出现 (≥2 帧 → 动作成立)
  312-339  放托盘消失 (≥8 帧 → 脉冲 1 → 进箱记账)
  340-351  放托盘再现 (断检余波 / 正常剧本无此段)
  352-379  再消失 → 脉冲 2 (距上次记账 <1s: 默认不应期 2s 吸收 / 配 0 则再记一次)
  正常节奏剧本: 第二次动作在 460 帧起 (距首次记账 >2s 墙钟) → 两次都记账.
"""
from __future__ import annotations

import os

from pytest_bdd import scenarios, given, when, then

from tests.custom_mix_helpers import (
    delete_project_by_name,
    set_project_to_mgr,
    start_synth_with_scenario,
    stop_all,
    wait_until,
)

scenarios("../features/container_action_cooldown.feature")

PROJECT_NAME = "__synth_tray_cooldown__"


# ==================== 项目模板 (镜像上银 SY 场景, 裁到最小) ====================
def _build_project(cooldown_s=None) -> dict:
    pipeline = {
        "custom_based_on": "sequential",
        "custom_mixed_with": "tracking",
        "custom_sequence_order": [{"step_id": "s1"}, {"step_id": "s2"}],
        "custom_mix_container_label": "托盘",
        "custom_mix_container_count_mode": "items_total",
        "custom_mix_container_item_target": 96,
        "custom_mix_container_gone_frames": 20,
        "custom_mix_container_confirm_by_frames": False,
        "custom_mix_container_confirm_by_action": True,
        "custom_mix_container_action_label": "放托盘",
    }
    if cooldown_s is not None:
        pipeline["custom_mix_container_action_cooldown_s"] = cooldown_s
    return {
        "name": PROJECT_NAME,
        "task_type": "detect",
        "logic_mode": "custom",
        "pipeline_config": pipeline,
        "steps_config": [
            {"id": "s1", "label": "贴标", "enabled": True, "threshold": 0.3,
             "min_frames": 1, "disappear_delay": 0},
            {"id": "s2", "label": "封箱", "enabled": True, "threshold": 0.3,
             "min_frames": 1, "disappear_delay": 0},
            {"id": "s3", "label": "放托盘", "enabled": True, "threshold": 0.3,
             "min_frames": 2, "disappear_delay": 0},
            {"id": "s4", "label": "滑块", "enabled": True, "threshold": 0.3,
             "detect_role": "item", "count_mode": "track", "expected_count": 24},
        ],
        "events_config": [],
        "counters_config": [],
        "data_config": {},
    }


# ==================== 剧本生成器 ====================
def _tray_frame_dets(with_action: bool):
    """托盘 + 24 滑块 (带 track_id, 中心都在托盘框内) ± 放托盘动作标签."""
    dets = [{"label": "托盘", "confidence": 0.95, "bbox": [0.05, 0.20, 0.55, 0.60]}]
    for i in range(24):
        cx = 0.08 + (i % 8) * 0.065
        cy = 0.28 + (i // 8) * 0.17
        dets.append({"label": "滑块", "confidence": 0.95, "track_id": 100 + i,
                     "bbox": [cx, cy, 0.04, 0.06]})
    if with_action:
        dets.append({"label": "放托盘", "confidence": 0.95,
                     "bbox": [0.30, 0.10, 0.25, 0.30]})
    return dets


def _build_scenario(name: str, *, split_pulse: bool) -> dict:
    idle, act = _tray_frame_dets(False), _tray_frame_dets(True)
    timeline = [
        {"from": 0, "to": 299, "detections": idle},
        {"from": 300, "to": 311, "detections": act},     # 动作成立
        {"from": 312, "to": 339, "detections": idle},    # 消失满帧 → 脉冲1 → 记账
    ]
    if split_pulse:
        timeline += [
            {"from": 340, "to": 351, "detections": act},   # 断检余波 (<2s)
            {"from": 352, "to": 449, "detections": idle},  # 脉冲2 → 默认吸收
        ]
    else:
        timeline += [
            {"from": 340, "to": 459, "detections": idle},
            {"from": 460, "to": 471, "detections": act},   # 第二次真动作 (>2s)
            {"from": 472, "to": 569, "detections": idle},  # 脉冲2 → 记账
        ]
    return {"name": name, "fps": 60, "timeline": timeline}


# ==================== 背景 ====================
@given("后端处于测试模式 (RUNTIME_MODE=test)")
def given_test_mode():
    assert os.environ.get("RUNTIME_MODE") == "test", "conftest 应该设置 RUNTIME_MODE=test"


@given("通道 0 的容器动作测试环境是干净的")
def given_clean_channel(client):
    stop_all(client, 0)


# ==================== Given: 项目 ====================
def _create_and_load(client, request, cooldown_s=None):
    delete_project_by_name(client, PROJECT_NAME)
    r = client.post("/api/v1/projects", json=_build_project(cooldown_s))
    assert r.status_code in (200, 201), f"创建项目失败: {r.text[:300]}"
    pid = r.json()["id"]
    request.addfinalizer(lambda: client.delete(f"/api/v1/projects/{pid}"))
    request.addfinalizer(lambda: stop_all(client, 0))
    set_project_to_mgr(client, pid, 0)


@given("一个动作确认进箱的托盘容器项目已载入通道 0")
def given_cooldown_project(client, request):
    _create_and_load(client, request)                 # 不配 → 缺省不应期 2s


@given("一个动作确认进箱但不应期为 0 的托盘容器项目已载入通道 0")
def given_no_cooldown_project(client, request):
    _create_and_load(client, request, cooldown_s=0)   # 显式关闭 = 老行为


# ==================== When: 跑剧本 ====================
def _run(client, ctx, *, split_pulse: bool):
    spec = _build_scenario(
        f"bdd_tray_cooldown_{'split' if split_pulse else 'normal'}",
        split_pulse=split_pulse)
    ctx["total_frames"] = max(seg["to"] for seg in spec["timeline"]) + 1
    r = start_synth_with_scenario(client, 0, spec)
    assert r.status_code == 200, f"start synthetic 失败: {r.text[:300]}"
    r = client.post("/api/v1/source/detection/start?channel=0",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, f"detection/start 失败: {r.text[:300]}"


@when("我跑一个动作中途断检拆成两个脉冲的剧本")
def when_run_split(client, ctx):
    _run(client, ctx, split_pulse=True)


@when("我跑一个正常节奏连续两次动作的剧本")
def when_run_normal(client, ctx):
    _run(client, ctx, split_pulse=False)


# ==================== Then: 进箱账本断言 ====================
def _container_state(client):
    r = client.get("/api/v1/source/detection/results?channel=0")
    if r.status_code != 200:
        return None
    return ((r.json().get("custom_mix_state") or {}).get("container")) or None


@then("容器已装托盘数应为 1")
def then_one_tray(client, ctx):
    _assert_trays_done(client, ctx, expect=1)


@then("容器已装托盘数应为 2")
def then_two_trays(client, ctx):
    _assert_trays_done(client, ctx, expect=2)


def _assert_trays_done(client, ctx, expect: int):
    # 先等账本到达期望值 (进箱发生在剧本中段, 不用等播完)
    reached = wait_until(
        lambda: (_container_state(client) or {}).get("trays_done", 0) >= expect or None,
        timeout_sec=float(ctx["total_frames"]) / 60.0 + 10.0)
    st = _container_state(client)
    assert reached and st is not None, f"等待已装托盘数达到 {expect} 超时: {st}"
    # 再守一段 (覆盖余波脉冲窗口): 确认不会继续虚增
    import time as _t
    _t.sleep(2.5)
    st = _container_state(client)
    assert st.get("trays_done") == expect, \
        f"已装托盘数应恰为 {expect}, 实际 {st.get('trays_done')} (虚增=断检拆分未被吸收)"
