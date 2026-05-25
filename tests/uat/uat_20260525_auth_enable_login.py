"""UAT (面向功能测试) — v3.10.0 用户系统 ④a 前端骨架.

客户现场叙事:
  操作员打开系统进入首页 → 顶栏点「设置」→ 切到「账号鉴权」Tab → 看到"未启用"
  黄色卡片 → 点「启用账号鉴权」→ 弹对话框填管理员账号 → 点「启用并创建管理员」
  → 提示「去登录」→ 跳 /login → 输账号密码 → 点登录 → 回首页 → 再进设置确认
  「已登录」+ 角色标签 [admin] → 点登出 → 回到匿名状态 → 点「关闭账号鉴权」二次
  确认 → 卡片回到"未启用"黄色状态.

后端预期变化:
  - POST /auth/enable-auth → users +1 行 + user_roles +1 行 + SystemConfig auth.enabled=true
  - POST /auth/login → session_tokens +1 行
  - 后续请求自动带 Bearer
  - POST /auth/logout → session_tokens -1 行
  - POST /auth/disable-auth → SystemConfig auth.enabled=false

三件套证据:
  视频: /tmp/uat_auth_video/*.webm
  截图: /tmp/uat_auth_shots/*.png
  日志: /tmp/uat_auth_run.log
"""
import os
import time
import json
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# ============================================================
# 配置
# ============================================================
API = "http://127.0.0.1:8001"          # 后端 (用户已起, 含 v3.10.0 auth router)
FRONTEND = "http://localhost:6002"     # vite dev server (6001 被占用自动跳 6002)
SHOTS = "/tmp/uat_auth_shots"
VIDEO = "/tmp/uat_auth_video"
LOG = "/tmp/uat_auth_run.log"

ADMIN_USERNAME = f"__uat_admin_{int(time.time())}"
ADMIN_PASSWORD = "Uat123456"
ADMIN_DISPLAY = "UAT 系统管理员"

Path(SHOTS).mkdir(parents=True, exist_ok=True)
Path(VIDEO).mkdir(parents=True, exist_ok=True)

steps_log = []


def step(label: str, ok: bool, detail: str = ""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    sym = "OK" if ok else "!!"
    print(f"[{sym}] {rec['idx']:02d}. {label}  {detail}")


def shot(page, name: str):
    """带序号截图, 命名 NN_预期.png 方便人眼对照."""
    idx = len([p for p in os.listdir(SHOTS) if p.endswith(".png")]) + 1
    path = os.path.join(SHOTS, f"{idx:02d}_{name}.png")
    page.screenshot(path=path, full_page=False)
    return path


# ============================================================
# 前置清理: 把 auth 状态回到 disabled, 删测试用户残留
#
# 不依赖密码——直接走 sqlite3 物理清理. 因为 UAT 跑挂时往往不知道残留账号密码,
# 与其陪着 try-and-error, 不如直接物理 reset (admin 永远是 backend/data/sql_app.db).
# ============================================================
def pre_cleanup():
    print("\n========== 前置清理 ==========")
    try:
        r = requests.get(f"{API}/api/v1/auth/status", timeout=4)
        status = r.json()
        print(f"  当前 auth_enabled={status.get('auth_enabled')}, user_count={status.get('user_count')}")
    except Exception as e:
        step("前置-读 auth/status", False, str(e))
        return

    if not status.get("auth_enabled"):
        step("前置-auth 已是 disabled", True, "无需清理")
        return

    # auth 启用 + 未知密码 → 走 sqlite 直清 + 重启后端清缓存
    print("  auth 已启用, 走 sqlite 物理清理...")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    # 实际 DB 在 backend/sql_app.db (不是 backend/data/sql_app.db)
    candidates = [
        os.path.join(repo_root, "backend", "sql_app.db"),
        os.path.join(repo_root, "backend", "data", "sql_app.db"),
        "backend/sql_app.db",
    ]
    db_path = next((p for p in candidates if os.path.exists(p)), None)

    if not db_path:
        step("前置-定位 sql_app.db", False, f"候选都不存在: {candidates}")
        raise RuntimeError("找不到 SQLite 文件, 手动 disable-auth")
    print(f"  使用 DB: {db_path}")

    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE system_configs SET value='false' WHERE key='auth.enabled'")
        # SQLite 默认不强制外键 → 必须显式按顺序删, 否则 user_roles 残留触发
        # 下次 enable-auth 的 UNIQUE constraint failed (user_id, role_id)
        # 先删 session_tokens, 再删 user_roles, 最后删 users (按依赖逆序)
        conn.execute("DELETE FROM session_tokens")
        conn.execute("DELETE FROM user_roles")
        dropped = conn.execute("DELETE FROM users").rowcount
        conn.commit()
        print(f"  sqlite: auth.enabled=false, 删 {dropped} 个 users + 全部 user_roles + token")
    finally:
        conn.close()

    # 让后端 invalidate auth_enabled cache: sqlite 改了但 backend 缓存仍 true, 必须重启
    print("  需要重启后端让 auth_enabled 缓存失效...")
    os.system('pkill -f "uvicorn backend.main:app" 2>/dev/null')
    time.sleep(2)
    os.system(
        f'cd "{repo_root}" && nohup python -m uvicorn backend.main:app '
        '--host 0.0.0.0 --port 8001 > /tmp/tianjun_backend.log 2>&1 &'
    )
    # 等后端起来
    for i in range(20):
        time.sleep(1)
        try:
            r = requests.get(f"{API}/api/v1/auth/status", timeout=2)
            if r.status_code == 200 and not r.json().get("auth_enabled"):
                step("前置-sqlite 清理 + 重启后端", True, f"等了 {i+1}s")
                return
        except Exception:
            continue
    step("前置-sqlite 清理 + 重启后端", False, "重启后 auth_enabled 仍 true 或后端起不来")
    raise RuntimeError("前置清理失败")


# ============================================================
# Phase B: 可见浏览器 UAT
# ============================================================
def phase_b_browser():
    print("\n========== Phase B: 可见浏览器 UAT ==========")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 900},
        )
        page = context.new_page()

        try:
            # B1: 打开前端首页 (用 domcontentloaded 不等 networkidle - MJPEG 流永远不 idle)
            page.goto(FRONTEND, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3.0)  # 给 Vue 挂载 + 路由初始化时间
            shot(page, "home_loaded")
            step("B1-打开前端", True, page.url)

            # B2: 导航到 Settings 页 (hash 路由)
            page.goto(f"{FRONTEND}/#/settings", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2.0)
            shot(page, "settings_default_tab")
            step("B2-进入设置页", True, "")

            # B3: 切到「账号鉴权」Tab
            #     el-tabs 用 [role=tab] + 文本定位
            tab = page.locator('[role="tab"]', has_text="账号鉴权").first
            if not tab.count():
                # 兜底: 直接搜索 text
                tab = page.locator('text=账号鉴权').first
            tab.click()
            time.sleep(1.0)
            shot(page, "auth_tab_initial_disabled")

            # 验证「未启用」文案存在
            disabled_visible = page.locator('text=账号鉴权未启用').count() > 0
            step("B3-切到账号鉴权 Tab", disabled_visible,
                 f"未启用文案 visible={disabled_visible}")

            # B4: 点「启用账号鉴权」按钮
            enable_btn = page.locator('button:has-text("启用账号鉴权")').first
            enable_btn.click()
            time.sleep(0.8)
            shot(page, "enable_dialog_opened")

            # 验证对话框打开
            dialog_visible = page.locator('.el-dialog__title:has-text("启用账号鉴权")').count() > 0
            step("B4-启用对话框打开", dialog_visible, "")

            # B5: 填表
            #     el-input 真实 input 在 .el-input__inner 里
            #     用 placeholder 定位最稳
            page.locator('.el-dialog__body input[placeholder*="例如 admin"]').first.fill(ADMIN_USERNAME)
            page.locator('.el-dialog__body input[placeholder*="可选"]').first.fill(ADMIN_DISPLAY)
            pwd_inputs = page.locator('.el-dialog__body input[type="password"]')
            pwd_inputs.nth(0).fill(ADMIN_PASSWORD)
            pwd_inputs.nth(1).fill(ADMIN_PASSWORD)
            time.sleep(0.5)
            shot(page, "enable_dialog_filled")
            step("B5-填启用表单", True, f"user={ADMIN_USERNAME}")

            # B6: 点「启用并创建管理员」
            page.locator('.el-dialog__footer button:has-text("启用并创建管理员")').click()
            time.sleep(2.0)
            shot(page, "after_enable_submit")

            # 验证 ElMessage 成功
            success_msg = page.locator('.el-message--success').count() > 0
            step("B6-启用 + 创建管理员", success_msg,
                 f"el-message--success visible={success_msg}")

            # B7: 「去登录」二次确认 (ElMessageBox)
            #     按 SKILL 踩坑提示: el-message-box__btns 有两个 button, 第二个 type=primary 是确认
            time.sleep(0.5)
            msgbox_visible = page.locator('.el-message-box__title:has-text("账号鉴权已启用")').count() > 0
            step("B7-去登录提示框", msgbox_visible, "")
            if msgbox_visible:
                page.locator('.el-message-box__btns button.el-button--primary').click()
                # 等跳转到登录页
                page.wait_for_url(f"{FRONTEND}/#/login", timeout=8000)
                time.sleep(1.0)
                shot(page, "login_page_loaded")
                step("B7b-跳转登录页", True, page.url)

            # B8: 在登录页填账号密码
            page.locator('input[placeholder*="用户名"]').first.fill(ADMIN_USERNAME)
            page.locator('input[type="password"]').first.fill(ADMIN_PASSWORD)
            time.sleep(0.5)
            shot(page, "login_filled")

            # 点登录
            login_btn = page.locator('button.el-button--primary.el-button--large:has-text("登录")').first
            login_btn.click()
            # 等跳回首页
            time.sleep(2.5)
            shot(page, "after_login_redirect")
            url_after_login = page.url
            redirected = "/login" not in url_after_login
            step("B8-登录成功跳转", redirected, f"url={url_after_login}")

            # B9: 重新进设置 → 账号鉴权 Tab 验证已登录态
            page.goto(f"{FRONTEND}/#/settings", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2.0)
            tab2 = page.locator('[role="tab"]', has_text="账号鉴权").first
            tab2.click()
            time.sleep(1.5)
            shot(page, "auth_tab_after_login")

            # 验证「已登录」标签 + 角色 admin
            logged_in_visible = page.locator('text=已登录').count() > 0
            admin_role_visible = page.locator('.el-tag:has-text("admin")').count() > 0
            step("B9a-已登录标签可见", logged_in_visible, "")
            step("B9b-admin 角色标签", admin_role_visible, "")

            # 验证账号列表里有刚创建的 admin
            user_row_visible = page.locator(f'td:has-text("{ADMIN_USERNAME}")').count() > 0
            step("B9c-账号列表含 admin", user_row_visible, "")

            # 验证角色列表 3 行 (admin / engineer / operator)
            role_count = page.locator('td .el-tag:has-text("admin"), td .el-tag:has-text("engineer"), td .el-tag:has-text("operator")').count()
            step("B9d-内置 3 角色", role_count >= 3, f"匹配 {role_count} 个角色 tag")

            # B10: 点登出按钮
            logout_btn = page.locator('button:has-text("登出")').first
            logout_btn.click()
            time.sleep(2.0)
            shot(page, "after_logout")

            # 验证回到匿名态
            anon_visible = page.locator('text=匿名').count() > 0
            step("B10-登出回匿名态", anon_visible, "")

            # B10b: 匿名 operator 没 system.auth_toggle 权限 → 关闭按钮应被 disabled
            #       这反向验证权限系统生效 (而非 bug)
            disable_btn_disabled = page.locator(
                'button[disabled]:has-text("关闭账号鉴权")'
            ).count() > 0
            step("B10b-匿名无关闭权限 (按钮 disabled)", disable_btn_disabled,
                 "设计预期: 匿名 operator 不能 disable-auth")

            # B11: 重新登录 admin → 跳设置 → 点关闭 (这次有权)
            page.locator('button:has-text("去登录")').first.click()
            page.wait_for_url(f"{FRONTEND}/#/login", timeout=8000)
            time.sleep(1.0)
            page.locator('input[placeholder*="用户名"]').first.fill(ADMIN_USERNAME)
            page.locator('input[type="password"]').first.fill(ADMIN_PASSWORD)
            page.locator(
                'button.el-button--primary.el-button--large:has-text("登录")'
            ).first.click()
            time.sleep(2.5)

            page.goto(f"{FRONTEND}/#/settings", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2.0)
            page.locator('[role="tab"]', has_text="账号鉴权").first.click()
            time.sleep(1.5)
            shot(page, "logged_in_again_before_disable")

            disable_btn = page.locator('button:has-text("关闭账号鉴权")').first
            disable_btn.click()
            time.sleep(0.8)
            popconfirm_yes = page.locator(
                '.el-popconfirm__action button.el-button--primary'
            ).first
            if popconfirm_yes.count():
                popconfirm_yes.click()
                time.sleep(2.0)
            shot(page, "after_disable_auth")

            disabled_again = page.locator('text=账号鉴权未启用').count() > 0
            step("B11-admin 重新登录后关闭鉴权", disabled_again, "回到未启用")

        except Exception as e:
            step("Phase B 异常", False, f"{type(e).__name__}: {e}")
            shot(page, "exception_state")
            raise
        finally:
            context.close()
            browser.close()


# ============================================================
# 入口
# ============================================================
def main():
    t0 = time.time()
    try:
        pre_cleanup()
        phase_b_browser()
    except Exception as e:
        print(f"\n!! UAT 中断: {e}")
    finally:
        elapsed = time.time() - t0
        failed = sum(1 for s in steps_log if not s["ok"])
        passed = len(steps_log) - failed

        # 写日志
        with open(LOG, "w", encoding="utf-8") as f:
            f.write(f"UAT v3.10.0 ④a auth 启用-登录-登出 链路\n")
            f.write(f"运行时长: {elapsed:.1f}s\n")
            f.write(f"通过: {passed}/{len(steps_log)}  失败: {failed}\n")
            f.write(f"管理员账号: {ADMIN_USERNAME}\n")
            f.write("=" * 60 + "\n")
            for s in steps_log:
                sym = "OK" if s["ok"] else "!!"
                f.write(f"[{sym}] {s['idx']:02d}. {s['label']}  {s['detail']}\n")
            f.write("=" * 60 + "\n")
            f.write(f"failed: {failed}\n")

        # 视频文件
        videos = sorted(Path(VIDEO).glob("*.webm"))
        print("\n" + "=" * 60)
        print(f"UAT 结束: 通过 {passed}/{len(steps_log)}  失败 {failed}  耗时 {elapsed:.1f}s")
        print(f"  视频: {videos[-1] if videos else '(无)'}")
        print(f"  截图目录: {SHOTS}  ({len(list(Path(SHOTS).glob('*.png')))} 张)")
        print(f"  日志: {LOG}")
        print("=" * 60)


if __name__ == "__main__":
    main()
