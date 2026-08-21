"""多工位布局 E2E 回归。

覆盖:
  1. 三工位 (v3.52 横向三列): 一屏 grid-cols-3 等宽三列, 每列一张完整工位卡
     (视频 16:9 / 四项计数 / 质量摘要 / SOP / 步骤表 / 四个控制按钮); 无横向溢出
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
    """三工位 = 横向三列, 每列一张完整工位卡 (计数/SOP/控制按钮)。"""
    channel_count_guard(3)
    page.set_viewport_size({"width": 1920, "height": 1080})
    _goto_monitor(page, base_url)

    assert page.get_by_role("button", name="开始").count() == 3
    assert page.get_by_role("button", name="停止").count() == 3
    assert page.get_by_role("button", name="待机").count() == 3
    assert page.get_by_role("button", name="清零").count() == 3
    assert page.locator("text=总产量").count() == 3
    assert page.locator("text=SOP").count() >= 3
    # 不应出现网格总览工具条 (那是 4+ 工位布局)
    assert page.locator("text=多工位总览").count() == 0


def test_triple_workstation_three_columns_layout(page, base_url, channel_count_guard):
    """v3.52 三工位横向三列: grid-cols-3, 三列从左到右, 视频 ~16:9, 无横向滚动。"""
    channel_count_guard(3)
    page.set_viewport_size({"width": 1920, "height": 1080})
    _goto_monitor(page, base_url)

    # —— 主容器是等宽三列 grid ——
    grid = page.locator("[data-testid='triple-grid']")
    assert grid.count() == 1
    tracks = grid.evaluate(
        "el => getComputedStyle(el).gridTemplateColumns.split(' ').length"
    )
    assert tracks == 3, f"应为三列 grid, 实际 {tracks} 列"

    # —— 三张工位卡从左到右排列 (x 递增), 且每列关键区域齐全 ——
    xs = []
    for ch in range(3):
        col = page.locator(f"[data-testid='triple-col-{ch}']")
        assert col.count() == 1, f"缺工位列 {ch}"
        box = col.bounding_box()
        assert box is not None
        xs.append(box["x"])
        # 视频卡
        assert page.locator(f"[data-testid='channel-card-{ch}']").count() == 1
        # 质量区: 合格率圆环 + NG TOP3 榜单 + 产出统计条 三块并排 (匀称, 从左到右)
        assert page.locator(f"[data-testid='triple-quality-{ch}']").count() == 1
        yield_block = page.locator(f"[data-testid='triple-yield-{ch}']")
        ngtop3_block = page.locator(f"[data-testid='triple-ngtop3-{ch}']")
        output_block = page.locator(f"[data-testid='triple-output-{ch}']")
        assert yield_block.count() == 1, f"工位{ch} 缺合格率圆环块"
        assert ngtop3_block.count() == 1, f"工位{ch} 缺 NG TOP3 榜单块"
        assert output_block.count() == 1, f"工位{ch} 缺产出统计块"
        # 合格率块内含圆环 svg; 三块从左到右: 合格率 < NG TOP3 < 产出统计
        assert yield_block.locator("svg circle").count() >= 2
        xq = yield_block.bounding_box()["x"]
        xn = ngtop3_block.bounding_box()["x"]
        xo = output_block.bounding_box()["x"]
        assert xq < xn < xo, f"质量区三块顺序异常: {xq},{xn},{xo}"
        # NG TOP3 与产出统计等宽 (flex-1), 不再一家独大
        wn = ngtop3_block.bounding_box()["width"]
        wo = output_block.bounding_box()["width"]
        assert abs(wn - wo) <= 8, f"NG TOP3 与产出统计应等宽: {wn} vs {wo}"
        # SOP 流程卡片 (默认显示) + 步骤表, 竖向堆叠 (SOP 在表上方)
        sop = page.locator(f"[data-testid='triple-sop-{ch}']")
        tbl = page.locator(f"[data-testid='triple-steptable-{ch}']")
        assert sop.count() == 1, f"工位{ch} 缺 SOP 流程卡片"
        assert tbl.count() == 1, f"工位{ch} 缺步骤表"
        assert sop.bounding_box()["y"] < tbl.bounding_box()["y"], f"工位{ch} SOP 应在步骤表上方"
    assert xs[0] < xs[1] < xs[2], f"三列未从左到右排列: {xs}"

    # —— 三路视频均保持约 16:9, 单路宽度接近 614 (给足容差) ——
    for ch in range(3):
        card = page.locator(f"[data-testid='channel-card-{ch}']")
        box = card.bounding_box()
        assert box is not None
        ratio = box["width"] / box["height"]
        assert 1.70 <= ratio <= 1.85, f"工位{ch} 视频比例 {ratio:.3f} 偏离 16:9"
        assert 520 <= box["width"] <= 700, f"工位{ch} 视频宽度 {box['width']:.0f} 超出预期范围"

    # —— 页面整体不出现横向滚动条 ——
    overflow_x = page.evaluate(
        "() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth"
    )
    assert overflow_x <= 2, f"出现横向溢出 {overflow_x}px"

    # —— 每列四个控制按钮齐全 (共 3×4) ——
    for name in ("开始", "停止", "待机", "清零"):
        assert page.get_by_role("button", name=name).count() == 3


def test_triple_sop_cards_display_toggle(page, base_url, channel_count_guard):
    """v3.52 显示设置「SOP 流程卡片」(display.monitor.stepStrip): 关掉后三列 SOP
    流程卡片整行消失; 视频保持满列宽 16:9 (零黑边、不变形), 让出的高度由步骤表吸收; 无横向溢出。"""
    channel_count_guard(3)
    page.set_viewport_size({"width": 1920, "height": 1080})

    # 默认 (开): 三列 SOP 卡片 + 步骤表都在, 视频贴 16:9
    _goto_monitor(page, base_url)
    assert page.locator("[data-testid^='triple-sop-']").count() == 3
    assert page.locator("[data-testid^='triple-steptable-']").count() == 3
    vid_box_before = page.locator("[data-testid='channel-card-0']").bounding_box()
    tbl_h_before = page.locator("[data-testid='triple-steptable-0']").bounding_box()["height"]

    # 关掉「SOP 流程卡片」显示开关 (stepStrip=false) — 写 store 持久化的 localStorage
    # 再整页刷新 (Monitor onMounted 会 loadSettings 读取), 等价于用户在系统设置里关掉。
    page.evaluate(
        "window.localStorage.setItem('display_settings',"
        " JSON.stringify({ monitor: { stepStrip: false } }))"
    )
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    # SOP 流程卡片整行消失, 步骤表仍在
    assert page.locator("[data-testid^='triple-sop-']").count() == 0
    assert page.locator("[data-testid^='triple-steptable-']").count() == 3
    # 视频卡小幅往下延长 (16:9 → 16:10), 画面仍 contain 等比 (宽度不变、不变形)
    vid_box_after = page.locator("[data-testid='channel-card-0']").bounding_box()
    ratio_after = vid_box_after["width"] / vid_box_after["height"]
    assert 1.55 <= ratio_after <= 1.65, f"关 SOP 后视频卡应约 16:10, 实际比例 {ratio_after:.3f}"
    dh = vid_box_after["height"] - vid_box_before["height"]
    assert 15 <= dh <= 90, f"视频卡应小幅往下延长, 实际增高 {dh:.0f}px"
    assert abs(vid_box_after["width"] - vid_box_before["width"]) <= 2, "视频卡宽度不应变化 (仍满列宽)"
    # 步骤表吃到 SOP 让出的其余高度 → 明显变大
    tbl_h_after = page.locator("[data-testid='triple-steptable-0']").bounding_box()["height"]
    assert tbl_h_after > tbl_h_before + 40, f"步骤表未吸收 SOP 让出的空间: {tbl_h_before}->{tbl_h_after}"
    # 仍无横向溢出
    overflow_x = page.evaluate(
        "() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth"
    )
    assert overflow_x <= 2, f"出现横向溢出 {overflow_x}px"
    # 说明: pytest-playwright 每个用例独立 context, localStorage 不跨用例污染, 无需手动还原。


def test_grid_overview_pagination_and_zoom(page, base_url, channel_count_guard):
    """6 工位: 总览网格 + 2x2 分页 + 放大单路 + 上一路/下一路循环 + 返回总览。"""
    channel_count_guard(6)
    _goto_monitor(page, base_url)

    # —— 总览 (默认 auto → 6 工位取 3x3, 单页) ——
    assert page.locator("text=多工位总览").count() == 1
    # 卡片左上角标签是"工位N"+可选项目名后缀 (激活项目会同步到各通道并上角标),
    # 不能用 ^工位\d+$ 精确匹配 —— 用前缀正则数卡片数
    assert page.locator("text=/^工位\\d+/").count() == 6

    # —— 切 2x2 → 2 页 ——
    page.get_by_role("button", name="2×2", exact=True).click()
    page.wait_for_timeout(1000)
    assert page.locator("text=1 / 2").count() == 1
    page.get_by_role("button", name="下一页 ›").click()
    page.wait_for_timeout(1000)
    assert page.locator("text=— 空 —").count() == 2  # 第 2 页: 工位5/6 + 2 空位

    # —— 点击工位5 → 放大详情 (v3.52 起放大详情复用 SingleChannelMonitor 组件) ——
    page.locator("text=工位5").first.click()
    page.wait_for_timeout(1000)
    assert page.get_by_role("button", name="‹ 返回总览").count() == 1
    assert page.get_by_test_id("single-channel-monitor").count() == 1

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
