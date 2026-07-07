# -*- coding: utf-8 -*-
"""可见浏览器 UAT: Monitor 录像异常遮罩外置 RecordingFailureOverlay.vue（拆分批次 M-1）。

现场叙事: 双工位客户现场录像器写盘失败, 后端把异常挂进轮询结果的 MES 区,
Monitor 右下角浮出「录像异常 N」琥珀色按钮; 操作员点开详情面板, 逐行看到
时间/工位/类型/原因/文件/已写帧; 点「清空列表」后按钮消失。

验证手法: 组件是纯展示件, 数据来自 150ms 结果轮询 → 用 Playwright 网络拦截
往真实轮询响应里注入 mes.recording_failures（前端真实代码通路: 轮询 → 通道
数据 → 聚合 computed → 组件 props）, 不 mock 前端内部状态。

覆盖:
  A. 双工位模式(遮罩4处调用点之一): 无异常时按钮不出现 → 注入 ch0+ch1 各一条
     → 按钮计数=2 → 面板行含 工位1/工位2 且原因文案映射正确（双通道标注不串台）
  B. 清空: 停注入后点「清空列表」→ 清空 API 真实发出(每通道一发) → 按钮消失
  C. 单工位模式恢复 + 无异常时按钮不出现（负验证）

前置: 后端 8001 + 前端 6001 已启动。收尾恢复单工位模式。
"""
from __future__ import annotations

import json
import sys
import time

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"

run = UatRun("recording_failure_overlay")

# 注入开关（route handler 闭包读它）与清空请求记录
inject_enabled = {"on": False}
clear_requests = []

FAKE_FAILURES = {
    0: [{
        "timestamp": time.time() - 60,
        "channel_id": 0,
        "recorder_type": "cycle",
        "reason": "write_failed",
        "error": "moov atom not found",
        "file_path": "D:/recordings/ch0/cycle_001.mp4",
        "frame_count": 128,
    }],
    1: [{
        "timestamp": time.time() - 30,
        "channel_id": 1,
        "recorder_type": "session",
        "reason": "open_failed",
        "error": "",
        "file_path": "D:/recordings/ch1/session_002.mp4",
        "frame_count": 0,
    }],
}


def _handle_results(route):
    """真实转发轮询请求, 按需往响应 JSON 的 mes 区注入录像异常。"""
    resp = route.fetch()
    if not inject_enabled["on"]:
        route.fulfill(response=resp)
        return
    try:
        data = resp.json()
    except Exception:
        route.fulfill(response=resp)
        return
    url = route.request.url
    ch = 1 if "channel=1" in url else 0
    mes = data.get("mes") or {}
    mes["recording_failures"] = FAKE_FAILURES[ch]
    data["mes"] = mes
    route.fulfill(status=resp.status, headers={"content-type": "application/json"},
                  body=json.dumps(data))


def _set_channels(count):
    requests.post(f"{API}/workstations/mode", json={"channel_count": count},
                  timeout=30).raise_for_status()


orig_count = requests.get(f"{API}/workstations/", timeout=10).json().get("channel_count", 1)

try:
    _set_channels(2)
    print(">>> 已切双工位模式")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.route("**/source/detection/results*", _handle_results)
        page.on("request", lambda req: clear_requests.append(req.url)
                if "recording-failures/clear" in req.url else None)

        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(3.0)

        # ---- A. 无异常 → 按钮不出现; 注入 → 按钮计数=2, 面板内容正确 ----
        btn = page.locator("button:has-text('录像异常')")
        run.step("A1 无异常时按钮不出现", btn.count() == 0, f"count={btn.count()}")

        inject_enabled["on"] = True
        page.wait_for_selector("button:has-text('录像异常')", timeout=10000)
        btn_text = page.locator("button:has-text('录像异常')").first.inner_text()
        run.step("A2 注入后按钮浮现且计数=2(双通道聚合)", "2" in btn_text, f"text={btn_text!r}")
        run.shot(page, "01_button_visible")

        page.locator("button:has-text('录像异常')").first.click()
        time.sleep(0.8)
        body = page.evaluate("document.body.innerText")
        run.step("A3 详情面板打开", "录像异常详情" in body and "仅记录最近异常" in body)
        run.step("A4 双通道各自标注不串台",
                 "工位1" in body and "工位2" in body
                 and "cycle_001.mp4" in body and "session_002.mp4" in body)
        run.step("A5 原因文案映射正确(组件内平移的 map)",
                 "写入失败/通道失效" in body and "录制器启动失败" in body
                 and "moov atom not found" in body)
        run.shot(page, "02_panel_rows")

        # ---- B. 清空: 停注入 → 点清空 → API 真实发出 → 按钮消失 ----
        inject_enabled["on"] = False
        # 双工位布局原生就是每列各渲染一份遮罩（共享开关状态）, 取第一份操作
        page.locator("button:has-text('清空列表')").first.click()
        time.sleep(2.0)
        run.step("B1 清空 API 每通道各发一次(clear emit → 父级真调后端)",
                 len(clear_requests) >= 2, f"reqs={clear_requests}")
        run.step("B2 清空后按钮消失",
                 page.locator("button:has-text('录像异常')").count() == 0)
        run.shot(page, "03_after_clear")

        # ---- C. 恢复单工位, 负验证 ----
        # 先离开 Monitor 停掉双工位轮询再切模式, 避免在途 channel=1 轮询吃 404 竞态噪声
        page.goto("about:blank")
        time.sleep(0.5)
        _set_channels(1)
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(3.0)
        run.step("C1 单工位无异常按钮不出现",
                 page.locator("button:has-text('录像异常')").count() == 0)
        run.shot(page, "04_single_mode")

        errs = filter_console_errors(cerrs)
        run.step("D1 无前端 console 错误", len(errs) == 0, f"errs={errs[:3]}")

        ctx.close()
        browser.close()
finally:
    try:
        _set_channels(orig_count)
        print(f">>> 已恢复工位数={orig_count}")
    except Exception as e:
        print(f"!!! 工位数恢复失败, 请手动检查: {e}")
    run.finish()
