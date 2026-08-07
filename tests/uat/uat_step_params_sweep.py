"""步骤参数细扫 UAT: 用 synthetic 剧本源逐项证明每个步骤参数真实生效.

现场叙事:
  工程师在项目管理里给某步骤改 threshold / min_frames / min_duration /
  max_duration(+timeout_ng) / disappear_delay(+uninterruptible) /
  enabled / cycle_max_duration → 检测行为必须立即跟着变。
  本脚本对每个参数做「挡住(负例)」+「放行(正例)」成对剧本, 断言 OK/NG/步骤计数
  的**增量** —— 参数没接线的话正反例增量会相同, 立刻红。

原理: synthetic 源走完整 pipeline (_update_step_stats → 结算 → _trigger_event),
剧本时间线确定性注入检测, sequential + first_step 结算 (A 重现收上一周期)。
⚠ 计数器跨 detection session 累计 (stop 不清零), 所以一律断增量不断绝对值。

覆盖不到的参数 (已有其它护栏):
  - box_max_width/height: 在真实模型 detection 出口过滤
    (source_detect_runners_mixin._passes_box_size_limit), synthetic 短路了该段
    → 由单测 tests/test_per_item_v310_features.py 覆盖。

用法 (后端需 RUNTIME_MODE=test, 端口 8004):
    python tests/uat/uat_step_params_sweep.py
"""
import sys
import time

import requests

API = "http://127.0.0.1:8004/api/v1"
CH = 0
FPS = 30
fails = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        fails.append(msg)


def seg(label, t0, t1, conf=0.95, bbox=None):
    return {"from": int(t0 * FPS), "to": int(t1 * FPS),
            "detections": [{"label": label, "confidence": conf,
                            "bbox": bbox or [0.1, 0.1, 0.2, 0.2]}]}


def base_timeline(b_conf=0.95, b_span=(2.5, 4.0), b_bbox=None, a_flicker=False):
    """A → B → C → (A 重现收周期). b_* 供各参数用例改造 B 步."""
    tl = []
    if a_flicker:
        tl += [seg("A", 0.5, 1.5), seg("A", 2.5, 3.5)]
        b_span = (4.5, 6.0)
        c_span = (6.5, 8.0)
        a2 = (10.0, 11.5)
    else:
        tl.append(seg("A", 0.5, 2.0))
        c_span = (4.5, 6.0)
        a2 = (8.0, 9.5)
    tl.append(seg("B", b_span[0], b_span[1], conf=b_conf, bbox=b_bbox))
    tl.append(seg("C", c_span[0], c_span[1]))
    tl.append(seg("A", a2[0], a2[1]))
    return tl, a2[1] + 3.0  # 剧本时长(秒)


def project_cfg(step_over=None, pipeline_over=None, seq=("A", "B", "C"),
                steps=("A", "B", "C")):
    step_over = step_over or {}
    steps_config = []
    for i, lb in enumerate(steps):
        s = {"id": f"sw-{i+1}", "label": lb, "threshold": 40, "min_frames": 1,
             "enabled": True, "color": "#1976d2"}
        s.update(step_over.get(lb, {}))
        steps_config.append(s)
    pipeline = {"sequence_order": [{"step_id": f"sw-{[*steps].index(lb)+1}"} for lb in seq],
                "settlement_mode": "first_step", "settle_dedup": False}
    pipeline.update(pipeline_over or {})
    events = [
        {"id": 1, "name": "合格(OK)", "actions": [
            {"counter_name": "合格总数", "delta": 1}, {"counter_name": "总产量", "delta": 1}]},
        {"id": 2, "name": "不合格(NG)", "actions": [
            {"counter_name": "不良总数", "delta": 1}, {"counter_name": "总产量", "delta": 1}]},
    ]
    return {"project_id": -1, "name": "__param_sweep__", "task_type": "detect",
            "logic_mode": "sequential", "steps_config": steps_config,
            "pipeline_config": pipeline, "events_config": events,
            "counters_config": [], "data_config": {}}


def read_state():
    r = requests.get(f"{API}/source/detection/results", params={"channel": CH},
                     timeout=10).json()
    c = r.get("counters") or {}
    sc = r.get("step_counts") or {}
    return (int(c.get("合格总数") or 0), int(c.get("不良总数") or 0),
            {k: int(v or 0) for k, v in sc.items()})


def run_case(name, timeline, dur_s, cfg, d_ok=None, d_ng=None, d_ng_min=None,
             d_steps=None):
    """d_ok/d_ng: 期望增量 (None=不检查); d_ng_min: 期望 NG 增量下限;
    d_steps: {label: 期望步骤计数增量}."""
    ok0, ng0, sc0 = read_state()
    requests.post(f"{API}/test/synthetic/start",
                  json={"scenario_json": {"name": name, "fps": FPS, "timeline": timeline},
                        "channel": CH}, timeout=15).raise_for_status()
    requests.post(f"{API}/source/detection/set-project", params={"channel": CH},
                  json=cfg, timeout=15).raise_for_status()
    requests.post(f"{API}/source/detection/start", params={"channel": CH},
                  json={"conf": 0.25, "iou": 0.45}, timeout=60).raise_for_status()
    time.sleep(dur_s + 2.0)
    ok1, ng1, sc1 = read_state()
    requests.post(f"{API}/source/detection/stop", params={"channel": CH}, timeout=30)
    requests.post(f"{API}/test/synthetic/stop", params={"channel": CH}, timeout=15)
    time.sleep(0.8)

    dok, dng = ok1 - ok0, ng1 - ng0
    detail = f"ΔOK={dok} ΔNG={dng}"
    good = True
    if d_ok is not None and dok != d_ok:
        good = False
    if d_ng is not None and dng != d_ng:
        good = False
    if d_ng_min is not None and dng < d_ng_min:
        good = False
    exp = []
    if d_ok is not None:
        exp.append(f"ΔOK={d_ok}")
    if d_ng is not None:
        exp.append(f"ΔNG={d_ng}")
    if d_ng_min is not None:
        exp.append(f"ΔNG≥{d_ng_min}")
    ok(good, f"{name}: 期望 {' '.join(exp)}, 实际 {detail}")
    for lb, want in (d_steps or {}).items():
        got = sc1.get(lb, 0) - sc0.get(lb, 0)
        ok(got == want, f"{name}: 步骤 {lb} 计数增量应={want} (实际 {got})")


def main():
    print("[sweep] ============ 步骤参数细扫 (synthetic 剧本, 增量断言) ============")

    tl, d = base_timeline()
    print("[sweep] --- 基线: 全默认参数走一个 OK 周期 ---")
    run_case("baseline_ok", tl, d, project_cfg(), d_ok=1, d_ng=0,
             d_steps={"A": 2, "B": 1, "C": 1})

    print("[sweep] --- threshold (步骤置信度门) ---")
    tl, d = base_timeline(b_conf=0.55)
    run_case("threshold_block(B conf0.55 < 门70%)", tl, d,
             project_cfg({"B": {"threshold": 70}}), d_ok=0, d_ng=1,
             d_steps={"B": 0})
    tl, d = base_timeline(b_conf=0.55)
    run_case("threshold_pass(B conf0.55 > 门40%)", tl, d,
             project_cfg({"B": {"threshold": 40}}), d_ok=1, d_ng=0,
             d_steps={"B": 1})

    print("[sweep] --- min_frames (确认帧数门) ---")
    tl, d = base_timeline(b_span=(2.5, 3.1))
    run_case("min_frames_block(B 0.6s < 50帧)", tl, d,
             project_cfg({"B": {"min_frames": 50}}), d_ok=0, d_ng=1,
             d_steps={"B": 0})
    tl, d = base_timeline(b_span=(2.5, 3.1))
    run_case("min_frames_pass(B 0.6s ≥ 1帧)", tl, d,
             project_cfg({"B": {"min_frames": 1}}), d_ok=1, d_ng=0,
             d_steps={"B": 1})

    print("[sweep] --- min_duration (最短持续时长门) ---")
    tl, d = base_timeline(b_span=(2.5, 3.3))
    run_case("min_duration_block(B 0.8s < 3s)", tl, d,
             project_cfg({"B": {"min_duration": 3}}), d_ok=0, d_ng=1,
             d_steps={"B": 0})
    tl, d = base_timeline(b_span=(2.5, 3.3))
    run_case("min_duration_pass(B 0.8s ≥ 0.3s)", tl, d,
             project_cfg({"B": {"min_duration": 0.3}}), d_ok=1, d_ng=0,
             d_steps={"B": 1})

    print("[sweep] --- max_duration + timeout_ng (步骤超时强制NG) ---")
    # B 驻留 6s > 2s 上限: 每次超时重置后 B 仍在场会再次确认→再次超时,
    # ΔNG 可能 >1 (每 ~1.5s 一次), 断下限.
    tl = [seg("A", 0.5, 2.0), seg("B", 2.5, 8.5), seg("C", 9.0, 10.5),
          seg("A", 12.0, 13.5)]
    run_case("max_duration_timeout_ng(B 6s > 2s)", tl, 16.5,
             project_cfg({"B": {"max_duration": 2, "timeout_ng": True}}),
             d_ok=0, d_ng_min=1)

    print("[sweep] --- disappear_delay (消失等待, 桥接闪断) ---")
    # A 出现1s→断1s→再1s. 桥接: 两段并成一次出现 (ΔA=2: 桥接段+收尾A2);
    # 不桥接: 三次独立出现 (ΔA=3). 直接证明消失等待生效.
    tl, d = base_timeline(a_flicker=True)
    run_case("disappear_bridge(A 闪断1s ≤ 等待3s → 并一次)", tl, d,
             project_cfg({"A": {"disappear_delay": 3,
                                "disappear_uninterruptible": True}}),
             d_ok=1, d_ng=0, d_steps={"A": 2})
    tl, d = base_timeline(a_flicker=True)
    run_case("disappear_zero(A 闪断即算两次出现)", tl, d,
             project_cfg({"A": {"disappear_delay": 0}}), d_steps={"A": 3})

    print("[sweep] --- enabled=false (停用步骤不参与) ---")
    tl, d = base_timeline()
    run_case("disabled_step(B 停用, 序列只 A,C)", tl, d,
             project_cfg({"B": {"enabled": False}}, seq=("A", "C")),
             d_ok=1, d_ng=0, d_steps={"B": 0})

    print("[sweep] --- cycle_max_duration (周期总时长超时NG) ---")
    tl = [seg("A", 0.5, 2.0), seg("B", 5.0, 6.5), seg("C", 7.0, 8.5),
          seg("A", 10.0, 11.5)]
    run_case("cycle_timeout(周期>3s 强制NG)", tl, 14.5,
             project_cfg(pipeline_over={"cycle_max_duration": 3}),
             d_ok=0, d_ng_min=1)

    if fails:
        print(f"[sweep] FAIL ({len(fails)}):")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("[sweep] 步骤参数细扫全部通过 ✅")


if __name__ == "__main__":
    main()
