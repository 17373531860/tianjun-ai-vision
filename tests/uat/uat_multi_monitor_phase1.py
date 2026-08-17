"""多屏工位显示一期可见浏览器 UAT。

前置：用隔离数据目录启动 backend 和 frontend，并通过环境变量传入地址。
产物：overview.png、single-channel.png、kiosk-readonly.png、settings.png、settings-disabled.png、disabled-legacy-selection.png、video/*.webm、run.log。
本脚本只改 workstation_config.json 的 channel_count 与 multi_monitor 段，finally 恢复原值。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:6008")
API_URL = os.environ.get("E2E_API_URL", "http://127.0.0.1:8008")
ARTIFACT_DIR = Path(os.environ.get(
    "UAT_ARTIFACT_DIR",
    "tests/uat/artifacts/multi-monitor-phase1",
)).resolve()


def request_json(method: str, path: str, payload: dict | None = None) -> dict:
    response = requests.request(method, f"{API_URL}{path}", json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def channel_from_url(url: str) -> int | None:
    values = parse_qs(urlparse(url).query).get("channel")
    return int(values[0]) if values else None


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    video_dir = ARTIFACT_DIR / "video"
    video_dir.mkdir(exist_ok=True)
    original_count = int(request_json("GET", "/api/v1/workstations/")["channel_count"])
    original_multi = request_json("GET", "/api/v1/workstations/multi-monitor")
    observed = {"snapshot": [], "mjpeg": [], "results": []}
    run_lines = [
        f"started_at={datetime.now().isoformat(timespec='seconds')}",
        f"frontend={BASE_URL}",
        f"backend={API_URL}",
        "browser=headless_false",
    ]

    try:
        request_json("POST", "/api/v1/workstations/mode", {"channel_count": 4})
        request_json("PUT", "/api/v1/workstations/multi-monitor", {
            "enabled": True,
            "readonly": True,
            "mapping": {
                "1": {"display_id": "fake-secondary", "bounds": {"x": 1600, "y": 0, "width": 1280, "height": 720}},
            },
        })

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(
                viewport={"width": 1600, "height": 900},
                record_video_dir=str(video_dir),
                record_video_size={"width": 1600, "height": 900},
            )
            context.add_init_script("""
                Object.defineProperty(window, 'electronAPI', {
                  configurable: true,
                  value: {
                    isElectron: true,
                    getLicenseStatus: async () => ({ valid: true }),
                    getDisplays: async () => ({ ok: true, displays: [
                      { id: 'fake-primary', label: '假主屏', bounds: {x: 0, y: 0, width: 1600, height: 900}, workArea: {x: 0, y: 0, width: 1600, height: 860}, isPrimary: true, isMainWindowDisplay: true },
                      { id: 'fake-secondary', label: '假副屏', bounds: {x: 1600, y: 0, width: 1280, height: 720}, workArea: {x: 1600, y: 0, width: 1280, height: 680}, isPrimary: false },
                    ]}),
                    applyMultiMonitor: async (payload) => {
                      const cloned = structuredClone(payload);
                      window.__multiMonitorApplied = cloned;
                      return { ok: true, enabled: cloned.enabled, windows: cloned.enabled ? [1] : [], warnings: [] };
                    },
                  },
                });
            """)

            page = context.new_page()
            main_video = page.video

            def fake_results(route) -> None:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "is_running": False,
                        "is_detecting": False,
                        "source_type": "video",
                        "fps": 18,
                        "latency": 12,
                        "counters": {"总产量": 5, "合格总数": 4, "不良总数": 1},
                        "project_config": {
                            "project_name": "多屏UAT",
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
                    }, ensure_ascii=False),
                )

            def observe_request(request) -> None:
                if "/snapshot" in request.url:
                    observed["snapshot"].append(channel_from_url(request.url))
                elif "/video_feed" in request.url:
                    observed["mjpeg"].append(channel_from_url(request.url))
                elif "/source/detection/results" in request.url:
                    observed["results"].append(channel_from_url(request.url))

            page.on("request", observe_request)
            context.route("**/api/v1/source/detection/results?**", fake_results)
            page.route("**/video_feed?**", lambda route: route.abort())
            page.route(
                "**/snapshot?**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="image/png",
                    path=str(Path("frontend/public/app-icon.png").resolve()),
                ),
            )

            page.goto(f"{BASE_URL}/#/settings", wait_until="domcontentloaded", timeout=20_000)
            card = page.get_by_test_id("multi-monitor-card")
            card.wait_for(state="visible", timeout=10_000)
            card.scroll_into_view_if_needed()
            page.get_by_test_id("multi-monitor-display-0").click()
            main_option = page.get_by_role("option", name=re.compile("主窗口.*不可绑定"))
            assert main_option.get_attribute("aria-disabled") == "true"
            page.screenshot(path=str(ARTIFACT_DIR / "settings.png"), full_page=True)
            page.keyboard.press("Escape")

            page.goto(f"{BASE_URL}/#/monitor", wait_until="domcontentloaded", timeout=20_000)
            page.get_by_test_id("channel-card-0").wait_for(state="visible", timeout=10_000)
            assert page.get_by_test_id("multi-monitor-emergency-exit").count() == 0
            page.wait_for_timeout(1_200)
            page.screenshot(path=str(ARTIFACT_DIR / "overview.png"), full_page=True)
            overview_mjpeg = list(observed["mjpeg"])
            overview_snapshots = list(observed["snapshot"])

            page.get_by_test_id("channel-card-0").click()
            single = page.get_by_test_id("single-channel-monitor")
            single.wait_for(state="visible", timeout=10_000)
            assert page.get_by_test_id("multi-monitor-emergency-exit").count() == 0
            page.wait_for_timeout(800)
            page.screenshot(path=str(ARTIFACT_DIR / "single-channel.png"), full_page=True)
            assert single.get_attribute("data-channel") == "0"
            single_video_box = page.get_by_test_id("single-channel-video").bounding_box()
            single_sop = page.get_by_test_id("sop-step-panel")
            single_sop_box = single_sop.bounding_box()
            assert single_video_box is not None and single_sop_box is not None
            assert single_sop_box["y"] >= single_video_box["y"] + single_video_box["height"]
            assert "步骤A" in single_sop.inner_text() and "步骤B" in single_sop.inner_text()
            assert page.get_by_test_id("single-channel-step-table").is_visible()
            main_gauge = page.get_by_test_id("single-channel-yield-gauge")
            assert main_gauge.is_visible() and main_gauge.get_attribute("data-rate") == "80.0"
            assert main_gauge.locator("canvas").count() == 1
            main_defect = page.get_by_test_id("single-channel-defect-chart")
            assert main_defect.is_visible()
            assert main_defect.get_attribute("data-good") == "4"
            assert main_defect.get_attribute("data-bad") == "1"
            assert main_defect.locator("canvas").count() == 1
            main_summary_widths = [page.get_by_test_id(test_id).bounding_box()["width"] for test_id in (
                "single-channel-defect-card",
                "single-channel-yield-card",
                "single-channel-ng-top3",
            )]
            assert max(main_summary_widths) - min(main_summary_widths) <= 2
            page.get_by_test_id("single-channel-back").click()
            page.get_by_test_id("channel-card-0").wait_for(state="visible", timeout=5_000)

            kiosk = context.new_page()
            kiosk_video = kiosk.video
            kiosk.on("request", observe_request)
            kiosk.route("**/video_feed?**", lambda route: route.abort())
            kiosk.goto(
                f"{BASE_URL}/#/monitor?channel=1&kiosk=1&readonly=1&multi_monitor=1",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            kiosk_monitor = kiosk.get_by_test_id("single-channel-monitor")
            kiosk_monitor.wait_for(state="visible", timeout=10_000)
            kiosk.wait_for_timeout(800)
            kiosk.screenshot(path=str(ARTIFACT_DIR / "kiosk-readonly.png"), full_page=True)
            assert kiosk_monitor.get_attribute("data-channel") == "1"
            assert kiosk_monitor.get_attribute("data-readonly") == "true"
            kiosk_controls = kiosk.get_by_test_id("single-channel-controls")
            assert kiosk_controls.count() == 1
            assert kiosk_controls.get_attribute("data-readonly") == "true"
            for action in ("start", "stop", "standby", "reset"):
                assert kiosk.get_by_test_id(f"single-channel-{action}").is_disabled()
            kiosk_gauge = kiosk.get_by_test_id("single-channel-yield-gauge")
            assert kiosk_gauge.is_visible() and kiosk_gauge.get_attribute("data-rate") == "80.0"
            assert kiosk_gauge.locator("canvas").count() == 1
            kiosk_defect = kiosk.get_by_test_id("single-channel-defect-chart")
            assert kiosk_defect.is_visible()
            assert kiosk_defect.get_attribute("data-good") == "4"
            assert kiosk_defect.get_attribute("data-bad") == "1"
            assert kiosk_defect.locator("canvas").count() == 1
            assert kiosk.get_by_test_id("multi-monitor-emergency-exit").count() == 0
            assert kiosk.locator("button[title='导航菜单']").count() == 0
            kiosk_video_box = kiosk.get_by_test_id("single-channel-video").bounding_box()
            kiosk_sop_box = kiosk.get_by_test_id("sop-step-panel").bounding_box()
            assert kiosk_video_box is not None and kiosk_sop_box is not None
            assert kiosk_sop_box["y"] >= kiosk_video_box["y"] + kiosk_video_box["height"]

            # 设置页是关闭多屏的唯一入口：真实点击开关并保存，验证落盘与 Electron 清窗 payload。
            page.goto(f"{BASE_URL}/#/settings", wait_until="domcontentloaded", timeout=20_000)
            settings_card = page.get_by_test_id("multi-monitor-card")
            settings_card.wait_for(state="visible", timeout=10_000)
            settings_card.scroll_into_view_if_needed()
            page.get_by_test_id("multi-monitor-enabled-switch").click()
            page.get_by_test_id("multi-monitor-save").click()
            page.wait_for_function(
                "() => window.__multiMonitorApplied?.enabled === false",
                timeout=10_000,
            )
            persisted_after_exit = request_json("GET", "/api/v1/workstations/multi-monitor")
            assert persisted_after_exit["enabled"] is False
            assert persisted_after_exit["readonly"] is True
            assert persisted_after_exit["mapping"] == {
                "1": {
                    "display_id": "fake-secondary",
                    "bounds": {"x": 1600, "y": 0, "width": 1280, "height": 720},
                }
            }
            assert page.evaluate("window.__multiMonitorApplied") == persisted_after_exit
            page.screenshot(path=str(ARTIFACT_DIR / "settings-disabled.png"), full_page=True)

            # 四工位关闭多屏后恢复原软件交互：整卡只切换选中工位，不进入单工位放大态。
            page.goto(f"{BASE_URL}/#/monitor", wait_until="domcontentloaded", timeout=20_000)
            page.get_by_test_id("channel-card-1").wait_for(state="visible", timeout=10_000)
            assert page.get_by_test_id("multi-monitor-emergency-exit").count() == 0
            channel_one = page.get_by_test_id("channel-card-1")
            channel_one.click()
            page.wait_for_timeout(300)
            assert page.get_by_test_id("single-channel-monitor").count() == 0
            assert "border-cyan-500" in (channel_one.get_attribute("class") or "")
            assert page.locator("[data-testid^='channel-zoom-']").count() == 0
            page.screenshot(path=str(ARTIFACT_DIR / "disabled-legacy-selection.png"), full_page=True)

            run_lines.extend([
                f"overview_snapshot_channels={sorted(set(overview_snapshots))}",
                f"overview_mjpeg_channels={sorted(set(overview_mjpeg))}",
                f"all_result_channels={sorted(set(observed['results']))}",
                "main_overview_to_shared_single_and_back=PASS",
                "single_and_kiosk_sop_below_video=PASS",
                "single_summary_three_equal_panels=PASS",
                "single_and_kiosk_shared_good_bad_chart=PASS",
                "single_and_kiosk_shared_yield_gauge=PASS",
                "kiosk_readonly_and_navigation_hidden=PASS",
                "readonly_controls_visible_disabled=PASS",
                "main_display_option_disabled=PASS",
                "main_monitor_has_no_emergency_exit=PASS",
                "electron_payload_structured_clone=PASS",
                "settings_disable_persisted_false_and_applied_electron=PASS",
                "disabled_four_station_click_keeps_legacy_selection=PASS",
                "status=PASS",
                "failed:0",
            ])
            context.close()
            if main_video is not None:
                main_video.save_as(str(video_dir / "main-overview-and-single.webm"))
            if kiosk_video is not None:
                kiosk_video.save_as(str(video_dir / "kiosk-readonly.webm"))
            browser.close()
    finally:
        request_json("PUT", "/api/v1/workstations/multi-monitor", original_multi)
        request_json("POST", "/api/v1/workstations/mode", {"channel_count": original_count})
        run_lines.append("config_restored=true")
        (ARTIFACT_DIR / "run.log").write_text("\n".join(run_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
