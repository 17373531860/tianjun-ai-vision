"""SAT 全流程 E2E：synthetic + Monitor 截图。

不依赖真实摄像头/模型；通过 backend (8001) 上挂载的
/api/v1/test/synthetic/* 启虚拟剧本，再用浏览器看 Monitor 反馈。

要求：backend 启动时设置了 RUNTIME_MODE=test。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import requests

from .pages import MonitorPage


SHOTS_DIR = Path(os.environ.get("SAT_SHOTS_DIR", "/tmp/sat_full_workflow_shots"))


def _post(api_url: str, path: str, **kwargs) -> requests.Response:
    return requests.post(f"{api_url}{path}", timeout=10, **kwargs)


def _get(api_url: str, path: str, **kwargs) -> requests.Response:
    return requests.get(f"{api_url}{path}", timeout=10, **kwargs)


@pytest.fixture
def runtime_mode_required(api_url):
    r = _get(api_url, "/api/v1/test/synthetic/state?channel=0")
    if r.status_code == 404:
        pytest.skip("backend 未启用 RUNTIME_MODE=test，无 synthetic 路由")


def test_sat_synthetic_drives_monitor(page, base_url, api_url, runtime_mode_required):
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)

    r = _post(
        api_url,
        "/api/v1/test/synthetic/start",
        json={"scenario": "ok_sequential_cycle.json", "channel": 0, "with_project": True},
    )
    assert r.status_code == 200, f"start synthetic 失败: {r.text[:300]}"

    r = _post(
        api_url,
        "/api/v1/source/detection/start?channel=0",
        json={"conf": 0.25, "iou": 0.45},
    )
    assert r.status_code == 200, f"detection start 失败: {r.text[:300]}"

    try:
        time.sleep(1.0)

        mp = MonitorPage(page, base_url).goto()
        mp.sleep(0.8)
        assert mp.has_fps_label(), "Monitor 应显示 FPS 标签"
        assert mp.has_video_area(), "Monitor 应有图像/视频/canvas 元素"
        mp.screenshot(str(SHOTS_DIR / "monitor_synthetic.png"))
    finally:
        _post(api_url, "/api/v1/source/detection/stop?channel=0")
        _post(api_url, "/api/v1/test/synthetic/stop?channel=0")


def test_sat_monitor_session_name_input(page, base_url, api_url, runtime_mode_required):
    """v3.6.2: Monitor 页《会话 ID》输入框可见可填可校验"""
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    _post(api_url, "/api/v1/source/detection/stop?channel=0")
    _post(api_url, "/api/v1/test/synthetic/stop?channel=0")

    mp = MonitorPage(page, base_url).goto()
    mp.sleep(0.8)
    assert mp.has_session_name_input(), "Monitor 应有《会话 ID》输入框"
    mp.fill_session_name("BATCH-A1234")
    assert mp.get_session_name_value() == "BATCH-A1234"

    # 非法字符校验：填一个 *
    mp.fill_session_name("bad*name")
    mp.sleep(0.3)
    assert mp.has_session_name_validation_error(), "应显示输入校验提示"
    mp.screenshot(str(SHOTS_DIR / "monitor_session_name_error.png"))

    # 改回合法值
    mp.fill_session_name("CLEAN-NAME")
    mp.sleep(0.2)
    assert not mp.has_session_name_validation_error()
    mp.screenshot(str(SHOTS_DIR / "monitor_session_name_ok.png"))
