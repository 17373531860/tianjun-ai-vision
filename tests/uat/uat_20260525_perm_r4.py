"""UAT — R4 (④b-2 第四轮): M2M API Key 体系验证.

测试矩阵:

| 阶段                             | 4 个 M2M 端点 cluster/report/heartbeat/mes-receive/license-cache |
|----------------------------------|-----------------------------------------------------------------|
| A. auth=off 匿名                 | 全部 ≠403 (放行, 业务可能 4xx)                                  |
| B. auth=on  匿名 (无 key)        | 全部 401 (缺 Header)                                            |
| C. auth=on  错 key               | 全部 403 (key 不存在)                                            |
| D. auth=on  对 key 但 scope 不匹配 | 全部 403 (拿 cluster scope key 调 mes.receive)                  |
| E. auth=on  对 key + scope 匹配  | 全部 ≠403 (业务可能 4xx)                                         |
| F. auth=on  禁用 key             | 全部 403                                                         |
| G. CRUD     create/toggle/delete | 经 admin 全通                                                   |

加上 admin 用户 token 调 CRUD = 总计约 30+ 步.
"""
import os
import sqlite3
import time

import requests

API = "http://127.0.0.1:8001"
TS = int(time.time())
ADMIN_USER = f"__uat_r4_admin_{TS}"
PASSWORD = "Uat123456"
LOG = "/tmp/uat_perm_r4_run.log"

# M2M 端点矩阵: (label, METHOD, URL, scope, payload)
M2M_ENDPOINTS = [
    ("cluster-report",   "POST", "/api/v1/cluster/report",
        "cluster",
        {"station_id": 1, "box_serial": "uat", "cycle_data": {}}),
    ("cluster-heartbeat","POST", "/api/v1/cluster/heartbeat",
        "cluster",
        {"station_id": 1, "status": "online"}),
    ("mes-orders-receive","POST","/api/v1/mes/orders/receive",
        "mes.receive",
        {"order_no": "uat_dummy", "product_no": "X"}),
    ("license-cache-put","PUT",  "/api/v1/system/license-cache",
        "license.cache",
        {"data": "dummy"}),
]

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    sym = "OK" if ok else "!!"
    print(f"[{sym}] {rec['idx']:02d}. {label}  {detail}")


def pre_cleanup():
    print("\n========== 前置清理 ==========")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.join(repo_root, "backend", "sql_app.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM session_tokens")
        conn.execute("DELETE FROM user_roles")
        n = conn.execute("DELETE FROM users").rowcount
        m = conn.execute("DELETE FROM api_keys").rowcount
        conn.execute("UPDATE system_configs SET value='false' WHERE key='auth.enabled'")
        conn.commit()
        print(f"  sqlite 清理: 删 {n} 用户, {m} api_key")
    finally:
        conn.close()

    r = requests.get(f"{API}/api/v1/auth/status", timeout=4)
    if r.json().get("auth_enabled"):
        print("  auth 此前启用, 重启后端...")
        os.system('pkill -f "uvicorn backend.main:app" 2>/dev/null')
        time.sleep(2)
        os.system(
            f'cd "{repo_root}" && nohup python -m uvicorn backend.main:app '
            '--host 0.0.0.0 --port 8001 > /tmp/tianjun_backend.log 2>&1 &'
        )
        for i in range(25):
            time.sleep(1)
            try:
                r = requests.get(f"{API}/api/v1/auth/status", timeout=2)
                if r.status_code == 200 and not r.json().get("auth_enabled"):
                    step("前置-重启后端", True, f"等 {i+1}s")
                    return
            except Exception:
                continue
        raise RuntimeError("后端重启失败")
    step("前置-清理完成", True, "auth=disabled, users=0, keys=0")


def phase_a_auth_off():
    print("\n========== A: auth=off — M2M 全放行 (无 key 也通) ==========")
    for name, method, url, _scope, payload in M2M_ENDPOINTS:
        r = requests.request(method, f"{API}{url}", json=payload, timeout=4)
        step(f"A {name}", r.status_code != 403,
             f"HTTP {r.status_code}")


def setup_admin():
    r = requests.post(f"{API}/api/v1/auth/enable-auth", json={
        "admin_username": ADMIN_USER,
        "admin_password": PASSWORD,
        "admin_display_name": "UAT-R4 管理员",
    }, timeout=8)
    assert r.status_code == 200, f"enable-auth 失败: {r.text[:120]}"
    step("setup-启用鉴权 + 建 admin", True, "")

    r = requests.post(f"{API}/api/v1/auth/login", json={
        "username": ADMIN_USER, "password": PASSWORD,
    }, timeout=4)
    return r.json()["token"]


def phase_b_no_key():
    print("\n========== B: auth=on, 无 key — 应 401 ==========")
    for name, method, url, _scope, payload in M2M_ENDPOINTS:
        r = requests.request(method, f"{API}{url}", json=payload, timeout=4)
        step(f"B {name} 无 key 应 401", r.status_code == 401,
             f"HTTP {r.status_code}")


def phase_c_wrong_key():
    print("\n========== C: auth=on, 错 key — 应 403 ==========")
    h = {"X-API-Key": "tk_DUMMY_NOT_EXISTS_XXXXXXXXXXXXXXXXXX"}
    for name, method, url, _scope, payload in M2M_ENDPOINTS:
        r = requests.request(method, f"{API}{url}",
                             json=payload, headers=h, timeout=4)
        step(f"C {name} 错 key 应 403", r.status_code == 403,
             f"HTTP {r.status_code}")


def phase_g_create_keys(admin_token):
    """用 admin 创 4 个 scope 的 key, 返回 dict{scope: plaintext}"""
    print("\n========== G: admin 创建 4 个 scope key ==========")
    h = {"Authorization": f"Bearer {admin_token}"}
    keys = {}
    ids = {}
    for scope in ["cluster", "mes.receive", "license.cache", "*"]:
        r = requests.post(f"{API}/api/v1/api-keys",
                          headers=h, timeout=4,
                          json={"name": f"uat-{scope}", "scope": scope})
        ok = r.status_code == 200 and "plaintext" in r.json()
        step(f"G 创建 key scope={scope}", ok, f"HTTP {r.status_code}")
        if ok:
            j = r.json()
            keys[scope] = j["plaintext"]
            ids[scope] = j["id"]

    # 验 list 端点能列出来
    r = requests.get(f"{API}/api/v1/api-keys", headers=h, timeout=4)
    step("G list keys", r.status_code == 200 and len(r.json()) == 4,
         f"HTTP {r.status_code} count={len(r.json()) if r.status_code==200 else '?'}")
    return keys, ids


def phase_d_scope_mismatch(keys):
    """用 cluster scope key 调 mes.receive 端点 → 403"""
    print("\n========== D: auth=on, scope 不匹配 — 应 403 ==========")
    cluster_key = keys.get("cluster")
    if not cluster_key:
        step("D 跳过", False, "缺 cluster key"); return
    h = {"X-API-Key": cluster_key}
    # 用 cluster key 调 mes-orders-receive 应 403
    r = requests.post(f"{API}/api/v1/mes/orders/receive",
                      headers=h, timeout=4,
                      json={"order_no": "uat", "product_no": "X"})
    step("D cluster-key 调 mes/orders/receive 应 403",
         r.status_code == 403, f"HTTP {r.status_code}")
    # 用 cluster key 调 license-cache 应 403
    r = requests.put(f"{API}/api/v1/system/license-cache",
                     headers=h, timeout=4, json={"data": "x"})
    step("D cluster-key 调 license-cache 应 403",
         r.status_code == 403, f"HTTP {r.status_code}")


def phase_e_match(keys):
    print("\n========== E: auth=on, scope 匹配 — 应通过 ==========")
    for name, method, url, scope, payload in M2M_ENDPOINTS:
        key = keys.get(scope)
        if not key:
            step(f"E {name} 跳过", False, "缺 key"); continue
        h = {"X-API-Key": key}
        r = requests.request(method, f"{API}{url}",
                             json=payload, headers=h, timeout=4)
        step(f"E {name} scope={scope} 对 key 应通过",
             r.status_code != 403 and r.status_code != 401,
             f"HTTP {r.status_code}")

    # 加测: 用 * 的 key 调 cluster/report 应通过 (通配)
    star_key = keys.get("*")
    if star_key:
        h = {"X-API-Key": star_key}
        r = requests.post(f"{API}/api/v1/cluster/report",
                          headers=h, timeout=4,
                          json={"station_id": 1, "box_serial": "uat",
                                "cycle_data": {}})
        step("E *-key 调 cluster/report 应通过",
             r.status_code != 403 and r.status_code != 401,
             f"HTTP {r.status_code}")


def phase_f_disabled_key(admin_token, keys, ids):
    print("\n========== F: 禁用 key 后应 403 ==========")
    h_admin = {"Authorization": f"Bearer {admin_token}"}
    cluster_id = ids.get("cluster")
    cluster_key = keys.get("cluster")
    if not cluster_id or not cluster_key:
        step("F 跳过", False, "缺 cluster id/key"); return

    # toggle off
    r = requests.put(f"{API}/api/v1/api-keys/{cluster_id}/toggle",
                     headers=h_admin, timeout=4)
    step("F toggle cluster key off",
         r.status_code == 200 and r.json()["enabled"] is False,
         f"HTTP {r.status_code}")

    # 用 disabled cluster key 调 cluster/heartbeat 应 403
    h = {"X-API-Key": cluster_key}
    r = requests.post(f"{API}/api/v1/cluster/heartbeat",
                      headers=h, timeout=4,
                      json={"station_id": 1, "status": "online"})
    step("F disabled key 调 cluster/heartbeat 应 403",
         r.status_code == 403, f"HTTP {r.status_code}")

    # toggle 回来 (恢复 enabled)
    r = requests.put(f"{API}/api/v1/api-keys/{cluster_id}/toggle",
                     headers=h_admin, timeout=4)
    step("F toggle cluster key on",
         r.status_code == 200 and r.json()["enabled"] is True,
         f"HTTP {r.status_code}")


def phase_h_delete_key(admin_token, ids):
    print("\n========== H: 删除 key ==========")
    h = {"Authorization": f"Bearer {admin_token}"}
    star_id = ids.get("*")
    if not star_id:
        step("H 跳过", False, "缺 *-key id"); return
    r = requests.delete(f"{API}/api/v1/api-keys/{star_id}",
                        headers=h, timeout=4)
    step("H delete * key",
         r.status_code == 200 and r.json().get("ok") is True,
         f"HTTP {r.status_code}")

    # 再 list, 应剩 3 个
    r = requests.get(f"{API}/api/v1/api-keys", headers=h, timeout=4)
    step("H 删后 list 剩 3",
         r.status_code == 200 and len(r.json()) == 3,
         f"HTTP {r.status_code} count={len(r.json()) if r.status_code==200 else '?'}")


def phase_z_disable(admin_token):
    print("\n========== Z: 收尾 关闭鉴权 ==========")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = requests.post(f"{API}/api/v1/auth/disable-auth",
                      headers=h, timeout=4)
    step("Z 关闭鉴权", r.status_code == 200, f"HTTP {r.status_code}")
    # 关闭后匿名调 cluster/heartbeat 应放行
    r = requests.post(f"{API}/api/v1/cluster/heartbeat",
                      json={"station_id": 1, "status": "online"}, timeout=4)
    step("Z 关闭后匿名 cluster/heartbeat 应放行",
         r.status_code != 403, f"HTTP {r.status_code}")


def main():
    t0 = time.time()
    try:
        pre_cleanup()
        phase_a_auth_off()
        admin_token = setup_admin()
        phase_b_no_key()
        phase_c_wrong_key()
        keys, ids = phase_g_create_keys(admin_token)
        phase_d_scope_mismatch(keys)
        phase_e_match(keys)
        phase_f_disabled_key(admin_token, keys, ids)
        phase_h_delete_key(admin_token, ids)
        phase_z_disable(admin_token)
    except Exception as e:
        step("UAT 异常中断", False, f"{type(e).__name__}: {e}")
    finally:
        elapsed = time.time() - t0
        failed = sum(1 for s in steps_log if not s["ok"])
        passed = len(steps_log) - failed
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("UAT R4 M2M API Key 体系验证\n")
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
