"""上银 SY 包装线 — 真实生产配置 (幂等, 可重复跑).

把真实 SY 项目 (id=10) + MES 连接 (id=1) + 包装结算配置 配好, 让"扫工单去-补-→拉
本机 mock MES→逐箱滑块结算→缺油嘴/塞工单 gate→漏箱/多装人工确认"整条链路在开发机
端到端可跑. 上线时只需把 MES 连接地址从本机 mock 改回客户那台 MES 机器 IP.

约定:
  - 后端 8011 (RUNTIME_MODE=test) + 本机 mock MES 9100 已起.
  - 滑块每箱数从 SY 项目读 (custom_mix_container_item_target=96), 不写死在包装配置.
  - 漏箱/多装/缺油嘴/缺工单/标签错 → 映射到 SY 项目里"需人工确认"事件 (id=3) →
    定格整条线 + 弹人工确认 (操作员无权限可借管理员密码提权).

跑法: /home/qianqian/anaconda3/envs/tianjun/bin/python tests/uat/setup_sy_packaging.py
"""
import json
import sys

import requests

API = "http://127.0.0.1:8011"
MOCK = "http://127.0.0.1:9100"
SY_PROJECT_ID = 10
MES_CONN_ID = 1
ACK_EVENT_ID = 3          # SY 项目里"包装异常-需人工确认"事件
PKG_CONFIG_NAME = "上银SY包装线"


def _j(r):
    try:
        return r.json()
    except Exception:
        return {"_status": r.status_code, "_text": r.text[:300]}


# ──────── 1) SY 项目加"需人工确认"事件 ────────
def ensure_ack_event():
    p = requests.get(f"{API}/api/v1/projects/{SY_PROJECT_ID}", timeout=10).json()
    events = list(p.get("events_config") or [])
    if any(str(e.get("id")) == str(ACK_EVENT_ID) for e in events):
        print(f"  · 事件 id={ACK_EVENT_ID} 已存在, 跳过")
    else:
        events.append({
            "id": ACK_EVENT_ID,
            "name": "包装异常-需人工确认",
            "color": "#ef4444",
            "actions": [{"counter_name": "异常总数", "delta": 1}],
            "show_notification": True,
            "notification_type": "error",
            "toast_id": "ng",
            "require_ack": True,       # 关键: 触发定格 + 人工确认
            "ack_timeout_sec": 0,      # 0 = 永不超时, 必须人工点
        })
        r = requests.put(f"{API}/api/v1/projects/{SY_PROJECT_ID}",
                         json={"events_config": events}, timeout=10)
        assert r.status_code in (200, 201), f"加事件失败 {r.status_code} {r.text}"
        print(f"  · 已加 require_ack 事件 id={ACK_EVENT_ID}")
    # 确保容器每箱目标 = 96
    pc = p.get("pipeline_config") or {}
    print(f"  · SY 容器每箱滑块目标 = {pc.get('custom_mix_container_item_target')}")


# ──────── 2) MES 连接指向本机 mock ────────
def point_conn_to_mock():
    conns = requests.get(f"{API}/api/v1/mes/gateway/connections", timeout=10).json()
    conn = next((c for c in conns if c["id"] == MES_CONN_ID), None)
    assert conn is not None, f"MES 连接 {MES_CONN_ID} 不存在"
    cfg = dict(conn.get("config") or {})
    cfg["pull"] = {
        "enabled": True,
        "url": f"{MOCK}/hiwin/webcn/ai_error_prevention_job_info/query",
        "method": "POST",
        "content_type": "application/json; charset=UTF-8",
        "request_body_template":
            '{"api":"hiwin/webcn/ai_error_prevention_job_info/query",'
            '"parameters":{"job_no":"{job_no}"}}',
        "success_path": "statusCode", "success_value": 200,
        "array_path": "response.resultData",
        "field_mapping": {"order_no": "job_no", "customer_name": "cust_name",
                          "product_spec": "spec", "planned_qty": "dispatch_qty"},
        "timeout_sec": 10, "retry_count": 0,
    }
    r = requests.put(f"{API}/api/v1/mes/gateway/connections/{MES_CONN_ID}",
                     json={"enabled": True, "pull_enabled": True, "config": cfg}, timeout=10)
    assert r.status_code in (200, 201), f"改连接失败 {r.status_code} {r.text}"
    print(f"  · MES 连接 {MES_CONN_ID} 已指向本机 mock (上线改回客户 MES 机器 IP)")


# ──────── 3) 包装结算配置 ────────
def ensure_pkg_config():
    existing = requests.get(f"{API}/api/v1/packaging-flows", timeout=10).json()
    items = existing.get("items", []) if isinstance(existing, dict) else (existing or [])
    body = {
        "name": PKG_CONFIG_NAME, "enabled": True, "channel_id": 0,
        "pull_conn_id": MES_CONN_ID,
        # 滑块口径 + 每箱数从项目读 (SY=96)
        "count_unit": "sliders",
        "items_per_box_source": "project",
        "slider_total_field": "dispatch_qty",
        # 连字符还原: 扫码去-→第12位后补回-
        "label_match": "insert_char", "hyphen_template": "-", "hyphen_pos": 12,
        # 异常策略
        "on_mes_fail": "block", "on_label_mismatch": "block", "on_short_box": "redo",
        # 收尾: 停止才收尾, 待机只暂停不结算 (待机维持检测/工单不收尾, 可恢复继续)
        "on_forced_stop": "settle", "forced_settle_on_standby": False,
        # 缺油嘴 gate (每箱) + 尾箱塞工单 gate
        "oil_nozzle_required": True, "oil_nozzle_step_label": "放油嘴包",
        "tail_paper_order_required": True, "tail_paper_step_label": "放工单",
        # 异常 → 项目事件映射 (硬阻断类全指 require_ack 事件 3; 拉单失败用 NG 提示 2)
        "event_short_box": ACK_EVENT_ID, "event_over_box": ACK_EVENT_ID,
        "event_box_ng": ACK_EVENT_ID, "event_label_mismatch": ACK_EVENT_ID,
        "event_missing_nozzle": ACK_EVENT_ID, "event_missing_paper": ACK_EVENT_ID,
        "event_mes_fail": 2,
    }
    cur = next((c for c in items if c.get("name") == PKG_CONFIG_NAME), None)
    if cur:
        cid = cur["id"]
        r = requests.put(f"{API}/api/v1/packaging-flows/{cid}", json=body, timeout=10)
        assert r.status_code in (200, 201), f"更包装配置失败 {r.status_code} {r.text}"
        print(f"  · 包装配置已更新 id={cid}")
    else:
        r = requests.post(f"{API}/api/v1/packaging-flows", json=body, timeout=10)
        assert r.status_code in (200, 201), f"建包装配置失败 {r.status_code} {r.text}"
        cid = _j(r).get("id")
        print(f"  · 包装配置已新建 id={cid}")
    return cid


def main():
    print("=== 上银 SY 包装线生产配置 ===")
    ensure_ack_event()
    point_conn_to_mock()
    cid = ensure_pkg_config()
    # 校验
    st = requests.get(f"{API}/api/v1/packaging-flows/{cid}/state", timeout=10)
    print(f"  · 包装配置 {cid} 已载入协调器: HTTP {st.status_code}")
    print(json.dumps(_j(st).get("config", {}), ensure_ascii=False, indent=1)[:600])
    print("=== 配置完成 ===")
    return cid


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"[FAIL] {e}")
        sys.exit(1)
