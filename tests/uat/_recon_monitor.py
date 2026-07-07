"""侦察脚本: 开可见浏览器登录 → Monitor, dump 关键 DOM 找选择器."""
import time
from playwright.sync_api import sync_playwright

FRONT = "http://127.0.0.1:6001"
SHOTS = "/tmp/uat_sy_shots"
import os; os.makedirs(SHOTS, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch(headless=False, slow_mo=200,
                          args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(viewport={"width": 1600, "height": 1000})
    pg = ctx.new_page()
    pg.goto(FRONT, wait_until="domcontentloaded"); time.sleep(3)
    pg.screenshot(path=f"{SHOTS}/recon_01_landing.png", full_page=True)
    print("URL:", pg.url)
    print("---- 登录页输入框 ----")
    for sel in ["input"]:
        els = pg.locator(sel).all()
        for i, e in enumerate(els[:6]):
            try:
                print(i, "ph=", e.get_attribute("placeholder"), "type=", e.get_attribute("type"))
            except Exception:
                pass
    print("---- 按钮 ----")
    for e in pg.locator("button").all()[:10]:
        try:
            print("btn:", (e.inner_text() or "").strip()[:20])
        except Exception:
            pass
    body = pg.evaluate("document.body.innerText")[:800]
    print("---- body ----\n", body)
    time.sleep(1)
    ctx.close(); b.close()
print("done, see", SHOTS)
