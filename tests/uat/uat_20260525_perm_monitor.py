"""UAT — R1 (④b-2 第一轮): 监控/检测端点 require_perm 验证.

测试矩阵 (纯 requests, 无浏览器):
  auth_enabled=false → 全部放行 (开关关闭, 客户零差异)
  auth_enabled=true  →
    admin (*)              : control / advanced / project.activate 全通过
    operator (默认)         : control 通过, advanced 拒绝 (403), set-project 拒绝
    匿名 (无 token)         : 同 operator

核心断言:
  ✅ admin POST /detection/recording-failures/clear → 200/4xx (perm 通过)
  ✅ admin POST /detection/reset-periodic           → 200    (perm 通过)
  ✅ admin POST /detection/set-project (空 payload) → 422/200 (perm 通过, 业务可能 422)
  ✅ operator POST /detection/recording-failures/clear → 200/4xx (control 有)
  ✅ operator POST /detection/reset-periodic        → 403    (advanced 无)
  ✅ operator POST /detection/set-project           → 403    (project.activate 无)
  ✅ 匿名 POST /detection/reset-periodic            → 403    (匿名 = operator)
  ✅ 匿名 POST /detection/set-project               → 403
"""
import os
import sqlite3
import time

import requests

API = "http://127.0.0.1:8001"
TS = int(time.time())
ADMIN_USER = f"__uat_admin_{TS}"
OPERATOR_USER = f"__uat_op_{TS}"
PASSWORD = "Uat123456"
LOG = "/tmp/uat_perm_monitor_run.log"

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    sym = "OK" if ok else "!!"
    print(f"[{sym}] {rec['idx']:02d}. {label}  {detail}")


# ============================================================
def pre_cleanup():
    """物理清干净: 删 UAT 用户 + 关闭鉴权"""
    print("\n========== 前置清理 ==========")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.join(repo_root, "backend", "sql_app.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM session_tokens")
        conn.execute("DELETE FROM user_roles")
        n = conn.execute("DELETE FROM users").rowcount
        conn.execute("UPDATE system_configs SET value='false' WHERE key='auth.enabled'")
        conn.commit()
        print(f"  sqlite 清理: 删 {n} 个用户")
    finally:
        conn.close()

    r = requests.get(f"{API}/api/v1/auth/status", timeout=4)
    if r.json().get("auth_enabled"):
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
        raise RuntimeError("后端重启失败")
    step("前置-清理完成", True, "auth=disabled, users=0")


# ============================================================
def phase_a_auth_disabled():
    """A: auth_enabled=false → 全部放行"""
    print("\n========== A: 鉴权未启用 (零差异) ==========")
    # 调 advanced 端点应该通过 (auth 关闭 → SUPERUSER 自动 *)
    r = requests.post(
        f"{API}/api/v1/source/detection/reset-periodic?channel=0",
        timeout=4,
    )
    step("A1-鉴权关闭 reset-periodic 应通过", r.status_code != 403,
         f"HTTP {r.status_code}")

    r = requests.post(
        f"{API}/api/v1/source/detection/recording-failures/clear?channel=0",
        timeout=4,
    )
    step("A2-鉴权关闭 recording-failures/clear 应通过",
         r.status_code != 403, f"HTTP {r.status_code}")


# ============================================================
def phase_b_admin():
    """B: 启用鉴权 + admin token 调 → 全部 perm 通过"""
    print("\n========== B: admin (*) 全通过 ==========")
    # 启用 + 建 admin
    r = requests.post(f"{API}/api/v1/auth/enable-auth", json={
        "admin_username": ADMIN_USER,
        "admin_password": PASSWORD,
        "admin_display_name": "UAT 管理员",
    }, timeout=8)
    assert r.status_code == 200, f"enable-auth 失败: {r.text[:120]}"
    step("B0-启用鉴权 + 创建 admin", True)

    r = requests.post(f"{API}/api/v1/auth/login", json={
        "username": ADMIN_USER, "password": PASSWORD,
    }, timeout=4)
    admin_token = r.json()["token"]
    headers_admin = {"Authorization": f"Bearer {admin_token}"}

    # B1: admin recording-failures/clear (control)
    r = requests.post(
        f"{API}/api/v1/source/detection/recording-failures/clear?channel=0",
        headers=headers_admin, timeout=4,
    )
    step("B1-admin recording-failures/clear", r.status_code != 403,
         f"HTTP {r.status_code}")

    # B2: admin reset-periodic (advanced)
    r = requests.post(
        f"{API}/api/v1/source/detection/reset-periodic?channel=0",
        headers=headers_admin, timeout=4,
    )
    step("B2-admin reset-periodic (advanced)", r.status_code != 403,
         f"HTTP {r.status_code}")

    # B3: admin set-project (project.activate)
    r = requests.post(
        f"{API}/api/v1/source/detection/set-project?channel=0",
        json={"project_id": 999999, "name": "uat_dummy", "task_type": "detection",
              "logic_mode": "sequential", "steps_config": [], "pipeline_config": {},
              "events_config": [], "counters_config": [], "data_config": {}},
        headers=headers_admin, timeout=4,
    )
    step("B3-admin set-project", r.status_code != 403, f"HTTP {r.status_code}")

    # 创建 operator 角色账号
    r = requests.post(
        f"{API}/api/v1/users",
        headers=headers_admin,
        json={
            "username": OPERATOR_USER,
            "password": PASSWORD,
            "display_name": "UAT 操作员",
            "role_codes": ["operator"],
        }, timeout=4,
    )
    assert r.status_code == 200, f"创建 operator 失败: {r.text[:120]}"
    step("B4-admin 创建 operator", True)

    # 拿 operator token
    r = requests.post(f"{API}/api/v1/auth/login", json={
        "username": OPERATOR_USER, "password": PASSWORD,
    }, timeout=4)
    return admin_token, r.json()["token"]


# ============================================================
def phase_c_operator(operator_token):
    """C: operator token 调 → control 通过, advanced/activate 拒绝"""
    print("\n========== C: operator 应被精确拦截 ==========")
    headers_op = {"Authorization": f"Bearer {operator_token}"}

    r = requests.post(
        f"{API}/api/v1/source/detection/recording-failures/clear?channel=0",
        headers=headers_op, timeout=4,
    )
    step("C1-operator recording-failures/clear (control)",
         r.status_code != 403, f"HTTP {r.status_code}")

    r = requests.post(
        f"{API}/api/v1/source/detection/reset-periodic?channel=0",
        headers=headers_op, timeout=4,
    )
    step("C2-operator reset-periodic 应被拒绝",
         r.status_code == 403, f"HTTP {r.status_code} {r.text[:100]}")

    r = requests.post(
        f"{API}/api/v1/source/detection/set-project?channel=0",
        json={"project_id": 1, "name": "x", "task_type": "detection",
              "logic_mode": "sequential", "steps_config": [], "pipeline_config": {},
              "events_config": [], "counters_config": [], "data_config": {}},
        headers=headers_op, timeout=4,
    )
    step("C3-operator set-project 应被拒绝",
         r.status_code == 403, f"HTTP {r.status_code}")

    r = requests.post(
        f"{API}/api/v1/source/detection/standby?channel=0",
        headers=headers_op, timeout=4,
    )
    step("C4-operator standby (control 应通过)",
         r.status_code != 403, f"HTTP {r.status_code}")


# ============================================================
def phase_d_anonymous():
    """D: 无 token (匿名 = operator) 调"""
    print("\n========== D: 匿名 = operator 默认权限 ==========")
    r = requests.post(
        f"{API}/api/v1/source/detection/recording-failures/clear?channel=0",
        timeout=4,
    )
    step("D1-匿名 recording-failures/clear (有 control)",
         r.status_code != 403, f"HTTP {r.status_code}")

    r = requests.post(
        f"{API}/api/v1/source/detection/reset-periodic?channel=0",
        timeout=4,
    )
    step("D2-匿名 reset-periodic 应被拒绝",
         r.status_code == 403, f"HTTP {r.status_code}")

    r = requests.post(
        f"{API}/api/v1/source/detection/set-project?channel=0",
        json={"project_id": 1, "name": "x", "task_type": "detection",
              "logic_mode": "sequential", "steps_config": [], "pipeline_config": {},
              "events_config": [], "counters_config": [], "data_config": {}},
        timeout=4,
    )
    step("D3-匿名 set-project 应被拒绝",
         r.status_code == 403, f"HTTP {r.status_code}")


# ============================================================
def phase_e_disable(admin_token):
    """E: admin 关闭鉴权 → 匿名 advanced 应当再次通过"""
    print("\n========== E: 关闭鉴权 收尾 ==========")
    headers_admin = {"Authorization": f"Bearer {admin_token}"}
    r = requests.post(f"{API}/api/v1/auth/disable-auth",
                      headers=headers_admin, timeout=4)
    step("E0-关闭鉴权", r.status_code == 200, f"HTTP {r.status_code}")

    r = requests.post(
        f"{API}/api/v1/source/detection/reset-periodic?channel=0",
        timeout=4,
    )
    step("E1-关闭后 reset-periodic 应回到放行",
         r.status_code != 403, f"HTTP {r.status_code}")


# ============================================================
def main():
    t0 = time.time()
    try:
        pre_cleanup()
        phase_a_auth_disabled()
        admin_token, operator_token = phase_b_admin()
        phase_c_operator(operator_token)
        phase_d_anonymous()
        phase_e_disable(admin_token)
    except Exception as e:
        step("UAT 异常中断", False, f"{type(e).__name__}: {e}")
    finally:
        elapsed = time.time() - t0
        failed = sum(1 for s in steps_log if not s["ok"])
        passed = len(steps_log) - failed
        with open(LOG, "w", encoding="utf-8") as f:
            f.write(f"UAT R1 监控/检测 require_perm 验证\n")
            f.write(f"耗时: {elapsed:.1f}s\n")
            f.write(f"通过: {passed}/{len(steps_log)}  失败: {failed}\n")
            f.write("=" * 60 + "\n")
            for s in steps_log:
                sym = "OK" if s["ok"] else "!!"
                f.write(f"[{sym}] {s['idx']:02d}. {s['label']}  {s['detail']}\n")
        print("\n" + "=" * 60)
        print(f"UAT 结束: 通过 {passed}/{len(steps_log)}  失败 {failed}  耗时 {elapsed:.1f}s")
        print(f"  日志: {LOG}")
        print("=" * 60)


if __name__ == "__main__":
    main()
