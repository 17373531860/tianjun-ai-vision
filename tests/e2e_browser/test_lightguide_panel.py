"""投影光引导设置卡 (P3) E2E: 参数保存回路 + 标定状态区。

测试 finally 还原引导参数，不留现场改动。
"""
from __future__ import annotations

import pytest
import requests

from .conftest import API_URL


def _get_params() -> dict:
    r = requests.get(f"{API_URL}/api/v1/lightguide/params", timeout=5)
    r.raise_for_status()
    return r.json()["params"]


def _put_params(params: dict) -> dict:
    r = requests.put(f"{API_URL}/api/v1/lightguide/params", json=params, timeout=5)
    r.raise_for_status()
    return r.json()["params"]


@pytest.fixture
def lightguide_params_guard():
    original = _get_params()
    try:
        yield original
    finally:
        _put_params(original)


def _goto_panel(page, base_url: str) -> None:
    page.goto(f"{base_url}/#/source", wait_until="domcontentloaded", timeout=15_000)
    page.wait_for_timeout(1_200)
    page.get_by_role("tab", name="多屏工位显示").click()
    card = page.get_by_test_id("lightguide-card")
    card.scroll_into_view_if_needed()
    card.wait_for(state="visible", timeout=5_000)


def test_lightguide_panel_param_roundtrip(page, base_url, lightguide_params_guard):
    """改悬停时长 + 关自动重标 → 保存 → 后端落库 → 重进页面回读一致。"""
    _put_params({"hover_dwell_ms": 1200, "auto_recalibrate": True})
    _goto_panel(page, base_url)

    dwell_input = page.get_by_test_id("lightguide-param-hover-dwell").locator("input")
    dwell_input.fill("2600")
    page.get_by_test_id("lightguide-param-auto-recal").click()
    page.get_by_test_id("lightguide-save").click()
    page.wait_for_timeout(1_000)

    saved = _get_params()
    assert saved["hover_dwell_ms"] == 2600
    assert saved["auto_recalibrate"] is False

    # 重进页面回读
    _goto_panel(page, base_url)
    assert page.get_by_test_id("lightguide-param-hover-dwell").locator("input").input_value() == "2600"


def test_lightguide_panel_status_and_reset(page, base_url, lightguide_params_guard):
    """标定状态区渲染每个工位一行；「恢复默认」把表单打回出厂值（不落库）。"""
    _put_params({"hover_dwell_ms": 3000})
    _goto_panel(page, base_url)

    # 工位 0 状态行始终存在（未标定/已标定都渲染）
    assert page.get_by_test_id("lightguide-status-0").count() == 1

    # 恢复默认只改表单
    page.get_by_test_id("lightguide-reset").click()
    assert page.get_by_test_id("lightguide-param-hover-dwell").locator("input").input_value() == "1200"
    # 未点保存 → 后端仍是 3000
    assert _get_params()["hover_dwell_ms"] == 3000
