"""Case2 A组: 删除入口后重扫 — 数据页删除/工件删除/工单删除, 重扫当新码.

每轮: 新 timeline(物品 8s 进场) + 扫码 → 双工位 OK → 灯亮 → 按入口删除 → 重扫验证.
"""
import sys, time, json
sys.path.insert(0, "/tmp")
from vhelp import (api, check, summary, scan, scanner_state, scanner_clear,
                   det_status, start_synth, start_det, stop_all, seg,
                   last_cycles, workpiece_by_sn)

TS = int(time.time()) % 100000


def fresh_round(name):
    """重启两通道 synthetic+检测: 物品 8s 进场, 双工位很快齐件."""
    stop_all(); time.sleep(1.5)
    start_synth(0, [seg(0, 79, []), seg(80, 6000, ["big_a", "big_b"])], name=name)
    start_synth(1, [seg(0, 79, []), seg(80, 6000, ["s_a", "s_b", "s_c", "s_d"])], name=name)
    start_det(0); start_det(1)
    time.sleep(3)


def ok_flow(code, tag):
    """扫码到双 OK 到灯亮的完整流. 返回 True=全部达成."""
    scanner_clear()
    scan(code)
    time.sleep(12)  # 8s 物品进场 + 结算裕量
    wp = workpiece_by_sn(code)
    both_ok = len([w for w in wp if w[2] == "ok"]) >= 2
    lamp = scanner_state()["lamp"]
    check(f"{tag} 双工位判 OK + 灯亮", both_ok and lamp == "ON",
          f"wp={wp} lamp={lamp}")
    return both_ok


# ---------- A4: 数据页删除记录后重扫 ----------
CODE = f"VT-A4-{TS}"
fresh_round("a4")
ok_flow(CODE, "A4 首轮")

# 重扫 → 拒绝 (基线)
scan(CODE); time.sleep(3)
wp = workpiece_by_sn(CODE)
check("A4 删除前重扫被拒 (仍只有2条ok记录)", len(wp) == 2 and all(w[2] == "ok" for w in wp), f"{wp}")

# 数据页全清 (等价把这个码所有周期记录删掉) — 必须先停检测(会话在途)
stop_all(); time.sleep(1.5)
r = api("DELETE", "/data/clear/all")
check("A4 数据页清空", r.status_code == 200, r.text[:120])
wp = workpiece_by_sn(CODE)
check("A4 清空后工件解封为 registered", wp and all(w[2] == "registered" for w in wp), f"{wp}")

# 重扫 → 当新码正常开检 → 再次双 OK
fresh_round("a4b")
ok2 = ok_flow(CODE, "A4 删除后重扫当新码")

# ---------- A5: MES 工件管理删除工件后重扫 ----------
CODE5 = f"VT-A5-{TS}"
fresh_round("a5")
ok_flow(CODE5, "A5 首轮")
# 删两条工件记录
for w in workpiece_by_sn(CODE5):
    r = api("DELETE", f"/mes/workpieces/{w[0]}")
    print(f"del wp#{w[0]} -> {r.status_code} {r.text[:80]}", flush=True)
wp = workpiece_by_sn(CODE5)
check("A5 工件已删干净", not wp, f"{wp}")
fresh_round("a5b")
ok_flow(CODE5, "A5 删工件后重扫当新码")

# ---------- A2: 检测中重复扫同码, 两工位行为一致 ----------
CODE2 = f"VT-A2-{TS}"
fresh_round("a2")
scanner_clear()
scan(CODE2)
time.sleep(2)  # 检测中 (物品还没进场)
scan(CODE2)   # 物理去重窗口外需要等 dedup_interval_sec=1
time.sleep(2)
st0, st1 = det_status(0), det_status(1)
sn0 = ((st0.get("mes") or {}).get("workpiece") or {}).get("serial_no") or \
      ((st0.get("mes") or {}).get("scan_event") or {}).get("serial_no")
sn1 = ((st1.get("mes") or {}).get("workpiece") or {}).get("serial_no") or \
      ((st1.get("mes") or {}).get("scan_event") or {}).get("serial_no")
check("A2 重复扫码后两工位状态一致", sn0 == sn1 == CODE2, f"sn0={sn0} sn1={sn1}")
wp = workpiece_by_sn(CODE2)
check("A2 重复扫码不产生多余工件 (每工位一条)", len(wp) == 2, f"{wp}")

stop_all()
rc = summary()
sys.exit(1 if rc else 0)
