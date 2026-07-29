"""v3.45 作业员从用户名单选择（可选项）CI E2E 回归。

背景（萍乡百斯特 2026-07-25 诉求）: 回传达梦的 OPERATOR 列取自监控页称重面板
"人员"框, 原为自由填写, 客户希望可强制从用户系统维护的名单里选（禁用即消失）。
落地为称重配置·前置要求里的开关 `operator_from_users`（默认关, 老项目行为不变）。

覆盖:
  1. 配置 Tab 出现新开关; UI 打开保存 → 后端项目详情回读落库（UI→后端双向）
  2. 开关开 → 监控页"人员"渲染为下拉, 选项与 /weighing/operators 名单一致,
     选中确定后引擎上下文 operator 即为所选（UI→引擎双向）
  3. 开关关（默认）→ 仍是自由填写输入框（回归不破坏老行为）

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX


def _create_weighing_project(api_url: str, operator_from_users: bool | None = None) -> dict:
    weighing = {"enabled": True, "materials": ["钢帽水泥"]}
    if operator_from_users is not None:
        weighing["operator_from_users"] = operator_from_users
    payload = {
        "name": f"{E2E_PREFIX}op_select_{uuid.uuid4().hex[:8]}",
        "task_type": "detection",
        "logic_mode": "weighing",
        "pipeline_config": {"weighing": weighing},
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


def test_配置开关UI打开保存落库(page, base_url, api_url):
    proj = _create_weighing_project(api_url)
    _goto_and_select(page, base_url, proj["name"])
    page.locator(".el-tabs__item:has-text('称重配置')").first.click(timeout=5000)
    time.sleep(0.8)

    body = page.evaluate("document.body.innerText")
    assert "作业员从用户名单选择" in body, "前置要求卡片应出现新开关"

    row = page.locator("div.flex:has-text('作业员从用户名单选择')").last
    row.locator(".el-switch").first.click(timeout=5000)
    time.sleep(0.3)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    r = requests.get(f"{api_url}/api/v1/projects/{proj['id']}", timeout=10)
    r.raise_for_status()
    weighing = (r.json().get("pipeline_config") or {}).get("weighing") or {}
    assert weighing.get("operator_from_users") is True, \
        f"开关未落库: {weighing.get('operator_from_users')}"


def test_开关开_监控页人员为名单下拉_选中写入引擎(page, base_url, api_url):
    _projects = requests.get(f"{api_url}/api/v1/projects", timeout=10).json().get("items", [])
    orig_active = next((p["id"] for p in _projects if p.get("is_active")), None)
    proj = _create_weighing_project(api_url, operator_from_users=True)
    try:
        requests.post(f"{api_url}/api/v1/projects/{proj['id']}/activate",
                      timeout=30).raise_for_status()
        expected = requests.get(f"{api_url}/api/v1/weighing/operators",
                                timeout=10).json().get("operators", [])

        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3.0)

        # 按组件类名定位: 引擎跨激活保留上次人员, 有值时占位符不渲染, 不能按占位符找
        sel = page.locator(".weighing-op-select")
        assert sel.count() > 0, "开关开时'人员'应渲染为下拉(选择人员)"
        assert page.locator("input[placeholder='操作人员']").count() == 0, \
            "开关开时不应再出现自由填写输入框"

        if expected:
            sel.first.click(timeout=5000)
            time.sleep(0.6)
            # el-select 下拉挂 body 下的 popper
            options = page.locator(".el-select-dropdown__item:visible")
            texts = [options.nth(i).inner_text().strip()
                     for i in range(options.count())]
            missing = [n for n in expected if n not in texts]
            assert not missing, f"下拉缺少名单成员: {missing} (实际: {texts})"
            options.first.click(timeout=5000)
            time.sleep(0.3)
            page.locator("button:has-text('确定人员/型号')").click(timeout=5000)
            time.sleep(1.5)
            snap = requests.get(f"{api_url}/api/v1/weighing/state",
                                params={"channel": 0}, timeout=10).json()
            assert snap.get("operator") == expected[0], \
                f"引擎 operator 应为所选 {expected[0]}, 实际 {snap.get('operator')}"
    finally:
        if orig_active:
            requests.post(f"{api_url}/api/v1/projects/{orig_active}/activate", timeout=30)


def test_开关默认关_仍为自由填写输入框(page, base_url, api_url):
    _projects = requests.get(f"{api_url}/api/v1/projects", timeout=10).json().get("items", [])
    orig_active = next((p["id"] for p in _projects if p.get("is_active")), None)
    proj = _create_weighing_project(api_url)  # 不带键 = 默认关
    try:
        requests.post(f"{api_url}/api/v1/projects/{proj['id']}/activate",
                      timeout=30).raise_for_status()
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3.0)
        assert page.locator(".weighing-op-input").count() > 0, \
            "默认关时'人员'应保持自由填写输入框"
        assert page.locator(".weighing-op-select").count() == 0, \
            "默认关时不应出现名单下拉"
    finally:
        if orig_active:
            requests.post(f"{api_url}/api/v1/projects/{orig_active}/activate", timeout=30)
