"""流水线称重 (drive_mode=pipeline) 配置 UI CI E2E 回归 (v3.39)。

覆盖:
  1. 驱动模式切到「两阶段流水线」→ 「流水线参数」「秤指令时序」两卡片出现
  2. UI 改收尾超时 → 保存 → 后端项目详情回读 drive_mode/finalize_timeout_sec 落库
  3. 默认逐道投料模式下两张流水线卡片不出现 (v-if 守门)

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX


def _create_weighing_project(api_url: str) -> dict:
    payload = {
        "name": f"{E2E_PREFIX}wpipe_{uuid.uuid4().hex[:8]}",
        "task_type": "detection",
        "logic_mode": "weighing",
        "pipeline_config": {},
        "steps_config": [],
        "events_config": [
            {"id": 1, "name": "合格", "type": "ok", "enabled": True},
            {"id": 2, "name": "NG", "type": "ng", "enabled": True},
        ],
        "counters_config": [],
        "alarm_config": {},
        "detection_config": {},
        "data_config": {},
        "default_model_id": None,
        "model_format": "pytorch_fp32",
    }
    r = requests.post(f"{api_url}/api/v1/projects", json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def _open_weighing_tab(page, base_url, name):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    card = page.locator(f"div.p-4:has-text('{name}')").first
    if card.count() == 0:
        card = page.get_by_text(name, exact=False).first
    card.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('称重配置')").first.click(timeout=5000)
    time.sleep(0.8)


def _switch_to_pipeline(page):
    drive_card = page.locator(".el-card:has-text('驱动模式')").first
    drive_card.locator(".el-select").first.click(timeout=5000)
    page.locator(".el-select-dropdown__item:has-text('两阶段流水线')").first.click(timeout=5000)
    time.sleep(0.6)


def test_切换流水线模式_两卡片出现(page, base_url, api_url):
    proj = _create_weighing_project(api_url)
    _open_weighing_tab(page, base_url, proj["name"])

    body = page.evaluate("document.body.innerText")
    assert "流水线参数" not in body, "默认 scale 模式不应显示流水线参数卡片"
    assert "秤指令时序" not in body, "默认 scale 模式不应显示秤指令时序卡片"

    _switch_to_pipeline(page)

    body = page.evaluate("document.body.innerText")
    assert "流水线参数" in body, "切 pipeline 后应出现流水线参数卡片"
    assert "秤指令时序" in body, "切 pipeline 后应出现秤指令时序卡片"
    assert "标签③收尾动作" in body, "流水线参数卡片应含标签③收尾动作绑定"


def test_改收尾超时保存_后端落库(page, base_url, api_url):
    proj = _create_weighing_project(api_url)
    _open_weighing_tab(page, base_url, proj["name"])
    _switch_to_pipeline(page)

    inp = (page.locator("label:has-text('收尾超时(秒)')")
           .locator("xpath=..").locator("input").first)
    inp.click(timeout=5000)
    inp.fill("45")
    inp.press("Enter")
    time.sleep(0.4)

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    r = requests.get(f"{api_url}/api/v1/projects/{proj['id']}", timeout=10)
    r.raise_for_status()
    weighing = (r.json().get("pipeline_config") or {}).get("weighing") or {}
    assert weighing.get("drive_mode") == "pipeline", \
        f"驱动模式未落库: {weighing.get('drive_mode')}"
    timing = weighing.get("timing") or {}
    assert float(timing.get("finalize_timeout_sec", -1)) == 45, \
        f"收尾超时未落库: {timing.get('finalize_timeout_sec')}"


def test_逐道投料模式无流水线卡片(page, base_url, api_url):
    proj = _create_weighing_project(api_url)
    _open_weighing_tab(page, base_url, proj["name"])

    assert page.locator(".el-card:has-text('流水线参数')").count() == 0
    assert page.locator(".el-card:has-text('秤指令时序')").count() == 0
