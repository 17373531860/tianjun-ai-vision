#!/usr/bin/env python
"""sensor-clean v1.4.3 可见浏览器 UAT。

前置：隔离后端/前端已经启动，后端启用 RUNTIME_MODE=test 与插件 dev mode。
验证链：synthetic 真管线 → 插件帧 hook → API 状态 → Monitor UI → 棉签记录落库。
产物：可见 Chromium 录像、四张关键截图、JSON run.log。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")


API = os.environ.get("SENSOR_CLEAN_UAT_API", "http://127.0.0.1:8013")
FRONT = os.environ.get("SENSOR_CLEAN_UAT_FRONT", "http://127.0.0.1:6013")
ROOT = Path(os.environ.get(
    "SENSOR_CLEAN_UAT_ARTIFACTS",
    str(Path(os.environ.get("TEMP", ".")) / "sensor_clean_v143_uat"),
))
SHOTS = ROOT / "screenshots"
VIDEO = ROOT / "video"
RUN_LOG = ROOT / "run.log"
SWAB = f"{API}/api/v1/plugins/sensor-clean/swab"
ANCHOR = "查看产品有无脏污"
PERMIT = "擦拭产品"
SWAP = "更换棉签"

SHOTS.mkdir(parents=True, exist_ok=True)
VIDEO.mkdir(parents=True, exist_ok=True)
steps: list[dict] = []
page_errors: list[str] = []


def record(label: str, ok: bool, detail: str = "") -> None:
    item = {"idx": len(steps) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps.append(item)
    print(f"[{'OK' if ok else '!!'}] {item['idx']:02d}. {label}  {detail}", flush=True)


def request(method: str, path: str, **kwargs):
    response = requests.request(method, f"{API}{path}", timeout=90, **kwargs)
    response.raise_for_status()
    return response


def swab_state() -> dict:
    return request("GET", "/api/v1/plugins/sensor-clean/swab/state").json()


def wait_state(predicate, timeout: float, description: str) -> dict:
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = swab_state()
        if predicate(last):
            return last
        time.sleep(0.4)
    raise AssertionError(f"等待超时: {description}; last={last}")


def stop_detection(channel: int) -> None:
    try:
        request("POST", f"/api/v1/source/detection/stop?channel={channel}")
    except Exception:
        pass
    try:
        request("POST", f"/api/v1/test/synthetic/stop?channel={channel}")
    except Exception:
        pass


def run_scenario(channel: int, name: str, timeline: list[dict], fps: int = 30) -> None:
    stop_detection(channel)
    request("POST", "/api/v1/test/synthetic/start", json={
        "scenario_json": {"name": name, "fps": fps, "timeline": timeline},
        "channel": channel,
        "with_project": False,
    })
    request("POST", f"/api/v1/source/detection/start?channel={channel}",
            json={"conf": 0.25, "iou": 0.45})


def product_timeline(count: int, *, start_y: float = 0.20) -> list[dict]:
    """生成互不落入位置锁的产品轨迹；每件保持生产默认时间门槛。"""
    timeline: list[dict] = []
    frame = 0
    for idx in range(count):
        y = start_y + idx * 0.052

        def detections(x: float) -> list[dict]:
            return [
                {"label": PERMIT, "confidence": 0.95, "bbox": [x, y, 0.05, 0.05]},
                {"label": ANCHOR, "confidence": 0.95, "bbox": [x, y, 0.05, 0.05]},
            ]

        timeline.extend([
            {"from": frame, "to": frame + 14, "detections": detections(0.38)},
            {"from": frame + 15, "to": frame + 29, "detections": detections(0.62)},
            {"from": frame + 30, "to": frame + 74, "detections": []},
        ])
        frame += 75
    timeline.append({"from": frame, "to": frame + 900, "detections": []})
    return timeline


def swap_timeline() -> list[dict]:
    det = [{"label": SWAP, "confidence": 0.96, "bbox": [0.45, 0.35, 0.12, 0.12]}]
    return [
        {"from": 0, "to": 59, "detections": det},  # 连续 2 秒，只能算一次动作段
        {"from": 60, "to": 180, "detections": []},
    ]


def screenshot(page, filename: str) -> None:
    path = SHOTS / filename
    page.screenshot(path=str(path), full_page=True, animations="disabled", timeout=15000)
    print(f">>> screenshot: {path}", flush=True)


def has_swab_ratio(body: str, used: int, limit: int) -> bool:
    """忽略 Vue 文本节点之间的空格差异，读取“已用 / 上限”数值。"""
    return re.search(rf"\b{used}\s*/\s*{limit}\b", body) is not None


def main() -> int:
    final_state: dict = {}
    records_payload: dict = {}
    browser = None
    context = None
    try:
        health = request("GET", "/api/v1/plugins/active/manifest").json()
        record("隔离后端与 sensor-clean 插件就绪", "sensor-clean" in json.dumps(health, ensure_ascii=False))

        imported = request("POST", "/api/v1/plugins/sensor-clean/swab/import-project").json()
        record("客户双视角项目与模型可导入", bool(imported.get("ok")), json.dumps(imported, ensure_ascii=False)[:400])

        applied = request("POST", "/api/v1/plugins/sensor-clean/swab/apply-preset").json()
        cfg = applied.get("config") or {}
        defaults_ok = (
            cfg.get("_config_revision") == 2
            and abs(float(cfg.get("lost_gone_sec", 0)) - 0.15) < 1e-9
            and abs(float(cfg.get("force_lock_sec", 0)) - 1.4) < 1e-9
            and abs(float(cfg.get("swab_min_sustain_sec", 0)) - 0.12) < 1e-9
            and abs(float(cfg.get("swab_lock_time", 0)) - 0.25) < 1e-9
        )
        record("v1.4.3 真值标定默认值已生效", defaults_ok, json.dumps(cfg, ensure_ascii=False)[:400])

        request("POST", "/api/v1/plugins/sensor-clean/swab/config", json={
            **cfg,
            "label_rois": {},
            "operator_absent_enabled": False,
            "fake_wipe_event_id": 0,
        })
        request("POST", "/api/v1/plugins/sensor-clean/swab/reset-counts")

        with sync_playwright() as playwright:
            browser_path = os.environ.get("SENSOR_CLEAN_UAT_BROWSER") or None
            browser = playwright.chromium.launch(
                headless=False,
                slow_mo=180,
                executable_path=browser_path,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                viewport={"width": 1600, "height": 1000},
                record_video_dir=str(VIDEO),
                record_video_size={"width": 1600, "height": 1000},
            )
            page = context.new_page()
            page.set_default_timeout(25000)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            body = ""
            for _ in range(3):
                page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
                time.sleep(8)
                body = page.locator("body").inner_text()
                if "总产量" in body and "棉签用量" in body:
                    break
                page.reload(wait_until="domcontentloaded")
            monitor_ok = "总产量" in body and "棉签用量" in body and "工位2 · 更换棉签" in body
            record("可见 Monitor 渲染双视角插件看板", monitor_ok, body[:180].replace("\n", " | "))
            screenshot(page, "01_monitor_initial.png")

            run_scenario(0, "sensor_clean_11_ok", product_timeline(11))
            state_11 = wait_state(lambda s: s.get("total_products") == 11, 45, "第11件完成")
            stop_detection(0)
            time.sleep(2.2)
            body_11 = page.locator("body").inner_text()
            rule_11_ok = (
                state_11.get("swab_used") == 11
                and state_11.get("ng_count") == 0
                and state_11.get("over_limit") is True
                and has_swab_ratio(body_11, 11, 11)
            )
            record("第11件仍为 OK，棉签到上限但 NG=0", rule_11_ok, json.dumps(state_11, ensure_ascii=False))
            screenshot(page, "02_use_11_ok.png")

            run_scenario(0, "sensor_clean_12_ng", product_timeline(1, start_y=0.83))
            state_12 = wait_state(lambda s: s.get("total_products") == 12, 12, "第12件完成")
            stop_detection(0)
            time.sleep(2.2)
            body_12 = page.locator("body").inner_text()
            rule_12_ok = (
                state_12.get("swab_used") == 12
                and state_12.get("ng_count") == 1
                and has_swab_ratio(body_12, 12, 11)
                and "累计不良 1" in body_12
            )
            record("第12件起精确判 NG", rule_12_ok, json.dumps(state_12, ensure_ascii=False))
            screenshot(page, "03_use_12_ng.png")

            run_scenario(1, "sensor_clean_swap_segment", swap_timeline())
            swapped = wait_state(lambda s: s.get("swab_used") == 0, 10, "有效换棉签清零")
            stop_detection(1)
            time.sleep(2.2)
            body_swap = page.locator("body").inner_text()
            swap_ok = (
                swapped.get("total_products") == 12
                and swapped.get("ng_count") == 1
                and has_swab_ratio(body_swap, 0, 11)
            )
            record("连续2秒换棉签动作段只执行一次并清零当前用量", swap_ok, json.dumps(swapped, ensure_ascii=False))
            screenshot(page, "04_swap_cleared.png")

            records_payload = request("GET", "/api/v1/plugins/sensor-clean/swab/records?limit=20").json()
            rows = records_payload.get("records") or []
            latest = rows[0] if rows else {}
            db_ok = (
                int(latest.get("products", -1)) == 12
                and int(latest.get("ng", -1)) == 1
                and bool(latest.get("over_limit"))
                and latest.get("end_reason") == "change"
            )
            record("棉签生命周期真实落库为12件/1 NG/换棉签结束", db_ok, json.dumps(latest, ensure_ascii=False))

            page.goto(f"{FRONT}/#/data", wait_until="domcontentloaded")
            time.sleep(5)
            data_body = page.locator("body").inner_text()
            data_ok = "棉签使用记录" in data_body and "产品总数" in data_body and "不良总数" in data_body
            record("可见 Data 页渲染棉签落库记录", data_ok, data_body[:180].replace("\n", " | "))
            screenshot(page, "05_data_record.png")

            final_state = swab_state()
            record("浏览器无未捕获 JavaScript 异常", not page_errors, json.dumps(page_errors, ensure_ascii=False))

            context.close()
            context = None
            browser.close()
            browser = None
    except Exception as exc:
        record("UAT 执行异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        stop_detection(0)
        stop_detection(1)
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

        failed = sum(1 for item in steps if not item["ok"])
        payload = {
            "name": "sensor-clean-v1.4.3-visible-uat",
            "api": API,
            "frontend": FRONT,
            "passed": len(steps) - failed,
            "failed": failed,
            "steps": steps,
            "final_state": final_state,
            "records_summary": records_payload.get("summary", {}),
            "artifacts": {
                "screenshots": str(SHOTS),
                "video": str(VIDEO),
                "run_log": str(RUN_LOG),
            },
        }
        RUN_LOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": payload["passed"], "failed": failed, "run_log": str(RUN_LOG)}, ensure_ascii=False), flush=True)
        return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
