"""虚拟双工位环境配置: 双工位+两项目+假扫码器+工位组."""
import sys
sys.path.insert(0, "/tmp")
from vhelp import api, check, summary

# 1) 双工位
r = api("POST", "/workstations/mode", json={"channel_count": 2, "channels": []})
check("双工位 channel_count=2", r.status_code == 200, r.text[:100])

# 2) 清理旧的 __vt_ 资源
for p in api("GET", "/projects").json():
    if isinstance(p, dict) and (p.get("name") or "").startswith("__vt_"):
        api("DELETE", f"/projects/{p['id']}")
devs = api("GET", "/scanner/devices").json()
items = devs.get("items", devs) if isinstance(devs, dict) else devs
for d in items or []:
    if (d.get("name") or "").startswith("__vt_"):
        api("DELETE", f"/scanner/devices/{d['id']}")
    elif d.get("enabled"):
        api("PUT", f"/scanner/devices/{d['id']}", json={"enabled": False})
gs = api("GET", "/channel-groups").json()
for g in (gs.get("items", gs) if isinstance(gs, dict) else gs) or []:
    api("PUT", f"/channel-groups/{g['id']}", json={"enabled": False})
    api("DELETE", f"/channel-groups/{g['id']}")

# 3) 两个跟踪模式项目 (齐件即结算 + 扫码后才计数)
def mk_project(name, items):
    steps = [{"id": f"s{i+1}", "label": lbl, "name": lbl, "enabled": True,
              "count": cnt, "count_mode": "track", "threshold": 0.3}
             for i, (lbl, cnt) in enumerate(items)]
    payload = {
        "name": name, "task_type": "detection", "logic_mode": "tracking",
        "pipeline_config": {
            "tracking_cycle_strategy": "roi_exit",
            "tracking_settle_on_complete": True,
            "tracking_scan_gate": True,
            "counting_expected_items": {lbl: cnt for lbl, cnt in items},
        },
        "steps_config": steps,
        "events_config": [
            {"id": 1, "name": "合格", "type": "ok", "enabled": True,
             "show_notification": True,
             "actions": [{"counter_name": "合格总数", "delta": 1},
                          {"counter_name": "总产量", "delta": 1}]},
            {"id": 2, "name": "不合格", "type": "ng", "enabled": True,
             "show_notification": True,
             "actions": [{"counter_name": "不良总数", "delta": 1},
                          {"counter_name": "总产量", "delta": 1}]},
        ],
        "counters_config": [], "alarm_config": {}, "detection_config": {},
        "data_config": {}, "default_model_id": None, "model_format": "pytorch_fp32",
    }
    r = api("POST", "/projects", json=payload)
    r.raise_for_status()
    return r.json()["id"]

p0 = mk_project("__vt_大件-工位0", [("big_a", 1), ("big_b", 1)])
p1 = mk_project("__vt_小件-工位1", [("s_a", 1), ("s_b", 1), ("s_c", 1), ("s_d", 1)])
check("建两个跟踪项目", p0 and p1, f"p0={p0} p1={p1}")

# 4) 收养开关关 (多工位多项目推荐配置)
r = api("PUT", "/projects/activate-config", json={"adopt_unbound": False})
check("收养开关=关", r.status_code == 200, r.text[:80])

# 5) 绑定工位→项目
r0 = api("PUT", "/workstations/channel-config", json={"channel_id": 0, "project_id": p0})
r1 = api("PUT", "/workstations/channel-config", json={"channel_id": 1, "project_id": p1})
check("绑定 ch0→p0 / ch1→p1", r0.status_code == 200 and r1.status_code == 200)

# 6) 分别激活两个项目 (收养关: 各自只同步自己绑定的工位)
a0 = api("POST", f"/projects/{p0}/activate")
a1 = api("POST", f"/projects/{p1}/activate")
check("激活 p0/p1", a0.status_code == 200 and a1.status_code == 200,
      f"a0={a0.status_code} a1={a1.status_code}")

# 7) 假扫码器: 广播[0,1], E 码-合格-码, 强制去重
r = api("POST", "/scanner/devices", json={
    "name": "__vt_广播枪", "ip": "127.0.0.1", "port": 24001,
    "device_type": "text_lon", "scan_mode": "E",
    "broadcast_channels": [0, 1], "channel_id": 0,
    "auto_create_workpiece": True, "auto_link_order": False,
    "scan_required": True, "strict_ok_dedup": True,
    "enabled": True, "dedup_interval_sec": 1,
})
check("建广播 E 模式假扫码器", r.status_code in (200, 201), r.text[:150])
SCANNER_ID = r.json().get("id")

# 8) 工位组: all_ok + 统一播报
r = api("POST", "/channel-groups", json={
    "name": "__vt_B站双工位", "member_channel_ids": [0, 1],
    "settle_strategy": "synchronized_all_ok", "timeout_ms": 20000,
    "timeout_action": "fallback_independent", "enabled": True,
    "unified_ok_report": True,
})
check("建工位组 all_ok+统一播报", r.status_code in (200, 201), r.text[:150])

import json
print(json.dumps({"p0": p0, "p1": p1, "scanner": SCANNER_ID}, ensure_ascii=False))
summary()
