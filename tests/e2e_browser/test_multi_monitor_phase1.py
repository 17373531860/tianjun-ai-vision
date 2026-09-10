"""多屏工位显示一期 E2E。

测试均记录并 finally 还原 channel_count 与 multi_monitor 顶层配置，不保留现场配置改动。
视频连接用 Playwright 路由观察/截断，不依赖真实摄像头。
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from playwright.sync_api import expect

from .conftest import API_URL


_BLACK_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42Y"
    "AAAAASUVORK5CYII="
)


def _get_channel_count() -> int:
    response = requests.get(f"{API_URL}/api/v1/workstations/", timeout=5)
    response.raise_for_status()
    return int(response.json()["channel_count"])


def _set_channel_count(count: int) -> None:
    response = requests.post(
        f"{API_URL}/api/v1/workstations/mode",
        json={"channel_count": count},
        timeout=10,
    )
    response.raise_for_status()


def _get_multi_monitor() -> dict:
    response = requests.get(f"{API_URL}/api/v1/workstations/multi-monitor", timeout=5)
    response.raise_for_status()
    return response.json()


def _set_multi_monitor(config: dict) -> dict:
    response = requests.put(
        f"{API_URL}/api/v1/workstations/multi-monitor",
        json=config,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


@pytest.fixture
def workstation_display_guard():
    """一次性保护两个会写 workstation_config.json 的分段。"""
    original_count = _get_channel_count()
    original_multi_monitor = _get_multi_monitor()
    try:
        yield
    finally:
        _set_multi_monitor(original_multi_monitor)
        _set_channel_count(original_count)


def _goto(page, base_url: str, hash_path: str) -> None:
    page.goto(f"{base_url}/#{hash_path}", wait_until="domcontentloaded", timeout=15_000)
    page.wait_for_timeout(1_200)


def _channel_from_url(url: str) -> int | None:
    values = parse_qs(urlparse(url).query).get("channel")
    return int(values[0]) if values else None


def _wait_for_channel(page, channels: list[int], target: int, timeout: float = 5.0) -> None:
    """等待被路由拦截器记录到目标视频请求，避免空集合包含关系造成假绿。"""
    remaining_ms = int(timeout * 1000)
    while target not in channels and remaining_ms > 0:
        page.wait_for_timeout(50)
        remaining_ms -= 50
    assert target in channels, f"未观察到 channel={target} 的视频请求，实际={channels}"


def test_settings_roundtrip_and_electron_apply(page, base_url, workstation_display_guard):
    """Settings 保存主/副屏坐标，读回后把规范化 payload 交给 Electron 热应用。"""
    _set_channel_count(2)
    _set_multi_monitor({"enabled": False, "readonly": True, "mapping": {}})
    page.add_init_script(
        """
        Object.defineProperty(window, 'electronAPI', {
          configurable: true,
          value: {
            isElectron: true,
            getLicenseStatus: async () => ({ valid: true }),
            getDisplays: async () => ({ ok: true, displays: [
              { id: '1001', label: '主屏 A', bounds: {x: 0, y: 0, width: 1920, height: 1080}, workArea: {x: 0, y: 0, width: 1920, height: 1040}, isPrimary: true, isMainWindowDisplay: true },
              { id: '2002', label: '副屏 B', bounds: {x: 1920, y: 0, width: 1280, height: 1024}, workArea: {x: 1920, y: 0, width: 1280, height: 984}, isPrimary: false },
            ]}),
            applyMultiMonitor: async (payload) => {
              window.__multiMonitorApplied = payload;
              window.__multiMonitorApplyCount = (window.__multiMonitorApplyCount || 0) + 1;
              const item = payload.mapping?.['0'] || {};
              const windows = [{ channelId: 0, role: 'main' }];
              if (item.aux_hands_enabled === true && (item.aux_display_id || item.aux_bounds)) {
                windows.push({ channelId: 0, role: 'aux' });
              }
              return { ok: true, enabled: payload.enabled, windows, warnings: [] };
            },
          },
        });
        """
    )

    # 多屏配置已从系统设置迁到「工位与输入源」页的独立 tab
    _goto(page, base_url, "/source")
    page.get_by_role("tab", name="多屏工位显示").click()
    card = page.get_by_test_id("multi-monitor-card")
    card.scroll_into_view_if_needed()
    enabled_switch = page.get_by_test_id("multi-monitor-enabled-switch")
    assert not enabled_switch.locator("input").is_checked()
    enabled_switch.click()
    main_select = page.get_by_test_id("multi-monitor-display-0")
    aux_enabled = page.get_by_test_id("multi-monitor-aux-hands-enabled-0")
    assert aux_enabled.locator("input").is_disabled()
    assert page.get_by_test_id("multi-monitor-aux-display-0").count() == 0
    assert page.get_by_test_id("multi-monitor-aux-view-mode-0").count() == 0
    main_select.click()
    main_option = page.get_by_role("option", name=re.compile("主屏 A.*主窗口当前所在"))
    assert main_option.get_attribute("aria-disabled") != "true"
    main_option.click()
    expect(aux_enabled.locator("input")).to_be_enabled()

    # 普通多屏工位只保存主屏，不会隐式创建手部副屏。
    page.get_by_test_id("multi-monitor-save").click()
    page.wait_for_function("() => window.__multiMonitorApplyCount === 1", timeout=10_000)
    applied_main_only = page.evaluate("window.__multiMonitorApplied")
    assert applied_main_only["mapping"]["0"] == {
        "display_id": "1001",
        "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
    }
    assert "1 个工位窗口" in page.get_by_test_id("multi-monitor-apply-result").inner_text()

    aux_enabled.click()
    aux_select = page.get_by_test_id("multi-monitor-aux-display-0")
    aux_mode_select = page.get_by_test_id("multi-monitor-aux-view-mode-0")
    expect(aux_select.locator("input")).to_be_enabled()
    expect(aux_mode_select).to_contain_text("手部跟随（固定大小）")
    # Element Plus 的 teleported 下拉有关闭过渡；等上一层销毁后再打开副屏选择，
    # 避免两个同名 option 在过渡帧中重叠。
    page.wait_for_timeout(300)
    aux_select.click()
    page.locator(".el-select-dropdown__item:visible").filter(has_text="副屏 B").click()
    page.wait_for_timeout(300)
    aux_mode_select.click()
    expect(
        page.locator(".el-select-dropdown__item:visible").filter(has_text="固定中心")
    ).to_be_visible()
    page.keyboard.press("Escape")
    page.get_by_test_id("multi-monitor-save").click()
    page.wait_for_function("() => window.__multiMonitorApplyCount === 2", timeout=10_000)

    applied = page.evaluate("window.__multiMonitorApplied")
    assert applied["enabled"] is True
    assert applied["readonly"] is True
    assert applied["mapping"]["0"] == {
        "display_id": "1001",
        "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
        "aux_display_id": "2002",
        "aux_bounds": {"x": 1920, "y": 0, "width": 1280, "height": 1024},
        "aux_hands_enabled": True,
        "aux_view_mode": "follow",
    }
    assert _get_multi_monitor() == applied
    assert "2 个工位窗口" in page.get_by_test_id("multi-monitor-apply-result").inner_text()
    assert page.evaluate("window.__multiMonitorApplyCount") == 2

    artifact_dir = os.environ.get("UAT_ARTIFACT_DIR")
    if artifact_dir:
        output_dir = Path(artifact_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(output_dir / "hands-aux-enabled-default-follow.png"),
            full_page=True,
        )

    # 关闭手部副屏后保留显示器和视角配置，但热应用只剩完整工位主窗。
    aux_enabled.click()
    assert page.get_by_test_id("multi-monitor-aux-display-0").count() == 0
    page.get_by_test_id("multi-monitor-save").click()
    page.wait_for_function("() => window.__multiMonitorApplyCount === 3", timeout=10_000)
    applied_disabled = page.evaluate("window.__multiMonitorApplied")
    assert applied_disabled["mapping"]["0"] == {
        **applied["mapping"]["0"],
        "aux_hands_enabled": False,
    }
    assert _get_multi_monitor() == applied_disabled
    assert "1 个工位窗口" in page.get_by_test_id("multi-monitor-apply-result").inner_text()

    aux_enabled.click()
    expect(page.get_by_test_id("multi-monitor-aux-view-mode-0")).to_contain_text(
        "手部跟随（固定大小）"
    )

    # 清主屏时保留已选副屏，但副屏不能脱离主屏被静默保存/丢弃。
    main_select.hover()
    main_select.locator(".el-select__clear").click()
    expect(page.get_by_test_id("multi-monitor-aux-requires-main-0")).to_be_visible()
    assert not aux_select.locator("input").is_disabled()  # 保持可用，便于用户清空孤立副屏。
    page.get_by_test_id("multi-monitor-save").click()
    expect(page.get_by_test_id("multi-monitor-apply-result")).to_contain_text(
        "工位 1 的副屏必须先配置主屏显示器"
    )
    assert page.evaluate("window.__multiMonitorApplyCount") == 3
    assert _get_multi_monitor() == applied_disabled


def test_monitor_does_not_render_emergency_exit_button(
    page, base_url, workstation_display_guard
):
    """主屏已由 Electron 保留，总览、放大和 kiosk 都不再渲染全局退出按钮。"""
    _set_channel_count(2)
    original_mapping = {
        "1": {
            "display_id": "2002",
            "bounds": {"x": 1920, "y": 0, "width": 1280, "height": 1024},
        }
    }
    _set_multi_monitor({"enabled": True, "readonly": True, "mapping": original_mapping})
    page.route("**/video_feed?**", lambda route: route.abort())
    page.route(
        "**/snapshot?**",
        lambda route: route.fulfill(
            status=200, content_type="image/jpeg", body=b"\xff\xd8\xff\xd9"
        ),
    )

    _goto(page, base_url, "/monitor")
    assert page.get_by_test_id("multi-monitor-emergency-exit").count() == 0
    page.get_by_test_id("channel-zoom-0").click()
    page.get_by_test_id("single-channel-monitor").wait_for(state="visible", timeout=5_000)
    assert page.get_by_test_id("multi-monitor-emergency-exit").count() == 0

    _goto(page, base_url, "/monitor?channel=1&kiosk=1&readonly=1&multi_monitor=1")
    page.get_by_test_id("single-channel-monitor").wait_for(state="visible", timeout=5_000)
    assert page.get_by_test_id("multi-monitor-emergency-exit").count() == 0
    assert _get_multi_monitor() == {
        "enabled": True,
        "readonly": True,
        "mapping": original_mapping,
    }


def test_kiosk_is_readonly_and_polls_only_requested_channel(page, base_url, workstation_display_guard):
    """kiosk 隐藏导航，控制按钮保留但禁用，只轮询 query 指定工位；readonly 缺省即 true。"""
    _set_channel_count(3)
    result_channels: list[int] = []
    video_info_channels: list[int] = []
    stream_channels: list[int] = []

    def observe_request(request):
        if "/source/detection/results" in request.url:
            result_channels.append(_channel_from_url(request.url))
        elif "/source/video/info" in request.url:
            video_info_channels.append(_channel_from_url(request.url))

    def fake_results(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "is_running": False,
                    "is_detecting": False,
                    "source_type": "video",
                    "fps": 18,
                    "latency": 12,
                    "counters": {"总产量": 5, "合格总数": 4, "不良总数": 1},
                    "project_config": {
                        "project_name": "E2E",
                        "steps_config": [
                            {"label": "step-a", "displayLabel": "步骤A", "enabled": True},
                            {"label": "step-b", "displayLabel": "步骤B", "enabled": True},
                        ],
                    },
                    "current_cycle_steps": ["step-a"],
                    "step_intervals": {"step-b": 1.2},
                    "cycle_sum_step_durations": {"step-a": 0.8},
                    "detections": [],
                    "recent_events": [],
                },
                ensure_ascii=False,
            ),
        )

    def abort_stream(route):
        stream_channels.append(_channel_from_url(route.request.url))
        route.abort()

    page.on("request", observe_request)
    page.route("**/api/v1/source/detection/results?**", fake_results)
    page.route("**/video_feed?**", abort_stream)
    _goto(page, base_url, "/monitor?channel=1&kiosk=1&multi_monitor=1")
    page.wait_for_timeout(800)
    _wait_for_channel(page, stream_channels, 1)

    monitor = page.get_by_test_id("single-channel-monitor")
    assert monitor.get_attribute("data-channel") == "1"
    assert monitor.get_attribute("data-readonly") == "true"
    assert page.get_by_test_id("hands-crop-view").count() == 0
    assert page.get_by_test_id("single-channel-title").inner_text() == "工位 2"
    controls = page.get_by_test_id("single-channel-controls")
    assert controls.count() == 1
    assert controls.get_attribute("data-readonly") == "true"
    for action in ("start", "stop", "standby", "reset"):
        assert page.get_by_test_id(f"single-channel-{action}").is_disabled()
    gauge = page.get_by_test_id("single-channel-yield-gauge")
    assert gauge.is_visible()
    assert gauge.get_attribute("data-rate") == "80.0"
    assert gauge.locator("canvas").count() == 1
    defect = page.get_by_test_id("single-channel-defect-chart")
    assert defect.is_visible()
    assert defect.get_attribute("data-good") == "4"
    assert defect.get_attribute("data-bad") == "1"
    assert defect.locator("canvas").count() == 1
    summary_cards = [
        page.get_by_test_id("single-channel-defect-card"),
        page.get_by_test_id("single-channel-yield-card"),
        page.get_by_test_id("single-channel-ng-top3"),
    ]
    summary_widths = [card.bounding_box()["width"] for card in summary_cards]
    assert max(summary_widths) - min(summary_widths) <= 2
    assert page.get_by_test_id("single-channel-back").count() == 0
    assert page.locator("button[title='导航菜单']").count() == 0
    sop_panel = page.get_by_test_id("sop-step-panel")
    step_table = page.get_by_test_id("single-channel-step-table")
    sop_panel.wait_for(state="visible", timeout=5_000)
    step_table.wait_for(state="visible", timeout=5_000)
    assert "步骤A" in sop_panel.inner_text()
    assert "步骤B" in sop_panel.inner_text()
    assert "步骤统计" in step_table.inner_text()
    video_box = page.get_by_test_id("single-channel-video").bounding_box()
    sop_box = sop_panel.bounding_box()
    assert video_box is not None and sop_box is not None
    assert sop_box["y"] >= video_box["y"] + video_box["height"]
    assert set(result_channels) == {1}
    assert set(video_info_channels) <= {1}
    assert set(stream_channels) <= {1}

    # 越界 query 钳制到最后工位；只有显式 readonly=0 才显示复用的既有控制入口。
    _goto(page, base_url, "/monitor?channel=99&kiosk=1&readonly=0&multi_monitor=1")
    monitor = page.get_by_test_id("single-channel-monitor")
    # 整页加载后工位数是异步拉取的, 元素先以默认 channelCount=1 (钳制=0) 可见,
    # 拉到 3 后才翻成 2 —— 用轮询断言等稳定态, 即时断言在回归负载下会输给竞态。
    expect(monitor).to_have_attribute("data-channel", "2", timeout=5_000)

    # 路由切换完成后再重置观察账本，排除上一工位已发出但尚未回调的瞬时请求。
    # reload 用当前 query 触发一轮确定的新流连接；稳定态仍严格要求只出现 channel=2。
    result_channels.clear()
    video_info_channels.clear()
    stream_channels.clear()
    page.reload(wait_until="domcontentloaded", timeout=15_000)
    monitor.wait_for(state="visible", timeout=5_000)
    expect(monitor).to_have_attribute("data-channel", "2", timeout=5_000)
    expect(monitor).to_have_attribute("data-readonly", "false", timeout=5_000)
    _wait_for_channel(page, result_channels, 2)
    _wait_for_channel(page, stream_channels, 2)
    controls = page.get_by_test_id("single-channel-controls")
    assert controls.count() == 1
    assert controls.get_attribute("data-readonly") == "false"
    expect(page.get_by_test_id("single-channel-start")).to_be_enabled(timeout=5_000)
    expect(page.get_by_test_id("single-channel-reset")).to_be_enabled(timeout=5_000)
    assert page.locator("button[title='导航菜单']").count() == 0
    assert page.get_by_test_id("single-channel-back").count() == 0
    assert set(result_channels) == {2}
    assert set(video_info_channels) <= {2}
    assert set(stream_channels) <= {2}


@pytest.mark.parametrize(
    ("mode_query", "expected_mode"),
    [("", "follow"), ("&aux_view_mode=fixed", "fixed")],
)
def test_hands_crop_kiosk_is_single_image_and_never_opens_full_monitor_streams(
    page, base_url, workstation_display_guard, mode_query, expected_mode
):
    """手部副屏只短轮询 hands snapshot，不实例化完整 Monitor 或连接 MJPEG。"""
    _set_channel_count(4)
    _set_multi_monitor({"enabled": True, "readonly": True, "mapping": {}})
    snapshot_urls: list[str] = []
    forbidden_urls: list[str] = []
    business_api_urls: list[str] = []

    def observe_request(request):
        if "/snapshot?" in request.url:
            snapshot_urls.append(request.url)
        if "/api/v1/" in request.url:
            business_api_urls.append(request.url)
        if any(
            path in request.url
            for path in (
                "/video_feed?",
                "/source/detection/results",
                "/source/video/info",
            )
        ):
            forbidden_urls.append(request.url)

    page.on("request", observe_request)
    page.add_init_script(
        """
        localStorage.setItem('tianjun:debug_flags', JSON.stringify({
          'page.nav': true,
          'app.lifecycle': true,
        }));
        window.__auxLifecycleRegistrations = 0;
        Object.defineProperty(window, 'electronAPI', {
          configurable: true,
          value: {
            isElectron: true,
            getLicenseStatus: async () => ({ valid: true }),
            onDeepGateDowngraded: () => { window.__auxLifecycleRegistrations += 1; },
            onBackendRecovered: () => { window.__auxLifecycleRegistrations += 1; },
          },
        });
        """
    )
    page.route(
        "**/snapshot?**",
        lambda route: route.fulfill(
            status=200, content_type="image/png", body=_BLACK_PIXEL_PNG
        ),
    )
    _goto(
        page,
        base_url,
        "/monitor?channel=2&kiosk=1&readonly=1&multi_monitor=1"
        f"&video_only=1&hands_crop=1{mode_query}",
    )

    crop_view = page.get_by_test_id("hands-crop-view")
    crop_view.wait_for(state="visible", timeout=5_000)
    page.wait_for_timeout(400)
    assert crop_view.get_attribute("data-channel") == "2"
    assert crop_view.get_attribute("data-crop-mode") == expected_mode
    assert page.get_by_test_id("hands-crop-image").count() == 1
    assert page.locator("img").count() == 1
    assert page.locator("button").count() == 0
    assert page.locator("canvas").count() == 0
    assert page.get_by_test_id("single-channel-monitor").count() == 0
    assert page.get_by_test_id("sop-step-panel").count() == 0
    assert page.get_by_test_id("single-channel-start").count() == 0
    assert snapshot_urls
    assert {
        _channel_from_url(url) for url in snapshot_urls
    } == {2}
    assert {
        parse_qs(urlparse(url).query).get("view", [None])[0]
        for url in snapshot_urls
    } == {"hands"}
    assert {
        parse_qs(urlparse(url).query).get("crop_mode", [None])[0]
        for url in snapshot_urls
    } == {expected_mode}
    assert forbidden_urls == []
    assert business_api_urls == []
    assert page.evaluate("window.__auxLifecycleRegistrations") == 0


@pytest.mark.parametrize("channel_count", [2, 3, 4])
def test_main_overview_uses_shared_single_channel_and_returns(
    page, base_url, workstation_display_guard, channel_count
):
    """2/3/4+ 总览都进入同一个 SingleChannelMonitor，并可返回原总览。"""
    _set_channel_count(channel_count)
    _set_multi_monitor({"enabled": True, "readonly": True, "mapping": {}})
    page.route("**/video_feed?**", lambda route: route.abort())
    page.route(
        "**/snapshot?**",
        lambda route: route.fulfill(status=200, content_type="image/jpeg", body=b"\xff\xd8\xff\xd9"),
    )
    _goto(page, base_url, "/monitor")

    # 多屏开启后各布局统一提供显式放大入口；关闭态由下一条用例守住整卡点击放大。
    page.get_by_test_id("channel-zoom-0").click()
    page.get_by_test_id("single-channel-monitor").wait_for(state="visible", timeout=5_000)
    assert page.get_by_test_id("single-channel-monitor").get_attribute("data-channel") == "0"
    assert page.get_by_test_id("single-channel-controls").count() == 1
    assert page.get_by_test_id("single-channel-defect-chart").is_visible()
    assert page.get_by_test_id("single-channel-yield-gauge").is_visible()
    page.get_by_test_id("single-channel-back").click()
    page.get_by_test_id("single-channel-monitor").wait_for(state="detached", timeout=5_000)
    assert page.get_by_test_id("channel-zoom-0").count() == 1


@pytest.mark.parametrize("channel_count", [2, 3, 4])
def test_disabled_overview_card_click_zooms_and_returns(
    page, base_url, workstation_display_guard, channel_count
):
    """多屏关闭时不增加额外按钮，但 2/3/4+ 总览视频卡点击均可放大并返回。"""
    _set_channel_count(channel_count)
    _set_multi_monitor({"enabled": False, "readonly": True, "mapping": {}})
    page.route("**/video_feed?**", lambda route: route.abort())
    page.route(
        "**/snapshot?**",
        lambda route: route.fulfill(status=200, content_type="image/jpeg", body=b"\xff\xd8\xff\xd9"),
    )
    _goto(page, base_url, "/monitor")

    assert page.locator("[data-testid^='channel-zoom-']").count() == 0
    assert page.get_by_test_id("multi-monitor-emergency-exit").count() == 0
    page.get_by_test_id("channel-card-1").click()
    page.get_by_test_id("single-channel-monitor").wait_for(state="visible", timeout=5_000)
    assert page.get_by_test_id("single-channel-monitor").get_attribute("data-channel") == "1"
    assert page.get_by_test_id("single-channel-controls").count() == 1
    page.get_by_test_id("single-channel-back").click()
    page.get_by_test_id("single-channel-monitor").wait_for(state="detached", timeout=5_000)
    assert page.get_by_test_id("channel-card-1").is_visible()


def test_enabled_overview_and_zoom_keep_using_snapshots(
    page, base_url, workstation_display_guard
):
    """多屏开启时普通主窗口总览与放大都不抢扩展工位的 MJPEG。"""
    _set_channel_count(4)
    _set_multi_monitor({"enabled": True, "readonly": True, "mapping": {}})
    snapshot_channels: list[int] = []
    stream_channels: list[int] = []

    def fulfill_snapshot(route):
        snapshot_channels.append(_channel_from_url(route.request.url))
        route.fulfill(status=200, content_type="image/jpeg", body=b"\xff\xd8\xff\xd9")

    def abort_stream(route):
        stream_channels.append(_channel_from_url(route.request.url))
        route.abort()

    page.route("**/snapshot?**", fulfill_snapshot)
    page.route("**/video_feed?**", abort_stream)
    _goto(page, base_url, "/monitor")
    page.wait_for_timeout(600)
    assert set(snapshot_channels) == {0, 1, 2, 3}
    assert stream_channels == []

    snapshot_channels.clear()
    page.get_by_test_id("channel-card-2").click()
    page.get_by_test_id("single-channel-monitor").wait_for(state="visible", timeout=5_000)
    _wait_for_channel(page, snapshot_channels, 2)
    page.wait_for_timeout(300)
    assert stream_channels == []
    assert snapshot_channels
    assert set(snapshot_channels[-3:]) == {2}


@pytest.mark.parametrize("channel_count", [2, 3])
def test_primary_station_view_is_operable_then_sidebar_monitor_returns_overview(
    page, base_url, channel_count
):
    """复用主窗口首次显示映射工位；停止态切页再回检测时恢复多工位总览。"""
    stream_channels: list[int] = []

    def fake_workstations(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"channel_count": channel_count, "source_configs": {}},
                ensure_ascii=False,
            ),
        )

    def fake_multi_monitor(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"enabled": True, "readonly": True, "mapping": {}}, ensure_ascii=False),
        )

    def fake_results(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "is_running": False,
                    "is_detecting": False,
                    "source_type": "camera",
                    "fps": 20,
                    "latency": 10,
                    "counters": {"总产量": 0, "合格总数": 0, "不良总数": 0},
                    "project_config": {
                        "project_name": "主窗口复用 E2E",
                        "steps_config": [],
                    },
                    "detections": [],
                    "recent_events": [],
                },
                ensure_ascii=False,
            ),
        )

    def abort_stream(route):
        stream_channels.append(_channel_from_url(route.request.url))
        route.abort()

    page.route(re.compile(r".*/api/v1/workstations/?(?:\?.*)?$"), fake_workstations)
    page.route("**/api/v1/workstations/multi-monitor", fake_multi_monitor)
    page.route("**/api/v1/source/detection/results?**", fake_results)
    page.route("**/video_feed?**", abort_stream)
    page.route(
        "**/snapshot?**",
        lambda route: route.fulfill(
            status=200, content_type="image/jpeg", body=b"\xff\xd8\xff\xd9"
        ),
    )

    _goto(
        page,
        base_url,
        "/monitor?channel=1&station_view=1&readonly=0&multi_monitor=1",
    )
    monitor = page.get_by_test_id("single-channel-monitor")
    expect(monitor).to_be_visible(timeout=5_000)
    expect(monitor).to_have_attribute("data-channel", "1", timeout=5_000)
    expect(monitor).to_have_attribute("data-readonly", "false", timeout=5_000)
    assert page.locator("button[title='导航菜单']").count() == 1
    assert page.get_by_test_id("single-channel-controls").count() == 1
    assert not page.get_by_test_id("single-channel-start").is_disabled()
    assert page.get_by_test_id("single-channel-back").count() == 0
    assert page.get_by_test_id("single-channel-previous").count() == 0
    assert page.get_by_test_id("single-channel-next").count() == 0
    _wait_for_channel(page, stream_channels, 1)
    assert set(stream_channels) == {1}

    # 停止态侧栏调试闭环：进入其它模块后，再点“检测”必须回到总览，
    # 不能被初始 station_view 查询参数永久锁在工位 1/指定工位。
    page.locator("button[title='导航菜单']").click()
    page.locator("aside a[href$='/source']").click()
    expect(page).to_have_url(re.compile(r"#/source$"), timeout=5_000)
    page.locator("button[title='导航菜单']").click()
    stream_channels.clear()
    page.locator("aside").get_by_role(
        "link", name="检测中心", exact=True
    ).click()
    expect(page).to_have_url(re.compile(r"#/monitor$"), timeout=5_000)
    expect(page.get_by_test_id("single-channel-monitor")).to_have_count(0)
    for channel in range(channel_count):
        expect(page.get_by_test_id(f"channel-card-{channel}")).to_be_visible(
            timeout=5_000
        )
    assert stream_channels == []

    # 返回总览后仍能继续放大、返回，不会再次丢失总览入口。
    page.get_by_test_id("channel-card-1").click()
    expect(page.get_by_test_id("single-channel-monitor")).to_have_attribute(
        "data-channel", "1"
    )
    assert stream_channels == []
    page.get_by_test_id("single-channel-back").click()
    expect(page.get_by_test_id("single-channel-monitor")).to_have_count(0)
    expect(page.get_by_test_id("channel-card-1")).to_be_visible(timeout=5_000)
