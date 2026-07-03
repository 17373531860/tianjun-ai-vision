# -*- coding: utf-8 -*-
"""可见浏览器 UAT: Monitor 自定义混合模式物品校验面板外置 CustomMixItemPanel.vue（拆分批次 M-3）。

现场叙事: 客户项目是"步骤+物品"混合检测（v3.19 自定义混合, 混合类型=跟踪清点）,
操作员在 Monitor 看两块并存面板——步骤看 SOP 卡片, 物品看"物品校验"看板:
扁平清点时显示每类物品 N/期望 + 缺件批次红字; 配了容器标签则切换成
"托盘装箱"看板（当前托盘峰值/已装托盘进度/已装明细/总数模式进箱计数）。
拆分是"只搬不改", 三种形态的渲染与配色必须与拆分前一致。

验证手法: 面板数据来自单工位 150ms 结果轮询 → Playwright 拦截:
  /source/status  → 强制 is_running 让 Monitor 走真实 startPolling 分支
  /source/detection/results → 注入 custom_mix_state + tracking.item_checklist
前端走完整真实通路（轮询 → customMixState/trackingChecklist ref → 组件
props → 组件内三个 computed 推导）, 不 mock 前端内部状态。

覆盖:
  A. 不注入 → 物品校验面板不出现（负验证）
  B. 扁平清点: 标题"跟踪清点" + 物品卡 3/5 + 缺件批次红字 + 周期中标记
  C. 容器盘计数: 标题"托盘装箱" + 已装 2/4 + 当前托盘峰值卡(达标变绿) + 已装明细
  D. 容器总数模式: "已进箱滑块 12/24" 头部计数
  E. 无 console 错误

前置: 后端 8001 + 前端 6001 已启动。测试项目 __uat_ 前缀, 收尾恢复原激活项目并删除。
"""
from __future__ import annotations

import json
import sys
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"
PNAME = f"__uat_mixitem_{uuid.uuid4().hex[:5]}"

run = UatRun("custom_mix_item_panel")

# 注入状态机: 0=不注入 1=扁平清点 2=容器盘计数 3=容器总数模式
phase = {"n": 0}

_ITEMS_FLAT = [{"label": "slider", "display_name": "滑块",
                "partials": [{"peak": 2, "required": 5}]}]
_CHECKLIST = {"slider": {"counted": 3, "expected": 5,
                         "display_name": "滑块", "prefix": "S"}}

INJECT = {
    1: {
        "custom_mix_state": {"mix_type": "tracking", "cycle_active": True,
                             "items": _ITEMS_FLAT},
        "tracking": {"item_checklist": _CHECKLIST},
    },
    2: {
        "custom_mix_state": {
            "mix_type": "tracking", "cycle_active": True, "items": [],
            "container": {
                "enabled": True, "count_mode": "trays",
                "container_display": "托盘", "box_count": 4, "trays_done": 2,
                "current_tray_items": [{
                    "label": "slider", "display_name": "滑块",
                    "expected_per_tray": 6, "peak_count": 6, "current_count": 4,
                }],
                "done_detail": [{"slider": 6}, {"slider": 6}],
            },
        },
        "tracking": {"item_checklist": _CHECKLIST},
    },
    3: {
        "custom_mix_state": {
            "mix_type": "tracking", "cycle_active": False, "items": [],
            "container": {
                "enabled": True, "count_mode": "items_total",
                "container_display": "箱", "box_count": 0, "trays_done": 0,
                "item_total_done": {"slider": 12}, "item_target": 24,
                "current_tray_items": [{
                    "label": "slider", "display_name": "滑块",
                    "expected_per_tray": 0, "peak_count": 3, "current_count": 3,
                }],
                "done_detail": [],
            },
        },
        "tracking": {"item_checklist": _CHECKLIST},
    },
}


def _handle_status(route):
    resp = route.fetch()
    try:
        data = resp.json()
    except Exception:
        route.fulfill(response=resp)
        return
    data.update({"is_running": True, "is_detecting": True,
                 "source_type": "video", "model_loaded": True})
    route.fulfill(status=200, headers={"content-type": "application/json"},
                  body=json.dumps(data))


def _handle_results(route):
    resp = route.fetch()
    try:
        data = resp.json()
    except Exception:
        route.fulfill(response=resp)
        return
    data.update({"is_detecting": True, "is_running": True, "fps": 30})
    if phase["n"] in INJECT:
        data.update(INJECT[phase["n"]])
    route.fulfill(status=200, headers={"content-type": "application/json"},
                  body=json.dumps(data))


def _wait_text(page, text, timeout_s=8.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if text in page.evaluate("document.body.innerText"):
            return True
        time.sleep(0.3)
    return False


_projects = requests.get(f"{API}/projects", timeout=10).json().get("items", [])
orig_active = next((p["id"] for p in _projects if p.get("is_active")), None)
pid = None

try:
    r = requests.post(f"{API}/projects", json={
        "name": PNAME, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/projects/{pid}", json={
        "steps_config": [
            {"id": 1, "label": "step_a", "name": "A", "enabled": True},
        ],
    }, timeout=10).raise_for_status()
    requests.post(f"{API}/projects/{pid}/activate", timeout=30).raise_for_status()
    print(f">>> 测试项目 {pid} 已激活 (原激活={orig_active})")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.route("**/source/status*", _handle_status)
        page.route("**/source/detection/results*", _handle_results)

        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(3.0)

        # ---- A. 负验证 ----
        body = page.evaluate("document.body.innerText")
        run.step("A1 不注入时物品校验面板不出现", "物品校验" not in body)
        run.shot(page, "01_no_panel")

        # ---- B. 扁平清点 ----
        phase["n"] = 1
        run.step("B1 标题'物品校验 · 跟踪清点'上屏", _wait_text(page, "物品校验 · 跟踪清点"))
        body = page.evaluate("document.body.innerText")
        run.step("B2 物品卡 滑块 3/5", "滑块" in body and "3" in body and "/ 5" in body)
        run.step("B3 缺件批次红字 2/5", "缺件批次: 2/5" in body)
        run.step("B4 周期中标记", "周期中..." in body)
        run.shot(page, "02_flat_checklist")

        # ---- C. 容器盘计数 ----
        phase["n"] = 2
        run.step("C1 标题切'托盘装箱'", _wait_text(page, "物品校验 · 托盘装箱"))
        body = page.evaluate("document.body.innerText")
        run.step("C2 已装托盘 2/4", "已装托盘" in body and "2 / 4" in body)
        run.step("C3 当前托盘峰值卡在(6/6+实时4)",
                 "当前托盘峰值 · 滑块" in body and "/ 6" in body and "实时 4" in body)
        run.step("C4 已装明细两盘", "已装明细" in body and "第2盘: 6" in body)
        peak_green = page.evaluate(
            """() => {
              const el = [...document.querySelectorAll('span')]
                .find(s => s.className.includes('text-4xl') && s.textContent.trim() === '6');
              return el ? el.className.includes('text-green-400') : null;
            }""")
        run.step("C5 峰值达标数字变绿", peak_green is True, f"green={peak_green}")
        run.shot(page, "03_container_trays")

        # ---- D. 容器总数模式 ----
        phase["n"] = 3
        run.step("D1 已进箱滑块 12/24", _wait_text(page, "已进箱滑块"))
        body = page.evaluate("document.body.innerText")
        run.step("D2 计数 12 / 24 上屏", "12 / 24" in body)
        run.step("D3 等待周期标记(cycle_active=false)", "等待周期开始" in body)
        run.shot(page, "04_items_total")

        errs = filter_console_errors(cerrs)
        run.step("E1 无前端 console 错误", len(errs) == 0, f"errs={errs[:3]}")

        page.unroute_all(behavior="ignoreErrors")
        ctx.close()
        browser.close()
finally:
    try:
        if orig_active:
            requests.post(f"{API}/projects/{orig_active}/activate", timeout=30)
        if pid:
            requests.delete(f"{API}/projects/{pid}", timeout=10)
    except Exception as e:
        print(f"!!! 收尾恢复失败, 请手动检查: {e}")
    run.finish()
