"""可见浏览器 UAT — 场景A: 短箱(漏箱) → 操作员人工确认被阻断 → 借管理员密码提权确认.

前置(本脚本内 admin API 完成):
  - reset 协调器 → 扫工单 JOB1503000213(MES 返 96/SY/上银) → 结算短箱 slider=50(<96) → pending_ack 激活
浏览器(op001 操作员):
  - 登录 → 监控页(看到「需要人工确认」遮罩 + 包装卡片短箱) → 点「我已确认」→ 403 弹提权窗
  - 输管理员账密「授权并确认」→ 阻塞解除
断言: 结束后 pending_ack.active == False
"""
import os, time, requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://127.0.0.1:6001"
SHOTS = "/tmp/uat_A_shots"
os.makedirs(SHOTS, exist_ok=True)
ADMIN_U, ADMIN_P = "admin", "admin123456"
OP_U, OP_P = "op001", "op123456"


def admin_tok():
    r = requests.post(f"{API}/auth/login", json={"username": ADMIN_U, "password": ADMIN_P})
    return r.json()["token"]


def setup_shortbox(tok):
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    # 让 ch0 进入运行态(待机), Monitor 挂载后才会轮询检测结果 → 才能拿到 pending_ack
    requests.post(f"{API}/source/detection/ack-event?channel=0", headers=H)  # 清遗留
    requests.post(f"{API}/test/synthetic/start", headers=H,
                  json={"channel": 0, "scenario_json": {"name": "idle", "timeline": []}, "fps": 5})
    requests.post(f"{API}/test/synthetic/packaging-reset", headers=H)
    r = requests.post(f"{API}/packaging-flows/scan", headers=H,
                      json={"code": "JOB1503000213", "channel_id": 0, "scan_device_id": 2})
    st = r.json().get("state", {})
    print(f"[setup] 扫单 → {st.get('order_no')} / {st.get('cust_name')} / {st.get('spec')} / 箱{st.get('box_total')} / 滑块{st.get('slider_total')}")
    requests.post(f"{API}/test/synthetic/packaging-settle", headers=H,
                  json={"channel_id": 0, "cycle_id": 1, "is_good": True, "slider_count": 50})
    pa = requests.get(f"{API}/source/detection/results?channel=0", headers=H).json().get("pending_ack")
    print(f"[setup] 结算短箱50 → pending_ack active={pa and pa.get('active')} event={pa and pa.get('event_name')}")
    return pa and pa.get("active")


def pending_active(tok):
    H = {"Authorization": f"Bearer {tok}"}
    pa = requests.get(f"{API}/source/detection/results?channel=0", headers=H).json().get("pending_ack")
    return bool(pa and pa.get("active"))


def main():
    tok = admin_tok()
    if not setup_shortbox(tok):
        print("[FAIL] 短箱未触发 pending_ack, 终止")
        return 1

    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, slow_mo=300,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(viewport={"width": 1600, "height": 1000})
        pg = ctx.new_page()

        # 1. 登录 op001
        pg.goto(f"{FRONT}/#/login", wait_until="domcontentloaded"); time.sleep(2.5)
        pg.fill('input[placeholder="请输入用户名"]', OP_U)
        pg.fill('input[placeholder="请输入密码"]', OP_P)
        pg.screenshot(path=f"{SHOTS}/A0_login_filled.png")
        pg.get_by_role("button", name="登录", exact=True).click(); time.sleep(2.5)
        pg.screenshot(path=f"{SHOTS}/A1_after_login.png")

        # 2. 进监控 — 应看到人工确认遮罩
        pg.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded"); time.sleep(3.5)
        pg.screenshot(path=f"{SHOTS}/A2_monitor_ack_overlay.png", full_page=True)
        body = pg.evaluate("document.body.innerText")
        for kw in ["需要人工确认", "包装异常", "上银科技", "JOB150300021-3"]:
            print(f"[chk] 遮罩含「{kw}」: {kw in body}")

        # 3. 点「我已确认」→ 期望 403 → 弹提权窗
        clicked = False
        for sel in ['button:has-text("我已确认")', 'text=我已确认']:
            try:
                pg.click(sel, timeout=3000); clicked = True; break
            except Exception:
                continue
        print(f"[act] 点击「我已确认」: {clicked}")
        time.sleep(2)
        pg.screenshot(path=f"{SHOTS}/A3_elevate_dialog.png", full_page=True)
        body2 = pg.evaluate("document.body.innerText")
        print(f"[chk] 弹出提权窗(含「借管理员密码」): {'借管理员密码' in body2}")

        # 4. 输管理员账密 → 授权并确认
        try:
            pg.fill('input[placeholder="管理员账号"]', ADMIN_U)
            pg.fill('input[placeholder="管理员密码"]', ADMIN_P)
            pg.screenshot(path=f"{SHOTS}/A4_elevate_filled.png", full_page=True)
            pg.get_by_role("button", name="授权并确认").click()
            time.sleep(2.5)
        except Exception as e:
            print(f"[FAIL] 提权窗操作异常: {e}")
        pg.screenshot(path=f"{SHOTS}/A5_after_elevate.png", full_page=True)

        ctx.close(); b.close()

    still = pending_active(tok)
    print(f"\n[断言] 提权确认后 pending_ack.active = {still} (期望 False)")
    print("[结果]", "PASS ✅" if not still else "FAIL ❌")
    return 0 if not still else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
