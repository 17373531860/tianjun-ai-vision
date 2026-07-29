# -*- coding: utf-8 -*-
"""收尾防呆 (v3.44) BDD step 实现 — 上银 SY3 两段现场视频叙事的可执行版.

走完整 pipeline (synthetic 剧本源 → 推理线程 → 状态机 → 混合跟踪容器累加器):
  数量门: 收尾步骤 (放油嘴包/封箱) 新出现时箱内数量未满 → 拒收 + 事件4报警
  缺步挂起: 末步结算仅缺步骤 → 不判 NG 挂起, 补齐自动 OK; 超时按原缺步 NG

项目模板 (镜像上银 SY3, 裁到最小):
  期望序列: 贴标 → 放油嘴包 → 封箱 (settlement=last_step 末步消失结算)
  容器: 托盘 items_total, 动作"放托盘"确认进箱, 每盘 24 支滑块
  事件: 1=合格 2=NG 4=包装违规提醒 (计数器"防呆提示", 不定格)
"""
from __future__ import annotations

import os

from pytest_bdd import scenarios, given, when, then

from tests.custom_mix_helpers import (
    delete_project_by_name,
    read_counters,
    set_project_to_mgr,
    start_synth_with_scenario,
    stop_all,
    wait_until,
)

scenarios("../features/closing_guard.feature")

PROJECT_NAME = "__synth_closing_guard__"


# ==================== 项目模板 ====================
def _build_project(*, guard_on: bool, item_target: int,
                   hold_timeout_s: float = 60.0,
                   short_count_hold: bool = False,
                   gate_escalate: bool = False) -> dict:
    pipeline = {
        "custom_based_on": "sequential",
        "custom_mixed_with": "tracking",
        "settlement_mode": "last_step",
        "custom_sequence_order": [
            {"step_id": "s1"}, {"step_id": "s5"}, {"step_id": "s2"}],
        "custom_mix_container_label": "托盘",
        "custom_mix_container_count_mode": "items_total",
        "custom_mix_container_item_target": item_target,
        "custom_mix_container_gone_frames": 20,
        "custom_mix_container_confirm_by_frames": False,
        "custom_mix_container_confirm_by_action": True,
        "custom_mix_container_action_label": "放托盘",
    }
    if guard_on:
        pipeline["closing_guard"] = {
            "gate_enabled": True,
            "gate_steps": ["放油嘴包", "封箱"],
            "hold_enabled": True,
            "hold_timeout_s": hold_timeout_s,
            "event_id": 4,
        }
    if gate_escalate:
        # v3.44.4 短拦长放: 数量门开 + 封箱升级放行; 缺步/少装都走挂起
        # (升级放行的封箱结算后应带着数量欠账进缺步挂起, 一次挂起说全)
        pipeline["ng_handling"] = {
            "violation": "none",
            "missing_step": "hold",
            "hold_timeout_s": hold_timeout_s,
            "hold_event_id": 4,
            "short_count": "hold",
            "gate_enabled": True,
            "gate_steps": ["放油嘴包", "封箱"],
            "gate_event_id": 4,
            "gate_escalate_steps": ["封箱"],
        }
    if short_count_hold:
        # v3.44.1 少装挂起: 数量门关 (少装放行到结算)、缺步 NG (证明少装挂起
        # 不依赖缺步挂起档), 仅 short_count=hold; 收尾步骤集仍走 gate_steps
        pipeline["ng_handling"] = {
            "violation": "none",
            "missing_step": "ng",
            "hold_timeout_s": hold_timeout_s,
            "hold_event_id": 4,
            "short_count": "hold",
            "gate_enabled": False,
            "gate_steps": ["放油嘴包", "封箱"],
            "gate_event_id": None,
        }
    return {
        "name": PROJECT_NAME,
        "task_type": "detect",
        "logic_mode": "custom",
        "pipeline_config": pipeline,
        "steps_config": [
            {"id": "s1", "label": "贴标", "enabled": True, "threshold": 0.3,
             "min_frames": 1, "disappear_delay": 0},
            {"id": "s5", "label": "放油嘴包", "enabled": True, "threshold": 0.3,
             "min_frames": 1, "disappear_delay": 0},
            {"id": "s2", "label": "封箱", "enabled": True, "threshold": 0.3,
             "min_frames": 1, "disappear_delay": 0},
            {"id": "s3", "label": "放托盘", "enabled": True, "threshold": 0.3,
             "min_frames": 2, "disappear_delay": 0},
            {"id": "s4", "label": "滑块", "enabled": True, "threshold": 0.3,
             "detect_role": "item", "count_mode": "track", "expected_count": 24},
        ],
        "events_config": [
            {"id": 1, "name": "合格(OK)", "color": "#10b981",
             "actions": [{"counter_name": "合格总数", "delta": 1}],
             "show_notification": False},
            {"id": 2, "name": "不良(NG)", "color": "#ef4444",
             "actions": [{"counter_name": "不良总数", "delta": 1}],
             "show_notification": False},
            {"id": 4, "name": "包装违规提醒", "color": "#f59e0b",
             "actions": [{"counter_name": "防呆提示", "delta": 1}],
             "show_notification": True, "toast_id": "ng"},
        ],
        "counters_config": [
            {"name": "合格总数", "value": 0},
            {"name": "不良总数", "value": 0},
            {"name": "防呆提示", "value": 0},
        ],
        "data_config": {},
    }


# ==================== 剧本生成器 ====================
def _dets(*labels, sliders: bool = False, slider_base: int = 100):
    """组合一帧的检测清单: 任意步骤标签 ± 托盘+24滑块."""
    out = []
    if sliders:
        out.append({"label": "托盘", "confidence": 0.95,
                    "bbox": [0.05, 0.20, 0.55, 0.60]})
        for i in range(24):
            cx = 0.08 + (i % 8) * 0.065
            cy = 0.28 + (i // 8) * 0.17
            out.append({"label": "滑块", "confidence": 0.95,
                        "track_id": slider_base + i,
                        "bbox": [cx, cy, 0.04, 0.06]})
    for lb in labels:
        out.append({"label": lb, "confidence": 0.95,
                    "bbox": [0.30, 0.05, 0.25, 0.20]})
    return out


def _video1_scenario(name: str, *, remediate: bool) -> dict:
    """视频一叙事 (单盘 24 支, item_target=24):
    贴标 → 装盘 → 放托盘进箱(托盘随之离开检测区, 24/24) → 忘放油嘴包直接封箱
    → [补放油嘴包]. 托盘进箱后必须从画面消失 — 留着会被重建身份当"在位托盘"
    重复凑数 (与现场物理一致: 托盘装进箱子, 检测区只看备盘工位)."""
    timeline = [
        {"from": 0, "to": 29, "detections": _dets("贴标")},
        {"from": 30, "to": 59, "detections": []},                    # 贴标完成
        {"from": 60, "to": 299, "detections": _dets(sliders=True)},  # 锁24
        {"from": 300, "to": 311, "detections": _dets("放托盘", sliders=True)},
        {"from": 312, "to": 359, "detections": []},                  # 托盘已进箱 24/24
        {"from": 360, "to": 371, "detections": _dets("封箱")},
        {"from": 372, "to": 449, "detections": []},                  # 末步消失→结算点
    ]
    if remediate:
        timeline += [
            {"from": 450, "to": 461, "detections": _dets("放油嘴包")},
            {"from": 462, "to": 599, "detections": []},              # 补步完成→销结OK
        ]
    else:
        timeline += [
            {"from": 450, "to": 899, "detections": []},              # 挂着不补
        ]
    return {"name": name, "fps": 60, "timeline": timeline}


def _video2_scenario(name: str) -> dict:
    """视频二叙事 (两盘 48 支, item_target=48):
    贴标 → 第一盘进箱(24/48) → 提前放油嘴包被数量门拦下报警 →
    补第二盘进箱(48/48) → 再放油嘴包(放行) → 封箱 → OK."""
    return {"name": name, "fps": 60, "timeline": [
        {"from": 0, "to": 29, "detections": _dets("贴标")},
        {"from": 30, "to": 59, "detections": []},
        {"from": 60, "to": 299, "detections": _dets(sliders=True, slider_base=100)},
        {"from": 300, "to": 311,
         "detections": _dets("放托盘", sliders=True, slider_base=100)},
        {"from": 312, "to": 379, "detections": []},                  # 第一盘已进箱 24/48
        # 提前放油嘴包 → 数量门拒收 + 报警 (不入周期)
        {"from": 380, "to": 409, "detections": _dets("放油嘴包")},
        {"from": 410, "to": 419, "detections": []},
        {"from": 420, "to": 659, "detections": _dets(sliders=True, slider_base=200)},  # 第二盘
        {"from": 660, "to": 671,
         "detections": _dets("放托盘", sliders=True, slider_base=200)},
        {"from": 672, "to": 739, "detections": []},                  # 第二盘进箱 48/48
        {"from": 740, "to": 751, "detections": _dets("放油嘴包")},   # 数量门放行
        {"from": 752, "to": 781, "detections": []},                  # 油嘴包完成
        {"from": 782, "to": 793, "detections": _dets("封箱")},
        {"from": 794, "to": 899, "detections": []},                  # 末步消失→OK
    ]}


def _video3_scenario(name: str, *, remediate: bool) -> dict:
    """少装挂起叙事 (两盘 48 支, item_target=48, 数量门关):
    贴标 → 只进一盘(24/48) → 放油嘴包/封箱一路做完 → 末步结算: 步骤全对但
    数量 24/48 → 少装挂起 (摘下放油嘴包/封箱, 报警) →
      remediate=True:  补第二盘(48/48) → 重做放油嘴包 → 重做封箱 → 自动 OK
      remediate=False: 挂着不补 → 超时按缺数量 NG 落账."""
    timeline = [
        {"from": 0, "to": 29, "detections": _dets("贴标")},
        {"from": 30, "to": 59, "detections": []},
        {"from": 60, "to": 299, "detections": _dets(sliders=True, slider_base=100)},
        {"from": 300, "to": 311,
         "detections": _dets("放托盘", sliders=True, slider_base=100)},
        {"from": 312, "to": 359, "detections": []},                  # 第一盘进箱 24/48
        {"from": 360, "to": 371, "detections": _dets("放油嘴包")},   # 数量门关, 放行
        {"from": 372, "to": 381, "detections": []},
        {"from": 382, "to": 393, "detections": _dets("封箱")},
        {"from": 394, "to": 479, "detections": []},                  # 末步消失→少装挂起
    ]
    if remediate:
        timeline += [
            {"from": 480, "to": 719, "detections": _dets(sliders=True, slider_base=200)},
            {"from": 720, "to": 731,
             "detections": _dets("放托盘", sliders=True, slider_base=200)},
            {"from": 732, "to": 779, "detections": []},              # 第二盘进箱 48/48
            {"from": 780, "to": 791, "detections": _dets("放油嘴包")},  # 重做收尾
            {"from": 792, "to": 801, "detections": []},
            {"from": 802, "to": 813, "detections": _dets("封箱")},
            {"from": 814, "to": 959, "detections": []},              # 销结→OK
        ]
    else:
        timeline += [
            {"from": 480, "to": 899, "detections": []},              # 挂着不补→超时NG
        ]
    return {"name": name, "fps": 60, "timeline": timeline}


def _video4_scenario(name: str, *, remediate: bool) -> dict:
    """数量门升级放行叙事 (两盘 48 支, item_target=48, 门开):
    贴标 → 只进第一盘(24/48) → 真放油嘴包被门拦(静默/报警按配置) →
    坚持封箱:
      升级配置: 封箱报警放行进周期 → 结算挂起(缺放油嘴包+数量欠账) →
        remediate=True: 补第二盘(48/48) → 重做放油嘴包 → 自动 OK
      未配升级: 封箱被静默拦 → 周期永不结算 (零差异对照)"""
    timeline = [
        {"from": 0, "to": 29, "detections": _dets("贴标")},
        {"from": 30, "to": 59, "detections": []},
        {"from": 60, "to": 299, "detections": _dets(sliders=True, slider_base=100)},
        {"from": 300, "to": 311,
         "detections": _dets("放托盘", sliders=True, slider_base=100)},
        {"from": 312, "to": 359, "detections": []},                  # 第一盘进箱 24/48
        {"from": 360, "to": 371, "detections": _dets("放油嘴包")},   # 门拦 (24/48 未满)
        {"from": 372, "to": 381, "detections": []},
        {"from": 382, "to": 393, "detections": _dets("封箱")},       # 升级放行 / 静默拦
        {"from": 394, "to": 479, "detections": []},                  # 末步消失→结算/或没结算
    ]
    if remediate:
        timeline += [
            {"from": 480, "to": 719, "detections": _dets(sliders=True, slider_base=200)},
            {"from": 720, "to": 731,
             "detections": _dets("放托盘", sliders=True, slider_base=200)},
            {"from": 732, "to": 779, "detections": []},              # 第二盘进箱 48/48
            {"from": 780, "to": 791, "detections": _dets("放油嘴包")},  # 补缺失步骤
            {"from": 792, "to": 929, "detections": []},              # 销结→OK
        ]
    else:
        timeline += [
            {"from": 480, "to": 899, "detections": []},              # 挂着啥也不做
        ]
    return {"name": name, "fps": 60, "timeline": timeline}


# ==================== 背景 ====================
@given("后端处于测试模式 (RUNTIME_MODE=test)")
def given_test_mode():
    assert os.environ.get("RUNTIME_MODE") == "test", "conftest 应该设置 RUNTIME_MODE=test"


@given("通道 0 的收尾防呆测试环境是干净的")
def given_clean_channel(client):
    stop_all(client, 0)


# ==================== Given: 项目 ====================
def _create_and_load(client, request, ctx, *, guard_on: bool,
                     item_target: int, hold_timeout_s: float = 60.0,
                     short_count_hold: bool = False,
                     gate_escalate: bool = False):
    delete_project_by_name(client, PROJECT_NAME)
    r = client.post("/api/v1/projects",
                    json=_build_project(guard_on=guard_on, item_target=item_target,
                                        hold_timeout_s=hold_timeout_s,
                                        short_count_hold=short_count_hold,
                                        gate_escalate=gate_escalate))
    assert r.status_code in (200, 201), f"创建项目失败: {r.text[:300]}"
    pid = r.json()["id"]
    request.addfinalizer(lambda: client.delete(f"/api/v1/projects/{pid}"))
    request.addfinalizer(lambda: stop_all(client, 0))
    set_project_to_mgr(client, pid, 0)
    ctx["item_target"] = item_target


@given("一个开启收尾防呆的托盘容器项目已载入通道 0")
def given_guard_project(client, request, ctx):
    # item_target 由具体剧本步骤覆盖设置 (视频一 24 / 视频二 48), 先按 24 建
    ctx["pending_project"] = dict(guard_on=True, hold_timeout_s=60.0)
    ctx["client_request"] = request


@given("一个开启收尾防呆且挂起超时很短的托盘容器项目已载入通道 0")
def given_guard_short_timeout_project(client, request, ctx):
    ctx["pending_project"] = dict(guard_on=True, hold_timeout_s=2.0)
    ctx["client_request"] = request


@given("一个未开启收尾防呆的托盘容器项目已载入通道 0")
def given_no_guard_project(client, request, ctx):
    ctx["pending_project"] = dict(guard_on=False, hold_timeout_s=60.0)
    ctx["client_request"] = request


@given("一个开启数量门且封箱升级放行的托盘容器项目已载入通道 0")
def given_gate_escalate_project(client, request, ctx):
    ctx["pending_project"] = dict(guard_on=False, hold_timeout_s=60.0,
                                  gate_escalate=True)
    ctx["client_request"] = request


@given("一个开启少装挂起的托盘容器项目已载入通道 0")
def given_short_hold_project(client, request, ctx):
    ctx["pending_project"] = dict(guard_on=False, hold_timeout_s=60.0,
                                  short_count_hold=True)
    ctx["client_request"] = request


@given("一个开启少装挂起且挂起超时很短的托盘容器项目已载入通道 0")
def given_short_hold_short_timeout_project(client, request, ctx):
    ctx["pending_project"] = dict(guard_on=False, hold_timeout_s=2.0,
                                  short_count_hold=True)
    ctx["client_request"] = request


# ==================== When: 跑剧本 ====================
def _run(client, ctx, spec: dict, item_target: int):
    # 项目延迟到这里建 — item_target 取决于剧本 (视频一 24 / 视频二 48)
    _create_and_load(client, ctx["client_request"], ctx,
                     item_target=item_target, **ctx["pending_project"])
    ctx["baseline"] = read_counters(client, 0)
    ctx["total_frames"] = max(seg["to"] for seg in spec["timeline"]) + 1
    r = start_synth_with_scenario(client, 0, spec)
    assert r.status_code == 200, f"start synthetic 失败: {r.text[:300]}"
    r = client.post("/api/v1/source/detection/start?channel=0",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, f"detection/start 失败: {r.text[:300]}"


@when("我跑一个装满托盘后忘放油嘴包直接封箱再补放的剧本")
def when_run_video1_remediate(client, ctx):
    _run(client, ctx, _video1_scenario("bdd_cg_video1_ok", remediate=True), 24)


@when("我跑一个装满托盘后忘放油嘴包直接封箱不再补放的剧本")
def when_run_video1_no_remediate(client, ctx):
    _run(client, ctx, _video1_scenario("bdd_cg_video1_ng", remediate=False), 24)


@when("我跑一个少装一盘就放油嘴包被拦下补盘后再收尾的剧本")
def when_run_video2(client, ctx):
    _run(client, ctx, _video2_scenario("bdd_cg_video2"), 48)


@when("我跑一个少装一盘就完成收尾后补盘重做收尾的剧本")
def when_run_video3_remediate(client, ctx):
    _run(client, ctx, _video3_scenario("bdd_cg_video3_ok", remediate=True), 48)


@when("我跑一个少装一盘就完成收尾后不再补盘的剧本")
def when_run_video3_no_remediate(client, ctx):
    _run(client, ctx, _video3_scenario("bdd_cg_video3_ng", remediate=False), 48)


@when("我跑一个真漏一盘就封箱后补盘补步骤的剧本")
def when_run_video4_remediate(client, ctx):
    _run(client, ctx, _video4_scenario("bdd_cg_video4_ok", remediate=True), 48)


@when("我跑一个真漏一盘就封箱不再补做的剧本")
def when_run_video4_no_remediate(client, ctx):
    _run(client, ctx, _video4_scenario("bdd_cg_video4_stall", remediate=False), 48)


# ==================== Then ====================
def _wait_counter(client, ctx, name: str, delta: int):
    baseline = ctx["baseline"]

    def _observed():
        counters = read_counters(client, 0)
        return counters if counters.get(name, 0) >= baseline.get(name, 0) + delta else None

    timeout = float(ctx["total_frames"]) / 60.0 + 15.0
    counters = wait_until(_observed, timeout_sec=timeout)
    assert counters is not None, \
        f"等待 {name} +{delta} 超时 (基线={baseline}, 现在={read_counters(client, 0)})"
    return counters


@then("收尾防呆提示应至少触发 1 次")
def then_guard_alarm_fired(client, ctx):
    _wait_counter(client, ctx, "防呆提示", 1)


@then("合格计数应增加 1 且不良计数不变")
def then_ok_plus_one(client, ctx):
    counters = _wait_counter(client, ctx, "合格总数", 1)
    baseline = ctx["baseline"]
    assert counters.get("合格总数", 0) - baseline.get("合格总数", 0) == 1, \
        f"合格总数应恰 +1 (基线={baseline}, 现在={counters})"
    assert counters.get("不良总数", 0) - baseline.get("不良总数", 0) == 0, \
        f"不良总数不应变化 — 缺步/少装应被防呆兜住 (基线={baseline}, 现在={counters})"


@then("不良计数应增加 1 且合格计数不变")
def then_ng_plus_one(client, ctx):
    counters = _wait_counter(client, ctx, "不良总数", 1)
    baseline = ctx["baseline"]
    assert counters.get("不良总数", 0) - baseline.get("不良总数", 0) == 1, \
        f"不良总数应恰 +1 (基线={baseline}, 现在={counters})"
    assert counters.get("合格总数", 0) - baseline.get("合格总数", 0) == 0, \
        f"合格总数不应变化 (基线={baseline}, 现在={counters})"


@then("合格与不良计数都不变")
def then_both_unchanged(client, ctx):
    """零差异对照: 门静默拦死 → 周期永不结算 (跑完剧本后双计数原地不动)."""
    import time as _t
    _t.sleep(float(ctx["total_frames"]) / 60.0 + 5.0)
    counters = read_counters(client, 0)
    baseline = ctx["baseline"]
    for name in ("合格总数", "不良总数"):
        assert counters.get(name, 0) == baseline.get(name, 0), \
            f"{name} 不应变化 (基线={baseline}, 现在={counters})"
