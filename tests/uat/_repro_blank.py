"""复现: 检测中心停止检测后刷新页面是否空白."""
import json
import time
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:6001/#/monitor"


def snapshot(page, tag):
    info = page.evaluate(
        """() => {
        const app = document.querySelector('#app');
        const text = app ? app.innerText : '';
        const hasStop = !!document.body.innerText.match(/停止|开始检测|待机/);
        const hasVideo = !!document.querySelector('canvas, video, img[src*="stream"]');
        const hasGrid = !!document.querySelector('.grid-cols-12, .grid-cols-2');
        return {
          url: location.href,
          textLen: text.length,
          hasStop,
          hasVideo,
          hasGrid,
          preview: text.slice(0, 200).replace(/\\n/g, ' | '),
        };
    }"""
    )
    print(f"[{tag}] {json.dumps(info, ensure_ascii=False)}")
    return info


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(f"CONSOLE[{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"PAGEERROR {e}"))

    page.goto(URL, wait_until="networkidle", timeout=60000)
    time.sleep(4)
    before = snapshot(page, "loaded")

    # 尝试点停止（若存在且未 disabled）
    stop_btn = page.locator("button", has_text="停止").first
    if stop_btn.count() and stop_btn.is_enabled():
        stop_btn.click()
        time.sleep(2)
        snapshot(page, "after-stop-click")
    else:
        print("[info] 停止按钮不可用或不存在，跳过点击")

    page.reload(wait_until="networkidle", timeout=60000)
    time.sleep(4)
    after = snapshot(page, "after-reload")

    page.screenshot(path="tests/uat/_repro_blank.png", full_page=True)

    print("\n===== errors =====")
    for e in errors[-30:]:
        print(e)

    if after["textLen"] < 50 or (not after["hasGrid"] and not after["hasStop"]):
        print("\n[FAIL] 刷新后页面疑似空白")
        raise SystemExit(1)
    print("\n[OK] 刷新后仍有内容")

    browser.close()
