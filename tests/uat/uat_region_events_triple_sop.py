"""区域事件三工位 SOP 规则名可见浏览器 UAT。

验证浏览器收到的 detection/results.project_config.region_events.rules 会成为
三工位 SOP 卡片，且不误用 steps_config 模型类别；同时覆盖 in-flight active。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, filter_console_errors, launch_browser


FRONT = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:6001")
API = os.environ.get("E2E_API_URL", "http://127.0.0.1:8001")
EVIDENCE_DIR = os.environ.get("UAT_ARTIFACT_DIR")
RULE_NAMES = ["测硬度", "扫码", "下工件"]


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    response = requests.request(method, f"{API}{path}", json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def main() -> int:
    run = UatRun("region_events_triple_sop", evidence_dir=EVIDENCE_DIR)
    original_count = int(_request("GET", "/api/v1/workstations/")["channel_count"])
    phase = {"value": "pending"}
    observed_channels: set[int] = set()
    delivered_rules: dict[int, list[str]] = {}

    def detection_results(route) -> None:
        channel = int(route.request.url.split("channel=")[-1].split("&")[0])
        observed_channels.add(channel)
        current_steps = ["测硬度"] if phase["value"] == "completed" else []
        inflight = {"测硬度": 1.2} if phase["value"] == "active" else {}
        payload = {
            "is_running": True,
            "is_detecting": True,
            "source_type": "video",
            "fps": 22,
            "latency": 12,
            "counters": {"总产量": 1, "合格总数": 1, "不良总数": 0},
            "detections": [],
            "current_cycle_steps": current_steps,
            "step_inflight_durations": inflight,
            "step_screenshots": {},
            "recent_events": [],
            "project_config": {
                "project_name": "区域事件三工位UAT",
                "logic_mode": "region_events",
                "steps_config": [
                    {"id": 1, "label": "工件", "enabled": True},
                    {"id": 2, "label": "测硬度笔", "enabled": True},
                    {"id": 3, "label": "扫码枪", "enabled": False},
                ],
                "pipeline_config": {
                    "region_events": {
                        "rules": [
                            {"id": "r1", "name": "测硬度"},
                            {"id": "r2", "name": "扫码"},
                            {"id": "r3", "name": "下工件"},
                        ],
                    },
                },
            },
        }
        delivered_rules[channel] = [
            item["name"]
            for item in payload["project_config"]["pipeline_config"]["region_events"]["rules"]
        ]
        route.fulfill(status=200, json=payload)

    browser = context = page = None
    video = None
    video_path = None
    try:
        _request("POST", "/api/v1/workstations/mode", {"channel_count": 3})
        run.step("隔离后端切换为三工位", True, f"原工位数={original_count}")

        with sync_playwright() as playwright:
            browser, context, page, console_errors = launch_browser(
                playwright,
                headless=False,
                slow_mo=120,
                viewport=(1920, 1080),
                record_video_dir=run.video_dir,
            )
            video = page.video
            results_route = "**/source/detection/results*"
            page.route(results_route, detection_results)
            page.route("**/video_feed?**", lambda route: route.abort())
            page.route("**/snapshot?**", lambda route: route.abort())
            page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded", timeout=20_000)

            grid = page.get_by_test_id("triple-grid")
            grid.wait_for(state="visible", timeout=10_000)
            page.wait_for_timeout(1_500)
            run.shot(page, "01_three_rule_cards_pending")

            sop = page.get_by_test_id("triple-sop-0")
            cards = sop.locator("div.w-28")
            dom_titles = [
                sop.get_by_text(name, exact=True).inner_text()
                for name in RULE_NAMES
                if sop.get_by_text(name, exact=True).count() == 1
            ]
            run.step("GET 已覆盖三条工位轮询", {0, 1, 2}.issubset(observed_channels),
                     f"channels={sorted(observed_channels)}")
            run.step("GET rules 与三工位 SOP 标题一致",
                     delivered_rules.get(0) == dom_titles == RULE_NAMES,
                     f"GET={delivered_rules.get(0)}, DOM={dom_titles}")
            run.step("模型类别陷阱未成为 SOP 标题",
                     cards.count() == 3 and sop.get_by_text("测硬度笔", exact=True).count() == 0,
                     f"cards={cards.count()}")

            phase["value"] = "active"
            page.wait_for_function(
                """() => [...document.querySelectorAll('[data-testid="triple-sop-0"] div.w-28')]
                  .some(card => card.innerText.includes('测硬度') && card.className.includes('border-cyan-500'))""",
                timeout=8_000,
            )
            run.shot(page, "02_inflight_active_cyan")
            active_class = cards.filter(has_text="测硬度").get_attribute("class") or ""
            run.step("in-flight 规则卡呈进行中青色", "border-cyan-500" in active_class,
                     active_class)

            phase["value"] = "completed"
            page.wait_for_function(
                """() => [...document.querySelectorAll('[data-testid="triple-sop-0"] div.w-28')]
                  .some(card => card.innerText.includes('测硬度') && card.className.includes('border-green-500'))""",
                timeout=8_000,
            )
            page.wait_for_timeout(1_000)
            run.shot(page, "03_confirmed_rule_green")
            completed_class = cards.filter(has_text="测硬度").get_attribute("class") or ""
            run.step("current_cycle_steps 规则名驱动完成态", "border-green-500" in completed_class,
                     completed_class)

            real_console_errors = filter_console_errors(console_errors)
            run.step("浏览器无前端逻辑错误", not real_console_errors,
                     f"errors={real_console_errors[:3]}")
            page.unroute_all(behavior="ignoreErrors")
            context.close()
            context = None
            video_path = Path(video.path())
            browser.close()
            browser = None
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()
        _request("POST", "/api/v1/workstations/mode", {"channel_count": original_count})
        run.step("工位数已还原", _request("GET", "/api/v1/workstations/")["channel_count"] == original_count,
                 f"channel_count={original_count}")

    exit_code = run.finish()
    summary = json.loads((Path(run.dir) / "run.json").read_text(encoding="utf-8"))
    (Path(run.dir) / "run.log").write_text(
        "\n".join([
            f"frontend: {FRONT}",
            f"backend: {API}",
            "browser: chromium headless=False",
            f"GET rules: {RULE_NAMES}",
            f"passed: {summary['passed']}",
            f"failed: {summary['failed']}",
        ]) + "\n",
        encoding="utf-8",
    )
    if video_path is not None:
        target = Path(run.dir) / "uat-visible.webm"
        if video_path != target:
            video_path.replace(target)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
