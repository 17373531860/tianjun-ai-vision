"""事件设置 Tab（EventsConfigTab.vue 外置组件, 拆分批次 P-3）CI E2E 回归。

覆盖:
  1. 事件设置 Tab 渲染事件定义卡片 + 两条系统预设事件（外置组件正常挂载）
  2. UI 新增事件改名 + 加计数器动作 → 保存 → 后端回读落库（UI→后端双向）

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time

import requests

from .conftest import E2E_PREFIX


def _open_events_tab(page, base_url, name):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    card = page.locator(f"div.p-4:has-text('{name}')").first
    if card.count() == 0:
        card = page.get_by_text(name, exact=False).first
    card.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('事件设置')").first.click()
    time.sleep(1.0)


def _first_e2e_project(api_url):
    r = requests.get(f"{api_url}/api/v1/projects", timeout=5)
    e2e = [x for x in r.json().get("items", []) if x["name"].startswith(E2E_PREFIX)]
    assert e2e, "conftest 应已创建项目"
    return e2e[0]


def test_事件Tab渲染_系统预设在列(page, base_url, api_url):
    proj = _first_e2e_project(api_url)
    _open_events_tab(page, base_url, proj["name"])
    body = page.evaluate("document.body.innerText")
    assert "事件定义" in body and "新增事件" in body, "外置事件 Tab 应渲染卡片"
    assert page.locator("span:has-text('系统预设')").count() >= 2, "OK/NG 预设事件应在列"


def test_新增事件加动作_保存落库(page, base_url, api_url):
    proj = _first_e2e_project(api_url)
    _open_events_tab(page, base_url, proj["name"])

    page.locator("button:has-text('+ 新增事件')").click()
    time.sleep(0.5)
    inputs = page.locator("input.bg-transparent.border-b")
    new_input = inputs.nth(inputs.count() - 1)
    ev_name = f"{E2E_PREFIX}事件A"
    new_input.fill(ev_name)
    page.locator("button:has-text('+ 添加动作')").last.click()
    time.sleep(0.5)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    detail = requests.get(f"{api_url}/api/v1/projects/{proj['id']}", timeout=5).json()
    mine = [e for e in (detail.get("events_config") or []) if e.get("name") == ev_name]
    assert mine, "新增事件应落库"
    assert len(mine[0].get("actions") or []) == 1, "计数器动作应落库"
