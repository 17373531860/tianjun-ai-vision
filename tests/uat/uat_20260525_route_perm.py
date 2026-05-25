"""UAT — v3.10.0 ④b-1 路由权限隐藏菜单 + 顶栏账号登录态.

客户现场叙事:
  1. 启用鉴权 + 创建 admin → admin 登录 → 打开侧边栏看到 8 个业务菜单全可见
     + 顶栏右侧出现"账号登录态"卡片显示用户名 + 登出按钮
  2. admin 通过 API 创建 operator 账号 → 登出 admin
  3. operator 登录 → 打开侧边栏只看到 Monitor 1 个菜单
     + 顶栏卡片显示 operator 用户名
  4. 收尾: operator 登出, admin 重登 → 关闭鉴权 → 回未启用

三件套:
  视频: /tmp/uat_perm_video/*.webm
  截图: /tmp/uat_perm_shots/*.png
  日志: /tmp/uat_perm_run.log
"""
import os
import sqlite3
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# ============================================================
API = "http://127.0.0.1:8001"
FRONTEND = "http://localhost:6002"
SHOTS = "/tmp/uat_perm_shots"
VIDEO = "/tmp/uat_perm_video"
LOG = "/tmp/uat_perm_run.log"

TS = int(time.time())
ADMIN_USER = f"__uat_admin_{TS}"
OPERATOR_USER = f"__uat_op_{TS}"
PASSWORD = "Uat123456"

Path(SHOTS).mkdir(parents=True, exist_ok=True)
Path(VIDEO).mkdir(parents=True, exist_ok=True)

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    sym = "OK" if ok else "!!"
    print(f"[{sym}] {rec['idx']:02d}. {label}  {detail}")


def shot(page, name):
    idx = len([p for p in os.listdir(SHOTS) if p.endswith(".png")]) + 1
    path = os.path.join(SHOTS, f"{idx:02d}_{name}.png")
    page.screenshot(path=path, full_page=False)
    return path


# ============================================================
# 前置: sqlite 物理清理 + 重启后端
# ============================================================
def pre_cleanup():
    print("\n========== 前置清理 ==========")
    try:
        r = requests.get(f"{API}/api/v1/auth/status", timeout=4)
        status = r.json()
    except Exception as e:
        step("前置-读 status", False, str(e))
        raise

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.join(repo_root, "backend", "sql_app.db")
    if not os.path.exists(db_path):
        step("前置-定位 DB", False, db_path)
        raise RuntimeError(f"未找到 {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM session_tokens")
        conn.execute("DELETE FROM user_roles")
        n = conn.execute("DELETE FROM users").rowcount
        conn.execute("UPDATE system_configs SET value='false' WHERE key='auth.enabled'")
        conn.commit()
        print(f"  sqlite 清理: 删 {n} 个用户 + 全部 user_roles + token")
    finally:
        conn.close()

    if status.get("auth_enabled"):
        # 重启后端清缓存
        print("  auth 此前是启用状态, 重启后端清缓存...")
        os.system('pkill -f "uvicorn backend.main:app" 2>/dev/null')
        time.sleep(2)
        os.system(
            f'cd "{repo_root}" && nohup python -m uvicorn backend.main:app '
            '--host 0.0.0.0 --port 8001 > /tmp/tianjun_backend.log 2>&1 &'
        )
        for i in range(20):
            time.sleep(1)
            try:
                r = requests.get(f"{API}/api/v1/auth/status", timeout=2)
                if r.status_code == 200 and not r.json().get("auth_enabled"):
                    step("前置-重启后端", True, f"等了 {i+1}s")
                    return
            except Exception:
                continue
        step("前置-重启后端", False, "auth_enabled 未变 false")
        raise RuntimeError("重启失败")
    step("前置-清理完成", True, "auth=disabled, users=0")


# ============================================================
# 通过 API 启用鉴权 + 创建 admin + 创建 operator
# (UI 流程已被 ④a UAT 覆盖, 这里走 API 节省时间, 直接进入菜单验证主题)
# ============================================================
def api_setup_users():
    print("\n========== API 预备账号 ==========")
    # 启用 + 创建 admin
    r = requests.post(f"{API}/api/v1/auth/enable-auth", json={
        "admin_username": ADMIN_USER,
        "admin_password": PASSWORD,
        "admin_display_name": "UAT 管理员",
    }, timeout=8)
    step("启用 + 创建 admin", r.status_code == 200,
         f"HTTP {r.status_code} {r.text[:80]}")
    if r.status_code != 200:
        raise RuntimeError("enable-auth 失败")

    # 登录拿 admin token
    r = requests.post(f"{API}/api/v1/auth/login", json={
        "username": ADMIN_USER, "password": PASSWORD,
    }, timeout=4)
    admin_token = r.json().get("token")
    step("admin API 登录", bool(admin_token), f"token_len={len(admin_token or '')}")

    # 用 admin token 创建 operator
    r = requests.post(f"{API}/api/v1/users",
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={
                          "username": OPERATOR_USER,
                          "password": PASSWORD,
                          "display_name": "UAT 操作员",
                          "role_codes": ["operator"],
                      }, timeout=4)
    step("admin 创建 operator", r.status_code in (200, 201),
         f"HTTP {r.status_code} {r.text[:120]}")

    # 退出 admin token (清干净, 下一段走 UI 登录)
    requests.post(f"{API}/api/v1/auth/logout",
                  headers={"Authorization": f"Bearer {admin_token}"}, timeout=4)


# ============================================================
# Phase B: 浏览器验证菜单可见性
# ============================================================
def phase_b():
    print("\n========== Phase B: 浏览器菜单验证 ==========")

    # 业务菜单文本 (与 layout/index.vue 中 i18n / 写死标签对齐):
    # 注: /data 用 $t('menu.data')='数据管理', /settings 用 $t('menu.settings')='显示设置'
    BIZ_MENUS = [
        "检测中心",   # /monitor (i18n menu.monitor)
        "项目管理",   # /project (i18n menu.project)
        "模型仓库",   # /model   (i18n menu.model)
        "输入源设置", # /source  (写死)
        "数据管理",   # /data    (i18n menu.data)
        "MES 管理",   # /mes     (写死)
        "报警设置",   # /alarm   (写死)
        "显示设置",   # /settings (i18n menu.settings)
    ]

    def count_visible_menus(page, name_for_shot):
        """打开 sidebar 后, 数业务菜单 router-link 的可见数."""
        page.locator('header button[title="导航菜单"]').click()
        page.locator('aside nav').wait_for(state="visible", timeout=4000)
        time.sleep(0.6)
        # 在 sidebar 真打开时截图 (这才是真正的状态)
        shot(page, name_for_shot)
        visible = []
        debug_dump = []
        # 列出所有 a.nav-item 内部文本作为 debug
        all_items = page.locator('aside nav a.nav-item').all_text_contents()
        for raw in all_items:
            debug_dump.append(raw.strip())
        for name in BIZ_MENUS:
            count = page.locator(f'aside nav a.nav-item:has-text("{name}")').count()
            if count > 0:
                visible.append(name)
        print(f"  [debug] aside 内 nav-item 实际文本: {debug_dump}")
        # 关 sidebar — 点 aside 头部的 Close 按钮 (Close icon)
        page.locator('aside button.text-gray-500').first.click(
            timeout=2000, force=True,
        )
        time.sleep(0.5)
        return visible

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250)
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 900},
        )
        page = context.new_page()

        try:
            # ── B1: admin 登录 ─────────────────────────────────
            page.goto(f"{FRONTEND}/#/login", wait_until="domcontentloaded", timeout=20000)
            time.sleep(2.0)
            shot(page, "login_page")
            page.locator('input[placeholder*="用户名"]').first.fill(ADMIN_USER)
            page.locator('input[type="password"]').first.fill(PASSWORD)
            page.locator(
                'button.el-button--primary.el-button--large:has-text("登录")'
            ).first.click()
            time.sleep(3.0)
            shot(page, "admin_home_after_login")
            # 登录成功的判据: header 出现 "UAT 管理员" 卡片 (而不是 url, hash mode 不稳)
            login_ok = page.locator(
                'header div:has(span:has-text("UAT 管理员"))'
            ).count() > 0
            step("B1-admin 登录", login_ok, f"卡片={login_ok}")

            # ── B2: admin 顶栏卡片应显示用户名 + 登出按钮 ──────
            account_card = page.locator(
                'header div:has(span:has-text("UAT 管理员"))'
            ).count() > 0
            logout_btn_visible = page.locator(
                'header button:has-text("登出")'
            ).count() > 0
            step("B2-admin 顶栏账号卡片", account_card, "")
            step("B2b-admin 顶栏登出按钮", logout_btn_visible, "")

            # ── B3: admin 打开 sidebar 应看到 8 个业务菜单 ──────
            visible_admin = count_visible_menus(page, "admin_sidebar_open")
            step("B3-admin 菜单全可见 (8/8)",
                 len(visible_admin) == 8,
                 f"实见 {len(visible_admin)}: {visible_admin}")

            # ── B4: 登出 admin ────────────────────────────────
            page.locator('header button:has-text("登出")').click()
            time.sleep(2.0)
            shot(page, "after_admin_logout")
            anon_card = page.locator(
                'header div:has(span:has-text("未登录"))'
            ).count() > 0
            login_btn = page.locator('header button:has-text("登录")').count() > 0
            step("B4-顶栏变 未登录 + 登录按钮", anon_card and login_btn,
                 f"anon_card={anon_card} login_btn={login_btn}")

            # ── B5: operator 登录 ────────────────────────────
            page.locator('header button:has-text("登录")').click()
            # 等登录页表单出现 (不用 wait_for_url, hash mode 不稳)
            page.locator('input[placeholder*="用户名"]').first.wait_for(timeout=8000)
            time.sleep(0.8)
            page.locator('input[placeholder*="用户名"]').first.fill(OPERATOR_USER)
            page.locator('input[type="password"]').first.fill(PASSWORD)
            page.locator(
                'button.el-button--primary.el-button--large:has-text("登录")'
            ).first.click()
            time.sleep(3.0)
            shot(page, "operator_home_after_login")
            op_login_ok = page.locator(
                'header div:has(span:has-text("UAT 操作员"))'
            ).count() > 0
            step("B5-operator 登录", op_login_ok, f"卡片={op_login_ok}")

            # ── B6: operator 顶栏应显示 operator 用户名 ────────
            op_card = page.locator(
                'header div:has(span:has-text("UAT 操作员"))'
            ).count() > 0
            step("B6-operator 顶栏账号卡片", op_card, "")

            # ── B7: operator 打开 sidebar 应只看到 1 个 Monitor ──
            visible_op = count_visible_menus(page, "operator_sidebar_open")
            step("B7-operator 仅 Monitor 可见 (1/8)",
                 len(visible_op) == 1 and visible_op[0] == "检测中心",
                 f"实见 {len(visible_op)}: {visible_op}")

            # ── B8: 收尾 — operator 登出 + admin 重登 + 关闭鉴权 ─
            page.locator('header button:has-text("登出")').click()
            time.sleep(2.0)
            page.locator('header button:has-text("登录")').click()
            page.locator('input[placeholder*="用户名"]').first.wait_for(timeout=8000)
            time.sleep(0.8)
            page.locator('input[placeholder*="用户名"]').first.fill(ADMIN_USER)
            page.locator('input[type="password"]').first.fill(PASSWORD)
            page.locator(
                'button.el-button--primary.el-button--large:has-text("登录")'
            ).first.click()
            time.sleep(2.5)

            page.goto(f"{FRONTEND}/#/settings", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2.0)
            page.locator('[role="tab"]', has_text="账号鉴权").first.click()
            time.sleep(1.5)
            page.locator('button:has-text("关闭账号鉴权")').first.click()
            time.sleep(0.8)
            popconfirm_yes = page.locator(
                '.el-popconfirm__action button.el-button--primary'
            ).first
            if popconfirm_yes.count():
                popconfirm_yes.click()
                time.sleep(2.0)
            shot(page, "after_disable")
            disabled = page.locator('text=账号鉴权未启用').count() > 0
            step("B8-收尾关闭鉴权", disabled, "")

        except Exception as e:
            step("Phase B 异常", False, f"{type(e).__name__}: {e}")
            shot(page, "exception_state")
            raise
        finally:
            context.close()
            browser.close()


# ============================================================
def main():
    t0 = time.time()
    try:
        pre_cleanup()
        api_setup_users()
        phase_b()
    except Exception as e:
        print(f"\n!! UAT 中断: {e}")
    finally:
        elapsed = time.time() - t0
        failed = sum(1 for s in steps_log if not s["ok"])
        passed = len(steps_log) - failed
        with open(LOG, "w", encoding="utf-8") as f:
            f.write(f"UAT v3.10.0 ④b-1 路由权限菜单隐藏\n")
            f.write(f"耗时: {elapsed:.1f}s\n")
            f.write(f"通过: {passed}/{len(steps_log)}  失败: {failed}\n")
            f.write(f"admin={ADMIN_USER}  operator={OPERATOR_USER}\n")
            f.write("=" * 60 + "\n")
            for s in steps_log:
                sym = "OK" if s["ok"] else "!!"
                f.write(f"[{sym}] {s['idx']:02d}. {s['label']}  {s['detail']}\n")
            f.write("=" * 60 + "\n")
            f.write(f"failed: {failed}\n")
        videos = sorted(Path(VIDEO).glob("*.webm"))
        print("\n" + "=" * 60)
        print(f"UAT 结束: 通过 {passed}/{len(steps_log)}  失败 {failed}  耗时 {elapsed:.1f}s")
        print(f"  视频: {videos[-1] if videos else '(无)'}")
        print(f"  截图: {SHOTS}  ({len(list(Path(SHOTS).glob('*.png')))} 张)")
        print(f"  日志: {LOG}")
        print("=" * 60)


if __name__ == "__main__":
    main()
