"""v3.47 — Source 页自定义工位数 UI → 后端落库 双向验证 (T5)"""
import json
import urllib.request
from playwright.sync_api import sync_playwright

FRONT = "http://localhost:6002"
BACK = "http://127.0.0.1:8002"


def backend_count():
    with urllib.request.urlopen(f"{BACK}/api/v1/workstations/") as r:
        return json.load(r)["channel_count"]


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(FRONT)
    page.evaluate("localStorage.setItem('developer_mode', 'true')")
    page.goto(f"{FRONT}/#/source")
    page.reload()
    page.wait_for_timeout(3000)

    # 自定义工位数输入 6 → 应用
    spin = page.locator(".el-input-number input").first
    spin.fill("6")
    spin.dispatch_event("change")
    page.wait_for_timeout(300)
    page.get_by_role("button", name="应用", exact=True).click()
    page.wait_for_timeout(1500)
    page.screenshot(path="evidence/screenshots/09_source_custom_count.png")

    cnt = backend_count()
    print(f"后端 channel_count = {cnt}")
    assert cnt == 6, "应用后后端应为 6 工位"

    # 工位配置卡应扩到 6 张
    cards = page.locator("text=/^工位 [0-9]+$/").count()
    print(f"Source 页工位配置卡数 = {cards}")
    assert cards >= 6, "应渲染 6 张工位配置卡"

    # 回切四工位快捷档 → 后端应为 4
    page.locator("span.el-radio-button__inner", has_text="四工位").click()
    page.wait_for_timeout(1500)
    cnt = backend_count()
    print(f"回切后 channel_count = {cnt}")
    assert cnt == 4, "快捷档回切后后端应为 4 工位"

    browser.close()
    print("PASS source")
