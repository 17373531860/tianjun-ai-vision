"""侦察+演示: 监控页包装卡片 (扫工单→客户名/工单号/箱数) + 人工确认弹层选择器."""
import os, time, json, requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://127.0.0.1:6001"
SHOTS = "/tmp/uat_sy_shots"; os.makedirs(SHOTS, exist_ok=True)

# admin token (用于模拟"已连接扫码器"驱动扫码 — 真实现场是网络扫码器直驱后端)
tok = requests.post(f"{API}/auth/login", json={"username": "admin", "password": "admin123456"}).json()["token"]
H = {"Authorization": f"Bearer {tok}"}

def scan(code):
    r = requests.post(f"{API}/packaging-flows/scan", headers=H,
                      json={"code": code, "channel_id": 0, "scan_device_id": 2})
    print("scan", code, "→", json.dumps(r.json(), ensure_ascii=False)[:160])

with sync_playwright() as p:
    b = p.chromium.launch(headless=False, slow_mo=200,
                          args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(viewport={"width": 1600, "height": 1000})
    pg = ctx.new_page()
    pg.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded"); time.sleep(3)

    # 扫工单(模拟连接的扫码器): 丢了 - 的原始码
    scan("JOB1503000213")
    time.sleep(2.5)  # 给前端两个 poll
    pg.screenshot(path=f"{SHOTS}/D1_scan_order_card.png", full_page=True)

    body = pg.evaluate("document.body.innerText")
    print("==== body 含关键词? ====")
    for kw in ["上银科技", "JOB150300021-3", "客户", "工单", "箱", "滑块", "96"]:
        print(f"  {kw}: {'有' if kw in body else '无'}")
    # 找包装卡片容器 class
    print("==== 可能的包装卡片 class ====")
    classes = pg.evaluate("""() => {
        const out=[]; document.querySelectorAll('[class*=pack],[class*=Pack],[class*=packaging]').forEach(e=>out.push(e.className)); return out.slice(0,15);
    }""")
    print(classes)
    # 扫一个查不到的工单 → 触发 on_mes_fail block + 事件3 人工确认
    scan("JOB9999999990")
    time.sleep(3)
    pg.screenshot(path=f"{SHOTS}/D2_unknown_order_ack.png", full_page=True)
    body2 = pg.evaluate("document.body.innerText")
    print("==== 异常后 body 含人工确认? ====")
    for kw in ["人工确认", "确认", "管理员", "登录", "异常", "需要"]:
        print(f"  {kw}: {'有' if kw in body2 else '无'}")
    print("==== overlay class ====")
    ov = pg.evaluate("""() => {
        const out=[]; document.querySelectorAll('[class*=ack],[class*=overlay],[class*=Ack],[class*=Overlay],[class*=mask]').forEach(e=>out.push(e.className)); return out.slice(0,15);
    }""")
    print(ov)
    time.sleep(1)
    ctx.close(); b.close()
print("done", SHOTS)
