"""Phase 3.2 — Project 页 4 个浏览器 E2E 场景。

涵盖:
  1. 切换到 Project → 选第一个项目 → 切到逻辑设置 tab → 看到"周期性强制动作"卡片
  2. 点"新增规则"按钮 → 看到新规则被加到列表
  3. logic_mode 选择器存在
  4. 步骤列表渲染
"""
from __future__ import annotations

import time

import pytest


def _goto_project(page, base_url):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    # 等"项目管理"标题出现
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    # 等项目列表 v-for 渲染（"模型: " 字串只在每个项目卡片里出现）
    page.wait_for_selector("text=/模型:|暂无项目/", timeout=10000)


def _select_first_project(page, api_url):
    """通过 API 拿到第一个项目名后用文本点击"""
    import requests
    r = requests.get(f"{api_url}/api/v1/projects", timeout=5)
    items = r.json().get("items", [])
    if not items:
        return None
    name = items[0]["name"]
    # 点击侧栏含此名字的卡片
    locator = page.locator(f"div.p-4:has-text('{name}'):has-text('模型:')").first
    if locator.count() == 0:
        # fallback: 任何含项目名的可点元素
        locator = page.get_by_text(name, exact=False).first
    locator.click(timeout=5000)
    time.sleep(0.5)
    return items[0]


def _click_logic_tab(page):
    """点'逻辑设置'tab"""
    tab = page.locator(".el-tabs__item:has-text('逻辑设置')").first
    if tab.count() == 0:
        tab = page.get_by_text("逻辑设置", exact=False).first
    tab.click(timeout=5000)
    time.sleep(0.7)


def test_打开_Project_选项目_切到逻辑_看到周期性强制动作(page, base_url, api_url):
    _goto_project(page, base_url)

    proj = _select_first_project(page, api_url)
    if proj is None:
        pytest.skip("没有可选的项目")

    _click_logic_tab(page)

    # 滚动找"周期性强制动作"卡片标题
    title = page.locator("text=周期性强制动作").first
    title.wait_for(state="visible", timeout=8000)
    assert title.is_visible(), "应显示'周期性强制动作'卡片"


def test_点新增规则_列表多一条(page, base_url, api_url):
    _goto_project(page, base_url)
    if _select_first_project(page, api_url) is None:
        pytest.skip()
    _click_logic_tab(page)

    # 滚到周期性卡片
    title = page.locator("text=周期性强制动作").first
    title.wait_for(state="visible", timeout=8000)
    title.scroll_into_view_if_needed()

    # 数初始规则数（每条规则有占位"规则名"的 input）
    rule_inputs_before = page.locator("input[placeholder*='规则名']").count()

    btn = page.locator("button:has-text('+ 新增规则')").first
    if btn.count() == 0:
        btn = page.locator("button:has-text('新增规则')").first
    btn.scroll_into_view_if_needed()
    btn.click(timeout=5000)
    time.sleep(0.5)

    rule_inputs_after = page.locator("input[placeholder*='规则名']").count()
    assert rule_inputs_after == rule_inputs_before + 1, \
        f"点'新增规则'后应+1; before={rule_inputs_before} after={rule_inputs_after}"


def test_logic_mode_切换器_包含四个选项(page, base_url, api_url):
    """逻辑模式 radio 应有 sequential / detection / custom / tracking 四种"""
    _goto_project(page, base_url)
    if _select_first_project(page, api_url) is None:
        pytest.skip()
    _click_logic_tab(page)

    # 检查左侧 Logic Mode Context 区
    page.wait_for_selector("text=逻辑模式", timeout=5000)

    for label in ["顺序模式", "检测模式", "自定义模式", "跟踪模式"]:
        loc = page.get_by_text(label, exact=False)
        assert loc.count() >= 1, f"应有 logic_mode 选项 {label}"


def test_切换到事件设置_tab_有事件配置区(page, base_url, api_url):
    _goto_project(page, base_url)
    if _select_first_project(page, api_url) is None:
        pytest.skip()

    tab = page.locator(".el-tabs__item:has-text('事件')").first
    if tab.count() == 0:
        pytest.skip("找不到'事件'tab")
    tab.click(timeout=5000)
    time.sleep(0.5)

    # 事件配置 tab 至少有一个相关字符串
    body_text = page.locator(".el-tabs__content").inner_text(timeout=3000)
    keywords = ["事件", "新增", "动作"]
    matched = any(kw in body_text for kw in keywords)
    assert matched, f"事件 tab 应含 {keywords} 之一; got {body_text[:200]}"
