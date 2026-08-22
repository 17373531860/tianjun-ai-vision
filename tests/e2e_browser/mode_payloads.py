"""多工位异模式 results 载荷工厂（e2e 公共 fixture 素材）。

背景：多工位下每个工位可绑不同项目（不同 logic_mode）。后端
`GET /source/detection/results?channel=N` 载荷里带 `project_config`
（logic_mode / steps_config / pipeline_config），前端按通道消费。

本模块提供三种典型模式的载荷工厂 + 一个"按 channel 参数分发"的
Playwright route handler 工厂，供特征测试与阶段 3 模式面板测试共用：

    from .mode_payloads import mixed_mode_router
    handler, route_pattern = mixed_mode_router()
    page.route(route_pattern, handler)

约定 channel 0 → tracking，channel 1 → per_item，channel 2 → region_events；
超出的通道回落到 sequential。
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

RESULTS_ROUTE = "**/source/detection/results*"

_BASE = {
    "is_running": True,
    "is_detecting": True,
    "counters": {},
    "detections": [],
    "current_cycle_steps": [],
    "step_screenshots": {},
}


def tracking_payload() -> dict:
    """tracking 清点模式。

    契约与真实后端一致（index.vue L5722-5739 消费路径）：
    `tracking.item_checklist` 为 {类别: {counted, expected, display_name, prefix}}。
    """
    return {
        **_BASE,
        "project_config": {
            "project_name": "__e2e_mix_tracking",
            "logic_mode": "tracking",
            "steps_config": [
                {"id": 1, "label": "bolt", "name": "螺栓", "enabled": True},
                {"id": 2, "label": "gasket", "name": "垫片", "enabled": True},
            ],
            "pipeline_config": {},
        },
        "tracking": {
            "item_checklist": {
                "bolt": {"counted": 1, "expected": 4,
                         "display_name": "螺栓", "prefix": "B"},
                "gasket": {"counted": 0, "expected": 2,
                           "display_name": "垫片", "prefix": "G"},
            },
            "cycle_active": True,
            "container_mode": False,
        },
    }


def per_item_payload() -> dict:
    """per_item 逐件覆盖模式。

    契约与真实后端一致：顶层 `per_item_state`（source_routes.py
    `mgr.get_per_item_state()`；前端 PerItemPanel 消费 state.steps）。
    """
    return {
        **_BASE,
        "project_config": {
            "project_name": "__e2e_mix_per_item",
            "logic_mode": "per_item",
            "steps_config": [
                {"id": 1, "label": "screw", "name": "打螺丝", "enabled": True},
            ],
            "pipeline_config": {},
        },
        "per_item_state": {
            "enabled": True,
            "cycle_active": True,
            "awaiting_remediation": False,
            "judged": False,
            "judged_ok": False,
            "cycle_start_time": None,
            "frame_id": 42,
            "config": None,
            "steps": [
                {
                    "step_id": 1,
                    "label": "screw",
                    "display_label": "打螺丝",
                    "item_label": "screw",
                    "action_label": "",
                    "expected_count": 6,
                    "total": 6,
                    "display_total": 6,
                    "locked_count": 6,
                    "covered_count": 3,
                    "completed": False,
                    "items": [
                        {
                            "id": i,
                            "bbox": [0.1 + i * 0.12, 0.30, 0.08, 0.08],
                            "covered": i < 3,
                            "covered_at": None,
                            "associated": False,
                            "dup": 0,
                        }
                        for i in range(6)
                    ],
                },
            ],
        },
    }


def region_events_payload() -> dict:
    """region_events 区域事件模式：SOP 按动作规则名建卡（v3.54.1）。

    带经典陷阱数据：steps_config 里的模型类别名与规则名不同，
    用于守住"不许再误用模型类别建卡"。
    """
    return {
        **_BASE,
        "step_inflight_durations": {"测硬度": 1.2},
        "project_config": {
            "project_name": "__e2e_mix_region_events",
            "logic_mode": "region_events",
            "steps_config": [
                {"id": 1, "label": "工件", "enabled": True},
                {"id": 2, "label": "测硬度笔", "enabled": True},
            ],
            "pipeline_config": {
                "region_events": {
                    "rules": [
                        {"id": "r1", "name": "测硬度"},
                        {"id": "r2", "name": "扫码"},
                        {"id": "r3", "name": "下工件"},
                    ],
                },
            },
        },
    }


def sequential_payload() -> dict:
    """默认顺序模式（超出约定通道数时的回落）。"""
    return {
        **_BASE,
        "project_config": {
            "project_name": "__e2e_mix_sequential",
            "logic_mode": "sequential",
            "steps_config": [
                {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
                {"id": 2, "label": "step_b", "name": "步骤B", "enabled": True},
            ],
            "pipeline_config": {},
        },
    }


def weighing_payload() -> dict:
    """weighing 称重投料模式。看板自轮询 /weighing/state, 本载荷只需 logic_mode。"""
    return {
        **_BASE,
        "project_config": {
            "project_name": "__e2e_mix_weighing",
            "logic_mode": "weighing",
            "steps_config": [],
            "pipeline_config": {"weighing": {}},
        },
    }


CHANNEL_PAYLOADS = {
    0: tracking_payload,
    1: per_item_payload,
    2: region_events_payload,
}


def _channel_of(url: str) -> int:
    qs = parse_qs(urlparse(url).query)
    try:
        return int(qs.get("channel", ["0"])[0])
    except (TypeError, ValueError):
        return 0


def mixed_mode_router(payloads=None):
    """返回 (handler, route_pattern)：按 ?channel= 分发异模式载荷。

    payloads 可覆盖默认映射（0=tracking / 1=per_item / 2=region_events）。
    """
    table = payloads or CHANNEL_PAYLOADS

    def handler(route):
        ch = _channel_of(route.request.url)
        factory = table.get(ch, sequential_payload)
        route.fulfill(status=200, json=factory())

    return handler, RESULTS_ROUTE
