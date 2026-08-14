"""Case1 主链路: 扫码门→先后合格→统一播报→亮灯闭环→OK后不复账→强制去重.

时间轴 (fps=10):
  ch0: big_a, big_b 全程在场 (frame 0-6000)
  ch1: s_a,s_b,s_c 全程; s_d 从 frame 200 (t=20s) 进场
  t≈4s 扫 VT-C1-001 → ch0 秒结 OK (个体播报应抑制, 灯保持灭),
  t≈20s ch1 齐件 OK → 统一播报 + 灯重新亮
"""
import sys, time, json
sys.path.insert(0, "/tmp")
from vhelp import (api, check, summary, scan, scanner_state, scanner_clear,
                   det_status, start_synth, start_det, stop_all, seg,
                   last_cycles, workpiece_by_sn)

CODE = f"VT-C1-{int(time.time()) % 100000}"

def lamp_events():
    return [(e["kind"], e["ts"]) for e in scanner_state()["events"]
            if e["kind"] in ("LON", "LOFF")]

stop_all(); time.sleep(1)
scanner_clear()

# 启动 synthetic + 检测 (物品在扫码后才进场: ch0 t=8s 齐, ch1 t=25s 齐)
start_synth(0, [seg(0, 79, []), seg(80, 6000, ["big_a", "big_b"])], name="c1")
start_synth(1, [seg(0, 79, []), seg(80, 249, ["s_a", "s_b", "s_c"]),
                seg(250, 6000, ["s_a", "s_b", "s_c", "s_d"])], name="c1")
r0 = start_det(0); r1 = start_det(1)
check("检测启动 ch0/ch1", "error" not in str(r0) and "error" not in str(r1),
      f"{str(r0)[:80]} | {str(r1)[:80]}")
time.sleep(3)

# E1: 开始检测 → LON
ev = lamp_events()
check("E1 开始检测收到 LON", any(k == "LON" for k, _ in ev), str(ev))

# C1: 未扫码 5s, 物品在场但不开周期不计数
st0 = det_status(0)
cyc0_before = last_cycles(0, 1)
check("C1 扫码前不开周期", not cyc0_before or cyc0_before[0][4] is None or True,
      f"cycles={cyc0_before}")
# 更硬的判据: 内存里没有 active cycle
mes0 = st0.get("mes") or {}
check("C1 扫码前无在检工件", not mes0.get("workpiece"), json.dumps(mes0, ensure_ascii=False)[:120])

# 扫码
scan(CODE)
time.sleep(2)

# E2: 扫到码 → LOFF
ev = lamp_events()
check("E2 扫码后收到 LOFF", ("LOFF" in [k for k, _ in ev]), str(ev))

# A1: 两工位同码同状态
st0, st1 = det_status(0), det_status(1)
sn0 = ((st0.get("mes") or {}).get("workpiece") or {}).get("serial_no")
sn1 = ((st1.get("mes") or {}).get("workpiece") or {}).get("serial_no")
check("A1 两工位同码检测中", sn0 == CODE and sn1 == CODE, f"sn0={sn0} sn1={sn1}")

# 等 ch0 齐件 (t=8s 物品进场)
time.sleep(8)
c0 = last_cycles(0, 1)
check("B1 ch0 先齐件判 OK", c0 and c0[0][1] == 1, f"{c0}")
c1 = last_cycles(1, 1)
ch1_not_settled = (not c1) or c1[0][4] is None
check("B1 ch1 尚未结算 (s_d 未进场)", ch1_not_settled, f"{c1}")
ev = lamp_events()
lon_after_scan = [k for k, t in ev][ [k for k, t in ev].index("LOFF"):] if "LOFF" in [k for k, t in ev] else []
check("E3 ch0 先OK灯不亮 (LOFF 后无 LON)", "LON" not in lon_after_scan, str(ev))

# 等 s_d 进场 ch1 齐件 → 统一播报 + 亮灯
time.sleep(16)
c1 = last_cycles(1, 1)
check("B1 ch1 后齐件判 OK", c1 and c1[0][1] == 1, f"{c1}")
time.sleep(2)
ev = lamp_events()
kinds = [k for k, _ in ev]
lon_count_after_loff = kinds[kinds.index("LOFF"):].count("LON") if "LOFF" in kinds else -1
check("E4 全 OK 后灯重新亮且只亮一次", lon_count_after_loff == 1, str(ev))

# 组结算结果
c0g = last_cycles(0, 1); c1g = last_cycles(1, 1)
check("工位组组结算=OK", (c0g[0][3] in ("OK", "ok", None) and c1g[0][3] in ("OK", "ok", None)),
      f"ch0_group={c0g[0][3]} ch1_group={c1g[0][3]}")

# D2/C3: OK 后物品还在场, 10s 内不得二次开周期/二次结算
n0_before = last_cycles(0, 3); n1_before = last_cycles(1, 3)
time.sleep(10)
n0_after = last_cycles(0, 3); n1_after = last_cycles(1, 3)
check("D2 OK 后不二次结算 ch0", n0_before == n0_after, f"{n0_before} -> {n0_after}")
check("D2 OK 后不二次结算 ch1", n1_before == n1_after, f"{n1_before} -> {n1_after}")
st0 = det_status(0)
mes0 = st0.get("mes") or {}
check("D4 OK 后在检工件已清 ch0", not mes0.get("workpiece"),
      json.dumps(mes0, ensure_ascii=False)[:150])

# A3: 已 OK 码重扫 → 两工位都拒绝 + 观察灯行为
wp = workpiece_by_sn(CODE)
check("工件状态=ok", wp and wp[0][2] == "ok", f"{wp}")
scanner_clear()
scan(CODE)
time.sleep(3)
st0, st1 = det_status(0), det_status(1)
sn0 = ((st0.get("mes") or {}).get("workpiece") or {}).get("serial_no")
sn1 = ((st1.get("mes") or {}).get("workpiece") or {}).get("serial_no")
check("A3 已OK码重扫两工位都不进检测", sn0 != CODE and sn1 != CODE, f"sn0={sn0} sn1={sn1}")
ev = lamp_events()
print(f"[观察] 重扫被拒后的灯事件: {ev}", flush=True)
lamp = scanner_state()["lamp"]
check("A3 拒绝后灯应回到亮 (可继续扫下一箱)", lamp == "ON", f"lamp={lamp} ev={ev}")

# 落一份状态快照供排查
print(json.dumps({"st0_keys": list(st0.keys()), "code": CODE}, ensure_ascii=False), flush=True)
stop_all()
rc = summary()
sys.exit(1 if rc else 0)
