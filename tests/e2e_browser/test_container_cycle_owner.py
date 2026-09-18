# -*- coding: utf-8 -*-
"""E2E — v3.59 自定义模式「周期定界=容器定界」(custom_cycle_owner) 真浏览器回路.

锁定:
  1. API 预置 owner='container' 的项目 → 逻辑设置 Tab 回填「容器定界」+
     容器标签/到位确认/离场确认参数区可见
  2. UI 把「步骤驱动」翻成「容器定界」+ 选容器标签 → 保存 → GET 断言
     pipeline_config.custom_cycle_owner / container_gate_label 真落库
  3. 默认「步骤驱动」时保存不落容器标签 (零差异)

前置: 后端/前端已起 (E2E_API_URL / E2E_BASE_URL)。资源用 __e2e_ 前缀,
conftest 自动清理。
"""
import re
import uuid

import pytest
import requests


def _mk_project(api_url, name, pipeline_extra=None):
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "custom",
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    pipeline = {"custom_based_on": "detection"}
    if pipeline_extra:
        pipeline.update(pipeline_extra)
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "steps_config": [
            {"id": 1, "label": "动作A", "enabled": True, "threshold": 50},
            {"id": 2, "label": "动作B", "enabled": True, "threshold": 50},
            {"id": 3, "label": "箱子", "enabled": False, "threshold": 50},
        ],
        "pipeline_config": pipeline,
    }, timeout=10).raise_for_status()
    return pid


def _get_pc(api_url, pid):
    return requests.get(f"{api_url}/api/v1/projects/{pid}",
                        timeout=10).json().get("pipeline_config") or {}


def _open_logic_tab(page, base_url, name):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded",
              timeout=20000)
    page.wait_for_load_state("networkidle")
    page.get_by_text(name, exact=True).first.click()
    page.wait_for_timeout(1000)
    page.get_by_role("tab", name="逻辑设置").click()
    page.wait_for_timeout(800)


def _cycle_owner_select(page):
    """周期定界的 el-select 输入框."""
    item = page.locator(".el-form-item").filter(
        has_text=re.compile("周期定界")).first
    item.scroll_into_view_if_needed()
    return item


def test_container_owner_prefill(page, base_url, api_url):
    """API 预置 owner=container → UI 回填容器定界 + 参数区可见."""
    name = f"__e2e_owner_prefill_{uuid.uuid4().hex[:6]}"
    _mk_project(api_url, name, pipeline_extra={
        "custom_cycle_owner": "container",
        "container_gate_label": "箱子",
        "container_gate_appear_seconds": 2.0,
        "container_gate_gone_seconds": 4.0,
    })
    _open_logic_tab(page, base_url, name)

    item = _cycle_owner_select(page)
    assert "容器定界" in item.inner_text()
    # 参数区回填
    assert page.get_by_text("容器定界参数", exact=False).count() >= 1
    appear = page.get_by_role("spinbutton", name="到位确认（秒）").first
    gone = page.get_by_role("spinbutton", name="离场确认（秒）").first
    assert appear.input_value() == "2"
    assert gone.input_value() == "4"


def test_flip_to_container_and_save(page, base_url, api_url):
    """UI 步骤驱动→容器定界 + 选标签 → 保存 → GET 落库断言."""
    name = f"__e2e_owner_flip_{uuid.uuid4().hex[:6]}"
    pid = _mk_project(api_url, name)
    assert _get_pc(api_url, pid).get("custom_cycle_owner") in (None, "steps")

    _open_logic_tab(page, base_url, name)

    # 翻到容器定界
    item = _cycle_owner_select(page)
    item.locator(".el-select").first.click()
    page.wait_for_timeout(400)
    page.get_by_role("option", name=re.compile("容器定界")).first.click()
    page.wait_for_timeout(400)

    # 选容器标签 (filterable + allow-create: 敲字 + 回车最稳)
    label_item = page.locator(".el-form-item").filter(
        has_text=re.compile("^容器标签")).first
    label_item.scroll_into_view_if_needed()
    label_input = label_item.locator(".el-select input").first
    label_input.click()
    page.wait_for_timeout(300)
    label_input.fill("箱子")
    page.wait_for_timeout(500)
    label_input.press("Enter")
    page.wait_for_timeout(300)

    page.get_by_role("button", name=re.compile("^保存")).first.click()
    page.wait_for_timeout(1500)

    pc = _get_pc(api_url, pid)
    assert pc.get("custom_cycle_owner") == "container"
    assert pc.get("container_gate_label") == "箱子"
    assert pc.get("container_gate_appear_seconds") == 1.0   # 缺省
    assert pc.get("container_gate_gone_seconds") == 3.0     # 缺省


def test_default_steps_owner_zero_diff(page, base_url, api_url):
    """默认步骤驱动: 保存后 owner='steps' 且容器标签为空 (零差异)."""
    name = f"__e2e_owner_default_{uuid.uuid4().hex[:6]}"
    pid = _mk_project(api_url, name)
    _open_logic_tab(page, base_url, name)

    item = _cycle_owner_select(page)
    assert "步骤驱动" in item.inner_text()

    page.get_by_role("button", name=re.compile("^保存")).first.click()
    page.wait_for_timeout(1500)

    pc = _get_pc(api_url, pid)
    assert pc.get("custom_cycle_owner") == "steps"
    assert not pc.get("container_gate_label")
