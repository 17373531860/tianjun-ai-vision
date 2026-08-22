"""按工位模式切工艺面板可见浏览器 UAT（v3.55）。

覆盖：三工位异模式列 / 网格放大 tracking / kiosk 逐件只读 / 单工位 mode-panel。
槽位 id 契约不动（dual=sop-row / triple=sop / zoom=sop / single=mode-panel）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, filter_console_errors, launch_browser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from e2e_browser.mode_payloads import (  # noqa: E402
    RESULTS_ROUTE,
    mixed_mode_router,
)


FRONT = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:6002")
API = os.environ.get("E2E_API_URL", "http://127.0.0.1:8002")
EVIDENCE_DIR = os.environ.get("UAT_ARTIFACT_DIR")


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    response = requests.request(method, f"{API}{path}", json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def _set_count(n: int) -> None:
    _request("POST", "/api/v1/workstations/mode", {"channel_count": n})


def main() -> int:
    run = UatRun("monitor_mode_panels", evidence_dir=EVIDENCE_DIR)
    original_count = int(_request("GET", "/api/v1/workstations/")["channel_count"])
    browser = context = page = None
    video_path = None

    try:
        with sync_playwright() as playwright:
            browser, context, page, console_errors = launch_browser(
                playwright,
                headless=False,
                slow_mo=80,
                viewport=(1920, 1080),
                record_video_dir=run.video_dir,
            )
            video = page.video
            handler, _route = mixed_mode_router()
            page.route(RESULTS_ROUTE, handler)
            page.route("**/video_feed?**", lambda route: route.abort())
            page.route("**/snapshot?**", lambda route: route.abort())

            # ── 三工位异模式 ──
            _set_count(3)
            page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(2_000)
            grid = page.get_by_test_id("triple-grid")
            grid.wait_for(state="visible", timeout=10_000)
            page.wait_for_timeout(800)
            run.shot(page, "01_triple_mixed")
            sop0 = page.get_by_test_id("triple-sop-0")
            sop1 = page.get_by_test_id("triple-sop-1")
            sop2 = page.get_by_test_id("triple-sop-2")
            run.step("ch0 清点看板", sop0.get_attribute("data-mode-panel") == "tracking"
                     and sop0.get_by_text("物品清点").count() == 1)
            run.step("ch1 逐件看板", sop1.get_attribute("data-mode-panel") == "per_item"
                     and sop1.get_by_text("逐件覆盖").count() >= 1)
            run.step("ch2 区域事件 SOP 规则名",
                     (sop2.get_attribute("data-mode-panel") in (None, ""))
                     and sop2.get_by_text("测硬度").count() >= 1
                     and sop2.get_by_text("测硬度笔").count() == 0)

            # ── 网格放大 tracking（同 URL hash 不会重挂载，必须 reload 才能拉新工位数） ──
            _set_count(6)
            page.reload(wait_until="domcontentloaded")
            page.route(RESULTS_ROUTE, handler)
            page.route("**/video_feed?**", lambda route: route.abort())
            page.route("**/snapshot?**", lambda route: route.abort())
            page.locator("[data-layout-canvas='grid']").wait_for(timeout=12_000)
            page.wait_for_timeout(800)
            page.get_by_test_id("channel-card-0").click()
            page.get_by_test_id("single-channel-monitor").wait_for(timeout=8_000)
            page.wait_for_timeout(800)
            zoom_sop = page.locator("[data-layout-canvas='zoom'] [data-layout-slot='sop']")
            run.shot(page, "02_zoom_tracking")
            run.step("放大态 sop 槽显示物品清点",
                     zoom_sop.count() == 1
                     and zoom_sop.get_attribute("data-mode-panel") == "tracking"
                     and zoom_sop.get_by_text("物品清点").count() == 1)

            # ── kiosk 逐件只读 ──
            _set_count(3)
            page.goto(f"{FRONT}/#/monitor?channel=1&kiosk=1",
                      wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(2_200)
            kiosk_sop = page.locator("[data-layout-canvas='zoom'] [data-layout-slot='sop']")
            run.shot(page, "03_kiosk_per_item")
            run.step("kiosk 逐件只读无写按钮",
                     page.get_by_test_id("single-channel-monitor").get_attribute("data-readonly") == "true"
                     and kiosk_sop.get_attribute("data-mode-panel") == "per_item"
                     and kiosk_sop.get_by_text("手动开始").count() == 0
                     and page.get_by_test_id("single-channel-start").is_disabled())

            # ── 单工位 mode-panel（跟 currentProject.logic_mode，不靠停机轮询） ──
            page.unroute(RESULTS_ROUTE)
            pname = f"__e2e_uat_trk_{os.getpid()}"
            created = requests.post(f"{API}/api/v1/projects", json={
                "name": pname, "task_type": "detection", "logic_mode": "tracking",
            }, timeout=10)
            created.raise_for_status()
            pid = created.json()["id"]
            requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=30).raise_for_status()
            _set_count(1)
            page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded", timeout=20_000)
            page.reload(wait_until="domcontentloaded")
            page.locator("[data-layout-canvas='single']").wait_for(timeout=12_000)
            page.wait_for_timeout(800)
            mode_panel = page.locator(
                "[data-layout-canvas='single'] [data-layout-slot='mode-panel']")
            run.shot(page, "04_single_tracking")
            run.step("单工位 mode-panel 清点",
                     mode_panel.count() == 1
                     and mode_panel.get_attribute("data-mode-panel") == "tracking"
                     and mode_panel.get_by_text("物品清点").count() == 1)
            try:
                requests.delete(f"{API}/api/v1/projects/{pid}", timeout=10)
            except Exception:
                pass

            real_console_errors = filter_console_errors(
                console_errors,
                extra_noise=("Project not found", "Channel 1 not configured",
                             "Channel 2 not configured", "not configured (active:"),
            )
            run.step("浏览器无前端逻辑错误", not real_console_errors,
                     f"errors={real_console_errors[:3]}")

            page.unroute_all(behavior="ignoreErrors")
            context.close()
            context = None
            if video is not None:
                video_path = Path(video.path())
            browser.close()
            browser = None
    finally:
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        _set_count(original_count)
        run.step("工位数已还原",
                 _request("GET", "/api/v1/workstations/")["channel_count"] == original_count,
                 f"channel_count={original_count}")

    exit_code = run.finish()
    summary = json.loads((Path(run.dir) / "run.json").read_text(encoding="utf-8"))
    (Path(run.dir) / "run.log").write_text(
        "\n".join([
            f"frontend: {FRONT}",
            f"backend: {API}",
            "browser: chromium headless=False",
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
