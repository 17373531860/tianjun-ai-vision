"""步骤/物品设置 Tab（StepsConfigTab.vue 外置组件, 拆分批次 P-5）CI E2E 回归。

覆盖:
  1. 三表按模式条件渲染: sequential 表A+表B(顺序列) / tracking Tab名「物品设置」+跟踪列
     / custom+per_item 混合出现「角色」列
  2. 表A禁用步骤 → 序列/检测清单同步剔除并落库（stepEnabled.js 共享链路）
  3. 混合模式角色切「物品」→ 表C出现 + 逐件默认参数落库（mixItemDefaults.js 共享链路）
  4. 步骤ROI「设置」按钮拉起父级外置 ROI 编辑器（open-step-roi-editor emit 链）

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX

TWO_STEPS = [
    {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
    {"id": 2, "label": "step_b", "name": "步骤B", "enabled": True},
]


def _mk_project(api_url, mode, pipeline=None):
    name = f"{E2E_PREFIX}st_{mode[:4]}_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": mode,
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    body = {"steps_config": [dict(s) for s in TWO_STEPS]}
    if pipeline:
        body["pipeline_config"] = pipeline
    requests.put(f"{api_url}/api/v1/projects/{pid}", json=body, timeout=5).raise_for_status()
    return pid, name


def _open_steps_tab(page, base_url, name, tab_label="步骤设置"):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    # 同 hash URL 二次 goto 不触发真实导航 → 项目列表停留旧快照, 必须显式 reload
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{name}')").first.click(timeout=5000)
    time.sleep(0.8)
    page.locator(f".el-tabs__item:has-text('{tab_label}')").first.click()
    time.sleep(0.8)
    return page.evaluate("document.body.innerText")


def test_三表按模式条件渲染(page, base_url, api_url):
    _pid, name = _mk_project(api_url, "sequential")
    body = _open_steps_tab(page, base_url, name)
    assert "标签与检测属性" in body, "表A 应渲染"
    assert "步骤行为参数 · 顺序模式" in body, "表B 顺序模式列应渲染"
    assert "角色" not in body.split("标签与检测属性")[1][:400], "非混合模式不应有角色列"

    _pid, name = _mk_project(api_url, "tracking")
    body = _open_steps_tab(page, base_url, name, tab_label="物品设置")
    assert "物品行为参数 · 跟踪模式" in body, "tracking 表B 应换跟踪列"
    assert "计数模式" in body

    _pid, name = _mk_project(api_url, "custom", pipeline={"custom_mixed_with": "per_item"})
    body = _open_steps_tab(page, base_url, name)
    assert "角色" in body, "混合模式表A 应出现角色列"


def test_禁用步骤_序列检测清单剔除落库(page, base_url, api_url):
    pid, name = _mk_project(api_url, "sequential")
    _open_steps_tab(page, base_url, name)
    page.locator("tbody tr:has-text('step_b') .el-switch").first.click()
    time.sleep(0.6)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    pc = detail.get("pipeline_config") or {}
    seq_ids = [it.get("step_id") for it in (pc.get("sequence_order") or [])]
    det_ids = pc.get("detection_steps") or []
    assert 2 not in seq_ids and 2 not in det_ids, f"禁用步骤应被剔除 seq={seq_ids} det={det_ids}"
    sb = next(s for s in detail["steps_config"] if s["label"] == "step_b")
    assert sb.get("enabled") is False


def test_混合模式角色切物品_表C与逐件参数落库(page, base_url, api_url):
    pid, name = _mk_project(api_url, "custom", pipeline={"custom_mixed_with": "per_item"})
    _open_steps_tab(page, base_url, name)
    page.locator("tbody tr:has-text('step_b') .el-select").first.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:visible", has_text="物品").first.click()
    time.sleep(0.8)
    body = page.evaluate("document.body.innerText")
    assert "物品校验参数 · 混合逐件覆盖" in body, "表C 应出现"
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    sb = next(s for s in detail["steps_config"] if s["label"] == "step_b")
    assert sb.get("detect_role") == "item"
    assert (sb.get("per_item") or {}).get("item_label") == "step_b", "逐件默认参数应注入"


def test_步骤ROI按钮拉起外置编辑器(page, base_url, api_url):
    _pid, name = _mk_project(api_url, "sequential")
    _open_steps_tab(page, base_url, name)
    page.locator("tbody tr:has-text('step_a') button:has-text('设置')").first.click()
    time.sleep(1.2)
    assert page.locator(".el-dialog:has-text('绘制步骤 [step_a] ROI')").count() > 0, \
        "open-step-roi-editor emit 应拉起父级 RoiEditorDialog"
