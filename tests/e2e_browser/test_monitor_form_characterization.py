"""Monitor 五形态特征测试（characterization，重构安全网）。

目的：巨石拆解（composable 化 / WorkstationColumn 组件化）期间，锁住
"重构前长什么样"——只断言**必须保持不变**的结构骨架：

  - 各形态的 data-layout-canvas / data-layout-slot 锚点（客户布局落库契约）
  - 每列/每形态的关键区块存在性与控制按钮数量
  - 不锁"多工位一律画 SOP 卡"这类已知待修行为（阶段 3 会改）

另外把各形态截图存到 TJ_E2E_SHOT_DIR（默认 /tmp/tj_monitor_baseline），
供重构各步人工比对；截图本身不做像素断言（跨机不稳定）。

异模式载荷用 mode_payloads.mixed_mode_router()：ch0=tracking,
ch1=per_item, ch2=region_events。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from .mode_payloads import mixed_mode_router
from .test_multi_workstation_layout import channel_count_guard  # noqa: F401 (fixture 复用)

SHOT_DIR = Path(os.environ.get("TJ_E2E_SHOT_DIR", "/tmp/tj_monitor_baseline"))


def _goto_monitor(page, base_url, settle_ms=2500):
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(settle_ms)


def _shot(page, name: str):
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOT_DIR / f"{name}.png"), full_page=False)


def _slot_count(page, canvas: str, slot: str) -> int:
    return page.locator(
        f"[data-layout-canvas='{canvas}'] [data-layout-slot='{slot}']"
    ).count()


# ─────────────────────── 单工位 ───────────────────────

def test_single_form_skeleton(page, base_url, channel_count_guard):
    channel_count_guard(1)
    page.set_viewport_size({"width": 1920, "height": 1080})
    _goto_monitor(page, base_url)

    assert page.locator("[data-layout-canvas='single']").count() == 1
    assert _slot_count(page, "single", "video") == 1
    assert _slot_count(page, "single", "charts") == 1
    # 图表区固定三块 (缺陷分布 / 产能趋势 / 合格率)
    charts = page.locator("[data-layout-canvas='single'] [data-layout-slot='charts'] > div")
    assert charts.count() == 3
    for name in ("开始", "停止", "待机", "清零"):
        assert page.get_by_role("button", name=name).count() == 1, f"单工位缺 {name} 按钮"
    _shot(page, "form_single")


# ─────────────────────── 双工位 ───────────────────────

def test_dual_form_skeleton(page, base_url, channel_count_guard):
    channel_count_guard(2)
    page.set_viewport_size({"width": 1920, "height": 1080})
    _goto_monitor(page, base_url)

    for ch in range(2):
        col = page.locator(f"[data-testid='dual-col-{ch}']")
        assert col.count() == 1, f"缺双工位列 {ch}"
        assert col.get_attribute("data-layout-canvas") == "dual"
        assert page.locator(f"[data-testid='dual-counters-{ch}']").count() == 1
        assert page.locator(f"[data-testid='dual-sop-{ch}']").count() == 1
        # 工艺主面板槽位 id 是布局落库契约, 永远叫 sop-row
        sop = page.locator(f"[data-testid='dual-sop-{ch}']")
        assert sop.get_attribute("data-layout-slot") == "sop-row"
        assert col.locator("[data-layout-slot='video']").count() == 1
        assert col.locator("[data-layout-slot='controls']").count() == 1
    # 计数条文案 (总产量/合格/不良/合格率) 每列一套
    for label in ("总产量", "合格率"):
        assert page.locator(f"text={label}").count() == 2
    for name in ("开始", "停止", "待机", "清零"):
        assert page.get_by_role("button", name=name).count() == 2
    _shot(page, "form_dual")


# ─────────────────────── 三工位（异模式载荷下骨架不变） ───────────────────────

def test_triple_form_skeleton_mixed_modes(page, base_url, channel_count_guard):
    """三工位骨架在"三列绑不同 logic_mode 项目"时依旧完整。

    只锁骨架（列/槽位/按钮），不锁 sop 槽内内容——槽内按模式切面板
    是阶段 3 的功能目标，届时内容断言在专门测试里做。
    """
    channel_count_guard(3)
    page.set_viewport_size({"width": 1920, "height": 1080})
    handler, route = mixed_mode_router()
    page.route(route, handler)
    try:
        _goto_monitor(page, base_url)

        assert page.locator("[data-testid='triple-grid']").count() == 1
        for ch in range(3):
            col = page.locator(f"[data-testid='triple-col-{ch}']")
            assert col.count() == 1, f"缺三工位列 {ch}"
            assert col.get_attribute("data-layout-canvas") == "triple"
            assert col.locator("[data-layout-slot='video']").count() == 1
            assert col.locator("[data-layout-slot='counters']").count() == 1
            assert page.locator(f"[data-testid='triple-quality-{ch}']").count() == 1
            # 工艺主面板槽位 id 契约: triple 叫 sop
            sop = page.locator(f"[data-testid='triple-sop-{ch}']")
            assert sop.count() == 1
            assert sop.get_attribute("data-layout-slot") == "sop"
            assert page.locator(f"[data-testid='triple-steptable-{ch}']").count() == 1
            assert col.locator("[data-layout-slot='controls']").count() == 1
        for name in ("开始", "停止", "待机", "清零"):
            assert page.get_by_role("button", name=name).count() == 3
        _shot(page, "form_triple_mixed_modes")
    finally:
        page.unroute(route, handler)


# ─────────────────────── 网格总览（4+ 工位） ───────────────────────

def test_grid_form_skeleton(page, base_url, channel_count_guard):
    channel_count_guard(6)
    page.set_viewport_size({"width": 1920, "height": 1080})
    _goto_monitor(page, base_url)

    grid_canvas = page.locator("[data-layout-canvas='grid']")
    assert grid_canvas.count() == 1
    assert _slot_count(page, "grid", "toolbar") == 1
    assert _slot_count(page, "grid", "grid-area") == 1
    assert page.locator("text=多工位总览").count() == 1
    assert page.locator("text=/^工位\\d+/").count() == 6
    _shot(page, "form_grid")


# ─────────────────────── 放大详情（zoom / SingleChannelMonitor） ───────────────────────

def test_zoom_form_skeleton(page, base_url, channel_count_guard):
    channel_count_guard(6)
    page.set_viewport_size({"width": 1920, "height": 1080})
    _goto_monitor(page, base_url)

    page.locator("text=工位1").first.click()
    page.wait_for_timeout(1000)

    scm = page.get_by_test_id("single-channel-monitor")
    assert scm.count() == 1
    assert page.locator("[data-layout-canvas='zoom']").count() == 1
    for slot in ("topbar", "video", "stats", "step-table", "controls"):
        assert _slot_count(page, "zoom", slot) == 1, f"zoom 缺槽位 {slot}"
    assert page.get_by_test_id("single-channel-controls").count() == 1
    for tid in ("single-channel-start", "single-channel-stop",
                "single-channel-standby", "single-channel-reset"):
        assert page.get_by_test_id(tid).count() == 1
    _shot(page, "form_zoom")
