"""Case4 C/D/E组 + A6: 用户"ABCD残留OK"场景全链路 + 撤走重放二次账 + range清理复活码.

时间轴 (fps=10):
  ch0: 6s big_a,big_b 进场(gen0) → 30s 撤走 → 33s 重新摆放(gen1, 新track)
  ch1: 6s s_a,s_b,s_c 进场; 12s s_d 最后放(用户场景) → 30s 撤走 → 33s 重放全套(gen1)
流程:
  t≈3s 扫 X → 12s ch1 齐件, 双OK统一播报, 灯亮
  t≈16s 扫 Y (物品全在场, 全是已结算豁免品) → 不得二次入账/不得残留OK
  t≈33s 重新摆放(新track) → Y 正常齐件 → 统一OK, 灯再亮
  尾声: clear/range 清今天数据 → X 码解锁 → 重扫 X 不被拒
"""
import sys, time, json, datetime
sys.path.insert(0, "/tmp")
from vhelp import (api, check, summary, scan, scanner_state, scanner_clear,
                   det_status, start_synth, start_det, stop_all, seg,
                   last_cycles, workpiece_by_sn)

TS = int(time.time()) % 100000
X, Y = f"VT-D1X-{TS}", f"VT-D1Y-{TS}"

GID = None
_gs = api("GET", "/channel-groups").json()
for g in (_gs.get("items", _gs) if isinstance(_gs, dict) else _gs):
    if g.get("name", "").startswith("__vt_"):
        GID = g["id"]
api("PUT", f"/channel-groups/{GID}", json={
    "timeout_ms": 60000, "timeout_action": "fallback_independent",
    "unified_ok_report": True, "enabled": True})


def vis_ok(ch, t0):
    evs = det_status(ch).get("recent_events") or []
    return [e for e in evs if e.get("timestamp", 0) >= t0
            and str(e.get("event_id")) == "1" and bool(e.get("show_notification"))]


def lamp():
    return scanner_state().get("lamp")


stop_all(); time.sleep(1.5)
scanner_clear()
start_synth(0, [seg(0, 59, []), seg(60, 299, ["big_a", "big_b"]),
                seg(300, 329, []), seg(330, 6000, ["big_a", "big_b"], gen=1)],
            name="c4")
start_synth(1, [seg(0, 59, []), seg(60, 119, ["s_a", "s_b", "s_c"]),
                seg(120, 299, ["s_a", "s_b", "s_c", "s_d"]),
                seg(300, 329, []),
                seg(330, 6000, ["s_a", "s_b", "s_c", "s_d"], gen=1)],
            name="c4")
start_det(0); start_det(1)
time.sleep(3)

# ---- 第一箱 X: D 最后放 → 统一 OK ----
t0 = time.time()
scan(X)
time.sleep(1)
check("E2 扫码后灯灭(LOFF)", lamp() == "OFF", f"lamp={lamp()}")
time.sleep(12)  # 12s: ch1 s_d 已进场并稳定 → 双双结算
wx = workpiece_by_sn(X)
check("D0 X 双工件均 OK", len(wx) == 2 and all(r[2] == "ok" for r in wx), f"{wx}")
e0, e1 = vis_ok(0, t0), vis_ok(1, t0)
check("D0 统一播报各一次(D最后放场景)", len(e0) == 1 and len(e1) == 1,
      f"e0={len(e0)} e1={len(e1)}")
check("E3 双OK后灯重新亮(LON)", lamp() == "ON", f"lamp={lamp()}")

# ---- 用户核心场景: 合格后物品未撤走, 扫新码 Y → 不得残留 OK / 不得二次入账 ----
n_cyc0 = len(last_cycles(0, 10)); n_cyc1 = len(last_cycles(1, 10))
t1 = time.time()
scan(Y)
time.sleep(1)
check("E4 扫Y后灯灭", lamp() == "OFF", f"lamp={lamp()}")
time.sleep(5)
# 豁免品不得入账 → 不得出现新的已结算周期, 不得有 OK 播报
c0 = [r for r in last_cycles(0, 10) if r[4] is not None]
c1 = [r for r in last_cycles(1, 10) if r[4] is not None]
n_settled0 = len(c0); n_settled1 = len(c1)
e0, e1 = vis_ok(0, t1), vis_ok(1, t1)
check("D1 残留物品不触发二次OK播报", len(e0) == 0 and len(e1) == 0,
      f"e0={len(e0)} e1={len(e1)}")
wy = workpiece_by_sn(Y)
check("D1 Y 工件仍在检/未OK(豁免生效)", wy and all(r[2] != "ok" for r in wy), f"{wy}")
st1 = det_status(1)
_cs = st1.get("cycle_steps") or []
_done = [s for s in _cs if s.get("status") in ("completed", "ok")]
check("D1 ch1 步骤无残留完成态", len(_done) == 0,
      f"steps={json.dumps(_cs, ensure_ascii=False)[:200]}")
check("E5 等待期灯保持灭", lamp() == "OFF", f"lamp={lamp()}")

# ---- 撤走重放(新track) → Y 正常入账齐件 OK ----
time.sleep(22)  # 至 ~38s: 33s 重放, 稳定后结算
wy = workpiece_by_sn(Y)
check("C2 重新摆放后正常入账并 OK", len(wy) == 2 and all(r[2] == "ok" for r in wy),
      f"{wy}")
e0, e1 = vis_ok(0, t1), vis_ok(1, t1)
check("D2 Y 统一播报各恰好一次", len(e0) == 1 and len(e1) == 1,
      f"e0={len(e0)} e1={len(e1)}")
check("E6 Y 双OK后灯再亮", lamp() == "ON", f"lamp={lamp()}")

# ---- A6: clear/range 清今天 → X 解锁 → 重扫不被拒 ----
today = datetime.date.today().isoformat()
r = api("DELETE", "/data/clear/range",
        json={"start_date": today, "end_date": today})
check("A6 range 清理成功", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
wx = workpiece_by_sn(X)
check("A6 清理后 X 工件解锁(registered)", wx and all(r[2] == "registered" for r in wx),
      f"{wx}")
scanner_clear()
t2 = time.time()
scan(X)
time.sleep(3)
# 重扫被接受的判据: 工件未被拒且不再是 ok (在场物品全是豁免品 → 账挂起,
# 状态保持 registered 直到新物品入账, 这是正确行为; 灯灭断言证明码被接受)
wx = workpiece_by_sn(X)
check("A6 重扫 X 被接受(未被去重拒绝)", wx and all(r[2] != "ok" for r in wx),
      f"{wx}")
check("A6 重扫后灯灭(接受了扫码)", lamp() == "OFF", f"lamp={lamp()}")

stop_all()
sys.exit(1 if summary() else 0)
