"""UAT — 阶段 6: Navbar / BottomBar 身份显示随鉴权状态切换.

验证 3 个场景 × 2 个位置 (顶栏 / 底栏):

  A. 鉴权关闭 + inspectorName 已设
     - Navbar 显示 "作业员: <inspectorName>" (cyan-300)
     - 无角色徽章, 无登录/登出按钮
     - BottomBar 同步显示 inspectorName

  B. 鉴权启用 + 已登录 admin
     - Navbar 显示 admin 的 display_name + "管理员" 红徽章 (emerald-300)
     - 显示登出按钮, 无登录按钮
     - BottomBar 显示 display_name + 角色徽章

  C. 鉴权启用 + 未登录 (登出后)
     - Navbar 显示 "操作员（未登录）" (amber-300)
     - 显示登录按钮, 无登出按钮, 无角色徽章
     - BottomBar 显示 "操作员（未登录）"

跑法: python -u tests/uat/uat_20260525_phase6_navbar_identity.py
"""
import os
import sqlite3
import sys
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8001"
FRONTEND = "http://127.0.0.1:6002"
TS = int(time.time())
ADMIN_USER = f"__uat_p6_admin_{TS}"
ADMIN_DISPLAY = "P6 管理员"
PASSWORD = "Uat123456"
INSPECTOR_NAME = "张师傅_P6"
LOG = "/tmp/uat_p6_run.log"
SHOT_DIR = "/tmp/uat_p6_shots"
os.makedirs(SHOT_DIR, exist_ok=True)

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    sym = "OK" if ok else "!!"
    print(f"[{sym}] {rec['idx']:02d}. {label}  {detail}")


def shot(page, name):
    path = f"{SHOT_DIR}/{len(steps_log):02d}_{name}.png"
    try:
        page.screenshot(path=path, full_page=False)
    except Exception:
        pass


def pre_cleanup():
    print("\n========== 前置清理 ==========")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.join(repo_root, "backend", "sql_app.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM session_tokens")
        conn.execute("DELETE FROM user_roles")
        conn.execute("DELETE FROM users")
        conn.execute("UPDATE system_configs SET value='false' WHERE key='auth.enabled'")
        conn.commit()
    finally:
        conn.close()

    # 重启后端使 auth_enabled 等持久状态重新加载
    r = requests.get(f"{API}/api/v1/auth/status", timeout=4)
    needs_restart = r.json().get("auth_enabled")
    if needs_restart:
        os.system('pkill -f "uvicorn backend.main:app" 2>/dev/null')
        time.sleep(2)
        os.system(
            f'cd "{repo_root}" && setsid -f python -u -m uvicorn backend.main:app '
            f'--host 0.0.0.0 --port 8001 > /tmp/tianjun_backend.log 2>&1 < /dev/null'
        )
        for _ in range(25):
            time.sleep(1)
            try:
                r = requests.get(f"{API}/api/v1/auth/status", timeout=2)
                if r.status_code == 200 and not r.json().get("auth_enabled"):
                    break
            except Exception:
                continue
    step("前置-清理完成", True, "auth=disabled")


def inject_inspector_name(page, name):
    """前端 display_settings 在 localStorage, 注入完整结构, 含 inspectorName + navbar.inspector=true."""
    payload = {
        "inspectorName": name,
        "deviceNumber": "P6-TEST",
        "navbar": {
            "inspector": True,
            "deviceId": True,
            "brandName": True,
            "appName": True,
            "projectSelector": True,
            "mode": True,
            "status": True,
            "runtime": True,
            "realtime": True,
        },
    }
    import json
    page.evaluate(
        f"localStorage.setItem('display_settings', {json.dumps(json.dumps(payload))})"
    )


def get_identity_text(page):
    """从 Navbar 读身份名 (data-testid 定位)."""
    loc = page.locator('[data-testid="navbar-identity-name"]')
    return loc.inner_text().strip() if loc.count() else ""


def get_role_badge_text(page):
    loc = page.locator('[data-testid="navbar-role-badge"]')
    return loc.inner_text().strip() if loc.count() else ""


def has_login_btn(page):
    return page.locator('[data-testid="navbar-login-btn"]').count() > 0


def has_logout_btn(page):
    return page.locator('[data-testid="navbar-logout-btn"]').count() > 0


def get_bottombar_text(page):
    loc = page.locator('[data-testid="bottombar-identity-name"]')
    return loc.inner_text().strip() if loc.count() else ""


def get_bottombar_role(page):
    loc = page.locator('[data-testid="bottombar-role-badge"]')
    return loc.inner_text().strip() if loc.count() else ""


# ============================================================
# 场景 A: 鉴权关闭, 显示 inspectorName 字符串
# ============================================================
def scene_a(page):
    print("\n========== 场景 A: 鉴权关闭 ==========")
    # 先打开页, 让 localStorage 可用, 再注入 display_settings, 再 reload
    page.goto(FRONTEND, wait_until="domcontentloaded")
    time.sleep(1.5)
    inject_inspector_name(page, INSPECTOR_NAME)
    page.reload(wait_until="domcontentloaded")
    time.sleep(3.5)
    shot(page, "scene_a")

    name = get_identity_text(page)
    badge = get_role_badge_text(page)
    has_login = has_login_btn(page)
    has_logout = has_logout_btn(page)
    bottom = get_bottombar_text(page)

    step("A-Navbar 显示 inspectorName", name == INSPECTOR_NAME,
         f"got='{name}', expect='{INSPECTOR_NAME}'")
    step("A-Navbar 无角色徽章", badge == "",
         f"badge='{badge}'")
    step("A-Navbar 无登录按钮 (鉴权关)", not has_login,
         f"has_login={has_login}")
    step("A-Navbar 无登出按钮 (鉴权关)", not has_logout,
         f"has_logout={has_logout}")
    step("A-BottomBar 显示 inspectorName", bottom == INSPECTOR_NAME,
         f"got='{bottom}'")


# ============================================================
# 场景 B: 启用鉴权 + 创建 admin + 登录
# ============================================================
def scene_b(page):
    print("\n========== 场景 B: 启用鉴权 + 登录 admin ==========")

    # 1) 通过 API 启用鉴权 + 建 admin (避开 UI 走对话框的复杂步骤)
    r = requests.post(
        f"{API}/api/v1/auth/enable-auth",
        json={
            "admin_username": ADMIN_USER,
            "admin_password": PASSWORD,
            "admin_display_name": ADMIN_DISPLAY,
        },
        timeout=8,
    )
    step("B-API 启用鉴权 + 建 admin", r.status_code == 200,
         f"status={r.status_code}, body={r.text[:100]}")
    if r.status_code != 200:
        return

    # 2) 登录拿 token
    r = requests.post(
        f"{API}/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": PASSWORD},
        timeout=4,
    )
    token = r.json().get("token") if r.status_code == 200 else None
    step("B-API 登录 admin 拿 token", bool(token),
         f"status={r.status_code}, token_len={len(token or '')}")
    if not token:
        return

    # 3) 注入 token + 强制刷新 (触发 authStore.init)
    page.evaluate(f"localStorage.setItem('tianjun:auth_token', '{token}')")
    page.reload(wait_until="domcontentloaded")
    time.sleep(4)
    shot(page, "scene_b")

    name = get_identity_text(page)
    badge = get_role_badge_text(page)
    has_login = has_login_btn(page)
    has_logout = has_logout_btn(page)
    bottom = get_bottombar_text(page)
    bottom_badge = get_bottombar_role(page)

    # 注意: 已登录后, "作业员"位置内容应该是 admin 的 display_name (覆盖 inspectorName)
    step("B-Navbar 显示登录用户名 (覆盖 inspectorName)",
         name == ADMIN_DISPLAY,
         f"got='{name}', expect='{ADMIN_DISPLAY}', (inspectorName 是 '{INSPECTOR_NAME}', 不应再显示)")
    step("B-Navbar 显示'管理员'角色徽章", badge == "管理员",
         f"badge='{badge}'")
    step("B-Navbar 显示登出按钮 (鉴权开+已登录)", has_logout,
         f"has_logout={has_logout}")
    step("B-Navbar 不显示登录按钮", not has_login,
         f"has_login={has_login}")
    step("B-BottomBar 显示登录用户名", bottom == ADMIN_DISPLAY,
         f"got='{bottom}'")
    step("B-BottomBar 显示'管理员'徽章", bottom_badge == "管理员",
         f"got='{bottom_badge}'")


# ============================================================
# 场景 C: 启用鉴权 + 登出 (回匿名态)
# ============================================================
def scene_c(page):
    print("\n========== 场景 C: 启用鉴权 + 登出 ==========")

    # API 登出 + 清 token 模拟用户登出
    token = page.evaluate("localStorage.getItem('tianjun:auth_token')")
    if token:
        try:
            requests.post(
                f"{API}/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
                timeout=4,
            )
        except Exception:
            pass
    page.evaluate("localStorage.removeItem('tianjun:auth_token')")
    page.reload(wait_until="domcontentloaded")
    time.sleep(4)
    shot(page, "scene_c")

    name = get_identity_text(page)
    badge = get_role_badge_text(page)
    has_login = has_login_btn(page)
    has_logout = has_logout_btn(page)
    bottom = get_bottombar_text(page)

    step("C-Navbar 显示'操作员（未登录）'",
         name == "操作员（未登录）",
         f"got='{name}'")
    step("C-Navbar 无角色徽章 (未登录)", badge == "",
         f"badge='{badge}'")
    step("C-Navbar 显示登录按钮", has_login,
         f"has_login={has_login}")
    step("C-Navbar 不显示登出按钮", not has_logout,
         f"has_logout={has_logout}")
    step("C-BottomBar 显示'操作员（未登录）'",
         bottom == "操作员（未登录）",
         f"got='{bottom}'")


# ============================================================
# 收尾: 关闭 auth, 让环境恢复原状
# ============================================================
def teardown(page):
    print("\n========== 收尾 ==========")
    # 重新登录 admin 才能关 auth (disable-auth 需要 system.auth.manage)
    r = requests.post(
        f"{API}/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": PASSWORD},
        timeout=4,
    )
    token = r.json().get("token") if r.status_code == 200 else None
    if token:
        try:
            r = requests.post(
                f"{API}/api/v1/auth/disable-auth",
                headers={"Authorization": f"Bearer {token}"},
                timeout=4,
            )
            step("收尾-关闭 auth", r.status_code == 200,
                 f"status={r.status_code}")
        except Exception as e:
            step("收尾-关闭 auth", False, str(e)[:80])

    # 删除测试用户, 不污染数据库
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.join(repo_root, "backend", "sql_app.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM session_tokens")
        conn.execute("DELETE FROM user_roles WHERE user_id IN (SELECT id FROM users WHERE username=?)",
                     (ADMIN_USER,))
        conn.execute("DELETE FROM users WHERE username=?", (ADMIN_USER,))
        conn.commit()
    finally:
        conn.close()


def main():
    pre_cleanup()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 900})
        page = context.new_page()
        try:
            scene_a(page)
            scene_b(page)
            scene_c(page)
        finally:
            try:
                teardown(page)
            except Exception as e:
                step("收尾出错", False, str(e)[:80])
            context.close()
            browser.close()

    # ============================================================
    # 总结
    # ============================================================
    total = len(steps_log)
    failed = [s for s in steps_log if not s["ok"]]
    passed = total - len(failed)

    print("\n" + "=" * 60)
    print(f"阶段 6 UAT 总览: {passed}/{total} 通过, {len(failed)} 失败")
    if failed:
        print("\n失败项:")
        for s in failed:
            print(f"  !! {s['idx']:02d}. {s['label']}  →  {s['detail']}")
    print("=" * 60)
    print(f"截图目录: {SHOT_DIR}")

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
