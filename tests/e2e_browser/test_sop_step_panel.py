"""Monitor SOP 流程卡片面板（SopStepPanel.vue 外置组件, 拆分批次 M-2）CI E2E 回归。

覆盖:
  1. 激活顺序模式项目 → SOP 面板与三张步骤卡片渲染, 初始 pending 置灰
  2. 注入周期进行中数据 → 已做步骤卡片点亮、未做仍置灰、步骤间隔秒数上屏

注入手法: route 拦截 /source/status (强制 is_running 走真实 startPolling 分支) +
/source/detection/results (注入 current_cycle_steps/step_counts/step_intervals),
数据走前端完整真实通路（轮询 → updateStepsFromBackend 状态推导 → steps ref →
SopStepPanel props 渲染）, 不 mock 前端内部状态。
测试收尾恢复原激活项目并删除测试项目。
"""
from __future__ import annotations

import json
import time
import uuid

import requests


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


def _wait_card_class(page, label, predicate, timeout_s=8.0):
    deadline = time.time() + timeout_s
    cls = None
    while time.time() < deadline:
        cls = page.evaluate(_CARD_CLASS_JS, label)
        if cls is not None and predicate(cls):
            return True, cls
        time.sleep(0.3)
    return False, cls


def test_sop流程卡片面板_注入点亮与间隔(page, base_url, api_url):
    inject = {"on": False}

    def handle_status(route):
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

    def handle_results(route):
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        data.update({"is_detecting": True, "is_running": True, "fps": 30})
        if inject["on"]:
            data.update({
                "current_cycle_steps": ["step_a"],
                "step_counts": {"step_a": 1},
                "step_intervals": {"step_b": 2.5},
            })
        route.fulfill(status=200, headers={"content-type": "application/json"},
                      body=json.dumps(data))

    pname = f"__e2e_sop_{uuid.uuid4().hex[:5]}"
    _projects = requests.get(f"{api_url}/api/v1/projects", timeout=10).json().get("items", [])
    orig_active = next((p["id"] for p in _projects if p.get("is_active")), None)
    pid = None
    try:
        r = requests.post(f"{api_url}/api/v1/projects", json={
            "name": pname, "task_type": "detection", "logic_mode": "sequential",
        }, timeout=10)
        r.raise_for_status()
        pid = r.json()["id"]
        requests.put(f"{api_url}/api/v1/projects/{pid}", json={
            "steps_config": [
                {"id": 1, "label": "step_a", "name": "A", "enabled": True},
                {"id": 2, "label": "step_b", "name": "B", "enabled": True},
                {"id": 3, "label": "step_c", "name": "C", "enabled": True},
            ],
        }, timeout=10).raise_for_status()
        requests.post(f"{api_url}/api/v1/projects/{pid}/activate", timeout=30).raise_for_status()

        page.route("**/source/status*", handle_status)
        page.route("**/source/detection/results*", handle_results)
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3.0)

        body = page.evaluate("document.body.innerText")
        assert "SOP流程卡片" in body, "SOP 面板应渲染"
        assert all(f"step_{x}" in body for x in ("a", "b", "c")), "三张步骤卡片应全渲染"
        cls0 = page.evaluate(_CARD_CLASS_JS, "step_a")
        assert cls0 and "opacity-60" in cls0, f"初始应 pending 置灰: {cls0}"

        inject["on"] = True
        ok_a, cls_a = _wait_card_class(
            page, "step_a",
            lambda c: "border-green-500" in c or "border-cyan-500" in c)
        assert ok_a, f"step_a 卡片应点亮: {cls_a}"
        cls_c = page.evaluate(_CARD_CLASS_JS, "step_c")
        assert cls_c and "opacity-60" in cls_c, f"step_c 未做应置灰: {cls_c}"
        assert "2.5s" in page.evaluate("document.body.innerText"), "步骤间隔 2.5s 应上屏"
    finally:
        page.unroute_all(behavior="ignoreErrors")
        if orig_active:
            requests.post(f"{api_url}/api/v1/projects/{orig_active}/activate", timeout=30)
        if pid:
            requests.delete(f"{api_url}/api/v1/projects/{pid}", timeout=10)
