"""LG 工时看板 v1.5.0 全参数生效扫描 (真后端 + 真周期结算).

逐参数改动 → 逐参数验证生效, 覆盖四个参数的全部档位:

  1. wait_value_type 四档 (NVA/BVA/VA/EXCLUDE):
     停检测冻结数据 → 以 EXCLUDE 档拿到原始四桶 → 逐档改设置查 summary,
     折算数学恒等式零容差核对 (等待折到哪个桶 / total 是否含等待)
  2. lean_scope 两档: good_only vs all → 轮数/累计随 NG 轮进出
  3. trend_days: 1 / 60 边界 + 999、非数字的静默规整
  4. default_value_type (冻结期参数, 最重的一条):
     临时把「放入」步骤的显式价值改成非法值 (=未配置) → 默认价值切 NVA →
     真起视频真跑一轮结算 → 新 lean 行 nva_seconds>0 (放入按新默认冻结),
     历史行逐字节不变; step-averages/live 的兜底价值同步跟随 → 全部还原
  5. /live 快照回显 settings + 步骤 value_type 跟随默认

用法 (dev: 后端 8004 + lg-worktime v1.5.0 已激活):
    python tests/uat/uat_lg_worktime_param_sweep.py
"""
import sys
import time
from pathlib import Path

import requests

BACKEND = "http://localhost:8004/api/v1"
DASH = f"{BACKEND}/plugins/lg-worktime/dashboard"
SET = f"{DASH}/settings"
PID = 2
STEP_IDX = 8          # 「放入」在 steps_config 的下标 (唯一 VA 步)
STEP_LABEL = "放入"
VIDEO_A = "/Users/tianjun/Downloads/飞书20260804-171657.mp4"
VIDEO_B = "/Users/tianjun/Downloads/飞书20260804-171701.mp4"
MODEL = ("/Users/tianjun/Projects/tianjun-worktime/backend/uploads/models/"
         "3f0ccf3b0cbf4277b3a330aedce73541_best_lg_feishu.pt")
DB = Path(__file__).resolve().parents[2] / "backend" / "sql_app.db"
FACTORY = {"default_value_type": "VA", "wait_value_type": "NVA",
           "lean_scope": "good_only", "trend_days": 7}
fails = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        fails.append(msg)


def post_settings(patch):
    r = requests.post(SET, json=patch, timeout=10)
    r.raise_for_status()
    return r.json()["settings"]


def summary(**params):
    return requests.get(f"{DASH}/summary", params=params, timeout=10).json()


def lean_rows(limit=200):
    """直读插件 lean 表 (只读), 校验冻结行。"""
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT id, cycle_id, is_good, va_seconds, bva_seconds, nva_seconds, wait_seconds"
            " FROM p_lg_worktime_cycle_lean ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        con.close()


def stop_all_detection():
    for ch in range(3):
        try:
            st = requests.get(f"{BACKEND}/source/status", params={"channel": ch}, timeout=10).json()
            if st.get("is_detecting"):
                requests.post(f"{BACKEND}/source/detection/stop", params={"channel": ch}, timeout=30)
        except Exception:
            pass
    time.sleep(1.5)


def set_project(ch):
    proj = requests.get(f"{BACKEND}/projects/{PID}", timeout=10).json()
    requests.post(f"{BACKEND}/source/detection/set-project", params={"channel": ch}, json={
        "project_id": proj["id"], "name": proj["name"],
        "task_type": proj.get("task_type") or "detection",
        "logic_mode": proj.get("logic_mode") or "sequential",
        "steps_config": proj.get("steps_config") or [],
        "pipeline_config": proj.get("pipeline_config") or {},
        "events_config": proj.get("events_config") or [],
        "counters_config": proj.get("counters_config") or [],
        "data_config": proj.get("data_config") or {},
    }, timeout=30).raise_for_status()


def start_channel(ch, video, speed=1.0):
    requests.post(f"{BACKEND}/workstations/{ch}/gpu", json={"device": "cpu"}, timeout=10).raise_for_status()
    requests.post(f"{BACKEND}/source/video/start", params={"channel": ch},
                  json={"file_path": video, "speed": speed}, timeout=30).raise_for_status()
    set_project(ch)
    st = requests.get(f"{BACKEND}/source/status", params={"channel": ch}, timeout=10).json()
    if not st.get("is_detecting"):
        requests.post(f"{BACKEND}/source/detection/start", params={"channel": ch},
                      json={"model_path": MODEL}, timeout=120).raise_for_status()
    requests.post(f"{BACKEND}/source/video/progress", params={"channel": ch},
                  json={"progress": 0.02}, timeout=10)


def patch_step_value(vt):
    """写「放入」步骤 plugin_data 价值; ''=非法值 → v1.5.0 视同未配置走全局默认。"""
    requests.put(f"{BACKEND}/projects/{PID}/plugin-data", json={
        "scope": "steps_config", "index": STEP_IDX,
        "customer_code": "lg-worktime", "data": {"value_type": vt},
    }, timeout=10).raise_for_status()


def step_avg_value(label):
    d = requests.get(f"{DASH}/step-averages", params={"project_id": PID}, timeout=10).json()
    for s in d["steps"]:
        if s["label"] == label:
            return s["value_type"]
    return None


def main():
    post_settings(FACTORY)

    # ================= 1. 等待归类四档 (冻结数据, 数学恒等零容差) =================
    print("[sweep] === 1. wait_value_type 四档 ===")
    stop_all_detection()
    post_settings({"wait_value_type": "EXCLUDE"})
    base = summary(project_id=PID)["lean"]
    va0, bva0, nva0, wait = base["va"], base["bva"], base["nva"], base["wait"]
    print(f"  原始四桶: va={va0} bva={bva0} nva={nva0} wait={wait}")
    ok(wait > 0, f"数据里应有等待秒数可供折算 (wait={wait})")
    ok(base["nva_total"] == nva0, f"EXCLUDE: nva_total 应=纯步骤 NVA (got {base['nva_total']})")

    # 容差 0.11: 后端"先求和原始浮点再取整", 基准值是各自取整后相加, 允许 ±0.1 舍入差
    close = lambda x, y: abs(x - y) <= 0.11  # noqa: E731

    post_settings({"wait_value_type": "NVA"})
    L = summary(project_id=PID)["lean"]
    ok(close(L["va"], va0) and close(L["bva"], bva0) and close(L["nva_total"], nva0 + wait),
       f"NVA 档: 等待应折入 nva_total ({L['va']}/{L['bva']}/{L['nva_total']})")

    post_settings({"wait_value_type": "BVA"})
    L = summary(project_id=PID)["lean"]
    ok(close(L["bva"], bva0 + wait) and close(L["va"], va0) and close(L["nva_total"], nva0),
       f"BVA 档: 等待应折入 bva ({L['va']}/{L['bva']}/{L['nva_total']})")

    post_settings({"wait_value_type": "VA"})
    L = summary(project_id=PID)["lean"]
    ok(close(L["va"], va0 + wait) and close(L["bva"], bva0) and close(L["nva_total"], nva0),
       f"VA 档: 等待应折入 va ({L['va']}/{L['bva']}/{L['nva_total']})")
    for tag in ("wait", "nva"):
        ok(L[tag] == base[tag], f"{tag} 键应恒为原始值不随档位漂移")

    # ================= 2. 统计范围两档 =================
    print("[sweep] === 2. lean_scope 两档 ===")
    post_settings({"wait_value_type": "NVA", "lean_scope": "good_only"})
    g = summary(project_id=PID)["lean"]
    post_settings({"lean_scope": "all"})
    a = summary(project_id=PID)["lean"]
    print(f"  good_only={g['good_rounds']}轮 va={g['va']} | all={a['good_rounds']}轮 va={a['va']}")
    ok(a["good_rounds"] > g["good_rounds"], "all 档轮数应多于 good_only (NG 轮进统计)")
    ok(a["va"] >= g["va"] and a["nva_total"] >= g["nva_total"], "all 档累计应≥good_only")
    ok(g["scope"] == "good_only" and a["scope"] == "all", "summary 应回显生效 scope")

    # ================= 3. 趋势天数边界与规整 =================
    print("[sweep] === 3. trend_days 边界 ===")
    for days_in, days_want in ((1, 1), (60, 60), (999, 60), ("abc", 7)):
        got = post_settings({"trend_days": days_in})["trend_days"]
        t = requests.get(f"{DASH}/trend", timeout=10).json()
        ok(got == days_want and t["trend_days"] == days_want and len(t["days"]) == days_want,
           f"trend_days={days_in!r} → 应规整为 {days_want} (设置={got}, 趋势={len(t['days'])}天)")

    # ================= 4. 默认价值真周期冻结 (最重) =================
    print("[sweep] === 4. default_value_type 真结算冻结 ===")
    before_rows = lean_rows()
    before_map = {r[0]: r for r in before_rows}
    n_nva_before = sum(1 for r in before_rows if (r[5] or 0) > 0)
    print(f"  现存 lean 行 {len(before_rows)} 条, 其中 nva>0 的 {n_nva_before} 条")

    try:
        patch_step_value("")            # 「放入」显式价值→非法 = 未配置
        post_settings({"default_value_type": "NVA", "lean_scope": "good_only",
                       "wait_value_type": "NVA", "trend_days": 7})
        time.sleep(3.5)                 # 双缓存 TTL 3s
        ok(step_avg_value(STEP_LABEL) == "NVA",
           "step-averages: 未配置步骤价值应跟随新默认 NVA")

        start_channel(0, VIDEO_A, speed=2.0)
        # /live 快照: settings 回显 + 进行中步骤 value_type 跟随默认
        deadline = time.time() + 180
        live_vt = None
        new_nva_row = None
        while time.time() < deadline:
            lv = requests.get(f"{DASH}/live", timeout=10).json()
            ok_settings = (lv.get("settings") or {}).get("default_value_type") == "NVA"
            chan = (lv.get("channels") or {}).get("0") or {}
            for s in (chan.get("steps_done") or []):
                if s.get("label") == STEP_LABEL:
                    live_vt = s.get("value_type")
            rows = lean_rows()
            fresh = [r for r in rows if r[0] not in before_map]
            hit = [r for r in fresh if (r[5] or 0) > 0]
            if hit and live_vt:
                new_nva_row = hit[0]
                break
            time.sleep(3)
        ok((requests.get(f"{DASH}/live", timeout=10).json().get("settings") or {})
           .get("default_value_type") == "NVA", "/live 应回显生效中的 settings")
        ok(live_vt == "NVA", f"live 快照: 「放入」实时价值应按新默认 NVA (got {live_vt})")
        ok(new_nva_row is not None, "新结算的 lean 行应出现 nva_seconds>0 (放入按 NVA 冻结)")
        if new_nva_row:
            print(f"  新冻结行: id={new_nva_row[0]} cycle={new_nva_row[1]} "
                  f"va={new_nva_row[3]} bva={new_nva_row[4]} nva={new_nva_row[5]} wait={new_nva_row[6]}")
        # 历史行逐字节不变 (冻结语义: 改默认不回写)
        after_map = {r[0]: r for r in lean_rows(len(before_rows) + 50)}
        changed = [rid for rid, row in before_map.items() if after_map.get(rid) != row]
        ok(not changed, f"历史 lean 行应逐字节不变 (变了 {len(changed)} 条: {changed[:5]})")
    finally:
        # ---- 还原: 步骤显式价值 + 出厂设置 ----
        stop_all_detection()
        patch_step_value("VA")
        post_settings(FACTORY)
        time.sleep(3.5)
    ok(step_avg_value(STEP_LABEL) == "VA", "还原后「放入」应回显式 VA")
    ok(requests.get(SET, timeout=10).json()["settings"] == FACTORY, "设置应还原出厂")

    # ================= 5. 恢复三工位运行现场 (与扫描前一致) =================
    print("[sweep] === 5. 恢复三工位 A/A/B 运行 ===")
    for ch, v in ((0, VIDEO_A), (1, VIDEO_A), (2, VIDEO_B)):
        start_channel(ch, v, speed=1.0)
    print("  三工位已恢复检测")

    if fails:
        print(f"[sweep] FAIL ({len(fails)}): {fails}")
        sys.exit(1)
    print("[sweep] 全参数生效扫描通过 (4 参数 × 全档位)")


if __name__ == "__main__":
    main()
