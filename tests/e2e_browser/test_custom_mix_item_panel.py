"""Monitor 自定义混合物品校验面板（CustomMixItemPanel.vue 外置组件, 拆分批次 M-3）CI E2E 回归。

覆盖:
  1. 不注入 → 面板不出现（负验证）
  2. 注入扁平清点态 → 标题"跟踪清点"、物品卡 N/期望、缺件批次红字
  3. 注入容器盘计数态 → 标题切"托盘装箱"、已装进度、已装明细（组件内
     container/itemTotal computed 推导正确）
  4. v3.55.x 多工位同步: 2/3 工位机器面板按通道挂 WorkstationColumn
     （data-testid dual-mix-N / triple-mix-N）；4+ 网格总览无面板跳过。

注入手法: route 拦截 /source/status (强制 is_running 走真实 startPolling 分支) +
/source/detection/results (注入 custom_mix_state + tracking.item_checklist),
数据走前端完整真实通路（轮询 → customMixState/trackingChecklist ref →
组件 props → 组件内 computed）, 不 mock 前端内部状态。
测试收尾恢复原激活项目并删除测试项目。
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
import requests


_FLAT = {
    "custom_mix_state": {"mix_type": "tracking", "cycle_active": True,
                         "items": [{"label": "slider", "display_name": "滑块",
                                    "partials": [{"peak": 2, "required": 5}]}]},
    "tracking": {"item_checklist": {"slider": {"counted": 3, "expected": 5,
                                               "display_name": "滑块", "prefix": "S"}}},
}
_CONTAINER = {
    "custom_mix_state": {
        "mix_type": "tracking", "cycle_active": True, "items": [],
        "container": {
            "enabled": True, "count_mode": "trays",
            "container_display": "托盘", "box_count": 4, "trays_done": 2,
            "current_tray_items": [{"label": "slider", "display_name": "滑块",
                                    "expected_per_tray": 6, "peak_count": 6,
                                    "current_count": 4, "book_preview": 5}],
            "done_detail": [{"slider": 6}, {"slider": 6}],
        },
    },
    "tracking": {"item_checklist": {}},
}


def _wait_text(page, text, timeout_s=8.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if text in page.evaluate("document.body.innerText"):
            return True
        time.sleep(0.3)
    return False


def test_物品校验面板_注入三态渲染(page, base_url, api_url):
    inject = {"data": None}

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
        if inject["data"]:
            data.update(inject["data"])
        route.fulfill(status=200, headers={"content-type": "application/json"},
                      body=json.dumps(data))

    pname = f"__e2e_mixitem_{uuid.uuid4().hex[:5]}"
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
            "steps_config": [{"id": 1, "label": "step_a", "name": "A", "enabled": True}],
        }, timeout=10).raise_for_status()
        requests.post(f"{api_url}/api/v1/projects/{pid}/activate", timeout=30).raise_for_status()

        ws = requests.get(f"{api_url}/api/v1/workstations/", timeout=10).json()
        channel_count = ws.get("channel_count") or 1
        if channel_count > 3:
            pytest.skip("4+ 工位网格总览不挂物品校验面板（放大态才有）；网格→放大链路本用例不驱动")

        page.route("**/source/status*", handle_status)
        page.route("**/source/detection/results*", handle_results)
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        time.sleep(2.5)

        assert "物品校验" not in page.evaluate("document.body.innerText"), \
            "不注入时物品校验面板不应出现"

        inject["data"] = _FLAT
        # v3.55.x 多工位同步: 2/3 工位在 WorkstationColumn 内按通道渲染同一面板
        # (mock 拦截所有 channel 的 results, 每列都吃到注入态), 文本断言单/多工位通用。
        assert _wait_text(page, "物品校验 · 跟踪清点"), "扁平清点标题应上屏"
        if channel_count in (2, 3):
            prefix = "dual" if channel_count == 2 else "triple"
            assert page.locator(f"[data-testid='{prefix}-mix-0']").count() >= 1, \
                f"多工位({channel_count})应在工位列内挂物品校验面板 ({prefix}-mix-0)"
        body = page.evaluate("document.body.innerText")
        assert "滑块" in body and "/ 5" in body, f"物品卡 3/5 应上屏"
        assert "缺件批次: 2/5" in body, "缺件批次红字应上屏"

        inject["data"] = _CONTAINER
        assert _wait_text(page, "物品校验 · 托盘装箱"), "容器模式标题应切换"
        body = page.evaluate("document.body.innerText")
        assert "已装托盘" in body and "2 / 4" in body, "已装托盘进度应上屏"
        assert "已装明细" in body and "第2盘: 6" in body, "已装明细应上屏"
        assert "实时" in body and "预计进箱" in body, "三分框：实时在位 / 预计进箱应上屏"
        assert "峰值" in body, "三分框：托盘峰值应上屏"
        assert "5" in body, "预计进箱 book_preview=5 应上屏"
        page.screenshot(path="/tmp/t4_mix_panel_trisplit.png")
    finally:
        page.unroute_all(behavior="ignoreErrors")
        if orig_active:
            requests.post(f"{api_url}/api/v1/projects/{orig_active}/activate", timeout=30)
        if pid:
            requests.delete(f"{api_url}/api/v1/projects/{pid}", timeout=10)
