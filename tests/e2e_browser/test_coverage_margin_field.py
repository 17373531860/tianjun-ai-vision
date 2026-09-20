"""动作框扩边救援 coverage_margin 配置字段 (v3.57) CI E2E 回归。

覆盖:
  1. 独立逐件 (LogicConfigTab「每个步骤的角色」卡): 字段可见 → 改 1.0 →
     保存 → steps_config[i].per_item.coverage_margin 落库
  2. 顺序/自定义混合逐件 (StepsConfigTab 逐件配对卡): 同字段可见可存

背景: 2026-09 六和螺丝锁付 — 电枪"已完成"框标注偏移螺丝中心, 个别螺丝
永远盖不到 → 31/32 误 NG。后端救援语义见 test_per_item_coverage_margin.py。

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX


def _mk_project(api_url, payload):
    name = f"{E2E_PREFIX}cvm_{uuid.uuid4().hex[:6]}"
    payload = {"name": name, "task_type": "detection", **payload}
    r = requests.post(f"{api_url}/api/v1/projects", json=payload, timeout=5)
    r.raise_for_status()
    return r.json()["id"], name


_PER_ITEM_STEP = {
    "id": 1, "label": "打螺丝", "displayLabel": "打螺丝", "enabled": True,
    "threshold": 50,
    "per_item": {
        "item_label": "螺丝", "action_label": "打螺丝",
        "item_tracking_iou": 0.3, "coverage_iou": 0.3,
        "coverage_use_center": True, "sustain_frames": 3,
        "completion": "all_covered", "min_item_count": "auto",
    },
}


def _open_project_tab(page, base_url, name, tab):
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
    page.locator(f".el-tabs__item:has-text('{tab}')").first.click()
    time.sleep(0.8)


def _save_and_fetch(page, api_url, pid):
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    return requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()


def test_独立逐件_扩边字段可见可存(page, base_url, api_url):
    pid, name = _mk_project(api_url, {
        "logic_mode": "per_item",
        "steps_config": [_PER_ITEM_STEP],
    })
    _open_project_tab(page, base_url, name, "逻辑设置")
    body = page.evaluate("document.body.innerText")
    assert "动作框扩边救援" in body, "独立逐件角色卡应渲染「动作框扩边救援」"
    inp = page.locator("[data-testid='coverage-margin-input'] input").first
    inp.fill("1.0")
    inp.blur()
    time.sleep(0.3)
    page.screenshot(path="/tmp/t4_coverage_margin_logic.png")
    detail = _save_and_fetch(page, api_url, pid)
    per = (detail.get("steps_config") or [{}])[0].get("per_item") or {}
    assert per.get("coverage_margin") == 1.0, \
        f"coverage_margin 应落库 1.0, 实际 {per.get('coverage_margin')}"


def test_整板拖动重配准开关_可见可存(page, base_url, api_url):
    """v3.57 board_rereg_enabled: 逐件覆盖通用参数卡可见, 默认关, 开启后落库."""
    pid, name = _mk_project(api_url, {
        "logic_mode": "per_item",
        "steps_config": [_PER_ITEM_STEP],
    })
    _open_project_tab(page, base_url, name, "逻辑设置")
    body = page.evaluate("document.body.innerText")
    assert "整板拖动重配准" in body, "通用参数卡应渲染「整板拖动重配准」开关"
    sw = page.locator("[data-testid='board-rereg-switch']").first
    sw.scroll_into_view_if_needed()
    sw.click()
    time.sleep(0.3)
    page.screenshot(path="/tmp/t4_board_rereg_switch.png")
    detail = _save_and_fetch(page, api_url, pid)
    per = (detail.get("pipeline_config") or {}).get("per_item") or {}
    assert per.get("board_rereg_enabled") is True, \
        f"board_rereg_enabled 应落库 True, 实际 {per.get('board_rereg_enabled')}"


def test_混合逐件_扩边字段可见可存(page, base_url, api_url):
    step = dict(_PER_ITEM_STEP)
    step["detect_role"] = "item"   # 混合逐件行 = detect_role='item'
    pid, name = _mk_project(api_url, {
        "logic_mode": "custom",
        "pipeline_config": {"custom_mixed_with": "per_item"},
        "steps_config": [step],
    })
    _open_project_tab(page, base_url, name, "步骤设置")
    body = page.evaluate("document.body.innerText")
    assert "动作框扩边救援" in body, "混合逐件配对卡应渲染「动作框扩边救援」"
    inp = page.locator("[data-testid='mix-coverage-margin-input'] input").first
    inp.scroll_into_view_if_needed()
    inp.fill("0.5")
    inp.blur()
    time.sleep(0.3)
    page.screenshot(path="/tmp/t4_coverage_margin_mix.png")
    detail = _save_and_fetch(page, api_url, pid)
    per = (detail.get("steps_config") or [{}])[0].get("per_item") or {}
    assert per.get("coverage_margin") == 0.5, \
        f"coverage_margin 应落库 0.5, 实际 {per.get('coverage_margin')}"
