"""v3.47 多工位布局重构 E2E 回归。

覆盖:
  1. 三工位: 三行横排 (每行左视频右数据), 3 组计数卡 + 3 组控制按钮
  2. 4+ 工位: 总览网格 (自动/2x2/3x3/4x4 可选) + 分页
  3. 点击卡片放大单路: 详细数据面板 + 上一路/下一路循环 + 返回总览回到所在页

约定: 走 conftest 的 E2E_BASE_URL / E2E_API_URL; 工位数由 fixture 设置并在
测试结束还原, 不污染环境原有 channel_count。
"""
from __future__ import annotations

import pytest
import requests

from .conftest import API_URL


def _get_channel_count() -> int:
    r = requests.get(f"{API_URL}/api/v1/workstations/", timeout=5)
    r.raise_for_status()
    return r.json()["channel_count"]


def _set_channel_count(count: int):
    r = requests.post(
        f"{API_URL}/api/v1/workstations/mode",
        json={"channel_count": count},
        timeout=10,
    )
    r.raise_for_status()


@pytest.fixture
def channel_count_guard():
    """记录并还原环境原有工位数。"""
    original = _get_channel_count()
    yield _set_channel_count
    _set_channel_count(original)


def _goto_monitor(page, base_url):
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2500)  # 等 fetchChannelCount + 布局渲染


def test_triple_workstation_rows(page, base_url, channel_count_guard):
    """三工位 = 三行横排, 每行 视频 + 数据面板 (计数/SOP/控制按钮)。"""
    channel_count_guard(3)
    _goto_monitor(page, base_url)

    assert page.get_by_role("button", name="开始").count() == 3
    assert page.locator("text=总产量").count() == 3
    assert page.locator("text=SOP").count() >= 3
    # 不应出现网格总览工具条
    assert page.locator("text=多工位总览").count() == 0


def test_grid_overview_pagination_and_zoom(page, base_url, channel_count_guard):
    """6 工位: 总览网格 + 2x2 分页 + 放大单路 + 上一路/下一路循环 + 返回总览。"""
    channel_count_guard(6)
    _goto_monitor(page, base_url)

    # —— 总览 (默认 auto → 6 工位取 3x3, 单页) ——
    assert page.locator("text=多工位总览").count() == 1
    assert page.locator("text=/^工位\\d+$/").count() == 6

    # —— 切 2x2 → 2 页 ——
    page.get_by_role("button", name="2×2", exact=True).click()
    page.wait_for_timeout(1000)
    assert page.locator("text=1 / 2").count() == 1
    page.get_by_role("button", name="下一页 ›").click()
    page.wait_for_timeout(1000)
    assert page.locator("text=— 空 —").count() == 2  # 第 2 页: 工位5/6 + 2 空位

    # —— 点击工位5 → 放大详情 ——
    page.locator("text=工位5").first.click()
    page.wait_for_timeout(1000)
    assert page.get_by_role("button", name="‹ 返回总览").count() == 1
    assert page.locator("text=SOP 流程").count() == 1

    # —— 下一路: 5 → 6 → 回卷 1; 上一路: 1 → 6 ——
    page.get_by_role("button", name="下一路 ›").click()
    page.wait_for_timeout(600)
    assert page.locator("text=工位 6").count() >= 1
    page.get_by_role("button", name="下一路 ›").click()
    page.wait_for_timeout(600)
    assert page.locator("text=工位 1").count() >= 1
    page.get_by_role("button", name="‹ 上一路").click()
    page.wait_for_timeout(600)
    assert page.locator("text=工位 6").count() >= 1

    # —— 返回总览: 应停在工位6 所在的 2x2 第 2 页 ——
    page.get_by_role("button", name="‹ 返回总览").click()
    page.wait_for_timeout(1000)
    assert page.locator("text=2 / 2").count() == 1

    # —— 3x3 单页: 无分页控件 + 3 空位 ——
    page.get_by_role("button", name="3×3", exact=True).click()
    page.wait_for_timeout(1000)
    assert page.locator("text=下一页 ›").count() == 0
    assert page.locator("text=— 空 —").count() == 3
