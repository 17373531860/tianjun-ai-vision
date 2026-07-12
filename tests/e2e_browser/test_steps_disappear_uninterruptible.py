"""v3.34 步骤级「消失等待不被打断」开关 (disappear_uninterruptible) CI E2E 回归。

覆盖:
  1. 步骤设置 Tab 表B渲染新列「等待不被打断」(仅非 tracking 模式)
  2. UI 勾选开关 → 保存 → 后端回读落库 (UI→后端双向)
  3. 未配置消失等待时间的步骤开关处于禁用态 (0秒等待无"打断"可言)

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX

# step_a 配了消失等待 → 开关可用; step_b 未配 → 开关禁用
TWO_STEPS = [
    {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True, "disappear_delay": 5},
    {"id": 2, "label": "step_b", "name": "步骤B", "enabled": True},
]

# 「消失等待时间」输入框的占位符全表唯一, 用它锚定紧随其后的开关列
_SWITCH_IN_ROW = "td:has(input[placeholder='默认0秒']) + td .el-switch"


def _mk_project(api_url):
    name = f"{E2E_PREFIX}dui_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "steps_config": [dict(s) for s in TWO_STEPS],
    }, timeout=5).raise_for_status()
    return pid, name


def _open_steps_tab(page, base_url, name):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{name}')").first.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
    time.sleep(0.8)
    return page.evaluate("document.body.innerText")


def _table_b_row(page, label):
    """表A/表B 都有该步骤的行, 用「消失等待时间」输入框(表B特有)锚定表B行。"""
    return page.locator(
        f"tbody tr:has-text('{label}'):has(input[placeholder='默认0秒'])").first


def test_新列渲染_开关可用性(page, base_url, api_url):
    _pid, name = _mk_project(api_url)
    body = _open_steps_tab(page, base_url, name)
    assert "等待不被打断" in body, "表B 应渲染「等待不被打断」列"

    sw_a = _table_b_row(page, "step_a").locator(_SWITCH_IN_ROW)
    sw_b = _table_b_row(page, "step_b").locator(_SWITCH_IN_ROW)
    assert sw_a.count() == 1 and sw_b.count() == 1, "两行都应有开关"
    assert "is-disabled" not in (sw_a.get_attribute("class") or ""), \
        "配了消失等待时间的步骤开关应可用"
    assert "is-disabled" in (sw_b.get_attribute("class") or ""), \
        "未配消失等待时间的步骤开关应禁用"


def test_勾选开关_保存落库(page, base_url, api_url):
    pid, name = _mk_project(api_url)
    _open_steps_tab(page, base_url, name)

    _table_b_row(page, "step_a").locator(_SWITCH_IN_ROW).click()
    time.sleep(0.5)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    sa = next(s for s in detail["steps_config"] if s["label"] == "step_a")
    sb = next(s for s in detail["steps_config"] if s["label"] == "step_b")
    assert sa.get("disappear_uninterruptible") is True, "勾选后应落库为 True"
    assert not sb.get("disappear_uninterruptible"), "未勾选的步骤不应被误写"
