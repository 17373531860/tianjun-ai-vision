"""逐件虚拟步骤 (v3.57 custom_mix_per_item_virtual_step) 配置 CI E2E 回归。

覆盖:
  1. 混合逐件下「逐件作业合并为一步」开关可见 → 开启 + 填步骤名 → 保存 →
     pipeline_config 两键落库 + steps_config 自动生成 per_item_virtual 行
  2. 关闭开关 → 自动生成的行被移除

背景: 2026-09 六和锁螺丝工艺新增首尾扫码 — 序列 [扫码, 锁付完成(虚拟), 扫码],
后端注入/结算语义见 tests/test_custom_mix_pi_virtual_step.py 与真实视频回放。

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX


_ITEM_STEP = {
    "id": 1, "label": "锁螺丝", "displayLabel": "锁螺丝", "enabled": True,
    "threshold": 50, "detect_role": "item",
    "per_item": {
        "item_label": "螺丝锁付位置", "action_label": "螺丝锁付-已完成",
        "item_tracking_iou": 0.3, "coverage_iou": 0.3,
        "coverage_use_center": True, "sustain_frames": 3,
        "completion": "all_covered", "expected_count": 32,
    },
}
_SCAN_STEP = {"id": 2, "label": "扫码", "displayLabel": "扫码确认",
              "enabled": True, "threshold": 45, "min_frames": 3,
              "disappear_delay": 8}


def _mk_project(api_url):
    name = f"{E2E_PREFIX}pivs_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection",
        "logic_mode": "custom",
        "pipeline_config": {"custom_based_on": "sequential",
                            "custom_mixed_with": "per_item"},
        "steps_config": [_SCAN_STEP, _ITEM_STEP],
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


def test_逐件虚拟步骤_开启落库并自动建行(page, base_url, api_url):
    pid, name = _mk_project(api_url)
    _open_steps_tab(page, base_url, name)
    body = page.evaluate("document.body.innerText")
    assert "逐件作业合并为一步" in body, "混合逐件物品卡应渲染「逐件作业合并为一步」"

    sw = page.locator("[data-testid='pi-virtual-step-switch']").first
    sw.scroll_into_view_if_needed()
    sw.click()
    time.sleep(0.3)
    inp = page.locator("input[data-testid='pi-virtual-step-label-input']").first
    if inp.count() == 0:
        inp = page.locator("[data-testid='pi-virtual-step-label-input'] input").first
    inp.fill("锁付完成")
    time.sleep(0.5)
    page.screenshot(path="/tmp/t4_pi_virtual_step_on.png")

    detail = _save_and_fetch(page, api_url, pid)
    pc = detail.get("pipeline_config") or {}
    assert pc.get("custom_mix_per_item_virtual_step") is True, \
        f"开关应落库 True, 实际 {pc.get('custom_mix_per_item_virtual_step')}"
    assert pc.get("custom_mix_per_item_virtual_step_label") == "锁付完成", \
        f"标签应落库, 实际 {pc.get('custom_mix_per_item_virtual_step_label')!r}"
    virt_rows = [s for s in (detail.get("steps_config") or [])
                 if s.get("per_item_virtual")]
    assert len(virt_rows) == 1 and virt_rows[0].get("label") == "锁付完成", \
        f"应自动生成一条 per_item_virtual 步骤行: {virt_rows}"
    assert virt_rows[0].get("enabled") is True


def test_逐件虚拟步骤_关闭移除自动行(page, base_url, api_url):
    pid, name = _mk_project(api_url)
    _open_steps_tab(page, base_url, name)
    sw = page.locator("[data-testid='pi-virtual-step-switch']").first
    sw.scroll_into_view_if_needed()
    sw.click()
    time.sleep(0.3)
    inp = page.locator("input[data-testid='pi-virtual-step-label-input']").first
    if inp.count() == 0:
        inp = page.locator("[data-testid='pi-virtual-step-label-input'] input").first
    inp.fill("锁付完成")
    time.sleep(0.5)
    detail = _save_and_fetch(page, api_url, pid)
    assert any(s.get("per_item_virtual") for s in detail.get("steps_config") or [])

    # 关闭开关 → 行移除
    sw = page.locator("[data-testid='pi-virtual-step-switch']").first
    sw.scroll_into_view_if_needed()
    sw.click()
    time.sleep(0.5)
    page.screenshot(path="/tmp/t4_pi_virtual_step_off.png")
    detail = _save_and_fetch(page, api_url, pid)
    pc = detail.get("pipeline_config") or {}
    assert pc.get("custom_mix_per_item_virtual_step") is False
    assert not any(s.get("per_item_virtual") for s in detail.get("steps_config") or []), \
        "关闭后自动生成的虚拟步骤行应被移除"
