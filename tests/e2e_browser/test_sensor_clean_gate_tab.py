"""sensor-clean v1.4.0 计数许可标签 + 按标签 ROI CI E2E 回归。

覆盖 UI→落库链路:
  1. 项目页渲染「传感器清洁配置」插件 Tab, 含「工位1 计数许可标签」输入框
     与「标签 ROI 区域」分组(四个角色行 + 绘制按钮)
  2. UI 填许可标签 → 主程序「保存配置」→ 插件配置接口读回新值(落库双向验证)
  3. ROI 绘制器: 打开画布点 3 点保存 → 「保存配置」→ 接口读回归一化多边形;
     清除后恢复不限制

运行时算法细节由 tests/step_defs/test_sensor_clean_count_gate.py (BDD) 与
tests/plugin_system/test_sensor_clean_judgments.py (单测) 覆盖, 本文件只守 UI 链路。

前置: 后端已激活 sensor-clean 插件(未激活自动 skip), 建议隔离环境
  E2E_BASE_URL=http://localhost:6002 E2E_API_URL=http://127.0.0.1:8002
"""
from __future__ import annotations

import time
import uuid

import pytest
import requests

from .conftest import E2E_PREFIX

FIELD_LABEL = "工位1 计数许可标签"
ROI_GROUP = "标签 ROI 区域"
PLUGIN_TAB = "传感器清洁配置"


@pytest.fixture(autouse=True)
def require_sensor_clean(api_url):
    """sensor-clean 未激活时跳过, 结束把许可标签/ROI 恢复原值。"""
    try:
        m = requests.get(f"{api_url}/api/v1/plugins/active/manifest", timeout=5).json()
    except Exception:
        m = {}
    if (m or {}).get("customer_code") != "sensor-clean":
        pytest.skip("sensor-clean 插件未激活")
    cfg_url = f"{api_url}/api/v1/plugins/sensor-clean/swab/config"
    original = requests.get(cfg_url, timeout=5).json()
    yield
    requests.post(cfg_url, json={
        "count_require_label": original.get("count_require_label", ""),
        "count_anchor_label": original.get("count_anchor_label", ""),
        "label_rois": original.get("label_rois", {}) or {},
    }, timeout=5)
    requests.post(f"{api_url}/api/v1/plugins/sensor-clean/swab/reset", timeout=5)


def _mk_project(api_url):
    name = f"{E2E_PREFIX}scg_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=5)
    r.raise_for_status()
    return r.json()["id"], name


def _open_plugin_tab(page, base_url, api_url, name=None):
    if name is None:
        _pid, name = _mk_project(api_url)
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass  # MJPEG 等长连接可能让 networkidle 永不满足, 不作硬条件
    # 项目激活/通道同步可能占住后端数秒, 首屏渲染放宽等待
    page.wait_for_selector("text=项目管理", timeout=60000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{name}')").first.click(timeout=5000)
    time.sleep(0.8)
    tab = page.locator(f".el-tabs__item:has-text('{PLUGIN_TAB}')")
    # 插件前端 ESM 异步加载, Tab 注册可能晚于页面首屏
    for _ in range(20):
        if tab.count() > 0:
            break
        time.sleep(0.5)
    assert tab.count() > 0, "插件应注入「传感器清洁配置」项目 Tab"
    tab.first.click()
    time.sleep(1.2)


def _field_input(page):
    return page.locator(f"div:has(> label:text-is('{FIELD_LABEL}')) > input").first


def test_许可标签与ROI分组渲染(page, base_url, api_url):
    _open_plugin_tab(page, base_url, api_url)
    body = page.evaluate("document.body.innerText")
    assert FIELD_LABEL in body, "配置 Tab 应渲染许可标签字段"
    assert "留空=不启用" in body, "字段应带启用说明"
    assert ROI_GROUP in body, "配置 Tab 应渲染标签 ROI 分组"
    assert "工位2 换棉签标签 ROI" in body, "ROI 分组应含四个角色行"
    inp = _field_input(page)
    assert inp.count() > 0 and inp.input_value() == "", "默认应为空(=不启用)"
    assert page.locator("button:has-text('绘制区域')").count() >= 4, \
        "四个标签角色都应有绘制按钮"


def test_许可标签保存落库(page, base_url, api_url):
    _pid, name = _mk_project(api_url)
    _open_plugin_tab(page, base_url, api_url, name=name)
    inp = _field_input(page)
    inp.fill("脏污产品")
    time.sleep(0.4)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    cfg = requests.get(
        f"{api_url}/api/v1/plugins/sensor-clean/swab/config", timeout=5).json()
    assert cfg.get("count_require_label") == "脏污产品", \
        f"许可标签应落库: {cfg.get('count_require_label')!r}"

    # 页面刷新后重新打开 Tab, 输入框应回显已落库值(不是只存前端内存)
    _open_plugin_tab(page, base_url, api_url, name=name)
    assert _field_input(page).input_value() == "脏污产品", "刷新后应回显落库值"


def test_ROI绘制保存落库(page, base_url, api_url):
    _pid, name = _mk_project(api_url)
    _open_plugin_tab(page, base_url, api_url, name=name)
    # 打开第一行(产品计数标签)的绘制器
    page.locator("button:has-text('绘制区域')").first.click()
    canvas = page.locator("canvas")
    canvas.wait_for(state="visible", timeout=10000)
    time.sleep(1.0)   # 等快照加载(失败也有 16:9 兜底画布)
    box = canvas.bounding_box()
    assert box and box["width"] > 50, "ROI 画布应可见"
    # 点 3 个顶点 (左上三角区域)
    for fx, fy in [(0.1, 0.1), (0.45, 0.1), (0.1, 0.45)]:
        page.mouse.click(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
        time.sleep(0.2)
    page.locator("button:has-text('保存区域')").click()
    time.sleep(0.5)
    body = page.evaluate("document.body.innerText")
    assert "已框定 3 点" in body, "行状态应显示已框定顶点数"
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    cfg = requests.get(
        f"{api_url}/api/v1/plugins/sensor-clean/swab/config", timeout=5).json()
    roi = (cfg.get("label_rois") or {}).get("count_anchor")
    assert isinstance(roi, list) and len(roi) == 3, f"锚标签 ROI 应落库 3 点: {roi!r}"
    for p in roi:
        assert 0.0 <= p[0] <= 1.0 and 0.0 <= p[1] <= 1.0, f"顶点应为归一化坐标: {p!r}"

    # 清除 → 保存 → 落库应移除该角色 ROI
    page.locator("button:has-text('清除')").first.click()
    time.sleep(0.4)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    cfg = requests.get(
        f"{api_url}/api/v1/plugins/sensor-clean/swab/config", timeout=5).json()
    assert not (cfg.get("label_rois") or {}).get("count_anchor"), "清除后应恢复不限制"
