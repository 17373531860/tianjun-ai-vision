"""SAT API 半自动验收脚本：纯 HTTP 黑盒。

7 大验收项（对照 run-tests SKILL.md 路径 F）：
  1. 系统健康
  2. 视频源可读
  3. 项目配置可读
  4. 检测启动/停止
  5. session 写库
  6. 导出系统可读
  7. 报警子系统可读
"""
from __future__ import annotations

import time

import requests


def _get(api: str, path: str):
    return requests.get(f"{api}{path}", timeout=8)


def _post(api: str, path: str, **kwargs):
    return requests.post(f"{api}{path}", timeout=8, **kwargs)


def test_sat_01_system_alive(sat_api_url):
    r = _get(sat_api_url, "/api/v1/system/version")
    assert r.status_code == 200, f"system/version 不可达: {r.status_code}"


def test_sat_02_source_state_readable(sat_api_url):
    r = _get(sat_api_url, "/api/v1/source/state?channel=0")
    assert r.status_code == 200


def test_sat_03_projects_listable(sat_api_url):
    r = _get(sat_api_url, "/api/v1/projects")
    assert r.status_code == 200


def test_sat_04_detection_lifecycle_smoke(sat_api_url):
    """无硬件场景：用 synthetic 起一次 cycle，验证检测启停链路。"""
    state_probe = _get(sat_api_url, "/api/v1/test/synthetic/state?channel=0")
    if state_probe.status_code == 404:
        import pytest
        pytest.skip("backend 未开 RUNTIME_MODE=test，跳过 lifecycle smoke")

    r = _post(
        sat_api_url,
        "/api/v1/test/synthetic/start",
        json={"scenario": "ok_sequential_cycle.json", "with_project": True},
    )
    assert r.status_code == 200, r.text[:300]

    try:
        r = _post(sat_api_url, "/api/v1/source/detection/start?channel=0",
                  json={"conf": 0.25, "iou": 0.45})
        assert r.status_code == 200, r.text[:300]
        time.sleep(1.0)
        r = _get(sat_api_url, "/api/v1/source/detection/results?channel=0")
        assert r.status_code == 200
    finally:
        _post(sat_api_url, "/api/v1/source/detection/stop?channel=0")
        _post(sat_api_url, "/api/v1/test/synthetic/stop?channel=0")


def test_sat_05_sessions_endpoint(sat_api_url):
    r = _get(sat_api_url, "/api/v1/sessions?limit=1")
    assert r.status_code == 200


def test_sat_06_export_templates_endpoint(sat_api_url):
    r = _get(sat_api_url, "/api/v1/export/templates")
    assert r.status_code == 200


def test_sat_07_alarm_state_endpoint(sat_api_url):
    r = _get(sat_api_url, "/api/v1/alarm/state")
    assert r.status_code in (200, 404), f"alarm/state 异常: {r.status_code}"
