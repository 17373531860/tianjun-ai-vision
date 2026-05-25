"""
v3.10+ 阶段 7+ Q1/Q2/Q3 真实端到端 UAT

测试矩阵:
  Q1. engineer 权限说明 (验证内置默认权限确实很广 + 缩窄后菜单变少)
  Q2. session_persist 开关
       a. 默认 true 时: login 响应 session_persist=true + DB 有 token + 重启后仍登录
       b. 切到 false 时: login 响应 session_persist=false + DB 没 token + 重启后失效
       c. 复原 true
  Q3. 创建账号"身份"单选 (UI + API 数据)

环境: 后端 8002, 前端 6002. admin 临时密码 uat_q3_2026.
"""
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

FRONTEND = "http://localhost:6002"
BACKEND = "http://localhost:8002/api/v1"
DB_PATH = REPO_ROOT / "backend" / "sql_app.db"

ADMIN_USER = "admin"
ADMIN_PASS = "uat_q3_2026"

# 测试用临时对象
UAT_OP_USER = f"__uat_q3_op_{uuid.uuid4().hex[:6]}"
UAT_OP_PASS = "uatpass123"


# ============================================================
# 工具
# ============================================================

def login_api(u, p):
    r = requests.post(f"{BACKEND}/auth/login",
                      json={"username": u, "password": p}, timeout=5)
    if r.status_code != 200:
        raise RuntimeError(f"login {u} failed: {r.status_code} {r.text}")
    return r.json()


def db_count_token(user_id):
    conn = sqlite3.connect(str(DB_PATH))
    n = conn.execute("SELECT COUNT(*) FROM session_tokens WHERE user_id=?", (user_id,)).fetchone()[0]
    conn.close()
    return n


def db_user_id(username):
    conn = sqlite3.connect(str(DB_PATH))
    r = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return r[0] if r else None


def restart_backend():
    """杀后端 + 重启, 模拟"软件重启" """
    import subprocess
    subprocess.run(["pkill", "-9", "-f", "uvicorn backend.main:app --host 0.0.0.0 --port 8002"],
                   capture_output=True)
    time.sleep(2)
    subprocess.Popen(
        ["setsid", "-f", "python", "-u", "-m", "uvicorn", "backend.main:app",
         "--host", "0.0.0.0", "--port", "8002"],
        stdout=open("/tmp/tianjun_副本_backend.log", "w"),
        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
    )
    # 等就绪
    for i in range(30):
        try:
            r = requests.get(f"{BACKEND}/auth/status", timeout=2)
            if r.status_code == 200:
                print(f"  [restart] 后端 {i+1}s 后 ready")
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("后端 30s 内没起来")


# ============================================================
# 主测试
# ============================================================

def run_q3_ui():
    """Q3: 创建账号"身份"单选 - 前端 UI 校验"""
    print("\n[Q3] 创建账号'身份'单选 UI 校验...")
    admin = login_api(ADMIN_USER, ADMIN_PASS)
    admin_token = admin["token"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            page.goto(f"{FRONTEND}/#/login", wait_until="domcontentloaded")
            time.sleep(1.5)
            page.evaluate(f"localStorage.setItem('tianjun:auth_token', '{admin_token}')")
            page.reload(wait_until="domcontentloaded")
            time.sleep(2.0)

            page.goto(f"{FRONTEND}/#/settings", wait_until="domcontentloaded")
            time.sleep(2.0)
            # 切 账号鉴权 tab
            page.locator('text=账号鉴权').first.click()
            time.sleep(1.0)

            # 点 创建账号
            page.locator('[data-testid="auth-create-user-btn"]').click()
            time.sleep(1.0)

            # 校验: 身份字段是单选 (没有 multiple class)
            role_select = page.locator('[data-testid="auth-create-user-role"]')
            assert role_select.count() == 1, "[Q3.1] 单选 'auth-create-user-role' selector 缺失"

            # el-select multiple 模式会有 .el-select__tags 元素; 单选没有
            multi_tags = role_select.locator('.el-select__tags').count()
            assert multi_tags == 0, f"[Q3.2] 身份字段仍是多选 (有 {multi_tags} 个 .el-select__tags)"
            print("  ✓ Q3.1: '身份' 字段是单选 (无 el-select__tags 多选标签容器)")

            # 默认值应是 operator
            select_inner = role_select.locator('input').first
            placeholder_or_value = select_inner.input_value()
            print(f"  ✓ Q3.2: 默认 select 显示值 = '{placeholder_or_value}'")

            # 填入数据并提交
            page.locator('input[placeholder*="工号"]').first.fill(UAT_OP_USER)
            pwd_inputs = page.locator('.el-dialog input[type="password"]')
            pwd_inputs.nth(0).fill(UAT_OP_PASS)
            pwd_inputs.nth(1).fill(UAT_OP_PASS)
            time.sleep(0.3)
            page.locator('[data-testid="auth-create-user-submit"]').click()
            time.sleep(2.0)

            # 校验: API 创建结果, user.roles 是 ['operator']
            r = requests.get(f"{BACKEND}/users",
                             headers={"Authorization": f"Bearer {admin_token}"},
                             timeout=5)
            users = r.json()
            new_user = next((u for u in users if u["username"] == UAT_OP_USER), None)
            assert new_user, f"[Q3.3] 新账号未创建; 列表: {[u['username'] for u in users]}"
            assert new_user["roles"] == ["operator"], \
                f"[Q3.4] 新账号角色错: 期望 ['operator'], 实际 {new_user['roles']}"
            print(f"  ✓ Q3.3: 新账号 {UAT_OP_USER} 创建成功, roles = ['operator']")

            # 校验: 账号列表"身份"列只显示一个 tag (取 row.roles[0])
            page.reload(wait_until="domcontentloaded")
            time.sleep(2.0)
            page.locator('text=账号鉴权').first.click()
            time.sleep(1.0)

            uat_row = page.locator(f'tr:has-text("{UAT_OP_USER}")').first
            tags_in_row = uat_row.locator('.el-tag').filter(has_text='operator').count()
            assert tags_in_row >= 1, f"[Q3.5] 列表里 {UAT_OP_USER} 行没有 operator tag"
            # 看看"身份"列是否表头改名为"身份"
            身份_header = page.locator('th:has-text("身份")').count()
            assert 身份_header >= 1, "[Q3.6] 表头未改名'身份'"
            print(f"  ✓ Q3.4: 列表表头已改名'身份', UAT 行有 operator tag")

            print("  ✅ Q3 全过\n")
            return admin_token
        except AssertionError as e:
            page.screenshot(path="/tmp/uat_q3_fail.png", full_page=True)
            raise
        finally:
            browser.close()


def run_q2_session_persist(admin_token):
    """Q2: session_persist 开关真实行为"""
    print("[Q2] session_persist 开关真实测试...")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # ---- Q2.a: 默认 true 时行为 ----
    cfg = requests.get(f"{BACKEND}/auth/config", headers=headers).json()
    assert cfg["session_persist"] is True, f"[Q2.1] 默认应 true, 实际 {cfg}"
    print(f"  ✓ Q2.1: 默认 session_persist = true")

    # 用新建的 operator 登录, 检查响应 + DB
    op_login = login_api(UAT_OP_USER, UAT_OP_PASS)
    assert op_login["session_persist"] is True, f"[Q2.2] login 响应应 session_persist=true"
    op_user_id = db_user_id(UAT_OP_USER)
    token_count = db_count_token(op_user_id)
    assert token_count >= 1, f"[Q2.3] persist=true 时应在 session_tokens 表落盘, 实际 {token_count} 条"
    print(f"  ✓ Q2.2: persist=true 时 login 响应正确 + DB 有 {token_count} 条 token")

    # ---- Q2.b: 切到 false ----
    r = requests.put(f"{BACKEND}/auth/config",
                     headers=headers,
                     json={"session_persist": False}, timeout=5)
    assert r.status_code == 200, f"[Q2.4] PUT config 失败 {r.status_code}"
    cfg = r.json()
    assert cfg["session_persist"] is False, f"[Q2.5] 切换后应 false, 实际 {cfg}"
    print(f"  ✓ Q2.3: 切到 session_persist=false 成功")

    # 删除 operator 之前的 token (清场)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM session_tokens WHERE user_id=?", (op_user_id,))
    conn.commit()
    conn.close()

    # 用 operator 再登录, 此时不应落盘
    op_login_2 = login_api(UAT_OP_USER, UAT_OP_PASS)
    assert op_login_2["session_persist"] is False, f"[Q2.6] login 响应应 session_persist=false"
    op_token_2 = op_login_2["token"]
    token_count = db_count_token(op_user_id)
    assert token_count == 0, f"[Q2.7] persist=false 时不该落盘, 实际 {token_count} 条"
    print(f"  ✓ Q2.4: persist=false 时 login 响应正确 + DB 0 条 token (仅内存)")

    # token 仍能调 /auth/me (内存缓存 OK)
    r = requests.get(f"{BACKEND}/auth/me",
                     headers={"Authorization": f"Bearer {op_token_2}"}, timeout=5)
    assert r.status_code == 200, f"[Q2.8] persist=false 但 token 仍应可用, 实际 {r.status_code}"
    me = r.json()
    assert me["user"]["username"] == UAT_OP_USER, "[Q2.9] /me 返回的用户不对"
    print(f"  ✓ Q2.5: token 在内存仍可用 (/auth/me 200)")

    # ---- Q2.c: 重启后端 → 内存 token 应失效 ----
    print("  [模拟重启后端 (杀进程 + 重启)]...")
    restart_backend()

    # 用同一 token 调 /me, 应该 401 (内存缓存清空 + DB 也没 token)
    r = requests.get(f"{BACKEND}/auth/me",
                     headers={"Authorization": f"Bearer {op_token_2}"}, timeout=5)
    # 注意: /auth/me 走 get_current_user, 此时 token 既不在内存也不在 DB
    # 但匿名兜底默认 true, 所以会返回匿名身份 (200 + is_anonymous=true)
    # 真正验证 token 失效: 看返回的是不是匿名
    assert r.status_code == 200, f"[Q2.10] /me 应 200 (匿名兜底), 实际 {r.status_code}"
    me_after_restart = r.json()
    is_anon = me_after_restart["user"].get("is_anonymous", False)
    assert is_anon is True, f"[Q2.11] 重启后 token 失效, 应回到匿名身份, 实际 {me_after_restart['user']}"
    print(f"  ✓ Q2.6: 重启后 token 失效, /auth/me 返回匿名身份 (验证内存型 token 丢了)")

    # ---- Q2.d: 复原 session_persist=true ----
    # 先用 admin 重登 (admin 的 token 也在重启时失效了)
    admin_relogin = login_api(ADMIN_USER, ADMIN_PASS)
    headers = {"Authorization": f"Bearer {admin_relogin['token']}"}

    r = requests.put(f"{BACKEND}/auth/config",
                     headers=headers,
                     json={"session_persist": True}, timeout=5)
    assert r.status_code == 200, f"[Q2.12] 复原失败"
    print(f"  ✓ Q2.7: 已复原 session_persist=true")

    # 再次用 operator 登录, 验证又落盘了
    op_login_3 = login_api(UAT_OP_USER, UAT_OP_PASS)
    assert op_login_3["session_persist"] is True, f"[Q2.13] 复原后 login 响应应 true"
    token_count = db_count_token(op_user_id)
    assert token_count >= 1, f"[Q2.14] 复原后应又落盘, 实际 {token_count}"
    print(f"  ✓ Q2.8: 复原后再 login, DB 又有 {token_count} 条 token")

    print("  ✅ Q2 全过\n")
    return admin_relogin["token"]


def run_q1_engineer_demo(admin_token):
    """Q1: engineer 权限说明 (验证)"""
    print("[Q1] engineer 内置默认权限说明...")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. 当前 engineer 角色权限确实很广
    r = requests.get(f"{BACKEND}/roles", headers=headers, timeout=5)
    engineer = next((ro for ro in r.json() if ro["code"] == "engineer"), None)
    assert engineer, "[Q1.1] engineer 角色不存在"
    perms = engineer["permissions"] or []
    print(f"  ✓ Q1.1: engineer 当前权限 ({len(perms)} 项): {perms[:6]}{'...' if len(perms)>6 else ''}")

    # 2. 验证用户提到的 7 个通配在权限里
    expected_wildcards = ["monitor.*", "project.*", "source.*", "model.*",
                          "alarm.*", "data.*", "mes.*"]
    has_all = all(w in perms for w in expected_wildcards)
    assert has_all, f"[Q1.2] engineer 应包含 7 个模块通配, 实际 {perms}"
    print(f"  ✓ Q1.2: 7 个业务模块通配 (monitor/project/source/model/alarm/data/mes).* 都在 - " +
          "说明菜单全开是符合权限定义的, 不是 bug")

    # 3. 演示: 临时把 engineer 缩窄到只 project.*, 用 222 拉 /auth/me 验证权限变窄
    print("  [演示] 临时把 engineer 缩窄到只 ['project.*']...")
    r = requests.put(f"{BACKEND}/roles/{engineer['id']}",
                     headers=headers,
                     json={"permissions": ["project.*"]}, timeout=5)
    assert r.status_code == 200, f"[Q1.3] 缩窄 engineer 失败 {r.text}"

    # 注意: 222 当前可能有缓存的 token 在内存, 直接用 admin 调 GET /users/{id} 看权限合集
    r = requests.get(f"{BACKEND}/users", headers=headers, timeout=5)
    user_222 = next((u for u in r.json() if u["username"] == "222"), None)
    if user_222:
        perms_222 = user_222.get("permissions") or []
        assert perms_222 == ["project.*"], \
            f"[Q1.4] 222 用户权限合集应是 ['project.*'], 实际 {perms_222}"
        print(f"  ✓ Q1.3: 缩窄后 222 用户合集权限 = {perms_222} (只能看项目菜单)")

    # 复原
    with open("/tmp/engineer_perms_backup_q3.txt", "r") as f:
        original = f.read()
    import json as _json
    original_list = _json.loads(original)
    r = requests.put(f"{BACKEND}/roles/{engineer['id']}",
                     headers=headers,
                     json={"permissions": original_list}, timeout=5)
    assert r.status_code == 200, f"[Q1.5] 复原 engineer 权限失败"
    print(f"  ✓ Q1.4: 已复原 engineer 原 {len(original_list)} 项权限")

    print("  ✅ Q1 全过\n")


# ============================================================
# 入口
# ============================================================

def main():
    print("\n" + "=" * 60)
    print("v3.10+ Q1/Q2/Q3 端到端真实 UAT")
    print("=" * 60)

    fail = False
    admin_token = None
    try:
        admin_token = run_q3_ui()
        admin_token = run_q2_session_persist(admin_token)
        run_q1_engineer_demo(admin_token)
        print("=" * 60)
        print("✅ Q1 + Q2 + Q3 全部真实通过")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n[!] 断言失败: {e}")
        fail = True
    except Exception as e:
        print(f"\n[!] 异常: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        fail = True
    finally:
        # 清理测试账号
        print("\n[cleanup] 清理...")
        try:
            admin = login_api(ADMIN_USER, ADMIN_PASS)
            h = {"Authorization": f"Bearer {admin['token']}"}
            r = requests.get(f"{BACKEND}/users", headers=h, timeout=5)
            for u in r.json():
                if u["username"] == UAT_OP_USER:
                    requests.delete(f"{BACKEND}/users/{u['id']}", headers=h, timeout=5)
                    print(f"  ✓ 清掉测试账号 {UAT_OP_USER}")
            # 复原 session_persist=true (兜底)
            requests.put(f"{BACKEND}/auth/config", headers=h,
                         json={"session_persist": True}, timeout=5)
        except Exception as e:
            print(f"  ! cleanup 异常 (忽略): {e}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
