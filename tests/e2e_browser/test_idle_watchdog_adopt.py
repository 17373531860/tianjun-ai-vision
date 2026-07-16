"""检测中心空闲看门狗 CI E2E 回归 (v3.40, 川南反馈).

场景: 监控页处于空闲 (未取流未轮询), 后端被外部指令 (开工报文自动开始检测)
自行拉起视频源+检测。老行为: 前端毫无感知 — 开始按钮不灰 / FPS 恒 0 /
信息条不出, 必须切页再切回才恢复。修复后: 空闲看门狗 2s 探活自动接管。

覆盖:
  1. 空闲时开始按钮可点
  2. 后端经 API 拉起检测后 ≤8s 前端自动接管 (开始按钮变灰)
  3. FPS 从 0 变 >0 (轮询已自动恢复)

依赖 RUNTIME_MODE=test 的后端 (synthetic 端点), 非 test 模式自动跳过。
"""
from __future__ import annotations

import time

import pytest
import requests


FPS = 30


def _synthetic_available(api_url: str) -> bool:
    r = requests.post(f"{api_url}/api/v1/test/synthetic/stop?channel=0", timeout=5)
    return r.status_code != 404


def _stop_all(api_url: str):
    requests.post(f"{api_url}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{api_url}/api/v1/test/synthetic/stop?channel=0", timeout=10)


def test_idle_watchdog_adopts_backend_started_detection(page, base_url, api_url):
    if not _synthetic_available(api_url):
        pytest.skip("后端非 RUNTIME_MODE=test, synthetic 端点不可用")

    _stop_all(api_url)
    time.sleep(1)

    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    time.sleep(4)

    start_btn = page.locator("button", has_text="开始").first
    assert start_btn.is_enabled(), "空闲时开始按钮应可点"

    # 模拟"开工报文让后端自行拉起检测" — 前端不知情
    timeline = [{"from": 0, "to": FPS * 90, "detections": [
        {"label": "A", "confidence": 0.95, "bbox": [0.1, 0.1, 0.3, 0.3]}]}]
    r = requests.post(f"{api_url}/api/v1/test/synthetic/start", json={
        "scenario_json": {"name": "watchdog-e2e", "fps": FPS, "timeline": timeline},
        "channel": 0, "with_project": True}, timeout=15)
    assert r.status_code == 200, r.text
    r = requests.post(f"{api_url}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25}, timeout=30)
    assert r.status_code == 200, r.text

    try:
        # 看门狗 2s 一探, 给 8s 裕量
        adopted = False
        deadline = time.time() + 8
        while time.time() < deadline:
            if not start_btn.is_enabled():
                adopted = True
                break
            time.sleep(0.3)
        assert adopted, "后端拉起检测后 8s 内开始按钮应自动变灰 (看门狗接管失败)"

        got_fps = False
        deadline = time.time() + 10
        while time.time() < deadline:
            fps_txt = page.evaluate(
                "() => document.body.innerText.match(/FPS[:：]?\\s*([\\d.]+)/)?.[1] || '0'")
            if float(fps_txt or 0) > 0:
                got_fps = True
                break
            time.sleep(0.4)
        assert got_fps, "接管后 FPS 应从 0 变 >0 (轮询未恢复)"
    finally:
        _stop_all(api_url)
