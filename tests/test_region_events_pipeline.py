"""区域事件模式 —— synthetic 剧本走真实 pipeline 的端到端回归.

不需要硬件/模型/前端: synthetic 按 TP 工位流程注入 4 类"物"检测框
(测硬度笔/扫码枪/工件), 区域事件引擎产出 测硬度→扫码→下工件 三事件,
映射进周期/步骤体系结算 OK 周期。

这条剧本锁定的关键集成点:
  1. logic_mode='region_events' → apply_project_config 建引擎
  2. 推理循环分支: 引擎替代步骤状态机 (_update_region_events)
  3. 事件确认 → 开周期 + 步骤计数; 结算事件 → _trigger_event 标准结算链
  4. 顺序校验乱序 → 借事件响应面涨"乱序次数"计数器, 不影响 OK 结算
"""
from __future__ import annotations

import time

import pytest


CH = 0

# 简化标定: A/B 共用台面区 (右下), C 下料出口 (右缘)
AB_RECT = [[0.5, 0.4], [0.9, 0.4], [0.9, 1.0], [0.5, 1.0]]
C_RECT = [[0.85, 0.0], [1.0, 0.0], [1.0, 1.0], [0.85, 1.0]]

WORK_ON_TABLE = {"label": "工件", "confidence": 0.9, "bbox": [0.6, 0.6, 0.2, 0.2]}
PEN_ON_WORK = {"label": "测硬度笔", "confidence": 0.8, "bbox": [0.68, 0.68, 0.04, 0.04]}
GUN_ON_WORK = {"label": "扫码枪", "confidence": 0.8, "bbox": [0.62, 0.55, 0.1, 0.1]}
WORK_IN_C = {"label": "工件", "confidence": 0.9, "bbox": [0.88, 0.44, 0.1, 0.12]}


def _scenario(scan_first=False):
    """一个完整工位循环: 测硬度(30帧) → 扫码(20帧) → 工件进 C 区(10帧) → 消失。
    scan_first=True 时前两步交换 (乱序对照组)。60fps。"""
    hardness = [WORK_ON_TABLE, PEN_ON_WORK]
    scan = [WORK_ON_TABLE, GUN_ON_WORK]
    first, second = (scan, hardness) if scan_first else (hardness, scan)
    tl = [
        {"from": 0, "to": 9, "detections": [WORK_ON_TABLE]},
        {"from": 10, "to": 39, "detections": first},
        {"from": 40, "to": 49, "detections": [WORK_ON_TABLE]},   # 空档 > 容忍帧, 闭合
        {"from": 50, "to": 69, "detections": second},
        {"from": 70, "to": 79, "detections": [WORK_ON_TABLE]},
        {"from": 80, "to": 89, "detections": [WORK_IN_C]},       # 工件进下料出口
        {"from": 90, "to": 900, "detections": []},               # 消失 → 结算
    ]
    return {"name": "region_events_tp_cycle", "fps": 60, "timeline": tl}


def _project_config():
    return {
        "project_id": -1,
        "name": "__region_events_pipeline__",
        "task_type": "detect",
        "logic_mode": "region_events",
        "steps_config": [
            {"id": "re-1", "label": "测硬度", "threshold": 0.1, "min_frames": 1},
            {"id": "re-2", "label": "扫码", "threshold": 0.1, "min_frames": 1},
            {"id": "re-3", "label": "下工件", "threshold": 0.1, "min_frames": 1},
        ],
        "pipeline_config": {
            "region_events": {
                "enabled": True,
                "class_conf": {"扫码枪": 0.45, "测硬度笔": 0.25, "工件": 0.5, "手": 0.35},
                "gap_tolerance_frames": 3,
                "rules": [
                    {"id": "r1", "name": "测硬度", "type": "overlap",
                     "subject_label": "测硬度笔", "object_label": "工件",
                     "region": AB_RECT, "region_mode": "and", "min_frames": 15},
                    {"id": "r2", "name": "扫码", "type": "overlap",
                     "subject_label": "扫码枪", "object_label": "工件",
                     "region": AB_RECT, "region_mode": "or", "min_frames": 10},
                    {"id": "r3", "name": "下工件", "type": "region_exit",
                     "subject_label": "工件", "region": C_RECT,
                     "min_frames": 3, "gone_frames": 8},
                ],
                "sequence_check": {"enabled": True,
                                   "order": ["测硬度", "扫码", "下工件"],
                                   "event_id": 3},
            },
        },
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
            {"name": "合格总数", "value": 0},
            {"name": "不良总数", "value": 0},
            {"name": "总产量", "value": 0},
            {"name": "乱序次数", "value": 0},
        ],
    }


def _start(client, scenario):
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": scenario, "channel": CH, "with_project": False,
    })
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}",
                    json=_project_config())
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.2, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]


def _stop(client):
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")


@pytest.fixture
def tp_channel(client):
    _start(client, _scenario())
    yield CH
    _stop(client)


@pytest.fixture
def tp_channel_out_of_order(client):
    _start(client, _scenario(scan_first=True))
    yield CH
    _stop(client)


def _poll(client, ch, predicate, timeout=25.0):
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


def _counters_baseline(client, ch):
    """同通道计数器跨测试持久化, 断言全部用相对基线增量。"""
    body = _poll(client, ch, lambda b: isinstance(b.get("counters"), dict),
                 timeout=10.0)
    assert body is not None, "/detection/results 无返回"
    c = body.get("counters") or {}
    sc = body.get("step_counts") or {}
    return c, sc


def test_confirmed_detections_passthrough_region_events():
    """v3.32 踩坑回归: region_events 不跑步骤状态机, step_frame_confirmed 恒空,
    老的"确认过滤"会把检测框全滤空 → 前端画面永远无框 (客户报
    "没开隐藏标注框也看不见框")。该模式必须原样放行检测框。

    注: synthetic 源在过滤前就直通, 所以剧本级测试测不到这个分支,
    必须用 video 源语义直接打单元桩。
    """
    from types import SimpleNamespace
    from backend.api.source import VideoSourceManager
    dets = [{"label": "工件", "confidence": 0.9},
            {"label": "扫码枪", "confidence": 0.8}]
    fake = SimpleNamespace(source_type="video",
                           project_config={"logic_mode": "region_events"},
                           step_frame_confirmed={}, _custom_mix=None)
    assert VideoSourceManager._get_confirmed_detections(fake, dets) == dets
    # 对照: 其他模式仍走确认过滤 (未确认标签被滤掉, 老语义不变)
    fake.project_config = {"logic_mode": "sequential"}
    assert VideoSourceManager._get_confirmed_detections(fake, dets) == []


def test_full_cycle_three_events_and_ok_settle(client, tp_channel):
    """三事件全部产出 + 结算 1 个 OK 周期 + 正序不涨乱序计数。"""
    base_c, base_sc = _counters_baseline(client, tp_channel)

    def _done(b):
        c = b.get("counters") or {}
        return c.get("合格总数", 0) >= base_c.get("合格总数", 0) + 1

    body = _poll(client, tp_channel, _done, timeout=30.0)
    assert body is not None, "/detection/results 无返回"
    sc = body.get("step_counts") or {}
    counters = body.get("counters") or {}
    for name in ("测硬度", "扫码", "下工件"):
        assert sc.get(name, 0) >= base_sc.get(name, 0) + 1, \
            f"{name} 未计数: step_counts={sc}"
    assert counters.get("合格总数", 0) == base_c.get("合格总数", 0) + 1, \
        f"OK 周期未结算: counters={counters}"
    assert counters.get("不良总数", 0) == base_c.get("不良总数", 0), \
        f"不应出 NG: counters={counters}"
    assert counters.get("乱序次数", 0) == base_c.get("乱序次数", 0), \
        f"正序不该涨乱序计数: counters={counters}"


def test_out_of_order_fires_violation_but_still_settles(client, tp_channel_out_of_order):
    """乱序 (先扫码后测硬度): 乱序计数 +1, 周期仍正常 OK 结算 (只记录不拦截)。"""
    base_c, _ = _counters_baseline(client, tp_channel_out_of_order)

    body = _poll(client, tp_channel_out_of_order, lambda b: (
        (b.get("counters") or {}).get("乱序次数", 0) >= base_c.get("乱序次数", 0) + 1),
        timeout=30.0)
    assert body is not None, "/detection/results 无返回"
    counters = body.get("counters") or {}
    assert counters.get("乱序次数", 0) >= base_c.get("乱序次数", 0) + 1, \
        f"乱序事件未触发: counters={counters}"
    # 乱序只记录, 周期照常 OK 收口
    body = _poll(client, tp_channel_out_of_order, lambda b: (
        (b.get("counters") or {}).get("合格总数", 0) >= base_c.get("合格总数", 0) + 1),
        timeout=15.0)
    counters = body.get("counters") or {}
    assert counters.get("合格总数", 0) >= base_c.get("合格总数", 0) + 1, \
        f"乱序不该阻止结算: counters={counters}"
