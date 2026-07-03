"""Monitor 录像异常遮罩（RecordingFailureOverlay.vue 外置组件, 拆分批次 M-1）CI E2E 回归。

覆盖:
  1. 双工位模式: 无异常按钮不出现 → 网络拦截往真实轮询响应注入 mes.recording_failures
     → 按钮浮现计数=2 → 面板行按通道正确标注（不串台）+ 原因文案映射
  2. 清空: clear emit → 父级真调后端清空 API（每通道一发）→ 按钮消失

注入手法: route 拦截 /source/detection/results, 数据走前端真实通路
（轮询 → 通道数据 → 聚合 computed → 组件 props）, 不 mock 前端内部状态。
测试收尾恢复原工位数。
"""
from __future__ import annotations

import json
import time

import requests


FAKE_FAILURES = {
    0: [{"timestamp": time.time() - 60, "channel_id": 0, "recorder_type": "cycle",
         "reason": "write_failed", "error": "moov atom not found",
         "file_path": "D:/rec/ch0/cycle_001.mp4", "frame_count": 128}],
    1: [{"timestamp": time.time() - 30, "channel_id": 1, "recorder_type": "session",
         "reason": "open_failed", "error": "",
         "file_path": "D:/rec/ch1/session_002.mp4", "frame_count": 0}],
}


def test_录像异常遮罩_注入显示与清空(page, base_url, api_url):
    inject = {"on": False}
    clear_reqs = []

    def handle(route):
        resp = route.fetch()
        if not inject["on"]:
            route.fulfill(response=resp)
            return
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        ch = 1 if "channel=1" in route.request.url else 0
        mes = data.get("mes") or {}
        mes["recording_failures"] = FAKE_FAILURES[ch]
        data["mes"] = mes
        route.fulfill(status=resp.status,
                      headers={"content-type": "application/json"},
                      body=json.dumps(data))

    orig = requests.get(f"{api_url}/api/v1/workstations/", timeout=10).json().get("channel_count", 1)
    try:
        requests.post(f"{api_url}/api/v1/workstations/mode",
                      json={"channel_count": 2}, timeout=30).raise_for_status()
        page.route("**/source/detection/results*", handle)
        page.on("request", lambda req: clear_reqs.append(req.url)
                if "recording-failures/clear" in req.url else None)

        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3.0)

        btn = page.locator("button:has-text('录像异常')")
        assert btn.count() == 0, "无异常时按钮不应出现"

        inject["on"] = True
        page.wait_for_selector("button:has-text('录像异常')", timeout=10000)
        assert "2" in btn.first.inner_text(), "双通道聚合计数应为 2"

        btn.first.click()
        time.sleep(0.8)
        body = page.evaluate("document.body.innerText")
        assert "录像异常详情" in body
        assert "工位1" in body and "工位2" in body, "双通道应各自正确标注"
        assert "写入失败/通道失效" in body and "录制器启动失败" in body, "原因文案映射应生效"

        inject["on"] = False
        # 双工位布局每列各渲染一份遮罩（共享开关状态, 与拆分前一致）, 取第一份操作
        page.locator("button:has-text('清空列表')").first.click()
        time.sleep(2.0)
        assert len(clear_reqs) >= 2, f"清空应每通道各发一次, 实际 {clear_reqs}"
        assert page.locator("button:has-text('录像异常')").count() == 0, "清空后按钮应消失"
    finally:
        page.goto("about:blank")
        requests.post(f"{api_url}/api/v1/workstations/mode",
                      json={"channel_count": orig}, timeout=30)
