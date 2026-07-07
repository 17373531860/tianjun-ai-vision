"""项目页四对话框外置组件（拆分批次 P-2）CI E2E 回归。

覆盖:
  1. 新建项目对话框(CreateProjectDialog)弹出 → 填名创建 → 落库
  2. 模型选择对话框(ModelSelectDialog)弹出且渲染列表/空态
  3. 步骤 ROI 编辑器(RoiEditorDialog)弹出 → 画三角形 → 保存 → 后端落库

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX


def _goto_project(page, base_url):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)


def _select_project(page, name):
    card = page.locator(f"div.p-4:has-text('{name}')").first
    if card.count() == 0:
        card = page.get_by_text(name, exact=False).first
    card.click(timeout=5000)
    time.sleep(0.8)


def test_新建项目对话框_创建落库(page, base_url, api_url):
    _goto_project(page, base_url)
    page.locator("button:has-text('新建项目')").first.click()
    time.sleep(0.8)
    assert page.locator(".el-dialog__title:has-text('新建项目')").count() > 0

    name = f"{E2E_PREFIX}dlg_{uuid.uuid4().hex[:8]}"
    page.locator("input[placeholder*='手机壳外观检测']").fill(name)
    page.locator("button:has-text('创建')").click()
    time.sleep(2.0)

    r = requests.get(f"{api_url}/api/v1/projects", timeout=5)
    names = [x["name"] for x in r.json().get("items", [])]
    assert name in names, "外置新建对话框创建的项目应落库"


def test_模型选择对话框弹出(page, base_url, api_url):
    r = requests.get(f"{api_url}/api/v1/projects", timeout=5)
    e2e = [x for x in r.json().get("items", []) if x["name"].startswith(E2E_PREFIX)]
    assert e2e, "conftest 应已创建项目"
    _goto_project(page, base_url)
    _select_project(page, e2e[0]["name"])

    page.locator("button:has-text('选择模型')").first.click()
    time.sleep(1.0)
    body = page.evaluate("document.body.innerText")
    assert "选择模型" in body and ("个类别" in body or "暂无可用模型" in body), \
        "外置模型选择对话框应渲染列表或空态"


def test_ROI编辑器画三角形_落库(page, base_url, api_url):
    r = requests.get(f"{api_url}/api/v1/projects", timeout=5)
    e2e = [x for x in r.json().get("items", []) if x["name"].startswith(E2E_PREFIX)]
    assert e2e, "conftest 应已创建项目"
    proj = e2e[0]
    _goto_project(page, base_url)
    _select_project(page, proj["name"])

    page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
    time.sleep(1.0)
    page.locator("button:has-text('设置')").first.click()
    time.sleep(2.0)

    canvas = page.locator("canvas.cursor-crosshair").first
    box = canvas.bounding_box()
    assert box and box["width"] > 100, "外置 ROI 编辑器快照 canvas 应渲染"

    for (fx, fy) in [(0.2, 0.2), (0.8, 0.3), (0.5, 0.8)]:
        page.mouse.click(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
        time.sleep(0.3)
    page.locator("button:has-text('完成绘制')").click()
    time.sleep(0.5)
    page.locator("button:has-text('保存 ROI')").click()
    time.sleep(0.8)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    detail = requests.get(f"{api_url}/api/v1/projects/{proj['id']}", timeout=5).json()
    roi = (detail.get("steps_config") or [{}])[0].get("roi")
    assert isinstance(roi, list) and len(roi) == 3, f"ROI 应落库 3 顶点, 实际 {roi}"
