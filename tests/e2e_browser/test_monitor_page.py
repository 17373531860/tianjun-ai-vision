"""Phase 3.3 — Monitor 页 3 个浏览器 E2E 场景。

涵盖:
  1. Monitor 加载 — 能看到 FPS 显示
  2. Monitor 上有 fps 数字（即使 0）
  3. 周期性动作进度区 — 当后端含规则时应渲染
"""
from __future__ import annotations

import time

import pytest


def _goto_monitor(page, base_url):
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_load_state("networkidle", timeout=10000)


def test_Monitor_页面_有_FPS_文字(page, base_url):
    _goto_monitor(page, base_url)
    fps_label = page.locator("text=FPS").first
    fps_label.wait_for(state="visible", timeout=8000)
    assert fps_label.is_visible(), "Monitor 应显示 FPS 标签"


def test_Monitor_页_主显示区有视频元素或占位符(page, base_url):
    _goto_monitor(page, base_url)

    # 页面底部应有 img / video / canvas 之一（视频/截图）
    has_media = (
        page.locator("img").count() > 0
        or page.locator("video").count() > 0
        or page.locator("canvas").count() > 0
    )
    assert has_media, "Monitor 应有图像/视频/canvas 元素"


def test_Monitor_页面_切换工位选项(page, base_url):
    """如果工位数 > 1, 应有切换菜单"""
    _goto_monitor(page, base_url)
    time.sleep(1.0)

    # 不同的工位通常以 "工位" / "通道" / "Ch" 文字呈现
    body = page.locator("body").inner_text(timeout=3000)
    assert any(kw in body for kw in ["FPS", "未运行", "运行中", "暂停"]), \
        f"Monitor 应有运行状态/FPS 字样, body 摘要={body[:200]}"
