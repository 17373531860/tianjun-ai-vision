"""多工位 SOP 按 sequence 建卡可见浏览器 UAT。

现场：双/三工位顺序项目的 SOP 应按 sequence 建卡（可重复 label），
未进序列的启用步骤不能出现。results 瘦快照必须带 sequence 身份字段。
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
    sequential_trap_payload,
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
    run = UatRun("monitor_sop_sequence", evidence_dir=EVIDENCE_DIR)
    original_count = int(_request("GET", "/api/v1/workstations/")["channel_count"])
    browser = context = page = None
    video_path = None
    pid = None

    try:
        # T5：真实通道 GET results 瘦快照含 sequence 身份（不靠页面 mock）
        pname = f"__e2e_uat_seq_{os.getpid()}"
        created = requests.post(f"{API}/api/v1/projects", json={
            "name": pname,
            "task_type": "detection",
            "logic_mode": "sequential",
            "steps_config": [
                {"id": 1, "label": "检查外观", "enabled": True},
                {"id": 2, "label": "未进序列", "enabled": True},
            ],
            "pipeline_config": {
                "sequence_order": [{"step_id": 1, "extra": "drop-me"}],
                "custom_conditions": [{"name": "条件A", "sequence": [1]}],
            },
        }, timeout=10)
        created.raise_for_status()
        pid = created.json()["id"]
        cfg = requests.get(f"{API}/api/v1/projects/{pid}", timeout=10).json()
        setp = requests.post(
            f"{API}/api/v1/source/detection/set-project?channel=0",
            json={
                "project_id": cfg["id"],
                "name": cfg["name"],
                "task_type": cfg.get("task_type") or "detection",
                "logic_mode": cfg.get("logic_mode") or "sequential",
                "steps_config": cfg.get("steps_config") or [],
                "pipeline_config": cfg.get("pipeline_config") or {},
            }, timeout=15)
        setp.raise_for_status()
        body = requests.get(
            f"{API}/api/v1/source/detection/results?channel=0", timeout=10).json()
        pipe = ((body.get("project_config") or {}).get("pipeline_config") or {})
        run.step("GET results 瘦快照含 sequence_order",
                 pipe.get("sequence_order") == [{"step_id": 1}],
                 f"sequence_order={pipe.get('sequence_order')}")
        run.step("GET results custom_conditions 只带 sequence",
                 pipe.get("custom_conditions") == [{"sequence": [1]}],
                 f"custom_conditions={pipe.get('custom_conditions')}")
        run.step("GET results 不带完整 pipeline 重载键",
                 "settlement_mode" not in pipe and "extra" not in str(pipe.get("sequence_order")))

        with sync_playwright() as playwright:
            browser, context, page, console_errors = launch_browser(
                playwright,
                headless=False,
                slow_mo=80,
                viewport=(1920, 1080),
                record_video_dir=run.video_dir,
            )
            video = page.video
            handler, _route = mixed_mode_router({
                0: sequential_trap_payload,
                1: sequential_trap_payload,
                2: sequential_trap_payload,
            })
            page.route(RESULTS_ROUTE, handler)
            page.route("**/video_feed?**", lambda route: route.abort())
            page.route("**/snapshot?**", lambda route: route.abort())

            _set_count(2)
            page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(2_000)
            sop0 = page.get_by_test_id("dual-sop-0")
            sop0.wait_for(state="visible", timeout=10_000)
            run.shot(page, "01_dual_seq_trap")
            cards0 = sop0.locator("div.w-28")
            run.step("双工位槽位仍叫 sop-row",
                     sop0.get_attribute("data-layout-slot") == "sop-row")
            run.step("双工位 SOP 两张检查外观卡，无未进序列",
                     cards0.count() == 2
                     and cards0.filter(has_text="检查外观").count() == 2
                     and sop0.get_by_text("未进序列").count() == 0)

            _set_count(3)
            page.reload(wait_until="domcontentloaded")
            page.route(RESULTS_ROUTE, handler)
            page.route("**/video_feed?**", lambda route: route.abort())
            page.route("**/snapshot?**", lambda route: route.abort())
            page.get_by_test_id("triple-grid").wait_for(state="visible", timeout=10_000)
            page.wait_for_timeout(800)
            run.shot(page, "02_triple_seq_trap")
            sop_t = page.get_by_test_id("triple-sop-0")
            table_t = page.get_by_test_id("triple-steptable-0")
            run.step("三工位槽位仍叫 sop",
                     sop_t.get_attribute("data-layout-slot") == "sop")
            run.step("三工位 SOP/步骤表按 sequence 建卡",
                     sop_t.locator("div.w-28").count() == 2
                     and sop_t.locator("div.w-28").filter(has_text="检查外观").count() == 2
                     and sop_t.get_by_text("未进序列").count() == 0
                     and table_t.get_by_text("检查外观").count() == 2
                     and table_t.get_by_text("未进序列").count() == 0)

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
        if pid:
            try:
                requests.delete(f"{API}/api/v1/projects/{pid}", timeout=10)
            except Exception:
                pass
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
