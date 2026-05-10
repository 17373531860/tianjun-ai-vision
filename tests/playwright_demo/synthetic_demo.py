"""虚拟剧本驱动的前端验证演示脚本（不在 pytest 收集中）。

用途：示例 run-tests skill 路径 G 的"端到端验证步骤"。脚本独立运行：

  RUNTIME_MODE=test python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
  cd frontend && npm run dev   # 前端 6001
  python tests/playwright_demo/synthetic_demo.py [scenario.json]

脚本流程：调起 synthetic 剧本 → 启动检测 → 浏览 Monitor → 截图 → 拉 detection/results 做断言。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

API = os.environ.get("API_URL", "http://127.0.0.1:8001")
WEB = os.environ.get("WEB_URL", "http://127.0.0.1:6001")
SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"
SHOTS_DIR = Path(os.environ.get("SHOTS_DIR", "/tmp/synthetic_demo_shots"))


def _post(path: str, **kwargs) -> dict:
    r = requests.post(f"{API}{path}", timeout=10, **kwargs)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text}


def _get(path: str, **kwargs) -> dict:
    r = requests.get(f"{API}{path}", timeout=10, **kwargs)
    r.raise_for_status()
    return r.json()


def _wait_label(label: str, channel: int, timeout_s: float = 5.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        data = _get("/api/v1/source/detection/results", params={"channel": channel})
        for d in data.get("detections") or []:
            if d.get("label") == label:
                return True
        time.sleep(0.05)
    return False


def run(scenario_path: Path, channel: int = 0) -> int:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    spec = json.loads(scenario_path.read_text(encoding="utf-8"))
    print(f"[demo] scenario={spec.get('name')} fps={spec.get('fps', 30)}")

    print("[demo] 启动 synthetic 源")
    _post(
        "/api/v1/test/synthetic/start",
        json={"scenario_json": spec, "channel": channel},
    )

    print("[demo] 启动检测（无模型）")
    _post(
        "/api/v1/source/detection/start",
        params={"channel": channel},
        json={"conf": 0.25, "iou": 0.45},
    )

    first_label = None
    for seg in spec.get("timeline", []):
        if seg.get("detections"):
            first_label = seg["detections"][0].get("label")
            break
    if first_label and _wait_label(first_label, channel):
        print(f"[demo] /detection/results 出现剧本标签 {first_label!r}")
    else:
        print(f"[demo][WARN] 未能在 detection/results 看到 {first_label!r}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{WEB}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_load_state("networkidle")
        time.sleep(1.0)
        shot = SHOTS_DIR / f"{spec.get('name', 'scenario')}.png"
        page.screenshot(path=str(shot), full_page=True)
        print(f"[demo] Monitor 截图：{shot}")
        browser.close()

    print("[demo] 清理 (停检测 + 停 synthetic)")
    try:
        _post("/api/v1/source/detection/stop", params={"channel": channel})
    finally:
        _post("/api/v1/test/synthetic/stop", params={"channel": channel})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default=str(SCENARIO_DIR / "ok_sequential_cycle.json"))
    ap.add_argument("--channel", type=int, default=0)
    args = ap.parse_args()
    return run(Path(args.scenario), channel=args.channel)


if __name__ == "__main__":
    sys.exit(main())
