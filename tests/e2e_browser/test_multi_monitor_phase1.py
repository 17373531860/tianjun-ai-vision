"""多屏工位显示一期 E2E。

测试均记录并 finally 还原 channel_count 与 multi_monitor 顶层配置，不保留现场配置改动。
视频连接用 Playwright 路由观察/截断，不依赖真实摄像头。
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from .conftest import API_URL


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
    """Settings 保存 display_id+bounds，读回后把规范化 payload 交给 Electron 热应用。"""
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
              return { ok: true, enabled: payload.enabled, windows: [0], warnings: [] };
            },
          },
        });
        """
    )

    _goto(page, base_url, "/settings")
    card = page.get_by_test_id("multi-monitor-card")
    card.scroll_into_view_if_needed()
    page.get_by_test_id("multi-monitor-enabled-switch").click()
    page.get_by_test_id("multi-monitor-display-0").click()
    main_option = page.get_by_role("option", name=re.compile("主窗口.*不可绑定"))
    assert main_option.get_attribute("aria-disabled") == "true"
    page.get_by_role("option", name=re.compile("副屏 B")).click()
    page.get_by_test_id("multi-monitor-save").click()
    page.wait_for_function("() => !!window.__multiMonitorApplied", timeout=10_000)

    applied = page.evaluate("window.__multiMonitorApplied")
    assert applied["enabled"] is True
    assert applied["readonly"] is True
    assert applied["mapping"]["0"] == {
        "display_id": "2002",
        "bounds": {"x": 1920, "y": 0, "width": 1280, "height": 1024},
    }
    assert _get_multi_monitor() == applied
    assert "1 个工位窗口" in page.get_by_test_id("multi-monitor-apply-result").inner_text()


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
    assert monitor.get_attribute("data-channel") == "2"

    # 路由切换完成后再重置观察账本，排除上一工位已发出但尚未回调的瞬时请求。
    # reload 用当前 query 触发一轮确定的新流连接；稳定态仍严格要求只出现 channel=2。
    result_channels.clear()
    video_info_channels.clear()
    stream_channels.clear()
    page.reload(wait_until="domcontentloaded", timeout=15_000)
    monitor.wait_for(state="visible", timeout=5_000)
    assert monitor.get_attribute("data-channel") == "2"
    _wait_for_channel(page, result_channels, 2)
    _wait_for_channel(page, stream_channels, 2)
    assert page.get_by_test_id("single-channel-controls").count() == 1
    assert set(result_channels) == {2}
    assert set(video_info_channels) <= {2}
    assert set(stream_channels) <= {2}


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

    # 多屏开启后各布局统一提供显式放大入口；关闭态由下一条用例守住原选择逻辑。
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
def test_disabled_workstations_keep_legacy_selection(
    page, base_url, workstation_display_guard, channel_count
):
    """多屏关闭时不增加放大按钮；2/3 工位整卡点击仍只改 selectedChannel，
    4+ 工位网格整卡点击保持 v3.47 的放大单路行为（零差异守门）。"""
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
    page.wait_for_timeout(200)
    if channel_count > 3:
        # v3.47 交付行为: 网格整卡点击=放大单路 (多屏关闭时必须与 v3.51.5 一致)
        page.get_by_test_id("single-channel-monitor").wait_for(state="visible", timeout=5_000)
        assert page.get_by_test_id("single-channel-monitor").get_attribute("data-channel") == "1"
        page.get_by_test_id("single-channel-back").click()
        page.get_by_test_id("single-channel-monitor").wait_for(state="detached", timeout=5_000)
    else:
        assert page.get_by_test_id("single-channel-monitor").count() == 0
        assert "border-cyan-500" in (page.get_by_test_id("channel-card-1").get_attribute("class") or "")


def test_enabled_overview_uses_snapshots_then_zoom_uses_one_mjpeg(
    page, base_url, workstation_display_guard
):
    """多屏开启时主屏总览不占 MJPEG；放大后只连接目标工位一路。"""
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
    _wait_for_channel(page, stream_channels, 2)
    assert stream_channels
    assert set(stream_channels) == {2}
