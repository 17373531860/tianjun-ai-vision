# -*- coding: utf-8 -*-
"""可见浏览器 UAT：三个非主工位扩展窗完整显示逐步骤 OK/NG。

现场叙事：四工位多屏开启后，操作员只在主屏控制检测；工位 2～4 的 Electron
扩展窗只读展示各自画面和步骤统计。后端结算并清空 current_cycle_steps 后，
扩展窗必须继续在最右结果列显示该工位的权威 OK/NG，不能变成 ``--``，也不能
把重复步骤 A-B-A 的两行 A 串位；展示补偿不得发起任何检测启停写请求。

前置：backend 以 RUNTIME_MODE=test 启动，frontend 指向该 backend。
证据默认写 E:\\CodexTemp，包含 headed Chromium 视频、三张截图、run.json/run.log。
"""
from __future__ import annotations

import base64
import json
import os
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from playwright.sync_api import expect, sync_playwright

from _common import UatRun, filter_console_errors, launch_browser


BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:6174").rstrip("/")
API_URL = os.environ.get("E2E_API_URL", "http://127.0.0.1:8011").rstrip("/")
ARTIFACT_DIR = Path(
    os.environ.get(
        "UAT_ARTIFACT_DIR",
        r"E:\CodexTemp\evidence_20260911_extension_step_results",
    )
).resolve()
VIDEO_PATH = ARTIFACT_DIR / "extension-step-results.webm"
BLACK_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42Y"
    "AAAAASUVORK5CYII="
)

run = UatRun("extension_step_results", str(ARTIFACT_DIR))
session = requests.Session()
requested_channels: list[int] = []
write_requests: list[str] = []
duplicate_key_warnings: list[str] = []


def request_json(method: str, path: str, **kwargs) -> dict:
    response = session.request(
        method,
        f"{API_URL}{path}",
        timeout=kwargs.pop("timeout", 20),
        **kwargs,
    )
    response.raise_for_status()
    return response.json()


def channel_from_url(url: str) -> int | None:
    value = (parse_qs(urlparse(url).query).get("channel") or [None])[0]
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return None


def result_payload(channel: int | None) -> dict:
    target = channel in {1, 2, 3}
    return {
        "channel_id": channel,
        "is_running": True,
        "is_detecting": True,
        "source_type": "camera",
        "fps": 25,
        "latency": 8,
        "counters": {"总产量": 6, "合格总数": 4, "不良总数": 2},
        "project_config": {
            "project_id": 8000 + int(channel or 0),
            "project_name": f"UAT-扩展工位{int(channel or 0) + 1}",
            "logic_mode": "sequential",
            "pipeline_config": {
                "sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 1}]
            },
            "steps_config": [
                {"id": 1, "label": "step-a", "displayLabel": "步骤A", "enabled": True},
                {"id": 2, "label": "step-b", "displayLabel": "步骤B", "enabled": True},
            ],
        },
        # 真实结算后一拍：周期序列已清空，事件明确指出 B 漏做。
        "current_cycle_id": None,
        "current_cycle_uuid": None,
        "current_cycle_steps": [],
        "step_counts": {"step-a": 2} if target else {},
        "cycle_sum_step_durations": {"step-a": 0.8} if target else {},
        "step_inflight_durations": {},
        "backup_covered_labels": [],
        "detections": [],
        "recent_events": ([{
            "event_id": "2",
            "seq": 11,
            "timestamp": time.time(),
            "reason": "周期不完整，缺少: ['step-b']",
        }] if target else []),
    }


def write_run_log() -> None:
    failed = [step for step in run.steps if not step["ok"]]
    lines = [
        f"name={run.name}",
        f"frontend={BASE_URL}",
        f"backend={API_URL}",
        "browser=headless_false",
    ]
    for step in run.steps:
        lines.append(
            f"[{'PASS' if step['ok'] else 'FAIL'}] {step['idx']:02d} "
            f"{step['label']} {step['detail']}"
        )
    lines.append(f"failed:{len(failed)}")
    (ARTIFACT_DIR / "run.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


original_count: int | None = None
original_multi: dict | None = None
caught: Exception | None = None
browser = None
context = None
page = None
video = None

try:
    original_count = int(request_json("GET", "/api/v1/workstations/")["channel_count"])
    original_multi = request_json("GET", "/api/v1/workstations/multi-monitor")
    request_json("POST", "/api/v1/workstations/mode", json={"channel_count": 4})
    request_json(
        "PUT",
        "/api/v1/workstations/multi-monitor",
        json={
            "enabled": True,
            "readonly": True,
            "mapping": {
                str(channel): {
                    "display_id": f"uat-display-{channel}",
                    "bounds": {
                        "x": channel * 1280,
                        "y": 0,
                        "width": 1280,
                        "height": 720,
                    },
                }
                for channel in (1, 2, 3)
            },
        },
    )
    run.step(
        "T0 四工位多屏测试配置已隔离建立",
        request_json("GET", "/api/v1/workstations/")["channel_count"] == 4,
        "channel_count=4, target_channels=1,2,3",
    )

    with sync_playwright() as playwright:
        try:
            browser, context, page, console_errors = launch_browser(
                playwright,
                headless=False,
                slow_mo=80,
                viewport=(1366, 768),
                record_video_dir=str(ARTIFACT_DIR / "video-raw"),
                default_timeout_ms=15_000,
            )
            video = page.video

            def fake_results(route) -> None:
                channel = channel_from_url(route.request.url)
                if channel is not None:
                    requested_channels.append(channel)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(result_payload(channel), ensure_ascii=False),
                )

            page.route("**/api/v1/source/detection/results?**", fake_results)
            page.route("**/video_feed?**", lambda route: route.abort())
            page.route(
                "**/snapshot?**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="image/png",
                    body=BLACK_PIXEL_PNG,
                ),
            )
            page.on(
                "request",
                lambda request: write_requests.append(
                    f"{request.method} {request.url}"
                )
                if request.method not in {"GET", "HEAD", "OPTIONS"}
                and "/source/detection/" in request.url
                else None,
            )
            page.on(
                "console",
                lambda message: duplicate_key_warnings.append(message.text)
                if "Duplicate keys" in message.text
                else None,
            )

            cases = [
                (1, {"width": 1280, "height": 720}),
                (2, {"width": 1366, "height": 768}),
                (3, {"width": 1280, "height": 720}),
            ]
            for shot_index, (channel, viewport) in enumerate(cases, start=1):
                page.set_viewport_size(viewport)
                page.goto(
                    f"{BASE_URL}/#/monitor?channel={channel}&kiosk=1&readonly=0&multi_monitor=1",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                monitor = page.get_by_test_id("single-channel-monitor")
                expect(monitor).to_have_attribute("data-channel", str(channel))
                table_panel = page.get_by_test_id("single-channel-step-table")
                expect(table_panel).to_be_visible()
                expect(table_panel.locator("thead th").last).to_have_text("结果")
                result_cells = table_panel.locator("tbody tr td:last-child")
                expect(result_cells).to_have_count(3)
                expected = ["OK", "NG", "OK"]
                for index, text in enumerate(expected):
                    expect(result_cells.nth(index)).to_have_text(text)
                    expect(result_cells.nth(index)).to_be_visible()

                geometry = table_panel.locator("table").evaluate(
                    """table => {
                      const scroller = table.parentElement;
                      const sr = scroller.getBoundingClientRect();
                      return {
                        scrollLeft: scroller.scrollLeft,
                        viewportWidth: window.innerWidth,
                        scrollerLeft: sr.left,
                        scrollerRight: sr.right,
                        cells: [...table.querySelectorAll('tbody tr td:last-child')]
                          .map(cell => {
                            const r = cell.getBoundingClientRect();
                            return { left: r.left, right: r.right };
                          }),
                      };
                    }"""
                )
                in_first_view = geometry["scrollLeft"] == 0 and all(
                    cell["left"] >= geometry["scrollerLeft"] - 1
                    and cell["right"] <= geometry["scrollerRight"] + 1
                    and cell["right"] <= geometry["viewportWidth"] + 1
                    for cell in geometry["cells"]
                )
                run.step(
                    f"T{shot_index} 扩展工位 {channel + 1} 显示 OK/NG/OK",
                    [cell.inner_text() for cell in result_cells.all()] == expected,
                    f"viewport={viewport['width']}x{viewport['height']}",
                )
                run.step(
                    f"T{shot_index}a 工位 {channel + 1} 最右结果列完整处于首屏",
                    in_first_view,
                    json.dumps(geometry, ensure_ascii=False),
                )
                # page.goto 期间上一页的 150ms timer 可能恰好发出最后一个请求；
                # 新页面已完成工位锁定后再开始采样，避免把旧页在途请求算到新工位。
                request_start = len(requested_channels)
                page.wait_for_timeout(900)
                run.shot(page, f"0{shot_index}_station_{channel + 1}_ok_ng")
                polled = requested_channels[request_start:]
                run.step(
                    f"T{shot_index}b 工位 {channel + 1} 只轮询自身结果",
                    bool(polled) and set(polled) == {channel},
                    f"requests={polled}",
                )

            page.wait_for_timeout(1800)
            run.step(
                "T4 重复步骤 A-B-A 未产生 Vue 重复 key 警告",
                not duplicate_key_warnings,
                str(duplicate_key_warnings),
            )
            run.step(
                "T5 结果展示未发起检测启停写请求",
                not write_requests,
                str(write_requests),
            )
            real_console_errors = filter_console_errors(console_errors)
            run.step(
                "T6 可见浏览器无前端逻辑报错",
                not real_console_errors,
                str(real_console_errors),
            )
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            if video is not None:
                try:
                    video.save_as(str(VIDEO_PATH))
                except Exception as exc:  # noqa: BLE001
                    run.step("视频证据保存", False, repr(exc))
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
except Exception as exc:  # noqa: BLE001
    caught = exc
    traceback.print_exc()
    run.step("UAT 执行无未处理异常", False, f"{type(exc).__name__}: {exc}")
finally:
    cleanup_errors: list[str] = []
    if original_multi is not None:
        try:
            session.put(
                f"{API_URL}/api/v1/workstations/multi-monitor",
                json=original_multi,
                timeout=20,
            ).raise_for_status()
        except Exception as exc:  # noqa: BLE001
            cleanup_errors.append(f"restore multi-monitor: {exc}")
    if original_count is not None:
        try:
            session.post(
                f"{API_URL}/api/v1/workstations/mode",
                json={"channel_count": original_count},
                timeout=20,
            ).raise_for_status()
        except Exception as exc:  # noqa: BLE001
            cleanup_errors.append(f"restore channel_count: {exc}")
    run.step(
        "T7 finally 已还原工位数与多屏配置",
        not cleanup_errors,
        "; ".join(cleanup_errors) or "cleanup ok",
    )

fresh_screenshots = [
    path for path in ARTIFACT_DIR.glob("*.png") if path.stat().st_mtime >= run.t0 - 1
]
video_size = VIDEO_PATH.stat().st_size if VIDEO_PATH.exists() else 0
run.step(
    "T8 三件套证据齐全且视频有效",
    len(fresh_screenshots) >= 3 and video_size >= 100_000,
    f"screenshots={len(fresh_screenshots)} video_bytes={video_size}",
)
run.step("T9 UAT 总流程无异常", caught is None, repr(caught))

exit_code = run.finish()
write_run_log()
raise SystemExit(exit_code)
