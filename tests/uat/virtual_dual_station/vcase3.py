"""Case3 B组: 统一播报 — 同时合格/超时两档/NG联动/开关对照."""
import sys, time, json
sys.path.insert(0, "/tmp")
from vhelp import (api, check, summary, scan, scanner_state, scanner_clear,
                   det_status, start_synth, start_det, stop_all, seg,
                   last_cycles, workpiece_by_sn)

TS = int(time.time()) % 100000
GID = None
_gs = api("GET", "/channel-groups").json()
for g in (_gs.get("items", _gs) if isinstance(_gs, dict) else _gs):
    if g.get("name", "").startswith("__vt_"):
        GID = g["id"]
assert GID, "工位组不存在"


def set_group(**kw):
    r = api("PUT", f"/channel-groups/{GID}", json=kw)
    assert r.status_code == 200, r.text


def ok_events_since(ch, t0, visible=True):
    """visible=True 只数用户可见播报 (show_notification=True); 被抑制的个体事件
    也进 events_log 但 show_notification=False."""
    evs = det_status(ch).get("recent_events") or []
    return [e for e in evs if e.get("timestamp", 0) >= t0
            and str(e.get("event_id")) == "1"
            and (bool(e.get("show_notification")) if visible else True)]


def grep_log(pat, since_marker=None):
    import subprocess
    out = subprocess.run(["grep", "-c", pat, "/tmp/vtest_backend.log"],
                         capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def fresh(name, ch1_has_d=True, item_frame=60):
    stop_all(); time.sleep(1.5)
    start_synth(0, [seg(0, item_frame - 1, []),
                    seg(item_frame, 6000, ["big_a", "big_b"])], name=name)
    ch1_items = ["s_a", "s_b", "s_c"] + (["s_d"] if ch1_has_d else [])
    start_synth(1, [seg(0, item_frame - 1, []),
                    seg(item_frame, 6000, ch1_items)], name=name)
    start_det(0); start_det(1)
    time.sleep(3)


# ---------- B2 同时合格: 统一播报只报一次 ----------
set_group(timeout_ms=10000, timeout_action="fallback_independent",
          unified_ok_report=True, enabled=True)
n_before = grep_log("统一播报:")
fresh("b2")
t0 = time.time()
scanner_clear(); scan(f"VT-B2-{TS}")
time.sleep(8)  # 6s 物品齐 → 双双结算
c0, c1 = last_cycles(0, 1), last_cycles(1, 1)
check("B2 双工位同时判 OK", c0[0][1] == 1 and c1[0][1] == 1, f"{c0} {c1}")
ev0, ev1 = ok_events_since(0, t0), ok_events_since(1, t0)
check("B2 两工位各恰好一次可见合格播报(统一)", len(ev0) == 1 and len(ev1) == 1,
      f"ev0={len(ev0)} ev1={len(ev1)} sample={json.dumps(ev0[:1], ensure_ascii=False)[:200]}")
sup0 = ok_events_since(0, t0, visible=False)
check("B2 个体OK事件有落日志但不可见", len(sup0) == 2 and sum(1 for e in sup0 if not e.get("show_notification")) == 1,
      f"total={len(sup0)}")
check("B2 灯亮", scanner_state()["lamp"] == "ON")

# ---------- B3a 超时退化独立: ch0 补播, PARTIAL ----------
fresh("b3a", ch1_has_d=False)
t0 = time.time()
scanner_clear(); scan(f"VT-B3A-{TS}")
time.sleep(8)  # ch0 已 OK, 处于等待
ev0 = ok_events_since(0, t0)
check("B3a 等待期 ch0 个体OK被抑制(无播报)", len(ev0) == 0, f"{len(ev0)}")
time.sleep(9)  # 10s timeout 到
ev0 = ok_events_since(0, t0)
check("B3a 超时后 ch0 补播合格", len(ev0) == 1,
      f"n={len(ev0)} {json.dumps(ev0[:1], ensure_ascii=False)[:200]}")
n_partial = grep_log("result=PARTIAL")
check("B3a 组结果=PARTIAL", n_partial >= 1, f"count={n_partial}")
check("B3a 灯保持灭(ch1未合格)", scanner_state()["lamp"] == "OFF",
      f"lamp={scanner_state()['lamp']}")

# ---------- B3b 超时强制 NG (绑死档): 应报整体 NG, 不该播'已合格' ----------
set_group(timeout_action="force_ng")
fresh("b3b", ch1_has_d=False)
t0 = time.time()
scanner_clear(); scan(f"VT-B3B-{TS}")
time.sleep(8)
time.sleep(9)  # 超时
n_forceng = grep_log("result=NG_BY_TIMEOUT")
check("B3b 组结果=NG_BY_TIMEOUT", n_forceng >= 1, f"count={n_forceng}")
ev0 = ok_events_since(0, t0)
check("B3b 绑死档超时不给 ch0 播'已合格'", len(ev0) == 0,
      f"n={len(ev0)} {json.dumps(ev0[:1], ensure_ascii=False)[:200]}")
# 整体 NG 前端可感知: ch0 应收到 NG 播报
evs0 = [e for e in (det_status(0).get("recent_events") or [])
        if e.get("timestamp", 0) >= t0 and str(e.get("event_id")) == "2"]
check("B3b ch0 收到整体 NG 播报", len(evs0) >= 1, f"n={len(evs0)}")
check("B3b 灯保持灭", scanner_state()["lamp"] == "OFF")

# ---------- B4 新码强制收旧账 NG: NG 不被抑制 ----------
set_group(timeout_ms=60000, timeout_action="fallback_independent")
fresh("b4", ch1_has_d=False)
t0 = time.time()
scanner_clear(); scan(f"VT-B4-{TS}")
time.sleep(8)  # ch0 OK 等待, ch1 缺 s_d 挂账
scan(f"VT-B4NEW-{TS}")  # 新码 → ch1 旧账强制 NG
time.sleep(4)
# 强制结算后 ch1 会为新码开一条新的在途 cycle, 所以在最近几条里找已结算的 NG
c1 = last_cycles(1, 3)
_ng = [r for r in c1 if r[1] == 0 and r[4] is not None and "missing" in (r[2] or "")]
check("B4 ch1 旧账被新码强制收为 NG", len(_ng) >= 1, f"{c1}")
evs1 = [e for e in (det_status(1).get("recent_events") or [])
        if e.get("timestamp", 0) >= t0 and str(e.get("event_id")) == "2"]
check("B4 ch1 NG 播报未被抑制", len(evs1) >= 1, f"n={len(evs1)}")
ev0 = ok_events_since(0, t0)
check("B4 组内出 NG 不播统一合格", len(ev0) == 0, f"n={len(ev0)}")

# ---------- B5 开关关: 恢复各报各的 ----------
set_group(unified_ok_report=False, timeout_ms=10000)
fresh("b5", ch1_has_d=False)
t0 = time.time()
scanner_clear(); scan(f"VT-B5-{TS}")
time.sleep(8)
ev0 = ok_events_since(0, t0)
check("B5 开关关: ch0 合格立即个体播报", len(ev0) >= 1, f"n={len(ev0)}")

# 还原配置
set_group(unified_ok_report=True, timeout_ms=20000,
          timeout_action="fallback_independent")
stop_all()
rc = summary()
sys.exit(1 if rc else 0)
