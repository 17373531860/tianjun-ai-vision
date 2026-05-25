"""
阶段 4 UAT — 数据归属重定向到 User (操作员 → 用户)

验证项 (8 大检查点 × 30+ 子断言):
  ① set_current_active_user / get_current_user_id 函数 + current_user.json 落盘
  ② POST /auth/login → 自动写 current_user.json
  ③ POST /auth/logout → 自动清 current_user.json
  ④ POST /auth/disable-auth → 自动清 current_user.json
  ⑤ GET /data/sessions 响应 operator_name 从 User 表查 (不是 Operator 表)
  ⑥ export_context._fill_operator 6 字段从 User 取
  ⑦ mes_gateway.build_cycle_end_context operator 段从 User 取
  ⑧ 旧 operator_id 指向已不存在的 user → operator_name=None (兼容历史数据)

运行前置:
  - 后端: 已起 (uvicorn 0.0.0.0:8001)
  - 数据库: backend/sql_app.db (可空)
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = "http://localhost:8001/api/v1"
DB_PATH = REPO_ROOT / "backend" / "sql_app.db"
CURRENT_USER_FILE = REPO_ROOT / "backend" / "current_user.json"

# 测试期专用 username, 避免污染真实数据
TEST_ADMIN = "uat_p4_admin"
TEST_ADMIN_PW = "uat-p4-pass-12345"

results: list[tuple[bool, str, str]] = []


def step(name: str, ok: bool, detail: str = ""):
    results.append((ok, name, detail))
    sym = "✓" if ok else "✗"
    print(f"  {sym} {name}" + (f"  [{detail}]" if detail else ""))


def http(method: str, path: str, **kwargs):
    return requests.request(method, f"{BACKEND}{path}", timeout=10, **kwargs)


def read_current_user_file():
    if not CURRENT_USER_FILE.exists():
        return None
    return json.loads(CURRENT_USER_FILE.read_text(encoding="utf-8"))


# ============================================================
# 阶段 0: 清理 + 准备环境
# ============================================================
def pre_cleanup():
    print("\n[阶段 0] 清理 + 准备")

    # 关掉鉴权 (走清理路径)
    try:
        st = http("GET", "/auth/status").json()
        if st.get("auth_enabled"):
            # 鉴权开了, 但可能没我们的 admin → 必须以现有 admin 登录后 disable
            # 直接 DB 改 flag 更稳
            pass
    except Exception as e:
        print(f"  ! 探测 status 失败 (忽略): {e}")

    # 直接 DB 清理: 关闭鉴权 + 删测试 admin + 清 current_user.json
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    # 关闭鉴权
    cur.execute("DELETE FROM system_configs WHERE key='auth.enabled'")
    # 删测试 admin (反向依赖: tokens → user_roles → users)
    cur.execute(
        "DELETE FROM session_tokens WHERE user_id IN (SELECT id FROM users WHERE username=?)",
        (TEST_ADMIN,),
    )
    cur.execute(
        "DELETE FROM user_roles WHERE user_id IN (SELECT id FROM users WHERE username=?)",
        (TEST_ADMIN,),
    )
    cur.execute("DELETE FROM users WHERE username=?", (TEST_ADMIN,))
    # 清测试遗留的 session / cycle
    cur.execute("DELETE FROM step_records WHERE cycle_id IN (SELECT id FROM detection_cycles WHERE cycle_uuid LIKE 'p4uat%')")
    cur.execute("DELETE FROM detection_cycles WHERE cycle_uuid LIKE 'p4uat%'")
    cur.execute("DELETE FROM detection_sessions WHERE session_uuid LIKE 'p4uat%'")
    conn.commit()
    conn.close()

    # 清落盘文件
    if CURRENT_USER_FILE.exists():
        CURRENT_USER_FILE.unlink()
    print(f"  - 落盘文件 {CURRENT_USER_FILE.name} 已清: {not CURRENT_USER_FILE.exists()}")

    # 重启后端让 SystemConfig 缓存刷掉 (auth_deps.is_auth_enabled 走库, 不需重启)
    # 但避免上次启动残留的 _token_cache, 还是重启一次最稳
    os.system("pkill -9 -f 'uvicorn backend.main:app' 2>/dev/null; sleep 1")
    os.chdir(str(REPO_ROOT))
    os.system(
        "setsid -f python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 "
        "> /tmp/tianjun_backend.log 2>&1 < /dev/null"
    )
    # 等就绪
    for _ in range(30):
        time.sleep(1)
        try:
            r = http("GET", "/auth/status")
            if r.status_code == 200:
                print(f"  - 后端就绪: {r.json()}")
                return
        except Exception:
            continue
    raise RuntimeError("backend not ready")


# ============================================================
# ① 函数 + 落盘
# ============================================================
def test_1_core_functions():
    print("\n[① core/auth.py 函数 + 落盘]")
    # 重新导入避免缓存
    sys.path.insert(0, str(REPO_ROOT))
    if "backend.core.auth" in sys.modules:
        del sys.modules["backend.core.auth"]
    from backend.core.auth import set_current_active_user, get_current_user_id  # type: ignore

    # 初始: 文件不存在
    if CURRENT_USER_FILE.exists():
        CURRENT_USER_FILE.unlink()
    step("初始 get_current_user_id() == None", get_current_user_id() is None,
         "文件不存在时应返回 None")

    # set(42) → 文件存在 + 内容正确
    set_current_active_user(42)
    step("set(42) 写落盘", CURRENT_USER_FILE.exists(),
         f"path={CURRENT_USER_FILE}")
    data = read_current_user_file()
    step("文件内容 = {user_id:42}", data == {"user_id": 42},
         f"actual={data}")
    step("get() 读出 42", get_current_user_id() == 42, f"got={get_current_user_id()}")

    # set(None) → 文件删
    set_current_active_user(None)
    step("set(None) 清落盘", not CURRENT_USER_FILE.exists(),
         "set(None) 应删文件")
    step("set(None) 后 get() == None", get_current_user_id() is None, "")

    # set(99) → set(None) → 文件残留检查
    set_current_active_user(99)
    set_current_active_user(None)
    step("set(99) → set(None) 文件正确清除", not CURRENT_USER_FILE.exists(), "")


# ============================================================
# ② login 端点自动写落盘
# ============================================================
def test_2_login_writes_file():
    print("\n[② POST /auth/login 自动写 current_user.json]")

    # 启用鉴权 + 创建测试 admin
    r = http("POST", "/auth/enable-auth", json={
        "admin_username": TEST_ADMIN,
        "admin_password": TEST_ADMIN_PW,
        "admin_display_name": "阶段4UAT管理员",
    })
    step("启用鉴权 + 创建测试 admin", r.status_code == 200,
         f"status={r.status_code} body={r.text[:200]}")
    if r.status_code != 200:
        return None
    admin_id = r.json()["admin"]["id"]
    step(f"测试 admin id={admin_id} 已建", admin_id > 0, f"id={admin_id}")

    # 启用后落盘文件应不存在 (无人登录)
    step("启用鉴权 ≠ 登录, 落盘文件不存在",
         not CURRENT_USER_FILE.exists(),
         "enable-auth 不写落盘")

    # 登录
    r = http("POST", "/auth/login", json={
        "username": TEST_ADMIN, "password": TEST_ADMIN_PW,
    })
    step("login 成功", r.status_code == 200,
         f"status={r.status_code} body={r.text[:200]}")
    if r.status_code != 200:
        return admin_id
    token = r.json()["token"]

    # 落盘文件应存在
    step("login 后落盘文件存在", CURRENT_USER_FILE.exists(), "")
    data = read_current_user_file()
    step(f"落盘 user_id == admin id ({admin_id})",
         data == {"user_id": admin_id}, f"actual={data}")

    return admin_id, token


# ============================================================
# ③ logout 自动清落盘
# ============================================================
def test_3_logout_clears_file(token):
    print("\n[③ POST /auth/logout 自动清 current_user.json]")
    r = http("POST", "/auth/logout", headers={"Authorization": f"Bearer {token}"})
    step("logout 200", r.status_code == 200, "")
    step("logout 后落盘清除", not CURRENT_USER_FILE.exists(), "")


# ============================================================
# ④ disable-auth 自动清落盘
# ============================================================
def test_4_disable_auth_clears_file():
    print("\n[④ POST /auth/disable-auth 自动清 current_user.json]")

    # 重新登录写落盘
    r = http("POST", "/auth/login",
             json={"username": TEST_ADMIN, "password": TEST_ADMIN_PW})
    step("重新 login (准备 disable 测试)", r.status_code == 200, "")
    if r.status_code != 200:
        return None
    token = r.json()["token"]
    step("登录后落盘文件存在", CURRENT_USER_FILE.exists(), "")

    # disable-auth
    r = http("POST", "/auth/disable-auth",
             headers={"Authorization": f"Bearer {token}"})
    step("disable-auth 200", r.status_code == 200, f"body={r.text[:200]}")
    step("disable-auth 后落盘清除", not CURRENT_USER_FILE.exists(),
         "关鉴权应清登录态")

    # 确认鉴权关闭
    st = http("GET", "/auth/status").json()
    step("auth_enabled = false", not st["auth_enabled"], f"st={st}")

    return token


# ============================================================
# ⑤ /data/sessions 响应 operator_name 从 User 表
# ============================================================
def test_5_sessions_api_uses_user_table(admin_id):
    print("\n[⑤ GET /data/sessions operator_name 从 User 表查]")

    # 直接 DB 插一个 session + cycle, operator_id = admin_id
    today_uuid = "p4uat" + str(int(time.time()))[-3:]
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 准备 project (取第一个, 没有就新建)
    cur.execute("SELECT id FROM projects LIMIT 1")
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO projects (name, is_active) VALUES (?, ?)",
            ("UAT_P4_Project", 1),
        )
        project_id = cur.lastrowid
    else:
        project_id = row[0]

    # 插 session, operator_id = admin_id (代表"管理员发起的检测")
    cur.execute(
        """INSERT INTO detection_sessions
           (session_uuid, name, project_id, start_time, status, channel_id, operator_id)
           VALUES (?, ?, ?, datetime('now'), 'completed', 0, ?)""",
        (today_uuid + "_s", "P4UAT Session A", project_id, admin_id),
    )
    sess_id_admin = cur.lastrowid

    # 插一个 session, operator_id = 99999 (不存在的 user, 模拟历史 Operator 数据)
    cur.execute(
        """INSERT INTO detection_sessions
           (session_uuid, name, project_id, start_time, status, channel_id, operator_id)
           VALUES (?, ?, ?, datetime('now'), 'completed', 0, ?)""",
        (today_uuid + "_h", "P4UAT Session B (legacy op)", project_id, 99999),
    )
    sess_id_legacy = cur.lastrowid

    # 插一个 cycle, operator_id = admin_id, session = sess_id_admin
    cur.execute(
        """INSERT INTO detection_cycles
           (cycle_uuid, session_id, cycle_number, start_time, is_good, operator_id)
           VALUES (?, ?, ?, datetime('now'), 1, ?)""",
        (today_uuid + "_c", sess_id_admin, 1, admin_id),
    )
    cycle_id_admin = cur.lastrowid

    conn.commit()
    conn.close()
    step(f"直插 session A (op_id={admin_id}, id={sess_id_admin})", True, "")
    step(f"直插 session B (op_id=99999 模拟历史, id={sess_id_legacy})", True, "")
    step(f"直插 cycle (op_id={admin_id}, id={cycle_id_admin})", True, "")

    # 调 /data/sessions/{id} 验证
    r = http("GET", f"/data/sessions/{sess_id_admin}")
    step("GET /data/sessions/{admin} 200", r.status_code == 200,
         f"status={r.status_code}")
    if r.status_code == 200:
        body = r.json()
        step(f"  operator_id = {admin_id}",
             body.get("operator_id") == admin_id,
             f"actual={body.get('operator_id')}")
        step("  operator_name = 阶段4UAT管理员",
             body.get("operator_name") == "阶段4UAT管理员",
             f"actual={body.get('operator_name')}")

    # 调 /data/sessions/{legacy} 验证: operator_id=99999 不存在 → name=None
    r = http("GET", f"/data/sessions/{sess_id_legacy}")
    step("GET /data/sessions/{legacy} 200 (历史脏数据不崩)",
         r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        body = r.json()
        step("  legacy operator_id 保留 (=99999)",
             body.get("operator_id") == 99999,
             f"actual={body.get('operator_id')}")
        step("  legacy operator_name = None (查 User 查不到)",
             body.get("operator_name") is None,
             f"actual={body.get('operator_name')}")

    # 调 /data/sessions/{id}/cycles
    r = http("GET", f"/data/sessions/{sess_id_admin}/cycles")
    step("GET cycles 200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        body = r.json()
        items = body.get("items") if isinstance(body, dict) else body
        if items:
            c = items[0]
            step(f"  cycle.operator_id = {admin_id}",
                 c.get("operator_id") == admin_id,
                 f"actual={c.get('operator_id')}")
            step("  cycle.operator_name = 阶段4UAT管理员",
                 c.get("operator_name") == "阶段4UAT管理员",
                 f"actual={c.get('operator_name')}")

    return sess_id_admin, sess_id_legacy, cycle_id_admin


# ============================================================
# ⑥ export_context._fill_operator 从 User 取
# ============================================================
def test_6_export_context(admin_id, cycle_id_admin):
    print("\n[⑥ export_context.build_cycle_context operator 段从 User 取]")
    # 清模块缓存
    for mod in list(sys.modules.keys()):
        if mod.startswith("backend."):
            del sys.modules[mod]

    from backend.db.database import SessionLocal  # type: ignore
    from backend.services.export_context import build_cycle_context  # type: ignore

    db = SessionLocal()
    try:
        ctx = build_cycle_context(db, cycle_id_admin)
    finally:
        db.close()

    step("build_cycle_context 返回非 None", ctx is not None, "")
    if ctx is None:
        return

    op = ctx.get("operator") or {}
    step(f"  operator.id = {admin_id}", op.get("id") == admin_id,
         f"actual={op.get('id')}")
    step("  operator.name = 阶段4UAT管理员",
         op.get("name") == "阶段4UAT管理员",
         f"actual={op.get('name')}")
    step(f"  operator.employee_no = {TEST_ADMIN} (= user.username)",
         op.get("employee_no") == TEST_ADMIN,
         f"actual={op.get('employee_no')}")
    step("  operator.role = admin (User 表角色 code)",
         op.get("role") == "admin",
         f"actual={op.get('role')}")


# ============================================================
# ⑦ mes_gateway build_cycle_end_context operator 段
# ============================================================
def test_7_mes_gateway(admin_id, cycle_id_admin):
    print("\n[⑦ mes_gateway build_cycle_end_context operator 段从 User 取]")
    for mod in list(sys.modules.keys()):
        if mod.startswith("backend."):
            del sys.modules[mod]

    from backend.db.database import SessionLocal  # type: ignore
    from backend.services.mes_gateway import MESGateway  # type: ignore

    gw = MESGateway()
    db = SessionLocal()
    try:
        ctx = gw.build_context_from_cycle(
            db=db, cycle_id=cycle_id_admin,
            event_name="OK", is_good=True, project_id=None,
        )
    finally:
        db.close()

    step("build_context_from_cycle 返回 dict", isinstance(ctx, dict),
         f"type={type(ctx)}")
    if not isinstance(ctx, dict):
        return

    op = ctx.get("operator") or {}
    step(f"  operator.id = {admin_id}", op.get("id") == admin_id,
         f"actual={op.get('id')}")
    step("  operator.name = 阶段4UAT管理员",
         op.get("name") == "阶段4UAT管理员",
         f"actual={op.get('name')}")
    step(f"  operator.employee_no = {TEST_ADMIN} (= user.username)",
         op.get("employee_no") == TEST_ADMIN,
         f"actual={op.get('employee_no')}")


# ============================================================
# ⑧ post-cleanup
# ============================================================
def post_cleanup(sess_id_admin, sess_id_legacy, cycle_id_admin):
    print("\n[⑧ post-cleanup 移除测试数据]")
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("DELETE FROM step_records WHERE cycle_id=?", (cycle_id_admin,))
    cur.execute("DELETE FROM detection_cycles WHERE id=?", (cycle_id_admin,))
    cur.execute("DELETE FROM detection_sessions WHERE id IN (?, ?)",
                (sess_id_admin, sess_id_legacy))
    # 删测试 admin
    cur.execute(
        "DELETE FROM session_tokens WHERE user_id IN (SELECT id FROM users WHERE username=?)",
        (TEST_ADMIN,),
    )
    cur.execute(
        "DELETE FROM user_roles WHERE user_id IN (SELECT id FROM users WHERE username=?)",
        (TEST_ADMIN,),
    )
    cur.execute("DELETE FROM users WHERE username=?", (TEST_ADMIN,))
    # 关闭鉴权状态
    cur.execute("DELETE FROM system_configs WHERE key='auth.enabled'")
    conn.commit()
    conn.close()
    if CURRENT_USER_FILE.exists():
        CURRENT_USER_FILE.unlink()
    print("  - 测试数据清理完毕")


def main():
    pre_cleanup()

    test_1_core_functions()

    ret = test_2_login_writes_file()
    if not ret or not isinstance(ret, tuple):
        print("\n!! ② 登录失败, 后续跳过")
        admin_id, token = None, None
    else:
        admin_id, token = ret

    if token:
        test_3_logout_clears_file(token)
    test_4_disable_auth_clears_file()

    # ⑤⑥⑦ 不需要鉴权打开, 直接用插入的 user_id 验证
    # 但 admin 用户记录已落盘 (鉴权关闭只是 flag, 用户表不删)
    sess_a = sess_b = cyc_a = None
    if admin_id:
        ret5 = test_5_sessions_api_uses_user_table(admin_id)
        if ret5:
            sess_a, sess_b, cyc_a = ret5
            test_6_export_context(admin_id, cyc_a)
            test_7_mes_gateway(admin_id, cyc_a)
        post_cleanup(sess_a or -1, sess_b or -1, cyc_a or -1)

    # 汇总
    print("\n" + "=" * 64)
    total = len(results)
    passed = sum(1 for ok, _, _ in results if ok)
    print(f"[阶段 4 UAT 汇总] {passed}/{total} 通过")
    failed = [(n, d) for ok, n, d in results if not ok]
    if failed:
        print("\n失败项:")
        for n, d in failed:
            print(f"  ✗ {n}  [{d}]")
        sys.exit(1)
    print("\n✅ 阶段 4 全部通过")


if __name__ == "__main__":
    main()
