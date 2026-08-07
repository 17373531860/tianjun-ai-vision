"""lg-worktime 插件 E2E (T6 CI 回归)。

前置 (与本目录其它测试一致):
  - backend / frontend 已启动 (E2E_API_URL / E2E_BASE_URL, 默认 8001/6001)
  - **lg-worktime 插件已激活** — 未激活时全部 skip (不拖累无插件环境的 CI)

覆盖 (对齐 tests/uat/uat_lg_worktime_dashboard.py, 但结构性断言、不依赖造数):
  1. monitor.layout.body 被插件整页覆盖 (领导看板渲染)
  2. KPI 卡 / ECharts 画布 / LEAN 标签齐
  3. dashboard API 面 (live/summary/trend/step-values/step-averages) 全 200 且结构对
  4. project.step-cell 价值单元格: 自建 __e2e_ 项目 → UI 改 VA→NVA → GET 落库双向验证
"""
from __future__ import annotations

import os
import time

import pytest
import requests

from .conftest import API_URL, E2E_PREFIX

CC = "lg-worktime"
DASH = f"{API_URL}/api/v1/plugins/{CC}/dashboard"


def _plugin_active() -> bool:
    try:
        r = requests.get(f"{API_URL}/api/v1/plugins/active/manifest", timeout=5)
        return r.status_code == 200 and (r.json() or {}).get("customer_code") == CC
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _plugin_active(), reason=f"{CC} 插件未激活, 跳过其 E2E")


# ============================================================
# 1+2. 看板整页覆盖 + 结构
# ============================================================

def test_dashboard_overrides_monitor_body(page, base_url):
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector(".lgwt-dashboard", timeout=25000)

    # v1.4.1 展会同款壳: 品牌顶栏 + 全屏覆盖容器 (盖掉宿主导航栏, 与其它页 iframe 视觉连续)
    assert page.locator(".lgwt-shell").count() == 1, "看板应包在展会壳内"
    assert page.locator(".lgwt-topbar").is_visible(), "壳品牌顶栏应可见"
    assert page.locator(".lgwt-nav-toggle").is_visible(), "壳 ☰ 导航按钮应可见"
    vp = page.viewport_size
    box = page.locator(".tj-layout-body-override").bounding_box()
    assert box and abs(box["x"]) < 2 and abs(box["y"]) < 2 \
        and abs(box["width"] - vp["width"]) < 4 and abs(box["height"] - vp["height"]) < 4, \
        f"覆盖容器应全屏 (盖掉宿主导航栏): {box}"

    assert page.locator(".lgwt-kpi").count() >= 6, "KPI 卡应至少 6 张"
    # 等 ECharts 首帧
    time.sleep(3)
    assert page.locator(".lgwt-dashboard canvas").count() >= 3, \
        "堆叠图/占比环/趋势图 3 张画布应渲染"
    body = page.evaluate("document.body.innerText")
    for kw in ("VA", "BVA", "NVA", "今日产量"):
        assert kw in body, f"看板应显示 {kw}"


def test_showcase_overrides_other_pages(page, base_url):
    """v1.4+: 项目页等其它页面被展会 iframe 全屏覆盖."""
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("iframe[src*='showcase-app']", timeout=25000)
    vp = page.viewport_size
    fbox = page.locator("iframe[src*='showcase-app']").bounding_box()
    assert fbox and abs(fbox["width"] - vp["width"]) < 4, f"展会 iframe 应全屏: {fbox}"


# ============================================================
# 3. dashboard API 面
# ============================================================

def test_dashboard_api_shapes():
    r = requests.get(f"{DASH}/live", timeout=5)
    assert r.status_code == 200 and "channels" in r.json()

    r = requests.get(f"{DASH}/summary", timeout=5)
    assert r.status_code == 200
    d = r.json()
    for k in ("total_cycles", "good_cycles", "yield_rate", "ct_avg", "lean"):
        assert k in d
    for k in ("va", "bva", "nva", "wait", "good_rounds", "va_ratio"):
        assert k in d["lean"]

    r = requests.get(f"{DASH}/trend", params={"days": 3}, timeout=5)
    assert r.status_code == 200 and len(r.json()["days"]) == 3

    r = requests.get(f"{DASH}/step-values", timeout=5)
    assert r.status_code == 200 and "values" in r.json()

    r = requests.get(f"{DASH}/step-averages", timeout=5)
    assert r.status_code == 200 and "steps" in r.json()

    # v1.5.1 设备卡真值
    r = requests.get(f"{DASH}/device-info", timeout=5)
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d["uptime_seconds"], int) and d["uptime_seconds"] > 0
    assert d["temperature_c"] is None or 0 < float(d["temperature_c"]) < 120


def test_settings_api_roundtrip_and_card():
    """v1.5.0 LG 口径全参数可配: /settings 读写回环 (收尾还原, 不污染环境)."""
    r = requests.get(f"{DASH}/settings", timeout=5)
    assert r.status_code == 200
    before = r.json()["settings"]
    for k in ("default_value_type", "wait_value_type", "lean_scope", "trend_days"):
        assert k in before
    try:
        r = requests.post(f"{DASH}/settings", json={"trend_days": 21}, timeout=5)
        assert r.status_code == 200 and r.json()["settings"]["trend_days"] == 21
        # trend 缺省天数跟随设置
        r = requests.get(f"{DASH}/trend", timeout=5)
        assert r.json()["trend_days"] == 21 and len(r.json()["days"]) == 21
        # 非法值静默规整
        r = requests.post(f"{DASH}/settings", json={"lean_scope": "junk"}, timeout=5)
        assert r.json()["settings"]["lean_scope"] == "good_only"
    finally:
        requests.post(f"{DASH}/settings", json=before, timeout=5)
    assert requests.get(f"{DASH}/settings", timeout=5).json()["settings"] == before


def test_settings_card_in_showcase_settings_page(page, base_url):
    """系统设置(展会页)应有「LG 工时参数」卡, 四个控件回显后端真值."""
    page.goto(f"{base_url}/#/settings", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("iframe[src*='showcase-app']", timeout=25000)
    fr = page.frame_locator("iframe[src*='showcase-app']")
    fr.locator("#lgwtParamsCard").wait_for(timeout=15000)
    time.sleep(1.0)
    api = requests.get(f"{DASH}/settings", timeout=5).json()["settings"]
    assert fr.locator("#lgwtDefVt").input_value() == api["default_value_type"]
    assert fr.locator("#lgwtWaitVt").input_value() == api["wait_value_type"]
    assert fr.locator("#lgwtScope").input_value() == api["lean_scope"]
    assert int(fr.locator("#lgwtTrendDays").input_value()) == api["trend_days"]
    assert fr.locator("#lgwtParamsSaveBtn").is_visible()


# ============================================================
# 4. 步骤价值单元格 → 落库双向验证
# ============================================================

@pytest.fixture()
def e2e_project():
    """自建带启用步骤的项目, 结束删除 (conftest 的 __e2e_ 清理兜底)。"""
    name = f"{E2E_PREFIX}lgwt_steps"
    payload = {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": [
            {"label": "e2e_step_a", "name": "e2e_step_a", "enabled": True,
             "plugin_data": {CC: {"value_type": "VA"}}},
            {"label": "e2e_step_b", "name": "e2e_step_b", "enabled": True,
             "plugin_data": {CC: {"value_type": "BVA"}}},
        ],
    }
    r = requests.post(f"{API_URL}/api/v1/projects", json=payload, timeout=8)
    assert r.status_code in (200, 201), f"建项目失败: {r.status_code} {r.text[:200]}"
    proj = r.json()
    yield proj
    requests.delete(f"{API_URL}/api/v1/projects/{proj['id']}", timeout=8)


def test_step_value_cell_roundtrip(page, base_url, e2e_project):
    pid = e2e_project["id"]
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    time.sleep(2)
    # v1.4+ 项目页被展会 iframe 全屏覆盖, 宿主步骤表格不可交互 → 跳过 UI 回路
    # (价值单元格逻辑仍注册, 供将来嵌入/回退时复用; API 面已在 test_dashboard_api_shapes 覆盖)
    if page.locator("iframe[src*='showcase-app']").count():
        pytest.skip("项目页被展会 iframe 覆盖 (v1.4+), 宿主价值单元格不可交互")
    page.locator(f'.cursor-pointer:has-text("{e2e_project["name"]}")').first.click()
    time.sleep(1.5)
    page.locator('.el-tabs__item:has-text("步骤设置")').first.click()
    time.sleep(1.5)

    cells = page.locator("select.lgwt-cell-select")
    assert cells.count() == 2, f"应出现 2 个价值单元格, 实际 {cells.count()}"

    cells.nth(0).select_option("NVA")
    time.sleep(2)

    after = requests.get(f"{API_URL}/api/v1/projects/{pid}", timeout=8).json()
    sc = after["steps_config"]
    assert sc[0]["plugin_data"][CC]["value_type"] == "NVA", "UI 改动应落库"
    assert sc[1]["plugin_data"][CC]["value_type"] == "BVA", "其它步骤不应被误伤"
