"""Visible UAT for USB manual exposure across Monitor pause/resume.

Prerequisites:
  * real backend on 8001, frontend on 6001
  * TSTC/Jicai USB camera configured as channel 0
  * workstation_config keeps auto_exposure=false

This is intentionally a standalone script, not a pytest test.  It creates a
temporary project only to enable Monitor controls and restores the previously
active project before deleting the temporary one.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright


def _wait_status(api: str, predicate, timeout: float = 75.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        response = requests.get(f"{api}/api/v1/source/status?channel=0", timeout=5)
        response.raise_for_status()
        last = response.json()
        if predicate(last):
            return last
        time.sleep(0.5)
    raise AssertionError(f"source status timeout, last={last}")


def _active_project(api: str) -> dict | None:
    response = requests.get(f"{api}/api/v1/projects/active/current", timeout=10)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    return data if data and data.get("id") else None


def _create_uat_project(api: str) -> dict:
    payload = {
        "name": f"__uat_usb_ae_{uuid.uuid4().hex[:8]}",
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {},
        "steps_config": [
            {"id": 1, "label": "usb_ae", "name": "USB曝光验收", "enabled": True},
        ],
        "events_config": [],
        "counters_config": [],
        "alarm_config": {},
        "detection_config": {},
        "data_config": {},
        "default_model_id": None,
        "model_format": "pytorch_fp32",
    }
    response = requests.post(f"{api}/api/v1/projects", json=payload, timeout=15)
    response.raise_for_status()
    project = response.json()
    activate = requests.post(
        f"{api}/api/v1/projects/{project['id']}/activate", timeout=20,
    )
    activate.raise_for_status()
    return project


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", default="http://localhost:6001")
    parser.add_argument("--api", default="http://localhost:8001")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()

    artifacts = args.artifacts.resolve()
    shots = artifacts / "shots"
    videos = artifacts / "videos"
    shots.mkdir(parents=True, exist_ok=True)
    videos.mkdir(parents=True, exist_ok=True)
    run_log = artifacts / "run.log"
    log_lines: list[str] = []
    failures: list[str] = []
    previous_project = None
    uat_project = None

    def log(message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        log_lines.append(line)
        print(line, flush=True)

    try:
        previous_project = _active_project(args.api)
        uat_project = _create_uat_project(args.api)
        log(f"temporary project={uat_project['id']} previous={previous_project and previous_project['id']}")

        current = _wait_status(
            args.api,
            lambda s: s.get("source_type") == "camera" and s.get("is_running"),
            timeout=20,
        )
        log(f"precondition status={json.dumps(current, ensure_ascii=False)}")

        requests_seen: list[dict] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="msedge", headless=False, slow_mo=120,
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(videos),
                record_video_size={"width": 1440, "height": 900},
            )
            page = context.new_page()

            def record_request(request) -> None:
                if any(path in request.url for path in (
                    "/source/camera/start",
                    "/source/detection/pause",
                    "/source/detection/resume",
                    "/source/detection/standby",
                    "/source/detection/resume-inference",
                )):
                    entry = {"method": request.method, "url": request.url}
                    if request.post_data:
                        try:
                            entry["body"] = request.post_data_json
                        except Exception:
                            entry["body"] = request.post_data
                    requests_seen.append(entry)
                    log(f"browser request={json.dumps(entry, ensure_ascii=False)}")

            page.on("request", record_request)
            page.add_init_script(
                """
                localStorage.setItem('source_config', JSON.stringify({
                  sourceType: 'camera',
                  cameraSettings: {
                    deviceIndex: 1,
                    resolution: '1280x720',
                    fps: 60,
                    autoExposure: false,
                    exposureValue: -5
                  }
                }));
                """
            )

            page.goto(
                f"{args.frontend}/#/monitor",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            running = _wait_status(
                args.api,
                lambda s: s.get("source_type") == "camera" and s.get("is_running"),
            )
            page.screenshot(path=str(shots / "01_initial_camera.png"), full_page=True)
            log(f"initial Monitor status={json.dumps(running, ensure_ascii=False)}")

            stop_button = page.locator(
                "button.bg-red-500, button.bg-red-600"
            ).filter(has_text="停止").first
            stop_button.wait_for(state="visible", timeout=20000)
            with page.expect_response(
                lambda response: "/api/v1/source/detection/pause" in response.url,
                timeout=30000,
            ) as paused:
                stop_button.click()
            assert paused.value.ok, f"pause failed: {paused.value.status}"
            paused_status = _wait_status(args.api, lambda s: not s.get("is_running"), timeout=20)
            page.screenshot(path=str(shots / "02_monitor_stopped.png"), full_page=True)
            log(f"Monitor stop status={json.dumps(paused_status, ensure_ascii=False)}")

            start_button = page.locator(
                "button.bg-emerald-500, button.bg-emerald-600"
            ).filter(has_text="开始").first
            start_button.wait_for(state="visible", timeout=20000)
            resume_started = time.monotonic()
            with page.expect_response(
                lambda response: "/api/v1/source/detection/resume" in response.url,
                timeout=75000,
            ) as resumed:
                start_button.click()
            resume_elapsed = time.monotonic() - resume_started
            assert resumed.value.ok, f"resume failed: {resumed.value.status}"
            log(f"Monitor resume API elapsed={resume_elapsed:.3f}s")
            assert resume_elapsed <= 8.0, (
                f"MSMF resume exceeded 8s: {resume_elapsed:.3f}s"
            )
            resumed_status = _wait_status(
                args.api,
                lambda s: s.get("is_running") and s.get("fps_actual", 0) >= 20,
                timeout=30,
            )
            fps_samples = []
            for _ in range(8):
                sample = requests.get(
                    f"{args.api}/api/v1/source/status?channel=0", timeout=5,
                ).json()
                fps_samples.append(sample.get("fps_actual", 0))
                time.sleep(0.5)
            fps_median = statistics.median(fps_samples)
            assert fps_median >= 20 and min(fps_samples[-3:]) >= 20, (
                f"FPS stayed low after resume: {fps_samples}"
            )
            page.screenshot(path=str(shots / "03_monitor_resumed.png"), full_page=True)
            log(f"Monitor resume status={json.dumps(resumed_status, ensure_ascii=False)}")
            log(f"post-resume FPS samples={fps_samples} median={fps_median}")

            assert any("/source/detection/pause" in item["url"] for item in requests_seen)
            assert any("/source/detection/resume" in item["url"] for item in requests_seen)
            assert not any("/source/detection/standby" in item["url"] for item in requests_seen)
            context.close()
            browser.close()

        video_files = list(videos.glob("*.webm"))
        assert video_files, "Playwright did not produce a UAT video"
        assert max(path.stat().st_size for path in video_files) > 100_000
        assert len(list(shots.glob("*.png"))) >= 3
        log(f"video={video_files[0]} size={video_files[0].stat().st_size}")
    except Exception as exc:
        failures.append(repr(exc))
        log(f"FAILED: {exc!r}")
    finally:
        if previous_project:
            try:
                requests.post(
                    f"{args.api}/api/v1/projects/{previous_project['id']}/activate",
                    timeout=20,
                ).raise_for_status()
                log(f"restored project={previous_project['id']}")
            except Exception as exc:
                failures.append(f"restore project: {exc!r}")
        if uat_project:
            try:
                requests.delete(
                    f"{args.api}/api/v1/projects/{uat_project['id']}", timeout=20,
                ).raise_for_status()
                log(f"deleted temporary project={uat_project['id']}")
            except Exception as exc:
                failures.append(f"delete project: {exc!r}")
        log(f"failed: {len(failures)}")
        if failures:
            log("failure details=" + json.dumps(failures, ensure_ascii=False))
        run_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
