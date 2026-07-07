"""操作员登录 → 监控页包装卡片显示 客户名/工单号/箱数 (头号需求验证)."""
import os, time, requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://127.0.0.1:6001"
SHOTS = "/tmp/uat_sy_shots"; os.makedirs(SHOTS, exist_ok=True)

# 确保有个开着的工单 (admin 模拟连接的扫码器驱动)
tok = requests.post(f"{API}/auth/login", json={"username":"admin","password":"admin123456"}).json()["token"]
requests.post(f"{API}/packaging-flows/scan", headers={"Authorization":f"Bearer {tok}"},
              json={"code":"JOB1503000213","channel_id":0,"scan_device_id":2})

with sync_playwright() as p:
    b = p.chromium.launch(headless=False, slow_mo=250,
                          args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(viewport={"width":1600,"height":1000})
    pg = ctx.new_page()
    pg.goto(f"{FRONT}/#/login", wait_until="domcontentloaded"); time.sleep(2.5)
    pg.screenshot(path=f"{SHOTS}/L0_login.png", full_page=True)
    # 填登录
    ins = pg.locator("input").all()
    print("登录页 input 数:", len(ins))
    try:
        pg.locator("input").nth(0).fill("op001")
        # 密码框: type=password
        pwd = pg.locator("input[type=password]").first
        pwd.fill("op123456")
        time.sleep(0.5)
        # 提交按钮
        pg.locator("button:has-text('登录'), button:has-text('登 录'), button[type=submit]").first.click()
        time.sleep(3)
    except Exception as e:
        print("登录交互异常:", e)
    pg.screenshot(path=f"{SHOTS}/L1_after_login.png", full_page=True)
    # 进监控
    pg.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded"); time.sleep(3.5)
    pg.screenshot(path=f"{SHOTS}/L2_monitor_card.png", full_page=True)
    body = pg.evaluate("document.body.innerText")
    print("==== 卡片关键词 ====")
    for kw in ["上银科技","JOB150300021-3","JOB150300021","客户","工单","滑块","箱","96","操作员","op001","产线操作员"]:
        print(f"  {kw}: {'有' if kw in body else '无'}")
    print("---- body 摘录 ----")
    print(body[:1200])
    time.sleep(1)
    ctx.close(); b.close()
print("done", SHOTS)
