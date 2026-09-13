# -*- coding: utf-8 -*-
"""内置能力模型入仓 (2026-09) CI E2E。

覆盖 (RFC 内置能力模型入仓与能力选用体系):
  1. 模型仓库页三分区: 「内置能力模型」分区渲染出厂行 (出厂内置徽标 +
     能力徽标 + 无删除按钮 + 试一试按钮);
  2. 试一试抽屉: 内置行点「试一试」打开能力试用面板 (原 AI 能力试用迁入);
  3. 上传向导带「能力类型」选择;
  4. 项目基础设置「能力挂件」卡: UI 加 pose 挂件 → 保存 →
     pipeline_config.capability_attachments 落库 (UI→DB 双向);
  5. 「AI 能力试用」独立页已下线: 菜单无入口 + 旧路由不再渲染;
     旧页三块管理功能在抽屉有承接 (记忆库管理 / VLM 配置 / 通道帧试用)。

运行时行为由 tests/test_builtin_models.py 覆盖; 本文件守 UI→落库链路。
资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX


def _open_models_page(page, base_url):
    page.goto(f"{base_url}/#/model", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=模型仓库", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    time.sleep(0.5)


# ==================== 模型仓库页 ====================

def test_内置能力分区渲染与禁删(page, base_url, api_url):
    # 前置: 后端 seed 过内置行 (启动时); 兜底断言 API 侧
    r = requests.get(f"{api_url}/api/v1/models/capabilities", timeout=5)
    assert r.status_code == 200
    items = {it["capability"]: it for it in r.json()["items"]}
    assert items["pose"]["builtin_model_id"], "pose 内置行应已 seed"

    _open_models_page(page, base_url)
    sec = page.locator("[data-test='model-section-builtin']")
    assert sec.count() == 1, "应渲染「内置能力模型」分区"
    body = sec.inner_text()
    assert "出厂内置" in body
    assert "OCR" in body or "文字" in body

    pose_id = items["pose"]["builtin_model_id"]
    card = page.locator(f"[data-test='model-card-{pose_id}']")
    assert card.count() == 1, "pose 内置卡应渲染"
    assert card.locator("button:has-text('删除')").count() == 0, "内置卡不应有删除按钮"
    assert card.locator("button:has-text('更换权重')").count() == 1, "pose 可绑定应有更换权重"
    assert card.locator(f"[data-test='model-try-{pose_id}']").count() == 1


def test_试一试抽屉打开(page, base_url, api_url):
    items = {it["capability"]: it for it in requests.get(
        f"{api_url}/api/v1/models/capabilities", timeout=5).json()["items"]}
    ocr_id = items["ocr"]["builtin_model_id"]
    assert ocr_id, "ocr 内置行应已 seed"

    _open_models_page(page, base_url)
    page.locator(f"[data-test='model-try-{ocr_id}']").click()
    time.sleep(0.6)
    drawer = page.locator("[data-test='cap-try-drawer']")
    assert drawer.count() == 1, "试一试抽屉应打开"
    assert drawer.locator("[data-test='cap-try-run']").count() == 1
    page.keyboard.press("Escape")


def test_上传向导带能力类型(page, base_url):
    _open_models_page(page, base_url)
    page.locator("button:has-text('上传新模型')").click()
    time.sleep(0.6)
    assert page.locator("[data-test='upload-capability']").count() == 1, \
        "上传向导应有能力类型选择"
    page.keyboard.press("Escape")


# ==================== 项目能力挂件 ====================

def test_能力挂件UI添加保存落库(page, base_url, api_url):
    name = f"{E2E_PREFIX}cap_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]

    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{name}')").first.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('基础设置')").first.click()
    time.sleep(0.8)

    card = page.locator("[data-test='cap-attach-card']")
    assert card.count() == 1, "基础设置应渲染能力挂件卡"
    card.locator("[data-test='cap-attach-add']").click()
    time.sleep(0.4)
    page.locator(".el-dropdown-menu__item:has-text('人体朝向')").click()
    time.sleep(0.5)
    assert card.locator("[data-test='cap-attach-pose']").count() == 1, \
        "挂件行应渲染"

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    atts = (detail.get("pipeline_config") or {}).get("capability_attachments") or []
    assert len(atts) == 1 and atts[0]["capability"] == "pose", \
        f"capability_attachments 应落库: {atts}"
    assert atts[0]["params"]["interval_s"] == 1.0


# ==================== AI 能力试用独立页已下线 ====================

def test_ai能力试用页已下线_菜单与路由(page, base_url):
    page.goto(f"{base_url}/#/model", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_load_state("networkidle", timeout=10000)
    assert page.locator(".nav-item:has-text('AI 能力试用')").count() == 0, \
        "左侧菜单不应再有 AI 能力试用入口"
    # 旧路由直达不应渲染旧页内容 (路由已删, vue-router 落到空匹配)
    page.goto(f"{base_url}/#/ai-tools", wait_until="domcontentloaded", timeout=15000)
    time.sleep(0.8)
    body = page.evaluate("document.body.innerText")
    assert "VLM 坐诊（看图问答）" not in body, "旧 AI 能力试用页不应再渲染"


def test_抽屉承接旧页管理功能(page, base_url, api_url):
    """旧页三块独有功能的新家: 记忆库管理(anomaly 抽屉) / VLM 配置(vlm 抽屉)
    / 读通道当前画面 (所有能力抽屉)。"""
    items = {it["capability"]: it for it in requests.get(
        f"{api_url}/api/v1/models/capabilities", timeout=5).json()["items"]}

    _open_models_page(page, base_url)

    # anomaly 抽屉: 记忆库管理块 (建库入口) + 通道帧试用
    page.locator(f"[data-test='model-try-{items['anomaly']['builtin_model_id']}']").click()
    time.sleep(0.6)
    drawer = page.locator("[data-test='cap-try-drawer']")
    assert drawer.locator("[data-test='cap-bank-manage']").count() == 1, \
        "anomaly 抽屉应含记忆库管理块"
    assert drawer.locator("[data-test='bank-create-btn']").count() == 1
    assert drawer.locator("[data-test='cap-try-frame-run']").count() == 1, \
        "抽屉应有读通道当前画面入口"
    page.keyboard.press("Escape")
    time.sleep(0.5)

    # vlm 抽屉: 连接配置块 (启用开关/保存)
    page.locator(f"[data-test='model-try-{items['vlm']['builtin_model_id']}']").click()
    time.sleep(0.6)
    drawer = page.locator("[data-test='cap-try-drawer']")
    assert drawer.locator("[data-test='cap-vlm-config']").count() == 1, \
        "vlm 抽屉应含连接配置块"
    assert drawer.locator("[data-test='vlm-save-btn']").count() == 1
    assert drawer.locator("[data-test='cap-try-question']").count() == 1
    page.keyboard.press("Escape")
