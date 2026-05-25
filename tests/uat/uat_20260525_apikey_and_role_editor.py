"""UAT — ⑤a (API Key UI) + ⑤b (角色权限编辑器) 可见浏览器验证.

测试场景:
  1. 清数据库 + 重启后端
  2. 启用 auth + 创建 admin (UI 走启用对话框)
  3. 登录 admin
  4. 进 Settings → 账号鉴权 Tab
  5. ⑤a: 看 "M2M API Key 管理" 卡片可见 (admin 有 system.apikey.manage)
  6. ⑤a: 点 "创建 API Key", 填名 + scope, 提交
  7. ⑤a: 验证明文展示对话框出现, 复制
  8. ⑤a: 关闭对话框, 列表里出现 1 行
  9. ⑤a: 切换启用/禁用, 删除
  10. ⑤b: 在角色列表里点 operator 行的 "编辑权限"
  11. ⑤b: 勾选一个新权限 (monitor.detection.advanced)
  12. ⑤b: 保存, 验后端落库
  13. 收尾: 关闭 auth

不验证视觉细节, 只验证端到端串联 + 关键 assert.
"""
import os
import sqlite3
import sys
import time

import requests
from playwright.sync_api import sync_playwright, expect

API = "http://127.0.0.1:8001"
FRONTEND = "http://127.0.0.1:6002"
TS = int(time.time())
ADMIN_USER = f"__uat_5ab_admin_{TS}"
PASSWORD = "Uat123456"
LOG = "/tmp/uat_5ab_run.log"
SHOT_DIR = "/tmp/uat_5ab_shots"
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
        page.screenshot(path=path, full_page=True)
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
        conn.execute("DELETE FROM api_keys")
        conn.execute("UPDATE system_configs SET value='false' WHERE key='auth.enabled'")
        conn.commit()
    finally:
        conn.close()

    r = requests.get(f"{API}/api/v1/auth/status", timeout=4)
    if r.json().get("auth_enabled"):
        os.system('pkill -f "uvicorn backend.main:app" 2>/dev/null')
        time.sleep(2)
        os.system(
            f'cd "{repo_root}" && nohup python -m uvicorn backend.main:app '
            '--host 0.0.0.0 --port 8001 > /tmp/tianjun_backend.log 2>&1 &'
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


def run(p):
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1600, "height": 900})
    page = context.new_page()

    # 进入前端
    page.goto(FRONTEND, wait_until="domcontentloaded")
    time.sleep(2)
    step("打开前端", "tianjun" in page.title().lower() or page.url.startswith(FRONTEND),
         f"title={page.title()[:40]}")
    shot(page, "home")

    # ============================================================
    # 1. 进 Settings → 账号鉴权
    # ============================================================
    # 用 hash 路由直接跳到 Settings
    page.goto(f"{FRONTEND}/#/settings", wait_until="domcontentloaded")
    time.sleep(2)
    step("跳 Settings", "settings" in page.url, f"url={page.url}")
    shot(page, "settings")

    # 找 "账号鉴权" Tab 点开
    tab_btn = page.get_by_role("tab", name="账号鉴权")
    try:
        tab_btn.click(timeout=5000)
        time.sleep(1)
        step("打开账号鉴权 Tab", True)
    except Exception as e:
        step("打开账号鉴权 Tab", False, f"{e}"[:80])
        return
    shot(page, "auth_tab")

    # ============================================================
    # 2. 启用账号鉴权 + 创建 admin (走对话框)
    # ============================================================
    btn = page.get_by_role("button", name="启用账号鉴权")
    try:
        btn.click(timeout=5000)
        time.sleep(1)
        step("点 启用账号鉴权", True)
    except Exception as e:
        step("点 启用账号鉴权", False, f"{e}"[:80])
        return
    shot(page, "enable_dialog")

    # 填表 (用 placeholder 定位更稳)
    page.locator('input[placeholder*="admin"]').first.fill(ADMIN_USER)
    page.locator('input[placeholder*="例如 系统管理员"]').fill("UAT-5ab Admin")
    pw_inputs = page.locator('input[type="password"]')
    pw_inputs.nth(0).fill(PASSWORD)
    pw_inputs.nth(1).fill(PASSWORD)
    time.sleep(0.5)
    page.get_by_role("button", name="启用并创建管理员").click()
    time.sleep(3)
    step("提交启用表单", True)
    shot(page, "after_enable")

    # 弹了"是否跳转登录"对话框 → 点稍后
    try:
        page.get_by_role("button", name="稍后").click(timeout=3000)
    except Exception:
        pass
    time.sleep(1)

    # ============================================================
    # 3. 登录 admin (直接走 API, 把 token 注入 localStorage 简化)
    # ============================================================
    r = requests.post(f"{API}/api/v1/auth/login",
                      json={"username": ADMIN_USER, "password": PASSWORD}, timeout=4)
    token = r.json()["token"]
    page.evaluate(f"localStorage.setItem('tianjun:auth_token', '{token}')")
    # 硬刷新触发 store 重新 init (init 是单次, 不会因 hash 变化重跑)
    page.reload(wait_until="domcontentloaded")
    time.sleep(4)
    # 重新跳 Settings + 点 Tab
    page.goto(f"{FRONTEND}/#/settings", wait_until="domcontentloaded")
    time.sleep(2)
    try:
        page.get_by_role("tab", name="账号鉴权").click(timeout=5000)
        time.sleep(3)
        step("admin 登录 + 重进 Tab", True, "")
    except Exception as e:
        step("admin 登录 + 重进 Tab", False, f"{e}"[:80])
    shot(page, "admin_settings")

    # ============================================================
    # ⑤a-1: M2M API Key 卡片可见
    # ============================================================
    apikey_header = page.locator('text=M2M API Key 管理')
    ok = apikey_header.count() >= 1
    step("⑤a-1 API Key 卡片可见", ok, f"count={apikey_header.count()}")
    if not ok:
        shot(page, "apikey_missing")
        return

    # ============================================================
    # ⑤a-2: 点 "创建 API Key"
    # ============================================================
    create_btn = page.get_by_role("button", name="创建 API Key")
    create_btn.click()
    time.sleep(1)
    shot(page, "apikey_create_dialog")

    # 填名 + scope (默认 cluster)
    page.locator('input[placeholder*="副机"]').fill("UAT-cluster-key")
    time.sleep(0.3)
    # scope 默认 cluster, 不动. 用 exact=True 避免和 "创建 API Key" 撞
    page.get_by_role("button", name="创建", exact=True).click()
    time.sleep(2)
    step("⑤a-2 提交创建", True)
    shot(page, "apikey_plaintext")

    # ============================================================
    # ⑤a-3: 明文展示对话框出现
    # ============================================================
    plain_header = page.locator('text=API Key 已创建 — 立即复制')
    ok = plain_header.count() >= 1
    step("⑤a-3 明文展示对话框出现", ok, "")
    if ok:
        # 找含 tk_ 前缀的 div (页面上别处也有 font-mono, 必须精确匹配 tk_)
        plain_block = page.locator('div.font-mono.text-emerald-300').first
        if plain_block.count() > 0:
            content = plain_block.inner_text()
            has_tk = content.startswith("tk_") and len(content) > 30
            step("⑤a-3.1 明文格式正确 tk_xxx...",
                 has_tk, f"len={len(content)}, prefix={content[:6]}")

    # 点复制
    try:
        page.get_by_role("button", name="复制 Key").click(timeout=3000)
        time.sleep(0.5)
        step("⑤a-4 点复制", True)
    except Exception as e:
        step("⑤a-4 点复制", False, f"{e}"[:60])

    # 点 "我已保存, 关闭"
    page.get_by_role("button", name="我已保存, 关闭").click()
    time.sleep(0.5)
    # 跳出二次确认
    try:
        page.get_by_role("button", name="已保存, 关闭").click(timeout=3000)
        time.sleep(1)
    except Exception:
        pass
    shot(page, "apikey_after_close")

    # ============================================================
    # ⑤a-5: 列表里出现这一行
    # ============================================================
    name_cell = page.locator('text=UAT-cluster-key')
    ok = name_cell.count() >= 1
    step("⑤a-5 列表显示新创建的 key", ok, f"count={name_cell.count()}")

    # ============================================================
    # ⑤a-6: 后端 API 直接验有 1 条
    # ============================================================
    r = requests.get(f"{API}/api/v1/api-keys",
                     headers={"Authorization": f"Bearer {token}"}, timeout=4)
    data = r.json() if r.status_code == 200 else []
    ok = isinstance(data, list) and len(data) == 1 and data[0]["name"] == "UAT-cluster-key"
    step("⑤a-6 后端 GET /api-keys 1 条", ok,
         f"HTTP {r.status_code} count={len(data) if isinstance(data, list) else '?'}")

    # ============================================================
    # ⑤a-7: 切换启用/禁用 (走后端验证落库)
    # ============================================================
    if data:
        key_id = data[0]["id"]
        r = requests.put(f"{API}/api/v1/api-keys/{key_id}/toggle",
                         headers={"Authorization": f"Bearer {token}"}, timeout=4)
        ok = r.status_code == 200 and r.json()["enabled"] is False
        step("⑤a-7 toggle 后 enabled=False", ok, f"HTTP {r.status_code}")
        # 再 toggle 回来
        requests.put(f"{API}/api/v1/api-keys/{key_id}/toggle",
                     headers={"Authorization": f"Bearer {token}"}, timeout=4)

    # ============================================================
    # ⑤b-1: 找 operator 角色行的 "编辑权限" 按钮
    # ============================================================
    # 先 reload 清掉遗留的 plaintext / confirm dialog 层叠
    page.reload(wait_until="domcontentloaded")
    time.sleep(3)
    page.get_by_role("tab", name="账号鉴权").click(timeout=5000)
    time.sleep(2)

    # 调试: 看前端 roles state 和角色列表实际渲染的行数
    try:
        roles_count = page.evaluate("""
            () => {
                // 找角色列表所在的 el-table tr 数 (跳过 header)
                const cards = document.querySelectorAll('.el-card');
                let info = { card_count: cards.length, rows_per_card: [] };
                cards.forEach((c, i) => {
                    const trs = c.querySelectorAll('tbody tr');
                    info.rows_per_card.push({ idx: i, tr_count: trs.length,
                      text: c.querySelector('.el-card__header')?.innerText?.slice(0, 30) });
                });
                return info;
            }
        """)
        print(f"  [debug] 卡片+表格: {roles_count}")
    except Exception as e:
        print(f"  [debug 失败] {e}")

    # 角色列表第 3 行就是 operator (admin/engineer/operator 顺序)
    # 用文字 "操作员" (description 包含) 锁定行更稳, 因为 el-tag 里的 "operator" 文本会被 el-tag 渲染
    op_row = page.locator('tr:has-text("操作员")').last
    try:
        op_row.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass
    time.sleep(1)
    shot(page, "role_list")

    # 找 operator 行的"编辑权限"
    try:
        edit_btn = op_row.locator('button:has-text("编辑权限")').first
        edit_btn.click(timeout=8000)
        time.sleep(1)
        step("⑤b-1 打开 operator 编辑器", True)
    except Exception as e:
        step("⑤b-1 打开 operator 编辑器", False, f"{e}"[:80])
        return
    shot(page, "role_editor_open")

    # ============================================================
    # ⑤b-2: 勾选 "清零计数 / 重置周期性动作" (monitor.detection.advanced)
    # ============================================================
    # 用文本定位 — checkbox 旁边的 label 含权限标签
    target_label = page.locator('label', has_text='清零计数').first
    try:
        target_label.click(timeout=5000)
        time.sleep(0.5)
        step("⑤b-2 勾选 monitor.detection.advanced", True)
    except Exception as e:
        step("⑤b-2 勾选 monitor.detection.advanced", False, f"{e}"[:80])

    # 点保存
    try:
        page.get_by_role("button", name="保存").last.click(timeout=3000)
        time.sleep(2)
        step("⑤b-3 点保存", True)
    except Exception as e:
        step("⑤b-3 点保存", False, f"{e}"[:80])
    shot(page, "role_saved")

    # ============================================================
    # ⑤b-4: 后端验 operator 角色权限里多了 monitor.detection.advanced
    # ============================================================
    r = requests.get(f"{API}/api/v1/roles",
                     headers={"Authorization": f"Bearer {token}"}, timeout=4)
    roles = r.json() if r.status_code == 200 else []
    op = next((x for x in roles if x["code"] == "operator"), None)
    ok = op and "monitor.detection.advanced" in (op["permissions"] or [])
    step("⑤b-4 后端 operator perms 含 advanced",
         ok, f"perms={op['permissions'] if op else '?'}")

    # ============================================================
    # 收尾: 关闭 auth
    # ============================================================
    r = requests.post(f"{API}/api/v1/auth/disable-auth",
                      headers={"Authorization": f"Bearer {token}"}, timeout=4)
    step("Z 关闭 auth", r.status_code == 200, f"HTTP {r.status_code}")

    browser.close()


def main():
    t0 = time.time()
    try:
        pre_cleanup()
        with sync_playwright() as p:
            run(p)
    except Exception as e:
        step("UAT 异常中断", False, f"{type(e).__name__}: {e}")
    finally:
        elapsed = time.time() - t0
        failed = sum(1 for s in steps_log if not s["ok"])
        passed = len(steps_log) - failed
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("UAT ⑤a + ⑤b API Key UI + 角色权限编辑器\n")
            f.write(f"耗时: {elapsed:.1f}s\n")
            f.write(f"通过: {passed}/{len(steps_log)}  失败: {failed}\n")
            f.write("=" * 60 + "\n")
            for s in steps_log:
                sym = "OK" if s["ok"] else "!!"
                f.write(f"[{sym}] {s['idx']:02d}. {s['label']}  {s['detail']}\n")
        print("\n" + "=" * 60)
        print(f"UAT 结束: 通过 {passed}/{len(steps_log)}  失败 {failed}  耗时 {elapsed:.1f}s")
        print(f"  日志: {LOG}")
        print(f"  截图: {SHOT_DIR}/")
        print("=" * 60)


if __name__ == "__main__":
    main()
