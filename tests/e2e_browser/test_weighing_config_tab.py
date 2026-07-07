"""称重配置 Tab（WeighingConfigTab.vue 外置组件, 拆分批次 P-1）CI E2E 回归。

覆盖:
  1. weighing 项目选中后出现「称重配置」Tab, 五张配置卡片齐全（外置组件正常挂载）
  2. UI 添加料别 → 保存 → 后端项目详情接口回读落库（UI→后端双向）
  3. 非 weighing 项目不出现该 Tab（v-if 守门未被拆分破坏）

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX


CARD_TITLES = ["前置要求", "料别（投料顺序）", "型号标准量表", "去皮与稳定判定", "校验与报警"]


def _create_weighing_project(api_url: str) -> dict:
    payload = {
        "name": f"{E2E_PREFIX}weighing_{uuid.uuid4().hex[:8]}",
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


def _goto_and_select(page, base_url, name):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    card = page.locator(f"div.p-4:has-text('{name}')").first
    if card.count() == 0:
        card = page.get_by_text(name, exact=False).first
    card.click(timeout=5000)
    time.sleep(0.8)


def test_称重项目出现Tab_五卡片齐全(page, base_url, api_url):
    proj = _create_weighing_project(api_url)
    _goto_and_select(page, base_url, proj["name"])

    tab = page.locator(".el-tabs__item:has-text('称重配置')").first
    assert tab.count() > 0, "weighing 项目应有'称重配置'Tab"
    tab.click(timeout=5000)
    time.sleep(0.8)

    body = page.evaluate("document.body.innerText")
    missing = [t for t in CARD_TITLES if t not in body]
    assert not missing, f"外置组件卡片缺失: {missing}"


def test_添加料别保存_后端落库(page, base_url, api_url):
    proj = _create_weighing_project(api_url)
    _goto_and_select(page, base_url, proj["name"])
    page.locator(".el-tabs__item:has-text('称重配置')").first.click(timeout=5000)
    time.sleep(0.8)

    page.locator("input[placeholder='新料别名']").fill("__e2e_料别X")
    page.locator("button:has-text('添加料别')").click()
    time.sleep(0.5)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    r = requests.get(f"{api_url}/api/v1/projects/{proj['id']}", timeout=10)
    r.raise_for_status()
    weighing = (r.json().get("pipeline_config") or {}).get("weighing") or {}
    assert "__e2e_料别X" in (weighing.get("materials") or []), \
        f"新增料别未落库: {weighing.get('materials')}"


def test_非称重项目不出现称重Tab(page, base_url, api_url):
    # conftest 已建好并激活一个 sequential 项目, 直接选它
    r = requests.get(f"{api_url}/api/v1/projects", timeout=5)
    seq = [p for p in r.json().get("items", [])
           if p["name"].startswith(E2E_PREFIX) and p.get("logic_mode") != "weighing"]
    assert seq, "conftest 应已创建 sequential 项目"
    _goto_and_select(page, base_url, seq[0]["name"])

    assert page.locator(".el-tabs__item:has-text('称重配置')").count() == 0, \
        "非 weighing 项目不应出现'称重配置'Tab"
