"""仅在 RUNTIME_MODE=test 时挂载：虚拟 synthetic 源控制 API。"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


def _require_test_mode() -> None:
    if os.environ.get("RUNTIME_MODE") != "test":
        raise HTTPException(status_code=404, detail="Not found")


class SyntheticStartBody(BaseModel):
    scenario: Optional[str] = None
    scenario_json: Optional[Dict[str, Any]] = None
    channel: int = 0
    fps: Optional[float] = None
    with_project: bool = False
    project_steps: Optional[list] = None
    logic_mode: str = "sequential"
    project_id: Optional[int] = None  # 显式注入项目 id (测试场景: 用真项目 id, 让 session 关联到该项目)


def _collect_labels_from_timeline(timeline) -> list:
    labels: list = []
    seen = set()
    for seg in timeline or []:
        for d in seg.get("detections") or []:
            label = d.get("label")
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
    return labels


def _build_min_project_config(labels: list, logic_mode: str = "sequential") -> dict:
    steps = [
        {
            "label": label,
            "threshold": 0.3,
            "min_frames": 1,
            "color": "#1976d2",
        }
        for label in labels
    ]
    return {
        "id": -1,  # synthetic 占位 id；让 start_detection 能创建 DetectionSession
        "name": "__synthetic__",
        "task_type": "detect",
        "logic_mode": logic_mode,
        "pipeline_config": {},
        "steps_config": steps,
        "events_config": [],
        "periodic_actions": [],
        "alarm_config": {},
    }


@router.post("/start")
def synthetic_start(body: SyntheticStartBody):
    _require_test_mode()
    from backend.api.source import _get_mgr

    mgr = _get_mgr(body.channel)

    try:
        if body.scenario_json is not None:
            spec = body.scenario_json
            mgr.start_synthetic(scenario_dict=spec, fps=body.fps)
        elif body.scenario:
            mgr.start_synthetic(scenario=body.scenario, fps=body.fps)
            spec = getattr(mgr, "_synthetic_spec", None) or {}
        else:
            spec = {"name": "empty", "timeline": []}
            mgr.start_synthetic(scenario_dict=spec, fps=body.fps)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    if body.with_project:
        labels = body.project_steps or _collect_labels_from_timeline(spec.get("timeline", []))
        if labels:
            cfg = _build_min_project_config(labels, body.logic_mode)
            # 优先级: body.project_id > mgr 已有 project_config.id > -1
            # 让 synthetic 创建的 session.project_id 落到指定项目，Data 页按项目过滤时也能看到。
            if isinstance(body.project_id, int) and body.project_id > 0:
                cfg["id"] = body.project_id
            else:
                existing = getattr(mgr, "project_config", None) or {}
                existing_id = existing.get("id")
                if isinstance(existing_id, int) and existing_id > 0:
                    cfg["id"] = existing_id
                    cfg["name"] = existing.get("name") or cfg.get("name")
            try:
                mgr.apply_temporary_project_config(cfg)
            except Exception as e:
                return {
                    "status": "ok",
                    "debug": mgr.get_synthetic_debug_state(),
                    "project_apply_warning": str(e),
                    "project_labels": labels,
                }
            return {
                "status": "ok",
                "debug": mgr.get_synthetic_debug_state(),
                "project_applied": True,
                "project_labels": labels,
            }

    return {"status": "ok", "debug": mgr.get_synthetic_debug_state()}


@router.post("/stop")
def synthetic_stop(channel: int = 0):
    _require_test_mode()
    from backend.api.source import _get_mgr

    mgr = _get_mgr(channel)
    if getattr(mgr, "source_type", None) == "synthetic":
        mgr.stop_synthetic()
    return {"status": "ok"}


@router.get("/state")
def synthetic_state(channel: int = 0):
    _require_test_mode()
    from backend.api.source import _get_mgr

    mgr = _get_mgr(channel)
    return mgr.get_synthetic_debug_state()


@router.post("/fire-plugin-cycle-end")
def fire_plugin_cycle_end_hook(channel: int = 0, cycle_id: int = 999, is_good: bool = False):
    """G1.5 调试端点 — 直接触发一次 cycle_end/post_cycle/post hook (仅 RUNTIME_MODE=test).

    避免 synthetic 推理链路 + 真 end_cycle 全跑通才能验证插件 hook 是否被调.
    返回 hook handlers 的执行结果列表 (含错误信息).
    """
    _require_test_mode()
    from backend.plugin_system.manager import plugin_manager

    if plugin_manager.registry is None:
        raise HTTPException(status_code=409, detail="没有 active 插件 / registry 未加载")

    ctx = {
        "channel_id": channel,
        "cycle_id": cycle_id,
        "cycle_uuid": f"debug-fire-{cycle_id}",
        "session_id": -1,
        "is_good": bool(is_good),
        "result": "OK" if is_good else "NG",
        "judgement": "OK" if is_good else "NG",
        "event_id": None,
        "event_name": "debug-trigger",
        "reason": "manual fire from test endpoint",
        "duration": 3.45,
        "step_sequence": ["step_a", "step_b", "step_c"],
        "project_id": None,
    }
    results = plugin_manager.registry.hooks.fire("cycle_end", "post_cycle", "post", ctx)
    return {
        "status": "fired",
        "handlers_count": len(results),
        "ctx": ctx,
        "results": results,
    }
