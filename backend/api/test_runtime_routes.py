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
            "gap_tolerance": 5,
            "color": "#1976d2",
        }
        for label in labels
    ]
    return {
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
            try:
                mgr.set_project_config(cfg)
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
