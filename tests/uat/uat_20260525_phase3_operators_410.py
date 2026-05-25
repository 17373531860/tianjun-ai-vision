"""UAT — v3.10.0 ⑤c 阶段 3: /api/v1/operators/* 全部 410 Gone.

验收契约:
  1. 6 个 HTTP 端点 (含 5 个写操作 + 1 个读) 全部返回 410 Gone
  2. 响应头 X-Deprecated-Replacement: /api/v1/users
  3. 鉴权关闭 (默认) / 鉴权启用 (admin token) 两种状态下行为一致 — 410 抢在 require_perm 之前
  4. 后端模块级 get_current_operator_id() 工具函数仍然 importable, 不影响 source_session_lifecycle_mixin
  5. 检测主流程冒烟: 后端 /source/* 健康检查 + /data/sessions 列表 不崩 (旧 operator_id 字段返回 null 不报错)

注意: 本 UAT 是后端纯 HTTP 验证 (无浏览器), 用 requests 直接打.
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

# ============================================================
API = "http://127.0.0.1:8001"

TS = int(time.time())
ADMIN_USER = f"__uat_phase3_admin_{TS}"
PASSWORD = "Uat123456"

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    sym = "OK" if ok else "!!"
    print(f"[{sym}] {rec['idx']:02d}. {label}  {detail}", flush=True)


# ============================================================
def pre_cleanup():
    print("\n========== 前置清理 ==========", flush=True)
    r = requests.get(f"{API}/api/v1/auth/status", timeout=4)
    status = r.json()

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.join(repo_root, "backend", "sql_app.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM session_tokens")
        conn.execute("DELETE FROM user_roles")
        n = conn.execute("DELETE FROM users").rowcount
        conn.execute(
            "UPDATE system_configs SET value='false' WHERE key='auth.enabled'"
        )
        conn.commit()
        print(f"  sqlite 清理: 删 {n} users, auth=off", flush=True)
    finally:
        conn.close()

    if status.get("auth_enabled") or status.get("user_count", 0) > 0:
        print("  此前是启用状态, 重启后端清缓存...", flush=True)
        os.system('pkill -f "uvicorn backend.main:app" 2>/dev/null')
        time.sleep(2)
        os.system(
            f'cd "{repo_root}" && setsid -f python -m uvicorn backend.main:app '
            '--host 0.0.0.0 --port 8001 > /tmp/tianjun_backend.log 2>&1'
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
# 验证 6 个端点 410
# ============================================================
ENDPOINTS = [
    ("GET",    "/api/v1/operators",                  None),
    ("POST",   "/api/v1/operators",                  {"name": "X", "employee_no": "Y"}),
    ("PUT",    "/api/v1/operators/1",                {"name": "X"}),
    ("DELETE", "/api/v1/operators/1",                None),
    ("POST",   "/api/v1/operators/set-current",      {"channel_id": 0, "operator_id": 1}),
    ("GET",    "/api/v1/operators/current?channel_id=0",  None),
]


def check_one(method, path, body, headers=None):
    r = requests.request(method, f"{API}{path}",
                         json=body if body else None,
                         headers=headers or {},
                         timeout=4)
    is_410 = r.status_code == 410
    has_dep_header = r.headers.get("X-Deprecated-Replacement", "") == "/api/v1/users"
    return is_410 and has_dep_header, r.status_code, r.headers.get("X-Deprecated-Replacement", "")


def phase_a_auth_off():
    print("\n========== Phase A: 鉴权关闭 → 6 端点全部 410 ==========",
          flush=True)
    for method, path, body in ENDPOINTS:
        ok, code, dep = check_one(method, path, body)
        step(f"A-{method} {path}", ok,
             f"HTTP {code} X-Deprecated-Replacement={dep!r}")


def api_setup_admin():
    r = requests.post(
        f"{API}/api/v1/auth/enable-auth",
        json={
            "admin_username": ADMIN_USER,
            "admin_password": PASSWORD,
            "admin_display_name": "UAT 阶段3 管理员",
        }, timeout=8,
    )
    step("启用鉴权 + 创 admin", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError("enable-auth 失败")
    r = requests.post(
        f"{API}/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": PASSWORD},
        timeout=4,
    )
    return r.json().get("token")


def phase_b_auth_on(admin_token):
    print("\n========== Phase B: 鉴权启用 (admin) → 6 端点仍 410 ==========",
          flush=True)
    H = {"Authorization": f"Bearer {admin_token}"}
    for method, path, body in ENDPOINTS:
        ok, code, dep = check_one(method, path, body, headers=H)
        step(f"B-{method} {path}", ok,
             f"HTTP {code} X-Deprecated-Replacement={dep!r}")


def phase_c_import_smoke():
    """import smoke (v3.10+ 阶段 5 修订):
    - 阶段 3 时: get_current_operator_id 仍可 import (本测试通过)
    - 阶段 5 后: 该函数已彻底删除, source_session_lifecycle_mixin 改用
      backend.core.auth.get_current_user_id, 不再引用旧函数
    """
    print("\n========== Phase C: 工具函数迁移到新版 (阶段 5) ==========",
          flush=True)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    import sys as _sys
    if repo_root not in _sys.path:
        _sys.path.insert(0, repo_root)

    # 旧函数应已删 (阶段 5 完成)
    try:
        from backend.api.operators import get_current_operator_id  # noqa
        # 还能 import = 阶段 5 未执行 → 失败
        step("C-get_current_operator_id 已彻底删除 (不可 import)",
             False, "意外仍可 import; 阶段 5 应已清理")
    except ImportError:
        step("C-get_current_operator_id 已彻底删除 (不可 import)",
             True, "阶段 5 清理完成")
    except Exception as e:
        step("C-get_current_operator_id 已彻底删除 (不可 import)",
             False, f"未预期异常: {e}")

    # 新函数 get_current_user_id 应可 import
    try:
        from backend.core.auth import get_current_user_id  # noqa
        step("C-新版 get_current_user_id 可 import", True, "")
    except Exception as e:
        step("C-新版 get_current_user_id 可 import", False, str(e))

    # source_session_lifecycle_mixin 内不再引用旧函数, 应改为新函数
    p_mixin = os.path.join(
        repo_root, "backend", "api", "source_session_lifecycle_mixin.py"
    )
    with open(p_mixin, "r", encoding="utf-8") as f:
        txt = f.read()
    old_cnt = txt.count("get_current_operator_id")
    new_cnt = txt.count("get_current_user_id")
    step("C-source_session_lifecycle 旧函数引用全部消失",
         old_cnt == 0, f"旧引用 {old_cnt} 处")
    step("C-source_session_lifecycle 改用新函数 (3 import + 3 call = 6)",
         new_cnt == 6, f"新引用 {new_cnt} 处")


def phase_d_smoke():
    """冒烟: /source/active-channel + /data 列表能正常调."""
    print("\n========== Phase D: 检测主流程冒烟 ==========", flush=True)
    # /source/active-channel — 老路径但稳定健康检查
    r = requests.get(f"{API}/api/v1/source/active-channel", timeout=4)
    step("D-/source/active-channel 健康", r.status_code in (200, 404),
         f"HTTP {r.status_code}")

    # /data/sessions 列表
    r = requests.get(f"{API}/api/v1/data/sessions", timeout=4)
    step("D-/data/sessions 列表", r.status_code == 200,
         f"HTTP {r.status_code} len={len(r.text)}")


def main():
    pre_cleanup()
    try:
        phase_a_auth_off()
        token = api_setup_admin()
        phase_b_auth_on(token)
        phase_c_import_smoke()
        phase_d_smoke()
    finally:
        # 收尾: 关 auth
        try:
            r = requests.get(f"{API}/api/v1/auth/status", timeout=3)
            if r.json().get("auth_enabled"):
                # 用任意手段关 auth — 因为我们刚创了 admin 还在
                login = requests.post(
                    f"{API}/api/v1/auth/login",
                    json={"username": ADMIN_USER, "password": PASSWORD},
                    timeout=3,
                )
                tk = login.json().get("token")
                requests.post(
                    f"{API}/api/v1/auth/disable-auth",
                    headers={"Authorization": f"Bearer {tk}"}, timeout=3,
                )
        except Exception as e:
            print(f"收尾失败: {e}", flush=True)

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
