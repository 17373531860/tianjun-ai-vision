"""采集现场配置指南用的真实 UI 截图（headless=False）。"""
import os
import time
from pathlib import Path

import requests
from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:6001")
API = os.environ.get("E2E_API_URL", "http://localhost:8001") + "/api/v1"
OUT = Path("/tmp/scan_guide_shots")
OUT.mkdir(parents=True, exist_ok=True)


def shot(page, name):
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print("shot", p.name, flush=True)


def main():
    ts = int(time.time())
    name = f"焊接六芯子_指南示范_{ts}"
    r = requests.post(f"{API}/projects/", json={
        "name": name, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    pid = r.json()["id"]
    requests.post(f"{API}/projects/{pid}/activate", timeout=30)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{BASE}/#/mes", wait_until="domcontentloaded")
        page.get_by_role("button", name="扫码器").click()
        time.sleep(0.4)
        shot(page, "01_mes_scanner_tabs")
        page.get_by_role("tab", name="多码采集").click()
        expect(page.get_by_test_id("sc-project-select")).to_be_visible(timeout=8000)
        time.sleep(0.3)
        shot(page, "02_tab_empty")
        page.get_by_test_id("sc-project-select").click()
        time.sleep(0.3)
        shot(page, "03_project_dropdown")
        page.keyboard.press("Escape")
        page.get_by_role("button", name="填入示例").click()
        expect(page.locator(".el-table__body tr")).to_have_count(3, timeout=5000)
        time.sleep(0.3)
        shot(page, "04_after_example")
        page.evaluate("window.scrollTo(0, 420)")
        time.sleep(0.3)
        shot(page, "05_policies_after_example")
        page.get_by_test_id("sc-enabled-switch").click()
        page.get_by_test_id("sc-save-btn").click()
        expect(page.locator(".el-message--success").last).to_be_visible(timeout=5000)
        time.sleep(0.4)
        shot(page, "06_saved")
        browser.close()

    requests.put(f"{API}/scan-collect/config?project_id={pid}",
                 json={"enabled": False, "slots": []}, timeout=5)
    requests.delete(f"{API}/projects/{pid}", timeout=10)
    print("done", OUT)


if __name__ == "__main__":
    main()
