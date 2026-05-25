"""
阶段 5 UAT — 旧 operators 系统彻底清空

验证项 (5 大检查点):
  ① 启动迁移日志: 看到 "DROP TABLE operators" 字样
  ② DB schema: operators 表已不存在
  ③ Python import: Operator 类已不可 import
  ④ Python import: get_current_operator_id 函数已不可 import
  ⑤ 残留代码痕迹: 生产代码再无 from backend.api.operators import 引用
  ⑥ ORM FK: DetectionSession.operator_id / DetectionCycle.operator_id 指向 users
  ⑦ 6 个 410 端点仍然返回 410 Gone (老客户脚本迁移引导)
  ⑧ 阶段 4 链路仍可工作 (登录写落盘 / sessions API 查 User)
"""
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = "http://localhost:8001/api/v1"
DB_PATH = REPO_ROOT / "backend" / "sql_app.db"
LOG_PATH = Path("/tmp/tianjun_backend.log")

# 让 import backend.xxx 能工作
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TEST_ADMIN = "uat_p5_admin"
TEST_ADMIN_PW = "uat-p5-pass-12345"

results: list[tuple[bool, str, str]] = []


def step(name: str, ok: bool, detail: str = ""):
    results.append((ok, name, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{detail}]" if detail else ""))


def http(method: str, path: str, **kw):
    return requests.request(method, f"{BACKEND}{path}", timeout=10, **kw)


def pre_cleanup():
    print("\n[阶段 0] 清理 + 准备")
    # 直接 DB 清: 关鉴权 + 删测试 admin + 清测试 session/cycle
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("DELETE FROM system_configs WHERE key='auth.enabled'")
    cur.execute(
        "DELETE FROM session_tokens WHERE user_id IN (SELECT id FROM users WHERE username=?)",
        (TEST_ADMIN,),
    )
    cur.execute(
        "DELETE FROM user_roles WHERE user_id IN (SELECT id FROM users WHERE username=?)",
        (TEST_ADMIN,),
    )
    cur.execute("DELETE FROM users WHERE username=?", (TEST_ADMIN,))
    cur.execute("DELETE FROM detection_cycles WHERE cycle_uuid LIKE 'p5uat%'")
    cur.execute("DELETE FROM detection_sessions WHERE session_uuid LIKE 'p5uat%'")
    conn.commit()
    conn.close()

    cu_file = REPO_ROOT / "backend" / "current_user.json"
    if cu_file.exists():
        cu_file.unlink()

    # 故意重建 operators 表, 让后端启动时再次 DROP, 验证迁移路径
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS operators ("
        "id INTEGER PRIMARY KEY, "
        "name VARCHAR(64), "
        "employee_no VARCHAR(32), "
        "role VARCHAR(20), "
        "active BOOLEAN, "
        "created_at DATETIME)"
    )
    conn.commit()
    conn.close()
    print("  - operators 表已恢复, 用于测试自动 DROP")

    # 杀重启
    os.system("pkill -9 -f 'uvicorn backend.main:app' 2>/dev/null; sleep 1")
    os.chdir(str(REPO_ROOT))
    LOG_PATH.unlink(missing_ok=True)
    os.system(
        "setsid -f python -u -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 "
        "> /tmp/tianjun_backend.log 2>&1 < /dev/null"
    )
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
# ① 启动迁移日志
# ============================================================
def test_1_startup_log():
    print("\n[① 启动日志: DROP TABLE operators 迁移触发]")
    log_text = LOG_PATH.read_text(encoding="utf-8", errors="ignore")
    drop_seen = "DROP TABLE operators" in log_text \
                or "阶段 5 清理" in log_text \
                or "DROP TABLE IF EXISTS operators" in log_text
    step("启动日志含 DROP TABLE operators / 阶段 5 清理 字样",
         drop_seen,
         "")


# ============================================================
# ② DB schema
# ============================================================
def test_2_db_schema():
    print("\n[② DB schema: operators 表已不存在]")
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    tables = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    step("operators 表已 DROP", "operators" not in tables,
         f"tables count={len(tables)}")
    step("users 表仍存在 (用户系统)", "users" in tables, "")
    step("session_tokens 表存在", "session_tokens" in tables, "")
    step("roles 表存在", "roles" in tables, "")


# ============================================================
# ③ Python import: Operator 类不可 import
# ============================================================
def test_3_orm_class_gone():
    print("\n[③ Python import: Operator 类已删除]")
    # 用 importlib + hasattr 替代 from-import 避免 ImportError 范围歧义
    # 注意: 不能 del 缓存后 reload, SQLAlchemy declarative_base 会重复注册表冲突
    import importlib
    try:
        mm = importlib.import_module("backend.models.models")
    except Exception as e:
        step("backend.models.models 模块可 import", False,
             f"模块导入失败: {e!r}")
        return
    step("backend.models.models 模块可 import", True, "")
    has_op = hasattr(mm, "Operator")
    step("Operator 类已删除 (不可 import)", not has_op,
         "意外仍存在" if has_op else "")


# ============================================================
# ④ Python import: get_current_operator_id 函数不可 import
# ============================================================
def test_4_legacy_func_gone():
    print("\n[④ Python import: get_current_operator_id 函数已删除]")
    import importlib
    try:
        ops = importlib.import_module("backend.api.operators")
    except Exception as e:
        step("backend.api.operators 模块可 import", False,
             f"模块导入失败: {e!r}")
        return
    step("backend.api.operators 模块可 import (保留 6 个 410 端点)", True, "")
    has_func = hasattr(ops, "get_current_operator_id")
    step("get_current_operator_id 已删除", not has_func,
         "意外仍存在" if has_func else "")

    # 验证新版函数仍可 import (不 reload, 直接拿缓存)
    try:
        core_auth = importlib.import_module("backend.core.auth")
        step("新版 get_current_user_id 仍可 import",
             hasattr(core_auth, "get_current_user_id"), "")
    except Exception as e:
        step("新版 get_current_user_id 仍可 import", False, str(e))


# ============================================================
# ⑤ 残留代码痕迹: 生产代码无 from backend.api.operators import 老函数
# ============================================================
def test_5_no_legacy_refs():
    print("\n[⑤ 生产代码无 get_current_operator_id 引用]")
    # 扫描 backend/ 下所有 .py, 排除注释和 operators.py 自身
    bad_refs = []
    backend_dir = REPO_ROOT / "backend"
    for p in backend_dir.rglob("*.py"):
        if p.name == "operators.py":
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for ln, line in enumerate(txt.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if "get_current_operator_id" in line:
                bad_refs.append(f"{p.relative_to(REPO_ROOT)}:{ln}  {stripped[:80]}")

    step("生产代码无 get_current_operator_id 引用 (operators.py 自身除外)",
         len(bad_refs) == 0,
         f"残留 {len(bad_refs)} 处" + (": " + bad_refs[0] if bad_refs else ""))


# ============================================================
# ⑥ ORM FK 检查: detection_sessions.operator_id 已指 users
# ============================================================
def test_6_orm_fk():
    print("\n[⑥ ORM FK: operator_id 列 FK 改指 users]")
    # 不能 reload (SQLAlchemy declarative Base 会重复注册表), 直接复用
    import importlib
    try:
        mm = importlib.import_module("backend.models.models")
    except Exception as e:
        step("models.models 可 import", False, str(e))
        return
    DetectionSession = mm.DetectionSession
    DetectionCycle = mm.DetectionCycle
    s_fks = list(DetectionSession.operator_id.foreign_keys)
    c_fks = list(DetectionCycle.operator_id.foreign_keys)
    step("DetectionSession.operator_id FK → users",
         any("users" in str(fk.column) for fk in s_fks),
         f"actual={[str(fk.column) for fk in s_fks]}")
    step("DetectionCycle.operator_id FK → users",
         any("users" in str(fk.column) for fk in c_fks),
         f"actual={[str(fk.column) for fk in c_fks]}")


# ============================================================
# ⑦ 6 个 410 端点仍正常 (老客户脚本迁移引导)
# ============================================================
def test_7_410_endpoints():
    print("\n[⑦ 6 个 /operators/* 端点仍 410 Gone]")
    endpoints = [
        ("GET", "/operators", None),
        ("POST", "/operators", {}),
        ("PUT", "/operators/1", {}),
        ("DELETE", "/operators/1", None),
        ("POST", "/operators/set-current", {}),
        ("GET", "/operators/current", None),
    ]
    for method, path, body in endpoints:
        try:
            r = http(method, path, json=body) if body is not None else http(method, path)
            ok = r.status_code == 410
            dep = r.headers.get("X-Deprecated-Replacement", "")
            step(f"{method} {path} → 410",
                 ok and "/users" in dep,
                 f"status={r.status_code} dep={dep!r}")
        except Exception as e:
            step(f"{method} {path} → 410", False, str(e))


# ============================================================
# ⑧ 阶段 4 链路回归
# ============================================================
def test_8_phase4_regression():
    print("\n[⑧ 阶段 4 链路回归: 登录 → 落盘 → sessions API 查 User]")

    # 启用鉴权 + 创建测试 admin
    r = http("POST", "/auth/enable-auth", json={
        "admin_username": TEST_ADMIN,
        "admin_password": TEST_ADMIN_PW,
        "admin_display_name": "阶段5UAT管理员",
    })
    step("启用鉴权 + 创建 admin", r.status_code == 200,
         f"status={r.status_code}")
    if r.status_code != 200:
        return
    admin_id = r.json()["admin"]["id"]

    # 登录
    r = http("POST", "/auth/login",
             json={"username": TEST_ADMIN, "password": TEST_ADMIN_PW})
    step("login 成功", r.status_code == 200, "")
    if r.status_code != 200:
        return
    token = r.json()["token"]

    # 落盘
    cu_file = REPO_ROOT / "backend" / "current_user.json"
    step("current_user.json 已落盘", cu_file.exists(), "")

    # 直插 session + cycle, op_id = admin_id
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT id FROM projects LIMIT 1")
    project_id = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO detection_sessions
           (session_uuid, name, project_id, start_time, status, channel_id, operator_id)
           VALUES (?, ?, ?, datetime('now'), 'completed', 0, ?)""",
        ("p5uat_s", "P5 session", project_id, admin_id),
    )
    sess_id = cur.lastrowid
    cur.execute(
        """INSERT INTO detection_cycles
           (cycle_uuid, session_id, cycle_number, start_time, is_good, operator_id)
           VALUES (?, ?, ?, datetime('now'), 1, ?)""",
        ("p5uat_c", sess_id, 1, admin_id),
    )
    cycle_id = cur.lastrowid
    conn.commit()
    conn.close()

    # 调 sessions API
    r = http("GET", f"/data/sessions/{sess_id}")
    step("GET /data/sessions/{id} 200", r.status_code == 200, "")
    if r.status_code == 200:
        body = r.json()
        step("operator_name = 阶段5UAT管理员 (查 User 表)",
             body.get("operator_name") == "阶段5UAT管理员",
             f"actual={body.get('operator_name')}")

    # 登出 → 落盘清
    r = http("POST", "/auth/logout", headers={"Authorization": f"Bearer {token}"})
    step("logout 200", r.status_code == 200, "")
    step("logout 后 current_user.json 清除", not cu_file.exists(), "")

    # 关闭鉴权
    r = http("POST", "/auth/login",
             json={"username": TEST_ADMIN, "password": TEST_ADMIN_PW})
    if r.status_code == 200:
        token2 = r.json()["token"]
        http("POST", "/auth/disable-auth",
             headers={"Authorization": f"Bearer {token2}"})

    # 清理插入数据
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("DELETE FROM detection_cycles WHERE id=?", (cycle_id,))
    cur.execute("DELETE FROM detection_sessions WHERE id=?", (sess_id,))
    cur.execute(
        "DELETE FROM session_tokens WHERE user_id IN (SELECT id FROM users WHERE username=?)",
        (TEST_ADMIN,),
    )
    cur.execute(
        "DELETE FROM user_roles WHERE user_id IN (SELECT id FROM users WHERE username=?)",
        (TEST_ADMIN,),
    )
    cur.execute("DELETE FROM users WHERE username=?", (TEST_ADMIN,))
    conn.commit()
    conn.close()


def main():
    pre_cleanup()
    test_1_startup_log()
    test_2_db_schema()
    test_3_orm_class_gone()
    test_4_legacy_func_gone()
    test_5_no_legacy_refs()
    test_6_orm_fk()
    test_7_410_endpoints()
    test_8_phase4_regression()

    print("\n" + "=" * 64)
    total = len(results)
    passed = sum(1 for ok, _, _ in results if ok)
    print(f"[阶段 5 UAT 汇总] {passed}/{total} 通过")
    failed = [(n, d) for ok, n, d in results if not ok]
    if failed:
        print("\n失败项:")
        for n, d in failed:
            print(f"  ✗ {n}  [{d}]")
        sys.exit(1)
    print("\n✅ 阶段 5 全部通过")


if __name__ == "__main__":
    main()
