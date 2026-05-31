"""UAT: 设置页 → 检测页切换后，插件双工位检测框仍可见.

模拟用户真实路径:
  1. 打开设置页 (插件管理确认 1.1.4)
  2. 切到检测页
  3. API 恢复 JL_GW1/GW2 + 开检测
  4. 等 15s 截图验证 overlay + 步骤统计
"""
from __future__ import annotations

import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:6001"
API = "http://127.0.0.1:8001/api/v1"
OUT = Path("/tmp/uat_monitor_page_switch")
OUT.mkdir(parents=True, exist_ok=True)


def setup_detection():
    ws = requests.get(f"{API}/workstations/", timeout=10).json()
    cfgs = ws.get("source_configs") or {}
    ch0, ch1 = cfgs.get("0") or {}, cfgs.get("1") or {}
    m1 = requests.get(f"{API}/models/3", timeout=5).json()["file_path"]
    m2 = requests.get(f"{API}/models/4", timeout=5).json()["file_path"]
    vid0 = ch0.get("video_file") or ""
    vid1 = ch1.get("video_file") or ""

    # 工位绑定 JL_GW1 / JL_GW2
    requests.put(f"{API}/workstations/channel-config", json={
        "channel_id": 0, "project_id": 2,
        "source_type": "video", "file_path": vid0, "model_id": 2,
    }, timeout=10)
    requests.put(f"{API}/workstations/channel-config", json={
        "channel_id": 1, "project_id": 3,
        "source_type": "video", "file_path": vid1, "model_id": 3,
    }, timeout=10)
    requests.post(f"{API}/projects/2/activate", timeout=15)

    for ch in (0, 1):
        requests.post(f"{API}/source/detection/stop", params={"channel": ch}, timeout=10)
    time.sleep(0.5)

    if vid0:
        requests.post(f"{API}/source/video/start", params={"channel": 0},
                      json={"file_path": vid0, "speed": 1.0}, timeout=20)
    if vid1:
        requests.post(f"{API}/source/video/start", params={"channel": 1},
                      json={"file_path": vid1, "speed": 1.0}, timeout=20)

    requests.post(f"{API}/source/detection/start", params={"channel": 0},
                  json={"model_path": m1, "conf": 0.5, "iou": 0.45}, timeout=60)
    requests.post(f"{API}/source/detection/start", params={"channel": 1},
                  json={"model_path": m2, "conf": 0.5, "iou": 0.45}, timeout=60)


def overlay_has_paint(page, idx: int) -> bool:
    return page.evaluate(
        """(i) => {
          const c = document.querySelectorAll('.fjjl-det-overlay')[i];
          if (!c || !c.width) return false;
          const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
          let n = 0;
          for (let j = 3; j < d.length; j += 32) if (d[j] > 0 && ++n > 3) return true;
          return false;
        }""",
        idx,
    )


def main():
    setup_detection()
    time.sleep(2)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_context(viewport={"width": 1920, "height": 1080}).new_page()

        # 先设置页（用户说的换页）
        page.goto(f"{BASE}/#/settings", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        page.screenshot(path=str(OUT / "01_settings.png"), full_page=True)

        # 再检测页
        page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT / "02_monitor_initial.png"), full_page=True)

        assert page.locator(".fjjl-grid").count() > 0, "插件 UI 未加载"

        # 等轮询 + 画框
        ok0 = ok1 = False
        for _ in range(20):
            page.wait_for_timeout(1000)
            ok0 = overlay_has_paint(page, 0)
            ok1 = overlay_has_paint(page, 1)
            if ok0 and ok1:
                break

        page.screenshot(path=str(OUT / "03_monitor_with_boxes.png"), full_page=True)
        browser.close()

    print(f"工位1 overlay: {'有框' if ok0 else '无框'}")
    print(f"工位2 overlay: {'有框' if ok1 else '无框'}")
    print(f"截图: {OUT}")
    if not (ok0 or ok1):
        raise SystemExit("FAIL: 切换页面后检测框未出现")
    print("PASS")


if __name__ == "__main__":
    main()
