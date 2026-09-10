"""双/三工位主窗口总览点击放大可见浏览器 UAT。

覆盖普通主窗口的双工位、三工位放大/返回、多屏开启后的快照隔离，
以及 station_view 停止态切侧栏后恢复总览；同时守住工位首屏单路 MJPEG。
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, filter_console_errors, launch_browser


FRONT = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:6001")
API = os.environ.get("E2E_API_URL", "http://127.0.0.1:8001")
EVIDENCE_DIR = os.environ.get("UAT_ARTIFACT_DIR")
_BLACK_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42Y"
    "AAAAASUVORK5CYII="
)


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    response = requests.request(method, f"{API}{path}", json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def _set_count(count: int) -> None:
    _request("POST", "/api/v1/workstations/mode", {"channel_count": count})


def _set_multi_monitor(config: dict) -> None:
    _request("PUT", "/api/v1/workstations/multi-monitor", config)


def _channel(url: str) -> int | None:
    values = parse_qs(urlparse(url).query).get("channel")
    return int(values[0]) if values else None


def _wait_for_channel(page, channels: list[int | None], expected: int) -> bool:
    for _ in range(50):
        if expected in channels:
            return True
        page.wait_for_timeout(100)
    return expected in channels


def main() -> int:
    run = UatRun("monitor_overview_zoom", evidence_dir=EVIDENCE_DIR)
    original_count = int(_request("GET", "/api/v1/workstations/")["channel_count"])
    original_multi = _request("GET", "/api/v1/workstations/multi-monitor")
    browser = context = page = None
    video = None
    video_path = None

    try:
        with sync_playwright() as playwright:
            browser, context, page, console_errors = launch_browser(
                playwright,
                headless=False,
                slow_mo=100,
                viewport=(1600, 1000),
                record_video_dir=run.video_dir,
            )
            video = page.video
            stream_channels: list[int | None] = []
            snapshot_channels: list[int | None] = []
            browser_config_writes: list[str] = []

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
                            "latency": 8,
                            "counters": {
                                "总产量": 0,
                                "合格总数": 0,
                                "不良总数": 0,
                            },
                            "project_config": {
                                "project_name": "总览放大 UAT",
                                "steps_config": [],
                            },
                            "detections": [],
                            "recent_events": [],
                        },
                        ensure_ascii=False,
                    ),
                )

            def abort_stream(route):
                stream_channels.append(_channel(route.request.url))
                route.abort()

            def fulfill_snapshot(route):
                snapshot_channels.append(_channel(route.request.url))
                route.fulfill(
                    status=200,
                    content_type="image/png",
                    body=_BLACK_PIXEL_PNG,
                )

            def observe_request(request):
                if (
                    "/api/v1/workstations/multi-monitor" in request.url
                    and request.method != "GET"
                ):
                    browser_config_writes.append(f"{request.method} {request.url}")

            page.on("request", observe_request)
            page.route("**/api/v1/source/detection/results?**", fake_results)
            page.route("**/video_feed?**", abort_stream)
            page.route("**/snapshot?**", fulfill_snapshot)

            disabled_config = {"enabled": False, "readonly": True, "mapping": {}}
            _set_multi_monitor(disabled_config)

            # 双工位：点击视频卡进入工位 2 完整详情，再返回双工位总览。
            _set_count(2)
            page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded", timeout=20_000)
            page.get_by_test_id("dual-col-0").wait_for(state="visible", timeout=10_000)
            page.wait_for_timeout(700)
            run.shot(page, "01_dual_overview")
            page.get_by_test_id("channel-card-1").click()
            monitor = page.get_by_test_id("single-channel-monitor")
            monitor.wait_for(state="visible", timeout=8_000)
            page.wait_for_timeout(500)
            run.shot(page, "02_dual_station_2_zoom")
            run.step(
                "双工位点击工位 2 进入完整详情",
                monitor.get_attribute("data-channel") == "1"
                and page.get_by_test_id("single-channel-controls").count() == 1,
            )
            page.get_by_test_id("single-channel-back").click()
            monitor.wait_for(state="detached", timeout=8_000)
            run.shot(page, "03_dual_returned")
            run.step("双工位详情可返回原总览", page.get_by_test_id("dual-col-0").is_visible())

            # 三工位：同一交互进入工位 3，再返回三列总览。
            _set_count(3)
            page.reload(wait_until="domcontentloaded", timeout=20_000)
            page.get_by_test_id("triple-grid").wait_for(state="visible", timeout=10_000)
            page.wait_for_timeout(700)
            run.shot(page, "04_triple_overview")
            page.get_by_test_id("channel-card-2").click()
            monitor.wait_for(state="visible", timeout=8_000)
            page.wait_for_timeout(500)
            run.shot(page, "05_triple_station_3_zoom")
            run.step(
                "三工位点击工位 3 进入完整详情",
                monitor.get_attribute("data-channel") == "2"
                and page.get_by_test_id("single-channel-controls").count() == 1,
            )
            page.get_by_test_id("single-channel-back").click()
            monitor.wait_for(state="detached", timeout=8_000)
            run.shot(page, "06_triple_returned")
            run.step("三工位详情可返回原总览", page.get_by_test_id("triple-grid").is_visible())

            # 多屏开启：普通主窗口放大继续短轮询 snapshot，不抢已扩展工位的 MJPEG。
            enabled_config = {"enabled": True, "readonly": True, "mapping": {}}
            _set_multi_monitor(enabled_config)
            page.reload(wait_until="domcontentloaded", timeout=20_000)
            page.get_by_test_id("triple-grid").wait_for(state="visible", timeout=10_000)
            page.wait_for_timeout(500)
            stream_channels.clear()
            snapshot_channels.clear()
            page.get_by_test_id("channel-card-1").click()
            monitor.wait_for(state="visible", timeout=8_000)
            saw_snapshot = _wait_for_channel(page, snapshot_channels, 1)
            page.wait_for_timeout(350)
            run.shot(page, "07_enabled_zoom_snapshot_only")
            run.step(
                "多屏开启后普通主窗口放大只取目标快照",
                saw_snapshot
                and stream_channels == []
                and snapshot_channels
                and set(snapshot_channels[-3:]) == {1},
                f"snapshot={snapshot_channels[-6:]} mjpeg={stream_channels}",
            )
            run.step(
                "总览点击不写多屏配置",
                browser_config_writes == []
                and _request("GET", "/api/v1/workstations/multi-monitor") == enabled_config,
                f"writes={browser_config_writes}",
            )

            # 真实工位主窗 station_view 仍保留侧栏、操作按钮和单路 MJPEG。
            stream_channels.clear()
            snapshot_channels.clear()
            page.goto(
                f"{FRONT}/#/monitor?channel=1&station_view=1&readonly=0&multi_monitor=1",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            monitor.wait_for(state="visible", timeout=8_000)
            saw_stream = _wait_for_channel(page, stream_channels, 1)
            page.wait_for_timeout(500)
            run.shot(page, "08_station_view_mjpeg")
            run.step(
                "station_view 工位主窗仍保留侧栏、控制与单路 MJPEG",
                saw_stream
                and set(stream_channels) == {1}
                and page.locator("button[title='导航菜单']").count() == 1
                and page.get_by_test_id("single-channel-controls").count() == 1
                and page.get_by_test_id("single-channel-back").count() == 0,
                f"mjpeg={stream_channels} snapshot={snapshot_channels}",
            )

            # 停止态从侧栏进入其它模块，再点“检测中心”应回到普通总览。
            page.locator("button[title='导航菜单']").click()
            page.locator("aside a[href$='/source']").click()
            page.wait_for_url(re.compile(r"#/source$"), timeout=8_000)
            page.locator("button[title='导航菜单']").click()
            stream_channels.clear()
            snapshot_channels.clear()
            page.locator("aside").get_by_role(
                "link", name="检测中心", exact=True
            ).click()
            page.wait_for_url(re.compile(r"#/monitor$"), timeout=8_000)
            page.get_by_test_id("triple-grid").wait_for(state="visible", timeout=8_000)
            saw_overview_snapshot = _wait_for_channel(page, snapshot_channels, 2)
            page.wait_for_timeout(500)
            run.shot(page, "09_sidebar_returned_to_triple_overview")
            run.step(
                "停止态切侧栏再回检测恢复三工位总览",
                page.get_by_test_id("single-channel-monitor").count() == 0
                and page.get_by_test_id("channel-card-0").is_visible()
                and page.get_by_test_id("channel-card-1").is_visible()
                and page.get_by_test_id("channel-card-2").is_visible()
                and saw_overview_snapshot
                and stream_channels == [],
                f"url={page.url} snapshot={snapshot_channels[-6:]} mjpeg={stream_channels}",
            )

            page.get_by_test_id("channel-card-1").click()
            monitor.wait_for(state="visible", timeout=8_000)
            zoomed_again = monitor.get_attribute("data-channel") == "1"
            page.get_by_test_id("single-channel-back").click()
            monitor.wait_for(state="detached", timeout=8_000)
            page.wait_for_timeout(350)
            run.shot(page, "10_sidebar_return_overview_still_operable")
            run.step(
                "侧栏返回后的总览仍可放大并返回",
                zoomed_again
                and page.get_by_test_id("triple-grid").is_visible()
                and stream_channels == [],
            )
            run.step(
                "侧栏返回总览不写多屏配置",
                browser_config_writes == []
                and _request("GET", "/api/v1/workstations/multi-monitor") == enabled_config,
                f"writes={browser_config_writes}",
            )

            real_console_errors = filter_console_errors(
                console_errors,
                extra_noise=(
                    "Project not found",
                    "Channel 1 not configured",
                    "Channel 2 not configured",
                    "not configured (active:",
                ),
            )
            run.step(
                "浏览器无前端逻辑错误",
                not real_console_errors,
                f"errors={real_console_errors[:3]}",
            )

            page.unroute_all(behavior="ignoreErrors")
            context.close()
            context = None
            if video is not None:
                video_path = Path(video.path())
            browser.close()
            browser = None
    except Exception as exc:  # noqa: BLE001 — 失败也必须落三件套证据与还原配置
        run.step("UAT 执行未抛异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            if context is not None:
                context.close()
                if video is not None:
                    video_path = Path(video.path())
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        restore_ok = True
        restore_detail = ""
        try:
            _set_multi_monitor(original_multi)
            _set_count(original_count)
            restored_count = int(_request("GET", "/api/v1/workstations/")["channel_count"])
            restored_multi = _request("GET", "/api/v1/workstations/multi-monitor")
            restore_ok = restored_count == original_count and restored_multi == original_multi
            restore_detail = f"channel_count={restored_count}"
        except Exception as exc:  # noqa: BLE001
            restore_ok = False
            restore_detail = f"{type(exc).__name__}: {exc}"
        run.step("工位数与多屏配置已还原", restore_ok, restore_detail)

    video_target = Path(run.dir) / "uat-visible.webm"
    if video_path is not None and video_path.exists():
        try:
            if video_path != video_target:
                video_path.replace(video_target)
        except Exception:
            pass
    current_screenshots = [
        path
        for path in Path(run.dir).glob("*.png")
        if path.stat().st_size > 1_000 and path.stat().st_mtime >= run.t0 - 1
    ]
    current_video_ok = (
        video_target.exists()
        and video_target.stat().st_size > 100_000
        and video_target.stat().st_mtime >= run.t0 - 1
    )
    run.step(
        "本次 UAT 截图与录像证据完整",
        len(current_screenshots) >= 3 and current_video_ok,
        f"screenshots={len(current_screenshots)} "
        f"video_bytes={video_target.stat().st_size if video_target.exists() else 0}",
    )

    exit_code = run.finish()
    summary = json.loads((Path(run.dir) / "run.json").read_text(encoding="utf-8"))
    (Path(run.dir) / "run.log").write_text(
        "\n".join(
            [
                f"frontend: {FRONT}",
                f"backend: {API}",
                "browser: chromium headless=False",
                f"passed: {summary['passed']}",
                f"failed: {summary['failed']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
