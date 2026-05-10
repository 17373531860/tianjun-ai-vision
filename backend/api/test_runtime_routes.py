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


@router.post("/start")
def synthetic_start(body: SyntheticStartBody):
    _require_test_mode()
    from backend.api.source import _get_mgr

    mgr = _get_mgr(body.channel)
    if body.scenario_json is not None:
        mgr.start_synthetic(scenario_dict=body.scenario_json, fps=body.fps)
    elif body.scenario:
        mgr.start_synthetic(scenario=body.scenario, fps=body.fps)
    else:
        mgr.start_synthetic(scenario_dict={"name": "empty", "timeline": []}, fps=body.fps)
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
