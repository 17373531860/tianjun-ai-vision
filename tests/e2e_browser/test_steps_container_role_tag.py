# -*- coding: utf-8 -*-
"""E2E — v3.44.4 步骤设置表B 容器角色警示标 (真浏览器).

背景 (上银 7-27 现场困惑): 托盘/放托盘被「托盘容器」机制接管后, 步骤行为
参数表里的最少帧数/消失等待等对它们不生效, 客户改了没反应以为是 bug。
本测试锁定: 混合容器模式下, 容器标签与进箱动作标签所在行渲染
「容器 · 参数在装箱清点」/「进箱动作 · 参数在装箱清点」警示标
（信息架构重构后容器参数自逻辑设置迁至独立「装箱清点」Tab）。

前置: 后端 8001 + 前端已起; 依赖项目 SY6 (id=37, 容器混合模式) 存在 —
没有则跳过, 不动用户其他项目。
"""
import re

import pytest
import requests

PROJECT_ID = 37  # SY6


def test_container_role_tags_render(page, base_url, api_url):
    r = requests.get(f"{api_url}/api/v1/projects/{PROJECT_ID}", timeout=10)
    if r.status_code != 200:
        pytest.skip("SY6 (id=37) 不存在")
    pc = (r.json().get("pipeline_config") or {})
    if not pc.get("custom_mix_container_label"):
        pytest.skip("SY6 未启用托盘容器 (前置条件变了)")
    container_label = pc["custom_mix_container_label"]
    action_label = pc.get("custom_mix_container_action_label") or ""

    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_load_state("networkidle")
    # 顶栏项目切换器也可能显示 "SY6" — 必须走左侧列表 (搜索过滤后点卡片)
    page.get_by_placeholder("搜索项目...").fill("SY6")
    page.wait_for_timeout(600)
    page.locator("div.cursor-pointer").filter(has_text="SY6").first.click()
    page.wait_for_timeout(1000)
    page.get_by_role("tab", name=re.compile("步骤")).click()
    page.wait_for_timeout(800)

    tags = page.locator("text=参数在装箱清点")
    texts = [tags.nth(i).inner_text() for i in range(tags.count())]
    assert any("容器" in t for t in texts), f"容器标签 [{container_label}] 行应挂警示标: {texts}"
    if action_label:
        assert any("进箱动作" in t for t in texts), \
            f"动作标签 [{action_label}] 行应挂警示标: {texts}"
    page.screenshot(path="/tmp/uat_shots/e2e_container_role_tag.png", full_page=True)
