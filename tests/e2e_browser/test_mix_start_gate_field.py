"""混合逐件「开始判定」配置字段 (v3.60.2) CI E2E 回归。

覆盖:
  1. 混合逐件配对卡渲染「开始判定」区块 (稳定窗口 + 开始标签双条件)
  2. 勾稳定窗口 → 保存 → pipeline_config.per_item.start_by_stability 落库
  3. 手输开始标签 → 保存 → start_labels/start_sustain_frames/start_conf 落库
  4. 都不动 → 保存 → per_item 保持 {} (存量零配置差异守门)
  5. 监控页逐件面板徽标: 注入 start_gate 未通过 → 「等待开始（缺 …）」;
     通过 → 「已开始」(route 拦截注入法, 同 test_custom_mix_item_panel)

背景: 2026-09 六和二工位 — 摆螺丝阶段模型误报"已完成"把逐件账本假盖章
灌满致假 OK; 补回中捷独立逐件"稳定窗口才开周期"语义 (混合版可选双条件)。
后端闸门语义见 tests/test_custom_mix_start_gate.py。

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
import requests

from .conftest import E2E_PREFIX


_PER_ITEM_STEP = {
    "id": 1, "label": "打螺丝", "displayLabel": "打螺丝", "enabled": True,
    "threshold": 50, "detect_role": "item",
    "per_item": {
        "item_label": "螺丝", "action_label": "打螺丝",
        "item_tracking_iou": 0.3, "coverage_iou": 0.3,
        "coverage_use_center": True, "sustain_frames": 3,
        "completion": "all_covered", "expected_count": 4,
    },
}


def _mk_mix_project(api_url):
    name = f"{E2E_PREFIX}sgate_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection",
        "logic_mode": "custom",
        "pipeline_config": {"custom_based_on": "sequential",
                            "custom_mixed_with": "per_item"},
        "steps_config": [
            {"id": "s1", "label": "扫码", "enabled": True},
            dict(_PER_ITEM_STEP),
        ],
    }, timeout=5)
    r.raise_for_status()
    return r.json()["id"], name


def _open_steps_tab(page, base_url, name):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    card = page.locator(f"div.p-4:has-text('{name}')").first
    if card.count() == 0:
        card = page.get_by_text(name, exact=False).first
    card.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
    time.sleep(0.8)


def _save_and_fetch(page, api_url, pid):
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    return requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()


def test_开始判定区块渲染(page, base_url, api_url):
    pid, name = _mk_mix_project(api_url)
    _open_steps_tab(page, base_url, name)
    body = page.evaluate("document.body.innerText")
    assert "开始判定" in body, "混合逐件配对卡应渲染「开始判定」区块"
    assert "稳定窗口" in body and "开始标签" in body
    page.screenshot(path="/tmp/t6_start_gate_card.png")


def test_稳定窗口勾选_落库(page, base_url, api_url):
    pid, name = _mk_mix_project(api_url)
    _open_steps_tab(page, base_url, name)
    cb = page.locator("[data-testid='mix-start-stability-checkbox']").first
    cb.scroll_into_view_if_needed()
    cb.click()
    time.sleep(0.3)
    detail = _save_and_fetch(page, api_url, pid)
    per = (detail.get("pipeline_config") or {}).get("per_item") or {}
    assert per.get("start_by_stability") is True, \
        f"start_by_stability 应落库 True, 实际 {per}"
    assert per.get("stability_window_frames") == 10   # 三件套随写


def test_开始标签手输_落库(page, base_url, api_url):
    pid, name = _mk_mix_project(api_url)
    _open_steps_tab(page, base_url, name)
    sel = page.locator("[data-testid='mix-start-labels-select']").first
    sel.scroll_into_view_if_needed()
    sel.click()
    time.sleep(0.3)
    # allow-create: 输入自定义标签名后点创建项 (default-first-option 下 Enter 亦可)
    sel.locator("input").first.fill("工件就位")
    time.sleep(0.8)
    created = page.locator(
        ".el-select-dropdown:visible .el-select-dropdown__item:has-text('工件就位')").first
    created.click()
    # 等 tag 真正挂上再收下拉 (点创建项后 Vue 响应式更新有延迟)
    page.locator("[data-testid='mix-start-labels-select'] .el-tag"
                 ":has-text('工件就位')").first.wait_for(timeout=5000)
    page.keyboard.press("Escape")
    time.sleep(0.3)
    # 标签非空后 sustain/conf 输入解禁。
    # ⚠️ 不用 is_disabled(): Element Plus el-input-number 解禁时移除 disabled
    # 属性但内层 input 的 aria-disabled 不响应式更新 (EP 怪癖, 真人可正常输入),
    # Playwright 会把 aria-disabled=true 当"未启用"误报。
    sus = page.locator("[data-testid='mix-start-sustain-input'] input").first
    assert sus.get_attribute("disabled") is None, "配了开始标签后连续帧输入应解禁"
    page.screenshot(path="/tmp/t6_start_gate_labels.png")
    detail = _save_and_fetch(page, api_url, pid)
    per = (detail.get("pipeline_config") or {}).get("per_item") or {}
    assert per.get("start_labels") == ["工件就位"], \
        f"start_labels 应落库 ['工件就位'], 实际 {per}"
    assert per.get("start_sustain_frames") == 3
    assert per.get("start_conf") == 0.5


def test_默认不动_per_item保持空_零配置差异(page, base_url, api_url):
    pid, name = _mk_mix_project(api_url)
    _open_steps_tab(page, base_url, name)
    detail = _save_and_fetch(page, api_url, pid)
    per = (detail.get("pipeline_config") or {}).get("per_item")
    assert per in (None, {}), \
        f"两条件都不配时 pipeline.per_item 应保持空 (零配置差异), 实际 {per}"


# ── 监控页徽标 (route 注入法: /source/status 强制 is_running +
#    /detection/results 注入 custom_mix_state.start_gate, 走前端真实轮询通路) ──

_MIX_STEPS = [{"label": "螺丝锁付-已完成", "display_label": "锁付", "role": "pair",
               "expected_count": 4, "covered_count": 0, "total": 0,
               "completed": False, "items": []}]


def _gate_state(started, missing):
    return {"custom_mix_state": {
        "mix_type": "per_item", "cycle_active": True,
        "steps": _MIX_STEPS, "items": [], "last_ng_detail": None,
        "start_gate": {"started": started, "missing": missing},
    }}


def _wait_text(page, text, timeout_s=8.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if text in page.evaluate("document.body.innerText"):
            return True
        time.sleep(0.3)
    return False


def test_监控徽标_等待开始与已开始(page, base_url, api_url):
    ws = requests.get(f"{api_url}/api/v1/workstations/", timeout=10).json()
    if (ws.get("channel_count") or 1) > 3:
        pytest.skip("4+ 工位网格总览不挂逐件面板（放大态才有）")

    inject = {"data": _gate_state(False, ["稳定窗口", "就位-右下"])}

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
        data.update(inject["data"])
        route.fulfill(status=200, headers={"content-type": "application/json"},
                      body=json.dumps(data))

    try:
        page.route("**/source/status*", handle_status)
        page.route("**/source/detection/results*", handle_results)
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)

        assert _wait_text(page, "等待开始"), "闸门未通过应亮「等待开始」徽标"
        body = page.evaluate("document.body.innerText")
        assert "就位-右下" in body, "缺失条件明细应上屏"
        page.screenshot(path="/tmp/t6_start_gate_waiting.png")

        inject["data"] = _gate_state(True, [])
        assert _wait_text(page, "已开始"), "闸门通过应转「已开始」"
        body = page.evaluate("document.body.innerText")
        assert "等待开始" not in body, "通过后等待徽标应消失"
        page.screenshot(path="/tmp/t6_start_gate_started.png")
    finally:
        page.unroute_all(behavior="ignoreErrors")
