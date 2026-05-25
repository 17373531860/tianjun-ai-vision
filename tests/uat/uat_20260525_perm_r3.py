"""UAT — R3 (④b-2 第三轮): 扫码器/外设/集群/数据/工位/系统/旧表/插件 require_perm 验证.

每模块挑代表端点验 admin/operator/匿名 3 个身份, 矩阵化的 perm 断言.

测试矩阵 (auth_enabled=true 状态下):
  | 模块                | 端点                                  | 需 perm                    | admin | operator | 匿名 |
  |---------------------|--------------------------------------|----------------------------|-------|----------|------|
  | 扫码器              | POST /scanner/devices                | mes.scanner.edit           |  ≠403 |  403     | 403  |
  | WMax                | POST /scanner/wmax/connect           | mes.scanner.edit           |  ≠403 |  403     | 403  |
  | 外设                | POST /external-devices/              | mes.external.edit          |  ≠403 |  403     | 403  |
  | 外设 barcode        | POST /external-devices/barcode       | mes.scanner.edit           |  ≠403 |  403     | 403  |
  | 集群配置            | PUT  /cluster/config                 | mes.cluster.edit           |  ≠403 |  403     | 403  |
  | 数据清理            | POST /data/cleanup/run               | data.cleanup               |  ≠403 |  403     | 403  |
  | 自定义导出          | POST /export/templates               | data.export                |  ≠403 |  403     | 403  |
  | 实时规则            | POST /export/realtime-rules          | data.export                |  ≠403 |  403     | 403  |
  | 定时规则            | POST /export/scheduled-rules         | data.export                |  ≠403 |  403     | 403  |
  | 工位                | POST /workstations/mode              | settings.edit              |  ≠403 |  403     | 403  |
  | 系统显示            | PUT  /system/display                 | settings.edit              |  ≠403 |  403     | 403  |
  | 旧操作员            | POST /operators                      | settings.operators.edit    |  ≠403 |  403     | 403  |
  | 旧相机              | POST /cameras                        | source.edit                |  ≠403 |  403     | 403  |
  | 插件                | DELETE /plugins/__uat_dummy_xxx__    | system.plugin.manage       |  ≠403 |  403     | 403  |

共 14 模块 × 3 角色 = 42 条断言. 注: 业务侧可能返回 422/400/500/404,
但只要不是 403 就说明 perm 通过, 业务报错与权限无关.

M2M 端点 (cluster.report/heartbeat, mes/orders/receive, license-cache) 留给 R4 API Key 体系,
本 UAT 不覆盖.
"""
import os
import sqlite3
import time

import requests

API = "http://127.0.0.1:8001"
TS = int(time.time())
ADMIN_USER = f"__uat_r3_admin_{TS}"
OPERATOR_USER = f"__uat_r3_op_{TS}"
PASSWORD = "Uat123456"
LOG = "/tmp/uat_perm_r3_run.log"

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
        conn.execute("UPDATE system_configs SET value='false' WHERE key='auth.enabled'")
        conn.commit()
        print(f"  sqlite 清理: 删 {n} 个用户")
    finally:
        conn.close()

    r = requests.get(f"{API}/api/v1/auth/status", timeout=4)
    if r.json().get("auth_enabled"):
        print("  auth 此前启用, 重启后端...")
        os.system('pkill -f "uvicorn backend.main:app" 2>/dev/null')
        time.sleep(2)
        os.system(
            f'cd "{repo_root}" && setsid -f python -m uvicorn backend.main:app '
            '--host 0.0.0.0 --port 8001 > /tmp/tianjun_backend.log 2>&1 < /dev/null'
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
    step("前置-清理完成", True, "auth=disabled, users=0")


def setup_users():
    r = requests.post(f"{API}/api/v1/auth/enable-auth", json={
        "admin_username": ADMIN_USER,
        "admin_password": PASSWORD,
        "admin_display_name": "UAT-R3 管理员",
    }, timeout=8)
    assert r.status_code == 200, f"enable-auth 失败: {r.text[:120]}"
    step("setup-启用鉴权 + 建 admin", True, "")

    r = requests.post(f"{API}/api/v1/auth/login", json={
        "username": ADMIN_USER, "password": PASSWORD,
    }, timeout=4)
    admin_token = r.json()["token"]
    headers_admin = {"Authorization": f"Bearer {admin_token}"}

    r = requests.post(f"{API}/api/v1/users",
                      headers=headers_admin,
                      json={
                          "username": OPERATOR_USER,
                          "password": PASSWORD,
                          "display_name": "UAT-R3 操作员",
                          "role_codes": ["operator"],
                      }, timeout=4)
    assert r.status_code == 200, f"建 operator 失败: {r.text[:120]}"

    r = requests.post(f"{API}/api/v1/auth/login", json={
        "username": OPERATOR_USER, "password": PASSWORD,
    }, timeout=4)
    op_token = r.json()["token"]
    step("setup-建 operator + 拿 token", True, "")
    return admin_token, op_token


# 端点矩阵: (模块名, METHOD, URL, payload, [需 perm — 仅记录用])
# payload 故意填非法或最简值 — 我们只测 perm, 不测业务. 状态码只要 != 403 就算 perm 通过.
ENDPOINTS = [
    ("扫码器-create",      "POST",   "/api/v1/scanner/devices",
        {"name": "uat_dummy", "ip": "1.2.3.4"}),
    ("WMax-connect",       "POST",   "/api/v1/scanner/wmax/connect",
        {"ip": "1.2.3.4"}),
    ("外设-create",        "POST",   "/api/v1/external-devices/",
        {"name": "uat_dummy", "device_type": "weighing", "protocol": "modbus_rtu",
         "port": "/dev/null", "baudrate": 9600}),
    ("外设-barcode",       "POST",   "/api/v1/external-devices/barcode",
        {"channel_id": 0, "barcode": "uat_dummy"}),
    ("集群-config",        "PUT",    "/api/v1/cluster/config",
        {"role": "standalone"}),
    ("数据-cleanup/run",   "POST",   "/api/v1/data/cleanup/run",
        None),
    ("导出-templates",     "POST",   "/api/v1/export/templates",
        {"name": "uat_dummy", "format": "txt", "scope": "cycle_end", "content": "x"}),
    ("导出-realtime",      "POST",   "/api/v1/export/realtime-rules",
        {"name": "uat_dummy", "template_id": 999999, "trigger_event": "cycle_end",
         "output_dir": "/tmp"}),
    ("导出-scheduled",     "POST",   "/api/v1/export/scheduled-rules",
        {"name": "uat_dummy", "template_id": 999999, "cron": "0 0 * * *",
         "output_dir": "/tmp"}),
    ("工位-mode",          "POST",   "/api/v1/workstations/mode",
        {"mode": "single"}),
    ("系统显示-PUT",       "PUT",    "/api/v1/system/display",
        {"brand_name": "uat_dummy"}),
    ("旧操作员-create",    "POST",   "/api/v1/operators",
        {"name": "uat_dummy"}),
    ("旧相机-create",      "POST",   "/api/v1/cameras",
        {"name": "uat_dummy", "source": "0"}),
    ("插件-delete",        "DELETE", "/api/v1/plugins/__uat_dummy_xxx__",
        None),
]


def call(method, url, payload, headers=None):
    kwargs = {"timeout": 5, "headers": headers or {}}
    if payload is not None:
        kwargs["json"] = payload
    return requests.request(method, f"{API}{url}", **kwargs)


def phase_admin(token):
    print("\n========== A: admin (*) — 全部应 perm 通过 (业务可能 4xx) ==========")
    h = {"Authorization": f"Bearer {token}"}
    for name, method, url, payload in ENDPOINTS:
        r = call(method, url, payload, headers=h)
        step(f"admin {name}", r.status_code != 403,
             f"HTTP {r.status_code}")


def phase_operator(token):
    print("\n========== B: operator — 全部应被 403 拒绝 ==========")
    h = {"Authorization": f"Bearer {token}"}
    for name, method, url, payload in ENDPOINTS:
        r = call(method, url, payload, headers=h)
        step(f"operator {name} 应 403", r.status_code == 403,
             f"HTTP {r.status_code}")


def phase_anonymous():
    print("\n========== C: 匿名 (无 token) — 全部应被 403 拒绝 ==========")
    for name, method, url, payload in ENDPOINTS:
        r = call(method, url, payload)
        step(f"匿名 {name} 应 403", r.status_code == 403,
             f"HTTP {r.status_code}")


def phase_disable(admin_token):
    print("\n========== D: 收尾 关闭鉴权 ==========")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = requests.post(f"{API}/api/v1/auth/disable-auth",
                      headers=h, timeout=4)
    step("关闭鉴权", r.status_code == 200, f"HTTP {r.status_code}")
    # 关闭后匿名调 scanner/devices 应回到放行 (业务 4xx OK, 只要不是 403)
    r = requests.post(f"{API}/api/v1/scanner/devices",
                      json={"name": "uat", "ip": "1.2.3.4"}, timeout=4)
    step("关闭后匿名 scanner/devices 应放行",
         r.status_code != 403, f"HTTP {r.status_code}")


def main():
    t0 = time.time()
    try:
        pre_cleanup()
        admin_token, op_token = setup_users()
        phase_admin(admin_token)
        phase_operator(op_token)
        phase_anonymous()
        phase_disable(admin_token)
    except Exception as e:
        step("UAT 异常中断", False, f"{type(e).__name__}: {e}")
    finally:
        elapsed = time.time() - t0
        failed = sum(1 for s in steps_log if not s["ok"])
        passed = len(steps_log) - failed
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("UAT R3 扫码器/外设/集群/数据/工位/系统/旧表/插件 require_perm 验证\n")
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
