# -*- coding: utf-8 -*-
"""可见浏览器 UAT: Monitor SOP 流程卡片面板外置 SopStepPanel.vue（拆分批次 M-2）。

现场叙事: 操作员激活顺序模式项目后开检, Monitor 视频下方 SOP 流程卡片栏
逐步点亮——做到哪步哪张卡片变色, 卡片之间显示步骤间隔秒数, 周期推进时
卡片自动滚动。拆分是"只搬不改", 卡片渲染/变色/间隔/滚动必须与拆分前一致。

验证手法: 面板数据来自单工位 150ms 结果轮询 → Playwright 拦截两处真实请求:
  /source/status  → 改 is_running=true 让 Monitor 走真实 startPolling 分支
  /source/detection/results → 注入 current_cycle_steps/step_counts/step_intervals
前端走完整真实处理链路（轮询 → updateStepsFromBackend ~300 行状态推导 →
steps ref → SopStepPanel props 渲染）, 不 mock 前端内部状态。

覆盖:
  A. 激活项目后 SOP 面板出现, 三张卡片全渲染, 初始 pending 置灰
  B. 注入周期进行中(step_a 已做) → step_a 卡片点亮(绿/青), step_c 仍置灰;
     步骤间隔 '2.5s' 上屏（formatInterval 平移后仍工作）
  C. 注入周期推进(三步全做) → step_c 卡片也点亮
  D. 无 console 错误（含 sopPanelRef 滚动驱动链不抛错）

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
PNAME = f"__uat_sop_{uuid.uuid4().hex[:5]}"

run = UatRun("sop_step_panel")

# 注入状态机: phase 0=不注入(空跑) 1=step_a 已做 2=三步全做
phase = {"n": 0}

INJECT = {
    1: {"current_cycle_steps": ["step_a"], "step_counts": {"step_a": 1},
        "step_intervals": {"step_b": 2.5}},
    2: {"current_cycle_steps": ["step_a", "step_b", "step_c"],
        "step_counts": {"step_a": 1, "step_b": 1, "step_c": 1},
        "step_intervals": {"step_b": 2.5, "step_c": 1.2}},
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


_CARD_CLASS_JS = """(label) => {
  const title = [...document.querySelectorAll('span')]
    .find(s => s.textContent.trim() === 'SOP流程卡片');
  if (!title) return null;
  const panel = title.closest('.h-44');
  if (!panel) return null;
  const card = [...panel.querySelectorAll('.w-32')]
    .find(c => c.innerText.includes(label));
  return card ? card.className : null;
}"""


def _sop_card_class(page, label):
    """SOP 面板内某步骤卡片的 class（变色断言用）: 从标题 span 定位面板再找卡片"""
    return page.evaluate(_CARD_CLASS_JS, label)


def _wait_card_class(page, label, predicate, timeout_s=8.0):
    """轮询等待卡片 class 满足条件（状态推导经 150ms 轮询异步落 DOM, 不能单点采样）"""
    deadline = time.time() + timeout_s
    cls = None
    while time.time() < deadline:
        cls = _sop_card_class(page, label)
        if cls is not None and predicate(cls):
            return True, cls
        time.sleep(0.3)
    return False, cls


# 记录原激活项目, 收尾恢复
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
            {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
            {"id": 2, "label": "step_b", "name": "步骤B", "enabled": True},
            {"id": 3, "label": "step_c", "name": "步骤C", "enabled": True},
        ],
    }, timeout=10).raise_for_status()
    requests.post(f"{API}/projects/{pid}/activate", timeout=30).raise_for_status()
    print(f">>> 测试项目 {pid} 已激活 (原激活={orig_active})")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.route("**/source/status*", _handle_status)
        page.route("**/source/detection/results*", _handle_results)

        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(3.5)

        # ---- A. 面板与卡片渲染 ----
        body = page.evaluate("document.body.innerText")
        run.step("A1 SOP 流程卡片面板在", "SOP流程卡片" in body)
        # 卡片显示名 = displayLabel || label, 测试项目未配 displayLabel → 显示原始 label
        run.step("A2 三张步骤卡片全渲染",
                 all(f"step_{x}" in body for x in ("a", "b", "c")))
        cls_c0 = _sop_card_class(page, "step_c")
        run.step("A3 初始 pending 卡片置灰", cls_c0 is not None and "opacity-60" in cls_c0,
                 f"cls={cls_c0}")
        run.shot(page, "01_panel_pending")

        # ---- B. 周期进行中: step_a 点亮 + 间隔上屏 ----
        phase["n"] = 1
        ok_a, cls_a = _wait_card_class(
            page, "step_a",
            lambda c: "border-green-500" in c or "border-cyan-500" in c)
        cls_c = _sop_card_class(page, "step_c")
        run.step("B1 step_a 卡片点亮(绿/青)", ok_a, f"cls={cls_a}")
        run.step("B2 step_c 未做仍置灰", cls_c is not None and "opacity-60" in cls_c,
                 f"cls={cls_c}")
        body = page.evaluate("document.body.innerText")
        run.step("B3 步骤间隔 2.5s 上屏(formatInterval 平移)", "2.5s" in body)
        run.shot(page, "02_step_a_done")

        # ---- C. 周期推进: 三步全做 ----
        phase["n"] = 2
        ok_c, cls_c2 = _wait_card_class(page, "step_c",
                                        lambda c: "opacity-60" not in c)
        run.step("C1 step_c 卡片点亮", ok_c, f"cls={cls_c2}")
        run.shot(page, "03_all_done")

        errs = filter_console_errors(cerrs)
        run.step("D1 无前端 console 错误", len(errs) == 0, f"errs={errs[:3]}")

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
