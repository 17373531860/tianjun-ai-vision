"""共用 helper：让 BDD step_defs 用统一姿势启动 synthetic + 项目。

不直接绑 fixture，需要 step 显式调用，避免污染顶层 conftest。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def start_synthetic(
    client,
    *,
    scenario: Optional[str] = None,
    scenario_dict: Optional[Dict[str, Any]] = None,
    channel: int = 0,
    fps: Optional[float] = None,
    with_project: bool = False,
    project_steps: Optional[list] = None,
    logic_mode: str = "sequential",
):
    """走 /api/v1/test/synthetic/start，返回 response。"""
    body: Dict[str, Any] = {"channel": channel, "with_project": with_project, "logic_mode": logic_mode}
    if scenario_dict is not None:
        body["scenario_json"] = scenario_dict
    if scenario:
        body["scenario"] = scenario
    if fps is not None:
        body["fps"] = fps
    if project_steps:
        body["project_steps"] = project_steps
    return client.post("/api/v1/test/synthetic/start", json=body)


def stop_synthetic(client, channel: int = 0):
    return client.post(f"/api/v1/test/synthetic/stop?channel={channel}")


def synthetic_state(client, channel: int = 0):
    return client.get(f"/api/v1/test/synthetic/state?channel={channel}")


def start_detection(client, channel: int = 0, conf: float = 0.25, iou: float = 0.45):
    return client.post(
        f"/api/v1/source/detection/start?channel={channel}",
        json={"conf": conf, "iou": iou},
    )


def stop_detection(client, channel: int = 0):
    return client.post(f"/api/v1/source/detection/stop?channel={channel}")


def detection_results(client, channel: int = 0):
    return client.get(f"/api/v1/source/detection/results?channel={channel}")
