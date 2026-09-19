# -*- coding: utf-8 -*-
"""可见浏览器 UAT：主屏放大副工位时，副屏 MJPEG 与推理保持连续。

现场叙事：多屏扩展开启后，副屏 Electron 窗口持续只读显示工位 2；操作员在
主屏总览反复放大工位 2 并返回。两个窗口必须同时看到同一摄像头的实时画面，
主屏的查看动作不得把副屏长连接踢掉，也不得降低采集/推理/显示帧率。

前置条件：
  - backend 以 ``RUNTIME_MODE=test`` 启动；
  - frontend 指向该 backend；
  - 使用隔离数据目录，工位 2 启动前不得已有真实视频源。

环境变量：
  - E2E_BASE_URL       前端地址，默认 http://127.0.0.1:6011
  - E2E_API_URL        后端根地址，默认 http://127.0.0.1:8011
  - UAT_ARTIFACT_DIR   证据目录；默认 tests/uat/evidence_20260911_...

证据：至少 5 张截图、两个 headed Chromium 窗口的 webm、metrics.json、
run.json 与 run.log。视频流和 snapshot 均走真实后端，不 route/mock/abort
``/video_feed``。finally 会停止 detection/synthetic 并还原多屏与工位数配置。
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, filter_console_errors


BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:6011").rstrip("/")
API_URL = os.environ.get("E2E_API_URL", "http://127.0.0.1:8011").rstrip("/")
ARTIFACT_DIR = Path(
    os.environ.get(
        "UAT_ARTIFACT_DIR",
        "tests/uat/evidence_20260911_multi_monitor_stream_isolation",
    )
).resolve()
VIDEO_DIR = ARTIFACT_DIR / "video"
CHANNEL = 1
SYNTHETIC_FPS = 30
ZOOM_ROUNDS = 4
MAX_ALLOWED_GAP_MS = 450.0
MIN_FPS_RATIO = 0.90

run = UatRun("multi_monitor_stream_isolation", str(ARTIFACT_DIR))
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()
request_events: dict[str, list[dict]] = {"main": [], "station": []}
response_events: dict[str, list[dict]] = {"main": [], "station": []}
failed_events: dict[str, list[dict]] = {"main": [], "station": []}
status_samples: dict[str, list[dict]] = {"baseline": [], "interaction": [], "post": []}
metrics: dict = {
    "frontend": BASE_URL,
    "backend": API_URL,
    "channel": CHANNEL,
    "synthetic_fps": SYNTHETIC_FPS,
    "zoom_rounds": ZOOM_ROUNDS,
    "thresholds": {
        "max_display_gap_ms": MAX_ALLOWED_GAP_MS,
        "minimum_during_to_baseline_fps_ratio": MIN_FPS_RATIO,
    },
}


def request_json(method: str, path: str, **kwargs) -> dict:
    response = session.request(method, f"{API_URL}{path}", timeout=kwargs.pop("timeout", 20), **kwargs)
    response.raise_for_status()
    return response.json()


def stream_identity(url: str) -> tuple[int | None, str | None]:
    query = parse_qs(urlparse(url).query)
    raw_channel = (query.get("channel") or [None])[0]
    viewer = (query.get("viewer") or [None])[0]
    try:
        channel = int(raw_channel) if raw_channel is not None else 0
    except (TypeError, ValueError):
        channel = None
    return channel, viewer


def observe_page(page, window_name: str) -> list[tuple[str, str]]:
    console_errors: list[tuple[str, str]] = []

    def on_request(request) -> None:
        if "/video_feed" not in request.url:
            return
        channel, viewer = stream_identity(request.url)
        request_events[window_name].append(
            {
                "wall_time": time.time(),
                "channel": channel,
                "viewer": viewer,
                "url": request.url,
            }
        )

    def on_response(response) -> None:
        if "/video_feed" not in response.url:
            return
        channel, viewer = stream_identity(response.url)
        response_events[window_name].append(
            {
                "wall_time": time.time(),
                "channel": channel,
                "viewer": viewer,
                "status": response.status,
                "url": response.url,
            }
        )

    def on_failed(request) -> None:
        if "/video_feed" not in request.url:
            return
        channel, viewer = stream_identity(request.url)
        failed_events[window_name].append(
            {
                "wall_time": time.time(),
                "channel": channel,
                "viewer": viewer,
                "failure": request.failure,
                "url": request.url,
            }
        )

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfailed", on_failed)
    page.on(
        "console",
        lambda message: console_errors.append(
            (message.text, (message.location or {}).get("url", ""))
        )
        if message.type == "error"
        else None,
    )
    return console_errors


FRAME_PROBE_SCRIPT = r"""
(() => {
  window.__uatVideoFrameTimes = [];
  const originalDrawImage = CanvasRenderingContext2D.prototype.drawImage;
  CanvasRenderingContext2D.prototype.drawImage = function(...args) {
    try {
      const videoHost = this.canvas?.closest?.('[data-testid="single-channel-video"]');
      const monitor = videoHost?.closest?.('[data-testid="single-channel-monitor"]');
      if (videoHost && monitor) {
        window.__uatVideoFrameTimes.push({
          t: performance.now(),
          channel: Number(monitor.dataset.channel),
        });
        if (window.__uatVideoFrameTimes.length > 20000) {
          window.__uatVideoFrameTimes.splice(0, 5000);
        }
      }
    } catch (_) {}
    return originalDrawImage.apply(this, args);
  };
})();
"""


def frame_times(page, channel: int = CHANNEL) -> list[float]:
    return page.evaluate(
        "channel => (window.__uatVideoFrameTimes || [])"
        ".filter(item => item.channel === channel).map(item => item.t)",
        channel,
    )


def perf_now(page) -> float:
    return float(page.evaluate("performance.now()"))


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def interval_stats(times: list[float], start_ms: float, end_ms: float) -> dict:
    points = sorted(t for t in times if start_ms <= t <= end_ms)
    intervals = [b - a for a, b in zip(points, points[1:])]
    edge_gaps: list[float] = []
    if points:
        edge_gaps = [points[0] - start_ms, end_ms - points[-1]]
    else:
        edge_gaps = [end_ms - start_ms]
    duration_s = max((end_ms - start_ms) / 1000.0, 0.001)
    return {
        "frames": len(points),
        "window_s": round(duration_s, 3),
        "window_fps": round(len(points) / duration_s, 3),
        "active_fps": round(
            (len(points) - 1) / ((points[-1] - points[0]) / 1000.0), 3
        )
        if len(points) >= 2 and points[-1] > points[0]
        else 0.0,
        "mean_interval_ms": round(statistics.mean(intervals), 3) if intervals else 0.0,
        "p95_interval_ms": round(percentile(intervals, 0.95), 3),
        "max_gap_ms": round(max(intervals + edge_gaps), 3),
    }


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_status(phase: str) -> dict:
    data = request_json("GET", f"/api/v1/source/status?channel={CHANNEL}")
    sample = {
        "wall_time": time.time(),
        "fps_actual": float(data.get("fps_actual") or 0),
        "fps_inference": float(data.get("fps_inference") or 0),
        "is_running": bool(data.get("is_running")),
        "is_detecting": bool(data.get("is_detecting")),
        "source_type": data.get("source_type"),
    }
    status_samples[phase].append(sample)
    return sample


def synthetic_sample() -> dict:
    return {
        "monotonic": time.monotonic(),
        **request_json("GET", f"/api/v1/test/synthetic/state?channel={CHANNEL}"),
    }


def sequence_fps(first: dict, last: dict) -> float:
    seconds = max(float(last["monotonic"]) - float(first["monotonic"]), 0.001)
    return (int(last.get("frame_seq") or 0) - int(first.get("frame_seq") or 0)) / seconds


def positive_median(samples: list[dict], key: str) -> float:
    values = [float(item.get(key) or 0) for item in samples if float(item.get(key) or 0) > 0]
    return statistics.median(values) if values else 0.0


def target_stream_requests(window_name: str, viewer: str) -> list[dict]:
    return [
        item
        for item in request_events[window_name]
        if item["channel"] == CHANNEL and item["viewer"] == viewer
    ]


def write_run_log(extra: str = "") -> None:
    failed = [step for step in run.steps if not step["ok"]]
    lines = [
        f"name={run.name}",
        f"frontend={BASE_URL}",
        f"backend={API_URL}",
        "browser=headless_false x2",
    ]
    for step in run.steps:
        lines.append(
            f"[{'PASS' if step['ok'] else 'FAIL'}] {step['idx']:02d} "
            f"{step['label']} {step['detail']}"
        )
    if extra:
        lines.append(extra)
    lines.append(f"failed:{len(failed)}")
    (ARTIFACT_DIR / "run.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


original_count: int | None = None
original_multi: dict | None = None
started_synthetic = False
caught: Exception | None = None
main_video_path = VIDEO_DIR / "main-window.webm"
station_video_path = VIDEO_DIR / "station-window.webm"

try:
    original_count = int(request_json("GET", "/api/v1/workstations/")["channel_count"])
    original_multi = request_json("GET", "/api/v1/workstations/multi-monitor")
    request_json("POST", "/api/v1/workstations/mode", json={"channel_count": 2})

    prior = request_json("GET", f"/api/v1/source/status?channel={CHANNEL}")
    isolated = not prior.get("is_running") and not prior.get("is_detecting")
    run.step(
        "T0 隔离后端未占用工位 2 的真实视频源",
        isolated,
        f"running={prior.get('is_running')} detecting={prior.get('is_detecting')}",
    )
    if not isolated:
        raise RuntimeError("工位 2 已在运行；请改用隔离数据目录启动 UAT 后端")

    multi_config = {
        "enabled": True,
        "readonly": True,
        "mapping": {
            "1": {
                "display_id": "uat-secondary",
                "bounds": {"x": 1280, "y": 0, "width": 1280, "height": 800},
            }
        },
    }
    request_json("PUT", "/api/v1/workstations/multi-monitor", json=multi_config)

    scenario = {
        "name": "multi-monitor-stream-isolation",
        "fps": SYNTHETIC_FPS,
        "timeline": [
            {
                "from": 0,
                "to": 100000,
                "detections": [
                    {
                        "label": "uat_part",
                        "confidence": 0.99,
                        "bbox": [0.30, 0.28, 0.24, 0.24],
                    }
                ],
            }
        ],
    }
    started = request_json(
        "POST",
        "/api/v1/test/synthetic/start",
        json={
            "scenario_json": scenario,
            "with_project": True,
            "channel": CHANNEL,
            "fps": SYNTHETIC_FPS,
        },
    )
    started_synthetic = True
    request_json(
        "POST",
        f"/api/v1/source/detection/start?channel={CHANNEL}",
        json={"conf": 0.25, "iou": 0.45},
        timeout=60,
    )
    run.step(
        "T1 工位 2 启动真实 synthetic 采集与推理线程",
        started.get("status") == "ok",
        str(started.get("debug")),
    )

    ready_deadline = time.time() + 15
    ready_status: dict = {}
    while time.time() < ready_deadline:
        ready_status = request_json("GET", f"/api/v1/source/status?channel={CHANNEL}")
        if (
            ready_status.get("is_running")
            and ready_status.get("is_detecting")
            and float(ready_status.get("fps_actual") or 0) > 0
            and float(ready_status.get("fps_inference") or 0) > 0
        ):
            break
        time.sleep(0.25)
    run.step(
        "T2 synthetic 采集与推理 FPS 已稳定",
        bool(
            ready_status.get("is_running")
            and ready_status.get("is_detecting")
            and float(ready_status.get("fps_actual") or 0) > 0
            and float(ready_status.get("fps_inference") or 0) > 0
        ),
        f"capture={ready_status.get('fps_actual')} inference={ready_status.get('fps_inference')}",
    )

    stream_config = request_json("GET", "/api/v1/source/stream/config")
    target_stream_fps = float(stream_config.get("target_stream_fps") or SYNTHETIC_FPS)
    metrics["target_stream_fps"] = target_stream_fps

    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-size=1280,800",
    ]

    with sync_playwright() as playwright:
        main_browser = None
        station_browser = None
        main_context = None
        station_context = None
        main_page = None
        station_page = None
        main_video = None
        station_video = None
        try:
            # 两个独立 headed Chromium 进程模拟 Electron 主窗口与扩展工位窗口。
            main_browser = playwright.chromium.launch(
                headless=False,
                slow_mo=40,
                args=browser_args + ["--window-position=0,0"],
            )
            station_browser = playwright.chromium.launch(
                headless=False,
                slow_mo=40,
                args=browser_args + ["--window-position=96,64"],
            )
            context_options = {
                "viewport": {"width": 1280, "height": 800},
                "record_video_size": {"width": 1280, "height": 800},
                "ignore_https_errors": True,
            }
            main_context = main_browser.new_context(
                **context_options,
                record_video_dir=str(VIDEO_DIR / "main-raw"),
            )
            station_context = station_browser.new_context(
                **context_options,
                record_video_dir=str(VIDEO_DIR / "station-raw"),
            )
            main_context.add_init_script(FRAME_PROBE_SCRIPT)
            station_context.add_init_script(FRAME_PROBE_SCRIPT)
            main_page = main_context.new_page()
            station_page = station_context.new_page()
            main_page.set_default_timeout(20_000)
            station_page.set_default_timeout(20_000)
            main_console = observe_page(main_page, "main")
            station_console = observe_page(station_page, "station")
            main_video = main_page.video
            station_video = station_page.video

            main_page.goto(f"{BASE_URL}/#/monitor", wait_until="domcontentloaded", timeout=30_000)
            main_page.get_by_test_id("channel-zoom-1").wait_for(state="visible", timeout=15_000)
            run.shot(main_page, "01_main_overview")

            station_page.goto(
                f"{BASE_URL}/#/monitor?channel={CHANNEL}&kiosk=1&readonly=1&multi_monitor=1",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            station_monitor = station_page.get_by_test_id("single-channel-monitor")
            station_monitor.wait_for(state="visible", timeout=15_000)
            station_page.wait_for_function(
                "channel => (window.__uatVideoFrameTimes || [])"
                ".filter(item => item.channel === channel).length >= 20",
                arg=CHANNEL,
                timeout=20_000,
            )
            run.shot(station_page, "02_station_baseline")
            run.step(
                "T3 副屏真实 MJPEG 已持续解码到视频 canvas",
                len(frame_times(station_page)) >= 20,
                f"frames={len(frame_times(station_page))}",
            )

            station_canvas = station_page.get_by_test_id("single-channel-video").locator("canvas").first
            baseline_hash_start = sha256(station_canvas.screenshot(timeout=8_000))
            baseline_seq_start = synthetic_sample()
            baseline_start_ms = perf_now(station_page)
            for _ in range(4):
                station_page.wait_for_timeout(900)
                source_status("baseline")
            baseline_end_ms = perf_now(station_page)
            baseline_seq_end = synthetic_sample()
            baseline_hash_end = sha256(station_canvas.screenshot(timeout=8_000))

            run.step(
                "T4 副屏 synthetic 画面确实在变化（非静态占位图）",
                baseline_hash_start != baseline_hash_end,
                f"start={baseline_hash_start[:12]} end={baseline_hash_end[:12]}",
            )

            interaction_seq_start = synthetic_sample()
            interaction_start_ms = perf_now(station_page)
            interaction_wall_start = time.time()
            main_zoom_windows: list[dict] = []
            for round_index in range(ZOOM_ROUNDS):
                prior_main_frames = len(frame_times(main_page))
                main_page.get_by_test_id("channel-zoom-1").click()
                main_monitor = main_page.get_by_test_id("single-channel-monitor")
                main_monitor.wait_for(state="visible", timeout=10_000)
                main_page.wait_for_function(
                    "([channel, before]) => (window.__uatVideoFrameTimes || [])"
                    ".filter(item => item.channel === channel).length >= before + 8",
                    arg=[CHANNEL, prior_main_frames],
                    timeout=15_000,
                )

                segment_start = perf_now(main_page)
                main_page.wait_for_timeout(1200)
                segment_end = perf_now(main_page)
                main_zoom_windows.append(
                    interval_stats(frame_times(main_page), segment_start, segment_end)
                )
                source_status("interaction")

                if round_index == 0:
                    run.shot(main_page, "03_main_zoom_channel_1")
                    run.shot(station_page, "04_station_while_main_zoomed")
                    stop_button = main_page.get_by_test_id("single-channel-stop")
                    run.step(
                        "T5 主屏放大态保留目标工位检测控制入口",
                        stop_button.is_visible() and not stop_button.is_disabled(),
                        "工位 2 正在检测，停止按钮可操作但 UAT 不触发它",
                    )

                main_page.get_by_test_id("single-channel-back").click()
                main_monitor.wait_for(state="detached", timeout=10_000)
                main_page.get_by_test_id("channel-zoom-1").wait_for(state="visible", timeout=10_000)
                main_page.wait_for_timeout(450)

            interaction_end_ms = perf_now(station_page)
            interaction_seq_end = synthetic_sample()
            run.shot(main_page, "05_main_return_overview")

            post_start_ms = perf_now(station_page)
            station_page.wait_for_timeout(2200)
            source_status("post")
            post_end_ms = perf_now(station_page)
            post_hash = sha256(station_canvas.screenshot(timeout=8_000))
            run.shot(station_page, "06_station_after_all_transitions")

            station_times = frame_times(station_page)
            baseline_display = interval_stats(station_times, baseline_start_ms, baseline_end_ms)
            interaction_display = interval_stats(
                station_times, interaction_start_ms, interaction_end_ms
            )
            post_display = interval_stats(station_times, post_start_ms, post_end_ms)
            display_ratio = (
                interaction_display["window_fps"] / baseline_display["window_fps"]
                if baseline_display["window_fps"] > 0
                else 0.0
            )
            capture_baseline_fps = sequence_fps(baseline_seq_start, baseline_seq_end)
            capture_interaction_fps = sequence_fps(interaction_seq_start, interaction_seq_end)
            capture_ratio = (
                capture_interaction_fps / capture_baseline_fps if capture_baseline_fps > 0 else 0.0
            )
            baseline_inference = positive_median(status_samples["baseline"], "fps_inference")
            interaction_inference = positive_median(status_samples["interaction"], "fps_inference")
            inference_ratio = (
                interaction_inference / baseline_inference if baseline_inference > 0 else 0.0
            )

            health_after = request_json("GET", f"/api/v1/source/health?channel={CHANNEL}")
            station_requests = target_stream_requests("station", "station")
            main_requests = target_stream_requests("main", "main")
            station_responses = [
                item
                for item in response_events["station"]
                if item["channel"] == CHANNEL and item["viewer"] == "station"
            ]
            main_responses = [
                item
                for item in response_events["main"]
                if item["channel"] == CHANNEL and item["viewer"] == "main"
            ]
            station_failures = [
                item
                for item in failed_events["station"]
                if item["channel"] == CHANNEL and item["viewer"] == "station"
            ]
            station_requests_during_interaction = [
                item for item in station_requests if item["wall_time"] >= interaction_wall_start
            ]
            station_failures_during_interaction = [
                item for item in station_failures if item["wall_time"] >= interaction_wall_start
            ]

            run.step(
                "T6 主屏/副屏使用独立 viewer 槽且都收到 HTTP 200",
                len(station_responses) >= 1
                and all(item["status"] == 200 for item in station_responses)
                and len(main_responses) == ZOOM_ROUNDS
                and all(item["status"] == 200 for item in main_responses),
                f"station={[(x['viewer'], x['status']) for x in station_responses]} "
                f"main={[(x['viewer'], x['status']) for x in main_responses]}",
            )
            run.step(
                "T7 主屏反复放大未触发副屏 MJPEG 重连",
                not station_requests_during_interaction
                and not station_failures_during_interaction,
                f"startup_requests={len(station_requests)} "
                f"during_requests={len(station_requests_during_interaction)} "
                f"during_failures={station_failures_during_interaction}",
            )
            run.step(
                "T8 每次主屏放大只建立一个 main 流，返回后不残留重连",
                len(main_requests) == ZOOM_ROUNDS,
                f"requests={len(main_requests)} expected={ZOOM_ROUNDS}",
            )
            run.step(
                "T9 副屏交互期间显示帧率不下降",
                display_ratio >= MIN_FPS_RATIO,
                f"baseline={baseline_display['window_fps']:.2f} "
                f"during={interaction_display['window_fps']:.2f} ratio={display_ratio:.3f}",
            )
            run.step(
                "T10 副屏无 0.5 秒级画面断流",
                interaction_display["max_gap_ms"] < MAX_ALLOWED_GAP_MS,
                f"max_gap={interaction_display['max_gap_ms']:.1f}ms "
                f"p95={interaction_display['p95_interval_ms']:.1f}ms",
            )
            run.step(
                "T11 主屏每轮放大均保持实时帧节奏",
                all(
                    item["frames"] >= 8 and item["max_gap_ms"] < MAX_ALLOWED_GAP_MS
                    for item in main_zoom_windows
                ),
                json.dumps(main_zoom_windows, ensure_ascii=False),
            )
            run.step(
                "T12 采集 FPS 未因第二个观看窗口下降",
                capture_ratio >= MIN_FPS_RATIO,
                f"baseline={capture_baseline_fps:.2f} during={capture_interaction_fps:.2f} "
                f"ratio={capture_ratio:.3f}",
            )
            run.step(
                "T13 推理 FPS 未因主屏放大/返回下降",
                inference_ratio >= MIN_FPS_RATIO,
                f"baseline={baseline_inference:.2f} during={interaction_inference:.2f} "
                f"ratio={inference_ratio:.3f}",
            )
            threads = health_after.get("threads") or {}
            inference_health = threads.get("inference") or {}
            capture_health = threads.get("capture") or {}
            detection_health = health_after.get("detection") or {}
            run.step(
                "T14 查看动作后采集/推理线程仍健康且检测未停止",
                bool(
                    inference_health.get("alive")
                    and inference_health.get("healthy")
                    and capture_health.get("alive")
                    and capture_health.get("healthy")
                    and detection_health.get("is_detecting")
                ),
                json.dumps(health_after, ensure_ascii=False),
            )
            run.step(
                "T15 副屏在全部主屏切换后仍继续出新帧",
                post_hash != baseline_hash_end and post_display["frames"] >= 10,
                f"post_frames={post_display['frames']} hash={post_hash[:12]}",
            )

            real_console_errors = {
                "main": filter_console_errors(main_console),
                "station": filter_console_errors(station_console),
            }
            run.step(
                "T16 两个窗口无前端逻辑报错",
                not real_console_errors["main"] and not real_console_errors["station"],
                json.dumps(real_console_errors, ensure_ascii=False),
            )

            metrics.update(
                {
                    "requests": request_events,
                    "responses": response_events,
                    "request_failures_before_window_close": failed_events,
                    "request_counts": {
                        "station_channel_1": len(station_requests),
                        "station_during_main_interaction": len(
                            station_requests_during_interaction
                        ),
                        "main_channel_1": len(main_requests),
                    },
                    "display": {
                        "station_baseline": baseline_display,
                        "station_during_main_transitions": interaction_display,
                        "station_post": post_display,
                        "during_to_baseline_fps_ratio": round(display_ratio, 4),
                        "main_zoom_rounds": main_zoom_windows,
                    },
                    "backend": {
                        "status_samples": status_samples,
                        "synthetic_baseline_start": baseline_seq_start,
                        "synthetic_baseline_end": baseline_seq_end,
                        "synthetic_interaction_start": interaction_seq_start,
                        "synthetic_interaction_end": interaction_seq_end,
                        "capture_baseline_fps": round(capture_baseline_fps, 3),
                        "capture_interaction_fps": round(capture_interaction_fps, 3),
                        "capture_fps_ratio": round(capture_ratio, 4),
                        "inference_baseline_median_fps": baseline_inference,
                        "inference_interaction_median_fps": interaction_inference,
                        "inference_fps_ratio": round(inference_ratio, 4),
                        "health_after": health_after,
                    },
                    "frame_hashes": {
                        "baseline_start": baseline_hash_start,
                        "baseline_end": baseline_hash_end,
                        "post": post_hash,
                    },
                    "console_errors": real_console_errors,
                }
            )
        finally:
            # 先关 page 让 Playwright 封口 webm，再保存为稳定文件名。
            for page in (main_page, station_page):
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
            if main_video is not None:
                try:
                    main_video.save_as(str(main_video_path))
                except Exception as exc:  # noqa: BLE001
                    run.step("主屏视频证据保存", False, repr(exc))
            if station_video is not None:
                try:
                    station_video.save_as(str(station_video_path))
                except Exception as exc:  # noqa: BLE001
                    run.step("副屏视频证据保存", False, repr(exc))
            for context in (main_context, station_context):
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
            for browser in (main_browser, station_browser):
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass

    def fresh_size(path: Path) -> int:
        if not path.exists() or path.stat().st_mtime < run.t0 - 1:
            return 0
        return path.stat().st_size

    video_sizes = {
        "main-window.webm": fresh_size(main_video_path),
        "station-window.webm": fresh_size(station_video_path),
    }
    metrics["video_sizes_bytes"] = video_sizes
    run.step(
        "T17 两个 headed Chromium 窗口均留下有效视频证据",
        all(size >= 100_000 for size in video_sizes.values()),
        str(video_sizes),
    )
except Exception as exc:  # noqa: BLE001
    caught = exc
    traceback.print_exc()
    run.step("UAT 执行无未处理异常", False, f"{type(exc).__name__}: {exc}")
finally:
    cleanup_errors: list[str] = []
    if started_synthetic:
        try:
            session.post(
                f"{API_URL}/api/v1/source/detection/stop?channel={CHANNEL}", timeout=20
            ).raise_for_status()
        except Exception as exc:  # noqa: BLE001
            cleanup_errors.append(f"stop detection: {exc}")
        try:
            session.post(
                f"{API_URL}/api/v1/test/synthetic/stop?channel={CHANNEL}", timeout=20
            ).raise_for_status()
        except Exception as exc:  # noqa: BLE001
            cleanup_errors.append(f"stop synthetic: {exc}")
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
        "T18 finally 已停止 synthetic/检测并还原多屏状态",
        not cleanup_errors,
        "; ".join(cleanup_errors) or "cleanup ok",
    )

metrics["caught_exception"] = repr(caught) if caught is not None else None
(ARTIFACT_DIR / "metrics.json").write_text(
    json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
)
fresh_screenshots = [
    path
    for path in ARTIFACT_DIR.glob("*.png")
    if path.stat().st_mtime >= run.t0 - 1
]
run.step(
    "T19 证据齐全（>=3 截图、2 视频、metrics.json）",
    len(fresh_screenshots) >= 3
    and main_video_path.exists()
    and main_video_path.stat().st_mtime >= run.t0 - 1
    and station_video_path.exists()
    and station_video_path.stat().st_mtime >= run.t0 - 1
    and (ARTIFACT_DIR / "metrics.json").exists(),
    f"fresh_screenshots={len(fresh_screenshots)}",
)

metrics["steps"] = run.steps
(ARTIFACT_DIR / "metrics.json").write_text(
    json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
)

exit_code = run.finish()
write_run_log()
raise SystemExit(exit_code)
