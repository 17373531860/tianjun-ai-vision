"""上银 SY 包装线 — 从零配置引导脚本 (UAT Phase 0).

前置: 后端 8001 (RUNTIME_MODE=test) + mock MES 9100 已起, DB 已清空只留项目+模型.
作用: 把"空白软件"一步步配成上银 SY 包装线可用状态, 全程打印每步结果可核对.
  1. 启用账号鉴权 + 建超级管理员
  2. 建操作员 (operator 角色: 只能看监控+开始检测, 无人工确认权 → 触发提权)
  3. 建 MES 连接 (指向 mock, 上银真实返回格式) + 拉单自测 JOB150300021-3 → 期望 96 滑块
  4. 建虚拟扫码枪 (usb_hid)
  5. 建包装结算配置 (滑块口径/每箱96/补连字符pos12/缺油嘴/塞工单/待机不结算/异常→事件3人工确认)

不带 test_ 前缀: 不进 pytest, 人工 `python tests/uat/uat_20260622_sy_packaging_bootstrap.py` 触发.
"""
import json
import sys

import requests

API = "http://127.0.0.1:8001/api/v1"
MOCK = "http://127.0.0.1:9100"
ADMIN_U, ADMIN_P = "admin", "admin123456"
OP_U, OP_P = "op001", "op123456"
SY_PROJECT_ID = 10
PACK_EVENT_ACK = 3  # SY 项目事件: "包装异常-需人工确认" (require_ack=true)

ok = []
def step(label, good, detail=""):
    ok.append(good)
    print(f"[{'OK' if good else '!!'}] {len(ok):02d}. {label}  {detail}")


def main():
    s = requests.Session()

    # ── 1. 启用鉴权 + 建管理员 ───────────────────────────────
    r = s.post(f"{API}/auth/enable-auth", json={
        "admin_username": ADMIN_U, "admin_password": ADMIN_P,
        "admin_display_name": "系统管理员"})
    if r.status_code == 400 and "已启用" in r.text:
        step("启用鉴权(已启用,跳过)", True)
    else:
        step("启用鉴权+建管理员", r.ok, f"HTTP {r.status_code}")

    r = s.post(f"{API}/auth/login", json={"username": ADMIN_U, "password": ADMIN_P})
    step("管理员登录", r.ok, f"HTTP {r.status_code}")
    token = r.json().get("token")
    H = {"Authorization": f"Bearer {token}"}

    # ── 2. 建操作员 ──────────────────────────────────────────
    r = s.post(f"{API}/users", headers=H, json={
        "username": OP_U, "password": OP_P, "display_name": "产线操作员",
        "role_codes": ["operator"]})
    step("建操作员(operator)", r.ok or "已存在" in r.text, f"HTTP {r.status_code}")

    # ── 3. MES 连接 (指向 mock, 上银格式) + 拉单自测 ─────────
    pull_cfg = {
        "url": f"{MOCK}/hiwin/webcn/ai_error_prevention_job_info/query",
        "method": "POST",
        "content_type": "application/json",
        "request_body_template": json.dumps(
            {"api": "ai_error_prevention_job_info",
             "parameters": {"job_no": "{job_no}"}}),
        "success_path": "statusCode",
        "success_value": 200,
        "array_path": "response.resultData",
        "field_mapping": {
            "order_no": "job_no",
            "customer_name": "cust_name",
            "product_spec": "spec",
            "planned_qty": "dispatch_qty",
        },
        "import_mode": "upsert",
        "timeout_sec": 10,
        "verify_ssl": False,
    }
    r = s.post(f"{API}/mes/gateway/connections", headers=H, json={
        "name": "上银MES(mock)", "adapter_type": "rest", "enabled": True,
        "config": {"pull": pull_cfg},
        "pull_enabled": False, "bound_channels": [0]})
    step("建 MES 连接(指向mock)", r.ok, f"HTTP {r.status_code}")
    conn_id = r.json().get("id") if r.ok else None

    # 拉单自测: 处理后的工单号 JOB150300021-3 → 期望 dispatch_qty=96 / spec=SY
    r = s.post(f"{API}/mes/gateway/pull-test", headers=H,
               json={"pull_config": pull_cfg, "job_no": "JOB150300021-3"})
    body = r.json() if r.ok else {}
    raw = json.dumps(body, ensure_ascii=False)
    got96 = ("96" in raw and "SY" in raw)
    step("拉单自测 JOB150300021-3 → 96滑块/SY", r.ok and got96,
         f"HTTP {r.status_code} {raw[:200]}")

    # ── 4. 虚拟扫码枪 (usb_hid, 无网络) ──────────────────────
    r = s.post(f"{API}/scanner/devices", headers=H, json={
        "name": "虚拟扫码枪", "ip": "", "port": 0, "channel_id": 0,
        "device_type": "usb_hid", "enabled": True})
    step("建虚拟扫码枪(usb_hid)", r.ok, f"HTTP {r.status_code}")
    scan_id = r.json().get("id") if r.ok else None

    # ── 5. 包装结算配置 (上银 SY) ────────────────────────────
    pack = {
        "name": "上银SY包装线", "enabled": True, "channel_id": 0,
        "scan_device_id": scan_id, "pull_conn_id": conn_id,
        # 箱数 = MES dispatch_qty(滑块总数) / 每箱96
        "box_count_source": "field", "box_count_field": "dispatch_qty",
        "count_unit": "sliders",
        "items_per_box_source": "config", "items_per_box_fixed": 96,
        "slider_total_field": "dispatch_qty",
        # 条码: 扫码枪丢 '-', 软件第12位后补 '-'
        "label_match": "insert_char", "hyphen_template": "-", "hyphen_pos": 12,
        # 异常策略: 查无此单/标签不符/漏箱 → 都触发事件3(需人工确认)硬阻断
        "on_mes_fail": "block", "on_label_mismatch": "block", "on_short_box": "redo",
        # 待机维持检测不结算
        "forced_settle_on_standby": False, "on_forced_stop": "settle",
        # 缺油嘴(每箱) + 塞工单(尾箱) gate
        "oil_nozzle_required": True, "oil_nozzle_step_label": "放油嘴包",
        "tail_paper_order_required": True, "tail_paper_step_label": "放工单",
        # 异常 → SY 项目事件3 (require_ack 弹人工确认)
        "event_short_box": PACK_EVENT_ACK, "event_over_box": PACK_EVENT_ACK,
        "event_mes_fail": PACK_EVENT_ACK, "event_label_mismatch": PACK_EVENT_ACK,
        "event_missing_paper": PACK_EVENT_ACK, "event_missing_nozzle": PACK_EVENT_ACK,
    }
    r = s.post(f"{API}/packaging-flows", headers=H, json=pack)
    step("建包装结算配置(上银SY)", r.ok, f"HTTP {r.status_code} {r.text[:200] if not r.ok else ''}")
    pack_id = r.json().get("id") if r.ok else None

    # ── 核对: 配置加载 + 状态快照 ───────────────────────────
    r = s.get(f"{API}/packaging-flows", headers=H)
    cfgs = r.json() if r.ok else []
    if not isinstance(cfgs, list):
        cfgs = []
    step("包装配置已落库", any(isinstance(c, dict) and c.get("name") == "上银SY包装线" for c in cfgs),
         f"共 {len(cfgs)} 条")

    print("\n==== 配置结果 ====")
    print(f"admin token: {token[:12]}...  conn_id={conn_id}  scan_id={scan_id}  pack_id={pack_id}")
    print(f"通过 {sum(ok)}/{len(ok)}")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
