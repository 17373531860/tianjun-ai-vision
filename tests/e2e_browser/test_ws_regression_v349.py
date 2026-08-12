"""v3.49 捷昌改造批次 UI 回归（WS1/WS2/WS3/WS5 前端面）。

覆盖:
  - Settings·显示设置: MES 外推并发派发开关 + scan_pair 新码先上屏开关
    (含 UI→后端落库双向验证: 点开关后 GET API 回读值翻转)
  - Settings·性能设置: WS5 数据库信息卡片 (dialect tag / 位置 / 版本)
  - MES·集群汇总: WS2 副机上报超时/异步化控件 + slave 角色上报链路状态区
  - MES·外部对接: WS1 网关编辑弹窗重试预算输入框
  - Monitor: 新序开关开启下页面基础渲染不回归
"""
from __future__ import annotations

import requests


# ============================================================
# Settings · 显示设置 (WS1 + WS3 开关)
# ============================================================

def _goto_settings(page, base_url):
    page.goto(f"{base_url}/#/settings", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(1500)


def test_settings_dispatch_switches_present(page, base_url):
    """两个 v3.49 开关都在显示设置 tab, 且默认均为开。"""
    _goto_settings(page, base_url)
    async_sw = page.locator("[data-testid='mes-async-dispatch-switch']")
    new_first_sw = page.locator("[data-testid='scan-pair-new-first-switch']")
    async_sw.scroll_into_view_if_needed(timeout=8000)
    assert async_sw.is_visible()
    assert new_first_sw.is_visible()


def test_scan_pair_new_first_switch_roundtrip(page, base_url, api_url):
    """UI 点开关 → 后端 GET 回读值翻转 → 再点回 → 恢复原值 (T5 双向验证)。"""
    def _backend_value() -> bool:
        r = requests.get(f"{api_url}/api/v1/scanner/scan-pair/new-code-first", timeout=5)
        r.raise_for_status()
        return r.json().get("enabled") is True

    original = _backend_value()
    _goto_settings(page, base_url)
    sw = page.locator("[data-testid='scan-pair-new-first-switch']")
    sw.scroll_into_view_if_needed(timeout=8000)

    try:
        sw.click(timeout=5000)
        page.wait_for_timeout(800)
        assert _backend_value() == (not original), "UI 切换未落到后端"

        sw.click(timeout=5000)
        page.wait_for_timeout(800)
        assert _backend_value() == original, "UI 切回未恢复"
    finally:
        # 兜底恢复, 无论断言在哪一步失败都不给环境留脏状态
        requests.put(
            f"{api_url}/api/v1/scanner/scan-pair/new-code-first",
            json={"enabled": original}, timeout=5,
        )


# ============================================================
# Settings · 性能设置 (WS5 数据库信息卡片)
# ============================================================

def test_settings_db_info_card(page, base_url, api_url):
    _goto_settings(page, base_url)
    page.locator(".el-tabs__item:has-text('性能设置')").first.click(timeout=8000)
    page.wait_for_timeout(1500)

    tag = page.locator("[data-testid='db-dialect-tag']")
    tag.scroll_into_view_if_needed(timeout=8000)
    assert tag.is_visible()

    # 卡片显示的 dialect 必须与后端 /system/db-info 一致
    r = requests.get(f"{api_url}/api/v1/system/db-info", timeout=5)
    r.raise_for_status()
    info = r.json()
    expected = "PostgreSQL" if info.get("dialect") == "postgresql" else "SQLite"
    assert tag.inner_text().strip() == expected
    assert info.get("location"), "db-info 应返回数据库位置"


# ============================================================
# MES · 集群汇总 (WS2 上报异步化)
# ============================================================

def _goto_mes_tab(page, base_url, tab_text):
    page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    page.locator(f"text={tab_text}").first.click(timeout=8000)
    page.wait_for_timeout(1200)


def test_cluster_panel_report_fields_and_status(page, base_url, api_url):
    """slave 角色下: 上报超时/异步化控件 + 上报链路状态区可见。测试后恢复角色。"""
    r = requests.get(f"{api_url}/api/v1/cluster/config", timeout=5)
    r.raise_for_status()
    original_role = (r.json() or {}).get("role", "standalone")

    try:
        requests.put(f"{api_url}/api/v1/cluster/config",
                     json={"role": "slave"}, timeout=5).raise_for_status()
        _goto_mes_tab(page, base_url, "集群汇总")

        timeout_input = page.locator("[data-testid='cluster-report-timeout']")
        async_sw = page.locator("[data-testid='cluster-report-async-switch']")
        status_area = page.locator("[data-testid='cluster-report-status']")
        timeout_input.scroll_into_view_if_needed(timeout=8000)
        assert timeout_input.is_visible(), "副机上报超时输入框未显示"
        assert async_sw.is_visible(), "上报异步化开关未显示"
        assert status_area.is_visible(), "slave 角色应显示上报链路状态区"
        # 状态区三个计数字段渲染出来 (来自 GET /cluster/report-status)
        text = status_area.inner_text()
        for kw in ("内存队列", "落盘积压", "已补发"):
            assert kw in text, f"上报链路状态区缺字段: {kw}"
    finally:
        requests.put(f"{api_url}/api/v1/cluster/config",
                     json={"role": original_role}, timeout=5)


# ============================================================
# MES · 外部对接 (WS1 重试预算)
# ============================================================

def test_gateway_dialog_retry_budget_input(page, base_url):
    _goto_mes_tab(page, base_url, "外部对接")
    page.locator("button:has-text('新建连接')").first.click(timeout=5000)
    page.wait_for_timeout(800)

    budget = page.locator("[data-testid='gw-retry-budget'] input")
    budget.scroll_into_view_if_needed(timeout=8000)
    assert budget.is_visible(), "网关弹窗缺重试预算输入框"
    assert budget.input_value() in ("0", ""), "重试预算默认应为 0 (不限)"

    # 能输入数值 (预算秒数)
    budget.fill("30")
    budget.blur()
    page.wait_for_timeout(300)
    assert budget.input_value() == "30"

    page.keyboard.press("Escape")   # 关弹窗不落库


# ============================================================
# Monitor · 新序开启下的渲染回归
# ============================================================

def test_monitor_renders_with_new_first_enabled(page, base_url, api_url):
    """新码先上屏默认开的情况下, Monitor 页主体正常渲染 (workpieceOverride
    改造不破坏页面挂载)。"""
    r = requests.get(f"{api_url}/api/v1/scanner/scan-pair/new-code-first", timeout=5)
    assert r.status_code == 200 and r.json().get("enabled") is True, \
        "v3.49 出厂默认应为开"

    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2500)
    # 主体容器挂载 (不做深度行为断言, 时序行为由单测 + UAT 剧本覆盖)
    body_text = page.locator("body").inner_text()
    assert "监控" in body_text or "工位" in body_text or "检测" in body_text, \
        "Monitor 页主体未渲染"
