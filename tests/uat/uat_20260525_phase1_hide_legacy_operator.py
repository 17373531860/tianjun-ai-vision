"""UAT — v3.10.0 ⑤c 阶段 1: 鉴权启用后, 旧操作员 UI 入口自动隐藏.

客户现场叙事:
  1. 鉴权关闭 (出厂默认): 进 Settings/Monitor/Data 三个页面,
     旧操作员相关 UI (3 处) 全部**可见** — 完全兼容现状
  2. 启用鉴权 + admin 登录: 重新进 Settings/Monitor/Data,
     旧操作员相关 UI (3 处) 全部**消失** — feature flag 生效
  3. 收尾: 关闭鉴权, 恢复出厂默认

三件套:
  视频: /tmp/uat_phase1_video/*.webm
  截图: /tmp/uat_phase1_shots/*.png
  日志: /tmp/uat_phase1_run.log
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# ============================================================
API = "http://127.0.0.1:8001"
FRONTEND = "http://localhost:6002"
SHOTS = "/tmp/uat_phase1_shots"
VIDEO = "/tmp/uat_phase1_video"

TS = int(time.time())
ADMIN_USER = f"__uat_phase1_admin_{TS}"
PASSWORD = "Uat123456"

Path(SHOTS).mkdir(parents=True, exist_ok=True)
Path(VIDEO).mkdir(parents=True, exist_ok=True)

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    sym = "OK" if ok else "!!"
    print(f"[{sym}] {rec['idx']:02d}. {label}  {detail}", flush=True)


def shot(page, name):
    idx = len([p for p in os.listdir(SHOTS) if p.endswith(".png")]) + 1
    path = os.path.join(SHOTS, f"{idx:02d}_{name}.png")
    page.screenshot(path=path, full_page=False)
    return path


# ============================================================
# 前置: 物理清理 sqlite 用户表 + 重置 auth 开关 + 重启后端 (如有必要)
# ============================================================
def pre_cleanup():
    print("\n========== 前置清理 ==========", flush=True)
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
        conn.execute(
            "UPDATE system_configs SET value='false' WHERE key='auth.enabled'"
        )
        conn.commit()
        print(
            f"  sqlite 清理: 删 {n} 个 user + user_roles + tokens, "
            "auth.enabled=false",
            flush=True,
        )
    finally:
        conn.close()

    if status.get("auth_enabled") or status.get("user_count", 0) > 0:
        print("  此前是启用状态, 重启后端清缓存...", flush=True)
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
# 工具: 等页面加载完成 (绕 MJPEG 长连接 networkidle 卡死)
# ============================================================
def goto_route(page, route, screenshot_name=None):
    page.goto(f"{FRONTEND}/#/{route}", wait_until="domcontentloaded", timeout=20000)
    time.sleep(2.5)
    if screenshot_name:
        shot(page, screenshot_name)


# ============================================================
# 工具: 在 Settings 页, 主动切到 "显示设置" Tab (默认就是, 但保险)
# ============================================================
def open_display_tab(page):
    tab = page.locator('div.el-tabs__item:has-text("显示设置")').first
    if tab.count() > 0:
        try:
            tab.click(timeout=2000)
            time.sleep(0.6)
        except Exception:
            pass


# ============================================================
# 校验: 3 处旧操作员 UI 是否可见
# ============================================================
def count_legacy_ui(page):
    """返回 {settings_inspector, settings_op_mgmt}. 各项 = True 表示 UI 在 DOM 里.

    v-if=false 时 Vue 根本不渲染 DOM, .count() > 0 就是真假判据,
    无需 visible filter (placeholder 不展开时也不算 visible, 会误报隐藏).
    """
    result = {}
    # Settings: "当前作业员" 块的 span 文字 (在 .text-gray-300 灰色标签里)
    result["settings_inspector"] = page.locator(
        'span.text-gray-300:has-text("当前作业员")'
    ).count() > 0

    # Settings: 操作员管理 卡片标题
    result["settings_op_mgmt"] = page.locator(
        'span.font-bold.text-white:has-text("操作员管理")'
    ).count() > 0

    return result


def count_monitor_legacy(page):
    # Monitor: "操作员:" 选择条 (精确 class+冒号)
    return page.locator(
        'span.text-cyan-400.font-bold.text-xs:has-text("操作员:")'
    ).count() > 0


def count_data_legacy(page):
    # Data: 操作员筛选 — 用左侧筛选区的小标签 "操作员"
    # (页面其它 "操作员" 在表格列头, 用 div.text-xs.text-gray-500 限定为筛选区标签样式)
    return page.locator(
        'div.text-xs.text-gray-500'
    ).filter(has_text='操作员').count() > 0


# ============================================================
# 通过 API 启用鉴权 + 创建 admin
# ============================================================
def api_enable_auth():
    r = requests.post(
        f"{API}/api/v1/auth/enable-auth",
        json={
            "admin_username": ADMIN_USER,
            "admin_password": PASSWORD,
            "admin_display_name": "UAT 阶段1 管理员",
        },
        timeout=8,
    )
    step("API 启用鉴权 + 创建 admin", r.status_code == 200,
         f"HTTP {r.status_code} {r.text[:80]}")
    if r.status_code != 200:
        raise RuntimeError("enable-auth 失败")

    r = requests.post(
        f"{API}/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": PASSWORD},
        timeout=4,
    )
    token = r.json().get("token")
    step("admin API 登录拿 token", bool(token),
         f"token_len={len(token or '')}")
    return token


def api_disable_auth_via_admin(admin_token):
    """收尾: 用 admin token 关闭鉴权 (不删账号, 让客户机回到 'auth=off') """
    r = requests.post(
        f"{API}/api/v1/auth/disable-auth",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=4,
    )
    step("API 关闭鉴权", r.status_code == 200,
         f"HTTP {r.status_code} {r.text[:80]}")


# ============================================================
# Phase A: 鉴权关闭 — 旧操作员 UI 全部可见
# ============================================================
def phase_a(page):
    print("\n========== Phase A: 鉴权关闭 — 旧 UI 应可见 ==========", flush=True)

    # 直接清前端 localStorage 里残留的 token, 避免上一轮污染
    page.goto(f"{FRONTEND}/", wait_until="domcontentloaded", timeout=15000)
    page.evaluate("() => localStorage.removeItem('tianjun:auth_token')")

    # Settings
    goto_route(page, "settings", "A1_settings")
    open_display_tab(page)
    time.sleep(1.0)
    shot(page, "A1b_settings_display_tab")
    legacy = count_legacy_ui(page)
    step("A1-Settings: 当前作业员块 可见",
         legacy["settings_inspector"], f"{legacy}")
    step("A1-Settings: 操作员管理 卡片 可见",
         legacy["settings_op_mgmt"], "")

    # Monitor
    goto_route(page, "monitor", "A2_monitor")
    time.sleep(1.5)
    mon_visible = count_monitor_legacy(page)
    step("A2-Monitor: 操作员: 选择条 可见",
         mon_visible, "")

    # Data
    goto_route(page, "data", "A3_data")
    time.sleep(1.5)
    data_visible = count_data_legacy(page)
    step("A3-Data: 操作员 筛选 可见",
         data_visible, "")


# ============================================================
# Phase B: 鉴权启用 + admin 登录 — 旧 UI 全部消失
# ============================================================
def phase_b(page, token):
    print("\n========== Phase B: 鉴权启用 — 旧 UI 应隐藏 ==========", flush=True)

    # 注入 admin token, 重载强制 store 重新拉 /auth/status + /auth/me
    page.goto(f"{FRONTEND}/", wait_until="domcontentloaded", timeout=15000)
    page.evaluate(
        "(t) => localStorage.setItem('tianjun:auth_token', t)", token
    )
    page.reload(wait_until="domcontentloaded")
    time.sleep(3.0)
    shot(page, "B0_after_token_inject")

    # 校验前端 Navbar 已经显示 admin 用户名 (说明 store 拿到 token + authEnabled=true)
    navbar_label = page.locator(
        'header div:has(span:has-text("UAT 阶段1 管理员"))'
    ).count() > 0
    step("B0-Navbar 显示 admin 名字", navbar_label,
         "鉴权启用 + token 注入生效")

    # Settings
    goto_route(page, "settings", "B1_settings")
    open_display_tab(page)
    time.sleep(1.0)
    shot(page, "B1b_settings_display_tab")
    legacy = count_legacy_ui(page)
    step("B1-Settings: 当前作业员块 隐藏",
         not legacy["settings_inspector"], f"{legacy}")
    step("B1-Settings: 操作员管理 卡片 隐藏",
         not legacy["settings_op_mgmt"], "")

    # Monitor
    goto_route(page, "monitor", "B2_monitor")
    time.sleep(2.0)
    mon_visible = count_monitor_legacy(page)
    step("B2-Monitor: 操作员: 选择条 隐藏",
         not mon_visible, "")

    # Data
    goto_route(page, "data", "B3_data")
    time.sleep(1.5)
    data_visible = count_data_legacy(page)
    step("B3-Data: 操作员 筛选 隐藏",
         not data_visible, "")


# ============================================================
# 主流程
# ============================================================
def main():
    pre_cleanup()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 900},
        )
        page = context.new_page()
        token = None

        try:
            # Phase A: 鉴权关闭, 旧 UI 应全可见
            phase_a(page)

            # 启用鉴权
            token = api_enable_auth()

            # Phase B: 鉴权启用, 旧 UI 应全隐藏
            phase_b(page, token)
        finally:
            if token:
                try:
                    api_disable_auth_via_admin(token)
                except Exception as e:
                    print(f"  收尾关闭鉴权失败: {e}", flush=True)
            context.close()
            browser.close()

    # 汇总
    print("\n========== 汇总 ==========", flush=True)
    ok = sum(1 for s in steps_log if s["ok"])
    total = len(steps_log)
    for s in steps_log:
        sym = "OK" if s["ok"] else "!!"
        print(f"  [{sym}] {s['idx']:02d}. {s['label']}  {s['detail']}",
              flush=True)
    print(f"\n通过 {ok}/{total}", flush=True)
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
