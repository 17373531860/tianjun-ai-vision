"""逻辑设置 Tab（LogicConfigTab.vue 外置组件, 拆分批次 P-4）CI E2E 回归。

覆盖:
  1. 五种逻辑模式各自的专属卡片按 v-if 条件渲染（外置组件五分支守门未破坏）
  2. sequential 加序列行选步骤 → 保存 → pipeline_config.sequence_order 落库
  3. custom 新增周期性规则 + 快捷建事件绑定 → 保存 → 落库（emit/平移函数双向）

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX

MODE_CARDS = {
    "sequential": ["结算方式", "顺序模式 - 步骤排序"],
    "detection": ["检测模式 - 需检测的步骤"],
    "custom": ["自定义模式 - 条件配置", "周期性强制动作"],
    "tracking": ["跟踪模式 - 物品清点配置"],
    "per_item": ["逐件覆盖 — 通用参数", "逐件覆盖 — 每个步骤的角色"],
}


def _mk_project(api_url, mode, steps=None):
    name = f"{E2E_PREFIX}lg_{mode[:4]}_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": mode,
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    if steps:
        requests.put(f"{api_url}/api/v1/projects/{pid}",
                     json={"steps_config": steps}, timeout=5).raise_for_status()
    return pid, name


def _open_logic_tab(page, base_url, name):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    # 同 hash URL 二次 goto 不触发真实导航 → 项目列表停留旧快照, 必须显式 reload
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    # 项目多时新建卡片可能在侧栏可视区外 → 先用搜索框过滤
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    card = page.locator(f"div.p-4:has-text('{name}')").first
    if card.count() == 0:
        card = page.get_by_text(name, exact=False).first
    card.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(0.8)
    return page.evaluate("document.body.innerText")


def test_五模式专属卡片按条件渲染(page, base_url, api_url):
    for mode, cards in MODE_CARDS.items():
        _pid, name = _mk_project(api_url, mode)
        body = _open_logic_tab(page, base_url, name)
        for c in cards:
            assert c in body, f"{mode} 模式应渲染卡片「{c}」"


def test_sequential序列编辑_保存落库(page, base_url, api_url):
    pid, name = _mk_project(api_url, "sequential", steps=[
        {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
    ])
    _open_logic_tab(page, base_url, name)
    page.locator("button:has-text('添加步骤')").first.click()
    time.sleep(0.5)
    page.locator(".el-card:has-text('步骤排序') .el-select").last.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:visible", has_text="step_a").first.click()
    time.sleep(0.5)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    seq = (detail.get("pipeline_config") or {}).get("sequence_order") or []
    assert seq and seq[-1].get("step_id") == 1, f"序列应落库, 实际 {seq}"


def test_custom周期规则快捷建事件_落库(page, base_url, api_url):
    pid, name = _mk_project(api_url, "custom")
    _open_logic_tab(page, base_url, name)
    page.locator("button:has-text('+ 新增规则')").click()
    time.sleep(0.5)
    page.locator("button:has-text('+ 新建事件')").first.click()
    time.sleep(0.8)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    pas = (detail.get("pipeline_config") or {}).get("periodic_actions") or []
    evs = detail.get("events_config") or []
    assert len(pas) == 1, f"周期性规则应落库, 实际 {len(pas)}"
    bound = pas[0].get("due_warning_event_id")
    assert bound and any(e.get("id") == bound for e in evs), "快捷事件应创建并绑定"
