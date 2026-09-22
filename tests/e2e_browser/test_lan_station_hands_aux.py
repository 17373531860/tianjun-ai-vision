"""吉田手部裁切副屏 E2E（一拖多第二笔）。

覆盖：按工位独立开关（默认关，其它工位一帧手部识别都不算）、副屏只拉
``/snapshot?view=hands`` 绝不拉 ``/video_feed``、以及「工位与输入源 → 局域网
工位屏」配置卡里逐工位可抄的地址（防止"后端做了前端没入口"）。
"""
from __future__ import annotations

import pytest
import requests

from .conftest import API_URL
from .test_lan_station_kiosk import (  # noqa: F401 — fixture 需在本模块可见
    _Recorder,
    requires_static_hosting,
    two_channel_workstation,
)


def _hands_aux() -> dict:
    r = requests.get(f"{API_URL}/api/v1/workstations/hands-aux", timeout=5)
    r.raise_for_status()
    return r.json()["channels"]


def _set_hands_aux(channel: int, enabled: bool) -> dict:
    r = requests.put(f"{API_URL}/api/v1/workstations/hands-aux", timeout=15,
                     json={"channel_id": channel, "enabled": enabled})
    r.raise_for_status()
    return r.json()["channels"]


@pytest.fixture
def hands_aux_guard():
    original = _hands_aux()
    try:
        yield original
    finally:
        for ch, enabled in original.items():
            _set_hands_aux(int(ch), bool(enabled))


def test_hands_aux_defaults_off_and_toggles_per_channel(hands_aux_guard):
    """默认全关（其它客户零差异），开一路不牵连另一路。"""
    channels = hands_aux_guard
    assert all(v is False for v in channels.values()), f"出厂应全关: {channels}"

    after = _set_hands_aux(0, True)
    assert after["0"] is True
    for ch, enabled in after.items():
        if ch != "0":
            assert enabled is False, f"工位 {ch} 被牵连开启了"


def test_hands_snapshot_returns_jpeg_for_disabled_channel_without_enabling_it(
        hands_aux_guard):
    """未启用的工位取 hands 快照 → 占位 JPEG，且不会把开关顺手打开。"""
    r = requests.get(f"{API_URL}/snapshot?channel=0&view=hands", timeout=10)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content.startswith(b"\xff\xd8")
    assert _hands_aux()["0"] is False, "一个 GET 不该打开每帧的算力"


@requires_static_hosting
def test_hands_aux_secondary_screen_never_pulls_video_feed(page, hands_aux_guard):
    """副屏只拉 /snapshot?view=hands，页面极瘦，绝不拉全帧率直播。"""
    _set_hands_aux(0, True)
    recorder = _Recorder(page)
    page.goto(
        f"{API_URL}/#/monitor?channel=0&kiosk=1&readonly=1&video_only=1&hands_crop=1",
        wait_until="domcontentloaded", timeout=20_000)
    page.wait_for_selector("[data-testid=hands-crop-view]", timeout=20_000)
    page.wait_for_timeout(3_000)

    assert page.locator("[data-testid=hands-crop-image]").count() == 1
    assert page.locator("button, nav, canvas").count() == 0
    assert page.locator("img").count() == 1
    assert not recorder.api, f"手部屏不应启动业务轮询: {recorder.api[:3]}"
    assert not recorder.feed, f"副屏不该拉 /video_feed: {recorder.feed[:2]}"
    assert recorder.snapshot, "副屏应该在轮询 /snapshot"
    assert all("view=hands" in u for u in recorder.snapshot), recorder.snapshot[:2]
    # 10~15fps 量级: 3 秒不该出现全帧率那种上百次请求
    assert 3 <= len(recorder.snapshot) <= 60, f"3 秒 {len(recorder.snapshot)} 帧"


@requires_static_hosting
def test_lan_station_panel_lists_copyable_urls_per_channel(
        page, two_channel_workstation, hands_aux_guard):
    """配置入口必须存在：工程师照着表格把地址抄进一体机。"""
    page.goto(f"{API_URL}/#/source", wait_until="domcontentloaded", timeout=20_000)
    page.wait_for_timeout(1_500)
    page.locator('.el-tabs__item:has-text("局域网工位屏")').click()
    page.wait_for_selector("[data-testid=lan-station-panel]", timeout=20_000)
    page.wait_for_timeout(1_200)

    url0 = page.locator("[data-testid=lan-station-url-0]").inner_text()
    assert "channel=0" in url0 and "kiosk=1" in url0 and "readonly=0" in url0, url0
    assert url0.startswith("http"), f"给一体机抄的必须是绝对地址: {url0}"
    url1 = page.locator("[data-testid=lan-station-url-1]").inner_text()
    assert "channel=1" in url1, url1

    # 副屏开关默认关 → 不显示副屏地址（避免误配）
    switch0 = page.locator("[data-testid=lan-station-hands-aux-0] input")
    assert switch0.is_checked() is False
    assert page.locator("[data-testid=lan-station-hands-url-0]").count() == 0

    page.locator("[data-testid=lan-station-hands-aux-0]").click()
    page.wait_for_timeout(1_800)
    assert _hands_aux()["0"] is True, "UI 开关没落到后端"
    hands_url = page.locator("[data-testid=lan-station-hands-url-0]").inner_text()
    for token in ("channel=0", "kiosk=1", "readonly=1", "video_only=1", "hands_crop=1"):
        assert token in hands_url, f"副屏地址缺 {token}: {hands_url}"
