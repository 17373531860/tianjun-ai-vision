"""可见浏览器 UAT — 操作员借管理员密码提权确认 NG (v3.23) + 人工确认定格遮罩.

客户诉求原话: "如果是没权限的操作员, 人工确认时应弹出是否登录管理员账号去确认, 等待登录;
取消则回到原人工确认窗口."

闭环演示 (检测运行态下, 前端轮询活, 遮罩真弹):
  1. 启用账号鉴权(若未启用)+建操作员账号(无 monitor.detection.ack 权限)
  2. 起合成空闲检测源(is_running=True 让前端轮询; project_config 仍是 SY 含 require_ack 事件3)
  3. 浏览器以"操作员"登录 → 进监控页
  4. 后端扫单 + 触发缺油嘴 → 整条线定格 → 前端弹"需要人工确认"全屏遮罩
  5. 操作员点"我已确认" → 后端 403(无权限) → 自动弹"借管理员密码授权确认"窗
  6. 输入管理员账密 → "授权并确认" → 定格解除(不改当前登录身份)

证据: 截图 E0~E3 + 录像. 跑完自动关闭鉴权恢复零差异默认.

前置: 后端 8011(RUNTIME_MODE=test) + 前端 6002 + mock MES 9100 已起; 已跑 setup_sy_packaging.py.
跑法: DISPLAY=:0 /home/qianqian/anaconda3/envs/tianjun/bin/python tests/uat/uat_20260622_elevate_ack.py
"""
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8011"
FRONTEND = "http://localhost:6002"
SHOTS = "/tmp/uat_sy9_shots"
VIDEO = "/tmp/uat_sy9_video"

ADMIN_USER, ADMIN_PWD = "admin", "admin123"
OP_USER, OP_PWD = "op1", "op1234"

steps = []


def step(label, ok, detail=""):
    steps.append((label, ok, detail))
    print(f"[{'OK' if ok else '!!'}] {len(steps):02d}. {label}  {detail}")


def _hdr(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


def setup_auth_and_source():
    # 1. 启用鉴权(幂等): 鉴权关时(人人超管, 无需 token)先清遗留测试用户, 再全新启用
    enabled = requests.get(f"{API}/api/v1/auth/status", timeout=10).json().get("auth_enabled")
    if not enabled:
        try:
            for u in requests.get(f"{API}/api/v1/users", timeout=10).json() or []:
                if isinstance(u, dict) and u.get("username") in (ADMIN_USER, OP_USER):
                    requests.delete(f"{API}/api/v1/users/{u.get('id')}", timeout=10)
        except Exception:
            pass
        r = requests.post(f"{API}/api/v1/auth/enable-auth", json={
            "admin_username": ADMIN_USER, "admin_password": ADMIN_PWD,
            "admin_display_name": "超级管理员"}, timeout=10)
        step("清遗留测试用户+全新启用账号鉴权(建超管)", r.status_code == 200, f"HTTP {r.status_code}")
    else:
        step("账号鉴权已启用(沿用)", True, "")

    # 2. 拿管理员 token
    lr = requests.post(f"{API}/api/v1/auth/login",
                       json={"username": ADMIN_USER, "password": ADMIN_PWD}, timeout=10)
    if lr.status_code != 200:
        step("管理员登录拿 token", False, f"HTTP {lr.status_code} {lr.text[:120]}")
        return None
    token = lr.json().get("token")
    step("管理员登录拿 token", bool(token), "")

    # 3. 建操作员(无 ack 权限; 幂等)
    cr = requests.post(f"{API}/api/v1/users", headers=_hdr(token), json={
        "username": OP_USER, "password": OP_PWD, "display_name": "产线操作员",
        "role_codes": ["operator"]}, timeout=10)
    step("建操作员账号(operator 角色, 无人工确认权限)",
         cr.status_code in (200, 201) or "已存在" in cr.text or cr.status_code == 400,
         f"HTTP {cr.status_code}")

    # 4. 起合成空闲检测源 (is_running=True → 前端轮询; 不动 project_config)
    requests.post(f"{API}/api/v1/source/detection/ack-event?channel=0", headers=_hdr(token))
    requests.post(f"{API}/api/v1/test/synthetic/packaging-reset", headers=_hdr(token))
    sr = requests.post(f"{API}/api/v1/test/synthetic/start", headers=_hdr(token),
                       json={"channel": 0}, timeout=20)
    st = requests.get(f"{API}/api/v1/source/status?channel=0", headers=_hdr(token), timeout=10).json()
    step("起合成空闲检测源(is_running 让前端轮询)",
         sr.status_code == 200 and st.get("is_running") is True,
         f"is_running={st.get('is_running')} source={st.get('source_type')}")

    # 5. 扫单开工 (让监控页有工单在途)
    requests.post(f"{API}/api/v1/packaging-flows/scan", headers=_hdr(token),
                  json={"code": "JOB1503000213", "channel_id": 0}, timeout=15)
    return token


def trigger_freeze(token):
    """后端触发缺油嘴 → 整条线定格."""
    requests.post(f"{API}/api/v1/test/synthetic/packaging-settle", headers=_hdr(token), json={
        "channel_id": 0, "cycle_id": 1, "is_good": True, "slider_count": 96,
        "probe_labels": {"放油嘴包": False, "放工单": True}}, timeout=15)


def pending_active(token):
    r = requests.get(f"{API}/api/v1/source/detection/results?channel=0",
                     headers=_hdr(token), timeout=10).json()
    return (r.get("pending_ack") or {}).get("active")


def browser_flow(token):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  record_video_dir=VIDEO,
                                  record_video_size={"width": 1600, "height": 1000})
        page = ctx.new_page()

        # 操作员登录
        page.goto(f"{FRONTEND}/#/login")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(2.5)
        page.get_by_placeholder("请输入用户名").fill(OP_USER)
        page.get_by_placeholder("请输入密码").fill(OP_PWD)
        page.screenshot(path=f"{SHOTS}/E0_login.png")
        page.locator("button:has-text('登录')").first.click()
        time.sleep(3)

        # 进监控页
        page.goto(f"{FRONTEND}/#/monitor")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(5)
        body = page.evaluate("document.body.innerText")
        step("操作员登录进入监控页", "操作员" in body or "op1" in body or "视觉" in body,
             f"身份见={'操作员' in body}")

        # 后端触发缺油嘴定格
        trigger_freeze(token)
        # 等前端轮询弹遮罩
        appeared = False
        for _ in range(12):
            time.sleep(1)
            if "需要人工确认" in page.evaluate("document.body.innerText"):
                appeared = True
                break
        page.screenshot(path=f"{SHOTS}/E1_overlay.png")
        step("缺油嘴定格 → 前端弹「需要人工确认」全屏遮罩", appeared,
             f"遮罩可见={appeared} 后端pending={pending_active(token)}")

        # 操作员点"我已确认" → 无权限 403 → 弹提权窗
        if appeared:
            page.locator("button:has-text('我已确认')").first.click()
            elev = False
            for _ in range(6):
                time.sleep(1)
                if "借管理员密码授权确认" in page.evaluate("document.body.innerText"):
                    elev = True
                    break
            page.screenshot(path=f"{SHOTS}/E2_elevate_dialog.png")
            step("操作员无权限确认被拒 → 弹「借管理员密码授权确认」窗", elev,
                 f"提权窗可见={elev}")

            # 输入管理员账密 → 授权并确认
            if elev:
                page.get_by_placeholder("管理员账号").fill(ADMIN_USER)
                page.get_by_placeholder("管理员密码").fill(ADMIN_PWD)
                page.screenshot(path=f"{SHOTS}/E3a_filled.png")
                page.locator("button:has-text('授权并确认')").first.click()
                time.sleep(3)
                page.screenshot(path=f"{SHOTS}/E3_done.png")
                step("管理员授权确认 → 定格解除(身份仍是操作员)",
                     pending_active(token) is False,
                     f"后端pending={pending_active(token)}")

        ctx.close()
        browser.close()


def cleanup(token):
    try:
        requests.post(f"{API}/api/v1/source/detection/ack-event?channel=0", headers=_hdr(token))
        requests.post(f"{API}/api/v1/test/synthetic/packaging-reset", headers=_hdr(token))
        requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", headers=_hdr(token))
        r = requests.post(f"{API}/api/v1/auth/disable-auth", headers=_hdr(token), timeout=10)
        step("收尾: 关闭账号鉴权恢复零差异默认", r.status_code == 200, f"HTTP {r.status_code}")
    except Exception as e:
        step("收尾", False, str(e))


if __name__ == "__main__":
    import os
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(VIDEO, exist_ok=True)
    token = setup_auth_and_source()
    if token:
        try:
            browser_flow(token)
        finally:
            cleanup(token)
    fails = [s for s in steps if not s[1]]
    print("\n" + "=" * 60)
    print(f"提权确认 UAT: {len(steps) - len(fails)}/{len(steps)} OK, failed: {len(fails)}")
    print(f"截图: {SHOTS}/E*.png  录像: {VIDEO}/")
    print("=" * 60)
