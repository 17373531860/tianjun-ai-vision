"""多屏同工位真实 MJPEG 隔离回归。

使用 RUNTIME_MODE=test 的 synthetic 源产生真实 JPEG 流；主窗口和 kiosk 窗口
分别运行在独立 Chromium context 中。测试只旁路记录 canvas 绘制与网络请求，
不 mock、不 fulfill、不 abort ``/video_feed``。
"""
from __future__ import annotations

import statistics
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from playwright.sync_api import BrowserContext, Page, expect


_FRAME_OBSERVER = r"""
(() => {
  window.__e2eVideoFrameTimes = [];
  const original = CanvasRenderingContext2D.prototype.drawImage;
  CanvasRenderingContext2D.prototype.drawImage = function(...args) {
    const result = original.apply(this, args);
    const canvas = this.canvas;
    const card = canvas && canvas.closest
      ? canvas.closest('[data-testid^="channel-card-"]')
      : null;
    // ChannelVideoCard 的第一块 canvas 是视频画布；第二块是检测框 overlay。
    if (card && canvas === card.querySelector('canvas')) {
      const raw = card.getAttribute('data-testid') || '';
      const channel = Number(raw.replace('channel-card-', ''));
      window.__e2eVideoFrameTimes.push({ channel, at: performance.now() });
      if (window.__e2eVideoFrameTimes.length > 2000) {
        window.__e2eVideoFrameTimes.splice(0, 1000);
      }
    }
    return result;
  };
})();
"""


def _api(api_url: str, method: str, path: str, **kwargs) -> requests.Response:
    return requests.request(method, f"{api_url}{path}", timeout=20, **kwargs)


def _channel_count(api_url: str) -> int:
    response = _api(api_url, "GET", "/api/v1/workstations/")
    response.raise_for_status()
    return int(response.json()["channel_count"])


def _set_channel_count(api_url: str, count: int) -> None:
    response = _api(
        api_url,
        "POST",
        "/api/v1/workstations/mode",
        json={"channel_count": count},
    )
    response.raise_for_status()


def _multi_monitor(api_url: str) -> dict:
    response = _api(api_url, "GET", "/api/v1/workstations/multi-monitor")
    response.raise_for_status()
    return response.json()


def _set_multi_monitor(api_url: str, config: dict) -> None:
    response = _api(
        api_url,
        "PUT",
        "/api/v1/workstations/multi-monitor",
        json=config,
    )
    response.raise_for_status()


@pytest.fixture
def live_multi_monitor_guard(api_url):
    """保护持久化的工位数和多屏配置，并确保 synthetic 工位被收干净。"""
    original_count = _channel_count(api_url)
    original_multi_monitor = _multi_monitor(api_url)
    _set_channel_count(api_url, 2)
    _set_multi_monitor(api_url, {"enabled": True, "readonly": True, "mapping": {}})
    try:
        yield
    finally:
        # detection/stop 只停推理；synthetic/stop 再停采集并恢复临时项目。
        try:
            _api(api_url, "POST", "/api/v1/source/detection/stop?channel=1")
        except requests.RequestException:
            pass
        try:
            _api(api_url, "POST", "/api/v1/test/synthetic/stop?channel=1")
        except requests.RequestException:
            pass
        _set_multi_monitor(api_url, original_multi_monitor)
        _set_channel_count(api_url, original_count)


def _stream_request(url: str, *, channel: int, viewer: str) -> bool:
    if "/video_feed" not in url:
        return False
    query = parse_qs(urlparse(url).query)
    try:
        request_channel = int((query.get("channel") or [0])[0])
    except (TypeError, ValueError):
        return False
    return request_channel == channel and (query.get("viewer") or [None])[0] == viewer


def _frame_times(page: Page, channel: int = 1) -> list[float]:
    return page.evaluate(
        """channel => (window.__e2eVideoFrameTimes || [])
          .filter(item => item.channel === channel)
          .map(item => item.at)""",
        channel,
    )


def _wait_for_frames(
    page: Page,
    *,
    after_count: int = 0,
    additional: int = 8,
    channel: int = 1,
    timeout_ms: int = 10_000,
) -> list[float]:
    target = after_count + additional
    page.wait_for_function(
        """arg => (window.__e2eVideoFrameTimes || [])
          .filter(item => item.channel === arg.channel).length >= arg.target""",
        arg={"channel": channel, "target": target},
        timeout=timeout_ms,
    )
    return _frame_times(page, channel)


def _wait_inference_running(api_url: str, timeout_s: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout_s
    latest: dict = {}
    while time.monotonic() < deadline:
        response = _api(
            api_url,
            "GET",
            "/api/v1/source/detection/results?channel=1",
        )
        response.raise_for_status()
        latest = response.json()
        if latest.get("is_running") and latest.get("is_detecting") \
                and float(latest.get("fps_inference") or 0) > 0:
            return latest
        time.sleep(0.1)
    raise AssertionError(f"synthetic 推理未进入稳定运行态: {latest}")


def _open_monitor(page: Page, base_url: str, hash_path: str) -> None:
    page.goto(f"{base_url}/#{hash_path}", wait_until="domcontentloaded", timeout=20_000)


def _new_context(browser) -> BrowserContext:
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    context.add_init_script(_FRAME_OBSERVER)
    return context


def test_main_zoom_does_not_stall_or_reconnect_station_stream(
    playwright,
    base_url,
    api_url,
    live_multi_monitor_guard,
):
    """主屏放大/返回/再放大时，副屏真实流保持同连接、同帧率且推理不停。"""
    availability = _api(api_url, "GET", "/api/v1/test/synthetic/state?channel=1")
    if availability.status_code == 404:
        pytest.skip("backend 未启用 RUNTIME_MODE=test，无法启动真实 synthetic MJPEG")
    availability.raise_for_status()

    active = _api(api_url, "GET", "/api/v1/projects/active/current")
    active.raise_for_status()
    active_project_id = int(active.json()["id"])

    # 长时间连续时间线：画面由 capture thread 以 30fps 真实生成，测试期间不结算周期。
    scenario = {
        "name": "multi-monitor-live-stream-isolation",
        "fps": 30,
        "timeline": [
            {
                "from": 0,
                "to": 30 * 300,
                "detections": [
                    {
                        "label": "stream_alive",
                        "confidence": 0.99,
                        "bbox": [0.2, 0.2, 0.2, 0.2],
                    }
                ],
            }
        ],
    }
    started = _api(
        api_url,
        "POST",
        "/api/v1/test/synthetic/start",
        json={
            "scenario_json": scenario,
            "channel": 1,
            "with_project": True,
            "project_id": active_project_id,
        },
    )
    assert started.status_code == 200, started.text
    detecting = _api(
        api_url,
        "POST",
        "/api/v1/source/detection/start?channel=1",
        json={"conf": 0.25, "iou": 0.45},
    )
    assert detecting.status_code == 200, detecting.text
    before_result = _wait_inference_running(api_url)

    # 两个 context + 禁后台节流，等价 Electron 的两个独立 BrowserWindow renderer。
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ],
    )
    main_context = _new_context(browser)
    station_context = _new_context(browser)
    main_page = main_context.new_page()
    station_page = station_context.new_page()
    main_streams: list[str] = []
    station_streams: list[str] = []

    def observe_main(request) -> None:
        if _stream_request(request.url, channel=1, viewer="main"):
            main_streams.append(request.url)

    def observe_station(request) -> None:
        if _stream_request(request.url, channel=1, viewer="station"):
            station_streams.append(request.url)

    main_page.on("request", observe_main)
    station_page.on("request", observe_station)

    try:
        # 主屏先停在总览；多屏开启时该态只走 snapshot，不占 channel 1 MJPEG 槽。
        _open_monitor(main_page, base_url, "/monitor")
        main_page.get_by_test_id("channel-card-1").wait_for(state="visible", timeout=10_000)
        main_page.wait_for_timeout(500)
        assert main_streams == []

        # 副屏真实 kiosk：绑定工位 2，只读，先让 MJPEG 与 canvas 进入稳定态。
        _open_monitor(
            station_page,
            base_url,
            "/monitor?channel=1&kiosk=1&readonly=1&multi_monitor=1",
        )
        station_monitor = station_page.get_by_test_id("single-channel-monitor")
        expect(station_monitor).to_have_attribute("data-channel", "1", timeout=10_000)
        expect(station_monitor).to_have_attribute("data-readonly", "true")
        station_frames = _wait_for_frames(station_page, additional=18)
        assert station_streams, "副屏没有发起 viewer=station 的真实 MJPEG 请求"

        # 原 UI 行为守门：副屏按钮存在但全禁用，未通过删按钮规避写通路。
        assert station_page.get_by_test_id("single-channel-controls").count() == 1
        for action in ("start", "stop", "standby", "reset"):
            expect(station_page.get_by_test_id(f"single-channel-{action}")).to_be_disabled()

        baseline_gaps = [
            b - a for a, b in zip(station_frames[-12:-1], station_frames[-11:])
        ]
        baseline_median_gap = statistics.median(baseline_gaps)
        transition_start = max(0, len(station_frames) - 4)
        station_request_count = len(station_streams)

        # 第一次从总览点击副工位卡片放大：主/副两窗开始并发消费 channel 1。
        main_page.get_by_test_id("channel-card-1").click()
        main_monitor = main_page.get_by_test_id("single-channel-monitor")
        expect(main_monitor).to_have_attribute("data-channel", "1", timeout=10_000)
        expect(main_monitor).to_have_attribute("data-readonly", "false")
        main_before = len(_frame_times(main_page))
        _wait_for_frames(main_page, after_count=main_before, additional=10)
        concurrent_start = len(_frame_times(station_page))
        concurrent_frames = _wait_for_frames(
            station_page,
            after_count=concurrent_start,
            additional=16,
        )

        # 原主屏控制保留：目标工位检测中时开始禁用，停止/待机/清零仍可操作。
        expect(main_page.get_by_test_id("single-channel-start")).to_be_disabled()
        for action in ("stop", "standby", "reset"):
            expect(main_page.get_by_test_id(f"single-channel-{action}")).to_be_enabled()

        concurrent_gaps = [
            b - a
            for a, b in zip(
                concurrent_frames[concurrent_start:-1],
                concurrent_frames[concurrent_start + 1 :],
            )
        ]
        concurrent_median_gap = statistics.median(concurrent_gaps)

        # 返回总览后，副屏不重连且继续出帧。
        before_back = len(concurrent_frames)
        main_page.get_by_test_id("single-channel-back").click()
        main_monitor.wait_for(state="detached", timeout=10_000)
        _wait_for_frames(station_page, after_count=before_back, additional=10)

        # 再次放大同一工位：主窗应新建自己的 main 连接，副窗 station 连接保持原样。
        main_page.get_by_test_id("channel-card-1").click()
        expect(main_page.get_by_test_id("single-channel-monitor")).to_have_attribute(
            "data-channel", "1", timeout=10_000
        )
        second_main_start = len(_frame_times(main_page))
        _wait_for_frames(main_page, after_count=second_main_start, additional=10)
        before_second_zoom = len(_frame_times(station_page))
        final_station_frames = _wait_for_frames(
            station_page,
            after_count=before_second_zoom,
            additional=12,
        )

        # 视图切换不能停止或重启推理；线程健康与检测状态都必须保持。
        after_result = _wait_inference_running(api_url)
        assert before_result["is_detecting"] is True
        assert after_result["is_detecting"] is True
        assert float(after_result.get("fps_inference") or 0) > 0
        health = _api(api_url, "GET", "/api/v1/source/health?channel=1")
        health.raise_for_status()
        threads = health.json().get("threads") or {}
        assert (threads.get("capture") or {}).get("healthy") is True
        assert (threads.get("inference") or {}).get("healthy") is True

        transition_frames = final_station_frames[transition_start:]
        transition_gaps = [
            b - a for a, b in zip(transition_frames[:-1], transition_frames[1:])
        ]
        assert transition_gaps, "副屏没有可用于连续性判定的真实 canvas 帧"
        max_transition_gap = max(transition_gaps)
        assert max_transition_gap < 500, (
            f"主屏切换期间副屏出现 {max_transition_gap:.1f}ms 停顿（阈值 500ms）"
        )

        # 同时打开主窗不应让副屏稳态帧率降档；仅给 CI 调度抖动留 10% 余量。
        allowed_median_gap = baseline_median_gap / 0.90
        assert concurrent_median_gap <= allowed_median_gap, (
            "主副窗并发后副屏帧率明显下降: "
            f"baseline={baseline_median_gap:.1f}ms, "
            f"concurrent={concurrent_median_gap:.1f}ms"
        )

        # 主窗每次放大允许建立一条 main 流；副窗在整个过程不得因被踢而重连。
        assert len(main_streams) == 2, f"主窗连接次数异常: {main_streams}"
        assert len(station_streams) == station_request_count, (
            f"主屏切换触发副屏重连: before={station_request_count}, "
            f"after={len(station_streams)}"
        )
        print(
            "[multi-monitor-live] "
            f"baseline_median={baseline_median_gap:.1f}ms "
            f"concurrent_median={concurrent_median_gap:.1f}ms "
            f"transition_max={max_transition_gap:.1f}ms "
            f"main_requests={len(main_streams)} "
            f"station_requests={len(station_streams)}"
        )
    finally:
        station_context.close()
        main_context.close()
        browser.close()
