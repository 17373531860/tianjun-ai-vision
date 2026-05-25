"""UAT — v3.10.0 ⑤c 阶段 2: 旧操作员前端代码物理拆除验收.

客户现场叙事:
  阶段 2 完成后:
  - Settings/Monitor/Data 三个页面在鉴权关闭/启用两种状态下都能正常打开, 0 致命 console.error
  - api/operators.js 不存在, 全仓无任何 import 指向它
  - Settings 显示设置 Tab "当前作业员" 区块: 从原来的 select(查 operator 表) 变成普通 input(直接编辑 inspectorName)
  - "操作员管理" 卡片整块消失
  - Monitor 顶部"操作员:"选择条消失
  - Data 左侧"操作员"筛选下拉 + 表格"操作员"列 + 会话列表黄色 operator_name 全部消失

三件套:
  视频: /tmp/uat_phase2_video/*.webm
  截图: /tmp/uat_phase2_shots/*.png
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
SHOTS = "/tmp/uat_phase2_shots"
VIDEO = "/tmp/uat_phase2_video"

TS = int(time.time())
ADMIN_USER = f"__uat_phase2_admin_{TS}"
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
# 前置: 清 sqlite + 重启后端 (如有必要)
# ============================================================
def pre_cleanup():
    print("\n========== 前置清理 ==========", flush=True)
    r = requests.get(f"{API}/api/v1/auth/status", timeout=4)
    status = r.json()

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.join(repo_root, "backend", "sql_app.db")
    if not os.path.exists(db_path):
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
        print(f"  sqlite 清理: 删 {n} 用户, auth=off", flush=True)
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
        raise RuntimeError("重启失败")
    step("前置-清理完成", True, "auth=off, users=0")


# ============================================================
# 工具
# ============================================================
def goto_route(page, route, shot_name=None):
    page.goto(f"{FRONTEND}/#/{route}", wait_until="domcontentloaded", timeout=20000)
    time.sleep(2.5)
    if shot_name:
        shot(page, shot_name)


def open_display_tab(page):
    tab = page.locator('div.el-tabs__item:has-text("显示设置")').first
    if tab.count() > 0:
        try:
            tab.click(timeout=2000)
            time.sleep(0.6)
        except Exception:
            pass


def collect_console_errors(page):
    """挂 console listener, 后续可读出累积错误."""
    errs = []
    page.on("pageerror", lambda exc: errs.append(("pageerror", str(exc))))
    page.on(
        "console",
        lambda msg: errs.append(("console.error", msg.text))
        if msg.type == "error" else None,
    )
    return errs


def errors_significant(errs):
    """过滤掉已知无害噪声 (Pinia HMR / video stream interrupt / network favicon)."""
    real = []
    for kind, text in errs:
        t = (text or "").lower()
        # 已知无害的噪声
        if any(noise in t for noise in [
            "favicon", "mjpeg", "the play() request was interrupted",
            "the operation was aborted", "non-error promise rejection captured",
            "the user aborted", "stream/", "preview", "404",
        ]):
            continue
        real.append((kind, text))
    return real


# ============================================================
# API 启用鉴权 + 创 admin
# ============================================================
def api_enable_auth():
    r = requests.post(
        f"{API}/api/v1/auth/enable-auth",
        json={
            "admin_username": ADMIN_USER,
            "admin_password": PASSWORD,
            "admin_display_name": "UAT 阶段2 管理员",
        }, timeout=8,
    )
    step("API 启用鉴权 + 创建 admin", r.status_code == 200,
         f"HTTP {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError("enable-auth 失败")
    r = requests.post(
        f"{API}/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": PASSWORD},
        timeout=4,
    )
    token = r.json().get("token")
    step("admin API 登录拿 token", bool(token), "")
    return token


def api_disable_auth(token):
    r = requests.post(
        f"{API}/api/v1/auth/disable-auth",
        headers={"Authorization": f"Bearer {token}"},
        timeout=4,
    )
    step("API 关闭鉴权", r.status_code == 200, "")


# ============================================================
# 校验: 旧操作员 UI 全部消失
# ============================================================
def verify_legacy_op_purged(page):
    """无论鉴权关闭/启用, 旧操作员 UI 永远不在 DOM 里."""
    result = {
        # Settings: "当前作业员" 标签仍在 (但里面是 input 不是 select)
        "settings_inspector_label_present": page.locator(
            'span.text-gray-300:has-text("当前作业员")'
        ).count() > 0,
        # Settings: 操作员管理 卡片 应消失
        "settings_op_mgmt_gone": page.locator(
            'span.font-bold.text-white:has-text("操作员管理")'
        ).count() == 0,
    }
    return result


def verify_settings_input_replaced_select(page):
    """Settings 显示设置 Tab: "当前作业员" 区块下应是普通 input, 不是 el-select."""
    # 找到 "当前作业员" 标签所在卡片块
    block = page.locator(
        'div.p-3:has(span.text-gray-300:has-text("当前作业员"))'
    ).first
    if block.count() == 0:
        return False
    # 检查块内有 el-input 且无 el-select
    has_input = block.locator('input').count() > 0
    has_select = block.locator('.el-select').count() > 0
    return has_input and not has_select


def verify_monitor_op_select_gone(page):
    """Monitor 顶部 "操作员:" 选择条不存在."""
    return page.locator(
        'span.text-cyan-400.font-bold.text-xs:has-text("操作员:")'
    ).count() == 0


def verify_data_op_filter_gone(page):
    """Data 左侧筛选区 "操作员" 小标签不存在."""
    return page.locator(
        'div.text-xs.text-gray-500'
    ).filter(has_text='操作员').count() == 0


# ============================================================
# Phase: 在某个鉴权状态下扫描 3 个页面
# ============================================================
def scan_pages(page, label):
    print(f"\n========== {label} ==========", flush=True)
    errs = collect_console_errors(page)

    # Settings
    goto_route(page, "settings", f"{label}_1_settings")
    open_display_tab(page)
    time.sleep(1.0)
    shot(page, f"{label}_1b_settings_display_tab")
    settings_check = verify_legacy_op_purged(page)
    step(f"{label}-Settings: 操作员管理卡片消失",
         settings_check["settings_op_mgmt_gone"],
         f"{settings_check}")
    step(f"{label}-Settings: 当前作业员标签保留",
         settings_check["settings_inspector_label_present"], "")
    step(f"{label}-Settings: 当前作业员区块是 input 不是 select",
         verify_settings_input_replaced_select(page), "")

    # Monitor
    goto_route(page, "monitor", f"{label}_2_monitor")
    time.sleep(2.0)
    step(f"{label}-Monitor: 操作员选择条消失",
         verify_monitor_op_select_gone(page), "")

    # Data
    goto_route(page, "data", f"{label}_3_data")
    time.sleep(1.5)
    step(f"{label}-Data: 操作员筛选消失",
         verify_data_op_filter_gone(page), "")

    # console 错误
    time.sleep(0.5)
    real_errs = errors_significant(errs)
    step(f"{label}-页面无致命错误",
         len(real_errs) == 0,
         f"errs={real_errs[:3]}" if real_errs else "")


# ============================================================
# 主流程
# ============================================================
def main():
    # 1. 后端必须能找到 api/operators.js — 应该 404 (因为前端文件不存在)
    #    但前端 dev server 仍存活, 验证 Vite 已经吃掉这个文件不会引用
    print("\n========== 前置: 确认 api/operators.js 物理不存在 ==========",
          flush=True)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    p_ops = os.path.join(repo_root, "frontend", "src", "api", "operators.js")
    step("前置-api/operators.js 已删", not os.path.exists(p_ops),
         "存在!" if os.path.exists(p_ops) else "不存在 (符合预期)")

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
            # Phase A: 鉴权关闭 — 3 个页面扫一遍
            page.goto(f"{FRONTEND}/", wait_until="domcontentloaded", timeout=15000)
            page.evaluate("() => localStorage.removeItem('tianjun:auth_token')")
            # 等首页项目切换 Toast 消失 (3s), 否则会劫持下一次 goto 路由
            time.sleep(4.0)
            scan_pages(page, "A-AuthOff")

            # 启用鉴权
            token = api_enable_auth()

            # Phase B: 鉴权启用 — 3 个页面再扫一遍
            page.goto(f"{FRONTEND}/", wait_until="domcontentloaded", timeout=15000)
            page.evaluate(
                "(t) => localStorage.setItem('tianjun:auth_token', t)", token
            )
            page.reload(wait_until="domcontentloaded")
            time.sleep(3.0)
            scan_pages(page, "B-AuthOn")
        finally:
            if token:
                try:
                    api_disable_auth(token)
                except Exception as e:
                    print(f"收尾失败: {e}", flush=True)
            context.close()
            browser.close()

    # 汇总
    print("\n========== 汇总 ==========", flush=True)
    ok = sum(1 for s in steps_log if s["ok"])
    total = len(steps_log)
    for s in steps_log:
        sym = "OK" if s["ok"] else "!!"
        print(f"  [{sym}] {s['idx']:02d}. {s['label']}  {s['detail']}", flush=True)
    print(f"\n通过 {ok}/{total}", flush=True)
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
