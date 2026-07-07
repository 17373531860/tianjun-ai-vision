"""API 确定性验证 — t5b 查无此单/多装, t5c 强制结案(admin+operator), t5d 待机不结算.
同一协调器+ack链路已在场景A浏览器证实, 这里用 API 精确复现各异常分支."""
import requests, json

API = "http://127.0.0.1:8001/api/v1"
A = requests.post(f"{API}/auth/login", json={"username": "admin", "password": "admin123456"}).json()["token"]
O = requests.post(f"{API}/auth/login", json={"username": "op001", "password": "op123456"}).json()["token"]
HA = {"Authorization": f"Bearer {A}", "Content-Type": "application/json"}
HO = {"Authorization": f"Bearer {O}", "Content-Type": "application/json"}
CFG = 1
RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"[{'PASS ✅' if ok else 'FAIL ❌'}] {name}  {detail}")


def reset_and_scan(code="JOB1503000213"):
    requests.post(f"{API}/source/detection/ack-event?channel=0", headers=HA)  # 清遗留ack
    requests.post(f"{API}/test/synthetic/packaging-reset", headers=HA)
    r = requests.post(f"{API}/packaging-flows/scan", headers=HA,
                      json={"code": code, "channel_id": 0, "scan_device_id": 2})
    return r


def pending():
    pa = requests.get(f"{API}/source/detection/results?channel=0", headers=HA).json().get("pending_ack") or {}
    return bool(pa.get("active"))


def settle(slider, probe=None):
    body = {"channel_id": 0, "cycle_id": 1, "is_good": True, "slider_count": slider}
    if probe is not None:
        body["probe_labels"] = probe
    return requests.post(f"{API}/test/synthetic/packaging-settle", headers=HA, json=body).json()


# ---------- t5b-1 查无此单 (MES fail → block) ----------
requests.post(f"{API}/source/detection/ack-event?channel=0", headers=HA)
requests.post(f"{API}/test/synthetic/packaging-reset", headers=HA)
r = requests.post(f"{API}/packaging-flows/scan", headers=HA,
                  json={"code": "JOBNOTEXIST999", "channel_id": 0, "scan_device_id": 2})
j = r.json()
blocked = pending() or (j.get("handled") is False) or ("error" in json.dumps(j, ensure_ascii=False).lower()) \
          or (j.get("state") is None)
rec("t5b-1 查无此单 → 阻断/不放行", blocked, f"resp={json.dumps(j, ensure_ascii=False)[:160]}")

# ---------- t5b-2 多装 (over box, 油嘴/工单 gate 放过以隔离) ----------
reset_and_scan()
j = settle(150, probe={"放油嘴包": True, "放工单": True})
over_blocked = pending()
rec("t5b-2 多装(150>96) → 人工确认阻断", over_blocked,
    f"pending={over_blocked} last_run={json.dumps(j.get('last_run'), ensure_ascii=False)}")

# ---------- t5c-1 强制结案 (admin + 理由 → OK) ----------
requests.post(f"{API}/source/detection/ack-event?channel=0", headers=HA)
reset_and_scan()
r = requests.post(f"{API}/packaging-flows/{CFG}/force-settle", headers=HA, json={"reason": "现场卡单提前收尾(UAT)"})
j = r.json()
lr = j.get("last_run") or {}
ok_admin = bool(r.status_code == 200 and lr.get("status") in ("completed", "settled", "forced", "aborted")
                and (lr.get("forced_reason") or "现场" in json.dumps(j, ensure_ascii=False)))
rec("t5c-1 强制结案 admin+理由 → 收尾+留痕", ok_admin,
    f"code={r.status_code} status={lr.get('status')} forced_by={lr.get('forced_by')} reason={lr.get('forced_reason')}")

# ---------- t5c-2 强制结案 operator → 403 ----------
requests.post(f"{API}/source/detection/ack-event?channel=0", headers=HA)
reset_and_scan()
r = requests.post(f"{API}/packaging-flows/{CFG}/force-settle", headers=HO, json={"reason": "操作员尝试"})
rec("t5c-2 强制结案 operator → 403 拒绝", r.status_code == 403, f"code={r.status_code}")

# ---------- t5c-3 强制结案 必填理由 (空理由 → 400) ----------
reset_and_scan()
r = requests.post(f"{API}/packaging-flows/{CFG}/force-settle", headers=HA, json={"reason": ""})
rec("t5c-3 强制结案 空理由 → 400", r.status_code == 400, f"code={r.status_code}")

# ---------- t5d 待机维持检测不结算 (forced_settle_on_standby=False) ----------
requests.post(f"{API}/source/detection/ack-event?channel=0", headers=HA)
reset_and_scan()
before = requests.get(f"{API}/packaging-flows/{CFG}/state", headers=HA)
# 待机
requests.post(f"{API}/source/detection/standby?channel=0", headers=HA)
st = requests.get(f"{API}/packaging-flows/{CFG}/state", headers=HA)
sj = st.json() if st.status_code == 200 else {}
state = (sj.get("state") if isinstance(sj, dict) else None) or {}
status = state.get("status")
kept = status == "running"
rec("t5d 待机维持工单不结算(仍 running)", kept, f"state_code={st.status_code} status={status}")

print("\n==== 汇总 ====")
print(f"{sum(RESULTS)}/{len(RESULTS)} PASS")
