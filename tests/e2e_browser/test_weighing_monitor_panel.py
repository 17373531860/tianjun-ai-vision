"""称重投料模式检测中心看板互斥 CI E2E 回归（v3.42.x 修复）。

背景（萍乡百斯特现场撞出）: Monitor 视频下方 SOP 流程卡片与称重看板同链互斥且
SOP 在前, 其显示条件此前未排除称重模式 —— 称重项目必配步骤 + SOP 显示开关出厂
默认开, 导致称重专属看板永远被 SOP 卡片抢占, 现场只能看到普通检测那套 UI。

覆盖:
  1. 激活称重投料项目(配了步骤) → 称重看板渲染、SOP 流程卡片不渲染
  2. 底栏「模式」显示"称重投料"而非"未定义"(i18n mode 表补齐回归)
顺序模式下 SOP 照旧的回归由 test_sop_step_panel.py 覆盖, 此处不重复。
测试收尾恢复原激活项目并删除测试项目。
"""
from __future__ import annotations

import time
import uuid

import requests


def test_称重模式看板互斥与底栏模式文案(page, base_url, api_url):
    pname = f"__e2e_weigh_panel_{uuid.uuid4().hex[:5]}"
    _projects = requests.get(f"{api_url}/api/v1/projects", timeout=10).json().get("items", [])
    orig_active = next((p["id"] for p in _projects if p.get("is_active")), None)
    pid = None
    try:
        r = requests.post(f"{api_url}/api/v1/projects", json={
            "name": pname, "task_type": "detection", "logic_mode": "weighing",
        }, timeout=10)
        r.raise_for_status()
        pid = r.json()["id"]
        requests.put(f"{api_url}/api/v1/projects/{pid}", json={
            "steps_config": [
                {"id": 1, "label": "step_on_scale", "name": "上秤", "enabled": True},
                {"id": 2, "label": "step_feed", "name": "加料", "enabled": True},
            ],
            "pipeline_config": {"weighing": {"enabled": True, "materials": []}},
        }, timeout=10).raise_for_status()
        requests.post(f"{api_url}/api/v1/projects/{pid}/activate", timeout=30).raise_for_status()

        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3.0)

        body = page.evaluate("document.body.innerText")
        # 1. 称重看板占位, SOP 卡片让位 (修复前: SOP 在前抢占, 称重看板永不渲染)
        assert "SOP流程卡片" not in body, "称重模式下 SOP 流程卡片必须让位给称重看板"
        assert "称重投料" in body, "称重看板(称重投料·工位N)应渲染"
        # 2. 底栏模式文案 (修复前: mode 映射表缺 weighing → 显示"未定义")
        assert "未定义" not in body, "底栏模式不应显示'未定义'"
    finally:
        if orig_active:
            requests.post(f"{api_url}/api/v1/projects/{orig_active}/activate", timeout=30)
        if pid:
            requests.delete(f"{api_url}/api/v1/projects/{pid}", timeout=10)
