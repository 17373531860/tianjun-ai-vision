"""多工位按工位 logic_mode 切工艺主面板（阶段3，v3.55）。

现场叙事:
  操作员开三工位监控，三列分别绑跟踪清点 / 逐件覆盖 / 区域事件项目。
  前端每列 sop 槽应显示该工位自己的工艺面板（物品清点 / 逐件覆盖 / SOP 规则卡），
  而不是三列都画通用 SOP。后端 results 已按通道带 tracking / per_item_state /
  project_config；本测试用 route mock 注入真实契约载荷。

槽位 id 契约不动：dual=sop-row / triple=sop。
"""
from __future__ import annotations

import os
from pathlib import Path

from .mode_payloads import mixed_mode_router, sequential_payload, weighing_payload
from .test_multi_workstation_layout import channel_count_guard  # noqa: F401

SHOT_DIR = Path(os.environ.get("TJ_E2E_SHOT_DIR", "/tmp/tj_monitor_baseline"))


def _goto_monitor(page, base_url, settle_ms=2800):
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(settle_ms)


def _shot(page, name: str):
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOT_DIR / f"{name}.png"), full_page=False)


def test_triple_mixed_modes_show_matching_panels(page, base_url, channel_count_guard):
    """三工位异模式：ch0 清点 / ch1 逐件 / ch2 区域事件 SOP 规则名。"""
    channel_count_guard(3)
    page.set_viewport_size({"width": 1920, "height": 1080})
    handler, route = mixed_mode_router()
    page.route(route, handler)
    try:
        _goto_monitor(page, base_url)

        sop0 = page.locator("[data-testid='triple-sop-0']")
        sop1 = page.locator("[data-testid='triple-sop-1']")
        sop2 = page.locator("[data-testid='triple-sop-2']")
        assert sop0.count() == 1 and sop1.count() == 1 and sop2.count() == 1
        assert sop0.get_attribute("data-layout-slot") == "sop"
        assert sop1.get_attribute("data-layout-slot") == "sop"
        assert sop2.get_attribute("data-layout-slot") == "sop"

        # ch0 tracking
        assert sop0.get_attribute("data-mode-panel") == "tracking"
        assert sop0.locator("text=物品清点").count() == 1
        assert sop0.locator("text=螺栓").count() == 1
        assert sop0.locator("text=垫片").count() == 1
        assert sop0.locator("text=SOP").count() == 0
        assert page.locator("[data-testid='triple-steptable-0']").count() == 0

        # ch1 per_item
        assert sop1.get_attribute("data-mode-panel") == "per_item"
        assert sop1.locator("text=逐件覆盖").count() >= 1
        assert sop1.locator("text=SOP").count() == 0
        assert page.locator("[data-testid='triple-steptable-1']").count() == 0

        # ch2 region_events：规则名建卡，不是模型类别
        assert sop2.get_attribute("data-mode-panel") in (None, "")
        assert sop2.locator("text=SOP").count() >= 1
        assert sop2.locator("text=测硬度").count() >= 1
        assert sop2.locator("text=扫码").count() >= 1
        assert sop2.locator("text=下工件").count() >= 1
        assert sop2.locator("text=测硬度笔").count() == 0
        assert page.locator("[data-testid='triple-steptable-2']").count() == 1

        _shot(page, "mode_panels_triple_mixed")
    finally:
        page.unroute(route, handler)


def test_dual_mixed_modes_keep_sop_row_slot(page, base_url, channel_count_guard):
    """双工位：槽位仍叫 sop-row；ch0 清点、ch1 逐件。"""
    channel_count_guard(2)
    page.set_viewport_size({"width": 1920, "height": 1080})
    handler, route = mixed_mode_router()
    page.route(route, handler)
    try:
        _goto_monitor(page, base_url)

        sop0 = page.locator("[data-testid='dual-sop-0']")
        sop1 = page.locator("[data-testid='dual-sop-1']")
        assert sop0.get_attribute("data-layout-slot") == "sop-row"
        assert sop1.get_attribute("data-layout-slot") == "sop-row"
        assert sop0.get_attribute("data-mode-panel") == "tracking"
        assert sop1.get_attribute("data-mode-panel") == "per_item"
        assert sop0.locator("text=物品清点").count() == 1
        assert sop1.locator("text=逐件覆盖").count() >= 1
        assert sop0.locator("text=SOP").count() == 0
        assert sop1.locator("text=SOP").count() == 0

        _shot(page, "mode_panels_dual_mixed")
    finally:
        page.unroute(route, handler)


def test_dual_weighing_replaces_sop(page, base_url, channel_count_guard):
    """双工位称重列显示称重投料看板，顺序模式列仍是 SOP。"""
    channel_count_guard(2)
    page.set_viewport_size({"width": 1920, "height": 1080})
    handler, route = mixed_mode_router({
        0: weighing_payload,
        1: sequential_payload,
    })
    page.route(route, handler)
    try:
        _goto_monitor(page, base_url)

        sop0 = page.locator("[data-testid='dual-sop-0']")
        sop1 = page.locator("[data-testid='dual-sop-1']")
        assert sop0.get_attribute("data-mode-panel") == "weighing"
        assert sop0.locator("text=称重投料").count() >= 1
        assert sop0.locator("text=SOP").count() == 0
        assert sop1.get_attribute("data-mode-panel") in (None, "")
        assert sop1.locator("text=SOP").count() >= 1

        _shot(page, "mode_panels_dual_weighing")
    finally:
        page.unroute(route, handler)
