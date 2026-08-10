"""SY9「只认收尾后放的工单」现场没拦住 — synthetic 剧本全链路复现.

对齐现场视频 2026-08-05 16-42-16(1).mkv 的时间线 (两箱工单):
  箱1: 贴标 → 托盘+滑块装箱 → 封箱 → **封箱后放工单 (提前! 工单纸只该进尾箱)**
  箱2(尾箱): 贴标 → 托盘+滑块装箱 → 封箱 → **没放工单**

预期 (tail_paper_only_after_awaiting=True):
  A. 箱1 的放工单检出被整条丢弃 — 不报 early_paper / 不刷 NG / 不收尾工单;
  B. 箱2 落账后工单挂「等放工单收尾」;
  C. 时限到点仍没放 → missing_paper 报警 + 工单判 NG 收尾 (缺工单被识别).

基线 (开关关, --switch off): 复现现场老毛病 —
  A'. 箱1 放工单 → early_paper 报警 (共用缺工单档=NG);
  B'. 尾箱落账时探历史账本命中箱1的误检 → 工单直接 OK 收尾, 缺工单漏报.

跑法 (先起 RUNTIME_MODE=test 后端):
  python tests/uat/repro_sy9_paper_synth.py --base http://127.0.0.1:8005 --switch on
"""
import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

ORDER_NO = "SY9T2BOX"
DISPATCH_QTY = 16          # 16 滑块 ÷ 8/箱 = 2 箱
ITEMS_PER_BOX = 8
MOCK_PORT = 19080
FPS = 10
PAPER_TIMEOUT_S = 10

# ───────────────────────── mock MES ─────────────────────────

class _MesHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        job_no = str(((body.get("parameters") or {}).get("job_no")) or "").strip()
        book = {ORDER_NO: DISPATCH_QTY, "SY9NEXT": ITEMS_PER_BOX}
        rows = ([{"job_no": job_no, "cust_name": "上银(复现)",
                  "dispatch_qty": book[job_no], "spec": "SY9T"}]
                if job_no in book else [])
        data = json.dumps({"statusCode": 200, "success": True,
                           "response": {"resultData": rows}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def start_mock_mes():
    srv = HTTPServer(("127.0.0.1", MOCK_PORT), _MesHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ───────────────────────── 项目 / 配置 ─────────────────────────

def project_payload():
    """缩比版上银 SY 包装项目 (每箱 8 滑块 1 托盘), 结构对齐真项目 41."""
    steps = [
        {"id": 1, "label": "托盘", "enabled": True, "threshold": 40,
         "min_frames": 1, "detection_type": "dynamic", "join_cycle": True},
        {"id": 3, "label": "贴标", "enabled": True, "threshold": 40,
         "min_frames": 3, "detection_type": "dynamic", "join_cycle": True,
         "accept_once": True},
        {"id": 5, "label": "滑块", "enabled": True, "threshold": 40,
         "min_frames": 1, "detection_type": "dynamic", "join_cycle": True,
         "detect_role": "item", "expected_count": ITEMS_PER_BOX},
        {"id": 7, "label": "封箱", "enabled": True, "threshold": 40,
         "min_frames": 3, "detection_type": "dynamic", "join_cycle": True,
         "disappear_delay": 3},
        {"id": 8, "label": "放工单", "enabled": True, "threshold": 40,
         "min_frames": 5, "detection_type": "dynamic", "join_cycle": True},
    ]
    pipeline = {
        "logic_mode": "custom",
        "custom_based_on": "sequential",
        "custom_mixed_with": "tracking",
        # 序列: 贴标 → 封箱 (放工单/托盘/滑块都是序列外)
        "sequence_order": [{"step_id": 3}, {"step_id": 7}],
        "custom_sequence_order": [{"step_id": 3}, {"step_id": 7}],
        "settlement_mode": "last_step",
        "settle_dedup": True, "settle_dedup_window_seconds": 2,
        # 容器混合记账: 托盘装滑块, 1 托盘/箱, 目标 8 (由包装协调器逐箱回设)
        "custom_mix_container_label": "托盘",
        "custom_mix_container_box_count": 1,
        "custom_mix_container_gone_frames": 10,
        "custom_mix_container_iou_match": 0.3,
        "custom_mix_container_count_mode": "items_total",
        "custom_mix_container_item_target": ITEMS_PER_BOX,
        "custom_mix_container_confirm_by_frames": True,
        "custom_mix_container_confirm_by_action": False,
        "custom_mix_container_peak_cap": ITEMS_PER_BOX,
        "custom_mix_container_stable_min_frames": 5,
    }
    events = [
        {"id": 1, "name": "合格(OK)", "actions": [
            {"counter_name": "合格总数", "delta": 1},
            {"counter_name": "总产量", "delta": 1}]},
        {"id": 2, "name": "不良(NG)", "actions": [
            {"counter_name": "不良总数", "delta": 1},
            {"counter_name": "总产量", "delta": 1}]},
        {"id": 3, "name": "包装异常-需人工确认", "actions": [
            {"counter_name": "异常总数", "delta": 1}]},
    ]
    return {
        "name": "__repro_sy9_paper",
        "description": "SY9 放工单严格顺序复现 (synthetic)",
        "task_type": "detect",
        "logic_mode": "custom",
        "pipeline_config": pipeline,
        "steps_config": steps,
        "events_config": events,
    }


def flow_payload(conn_id, switch_on, judge="timeout"):
    return {
        "name": "__repro_sy9_flow", "enabled": True, "channel_id": 0,
        "pull_conn_id": conn_id,
        "count_unit": "sliders",
        "items_per_box_source": "config",
        "items_per_box_fixed": ITEMS_PER_BOX,
        "slider_total_field": "dispatch_qty",
        "box_count_source": "field", "box_count_field": "dispatch_qty",
        "label_match": "exact", "label_len": 0,
        "on_mes_fail": "block", "on_label_mismatch": "off", "on_short_box": "redo",
        # 尾箱放工单三开关 (对齐现场)
        "tail_paper_order_required": True,
        "tail_paper_step_label": "放工单",
        "tail_paper_as_close_action": True,
        "tail_paper_only_after_awaiting": bool(switch_on),
        # 判定方式: timeout=时限判定 / scan=扫新单判定 (现场默认)
        "tail_paper_scan_alarm": judge == "scan",
        "tail_paper_timeout_s": 0 if judge == "scan" else PAPER_TIMEOUT_S,
        # 事件映射对齐现场: 缺工单(含提前放)=不良NG(2), 其余=人工确认(3)
        "event_missing_paper": 2,
        "event_short_box": 3, "event_box_ng": 3, "event_mes_fail": 3,
        "auto_switch_project": False,
    }


# ───────────────────────── synthetic 剧本 ─────────────────────────

def _sliders_in_tray(tx, ty, tw, th):
    """托盘 bbox 内铺 8 个滑块小框 (2 行 x 4 列)."""
    out = []
    for r in range(2):
        for c in range(4):
            out.append({"label": "滑块", "confidence": 0.9,
                        "bbox": [round(tx + 0.05 * tw + c * 0.24 * tw, 4),
                                 round(ty + 0.10 * th + r * 0.45 * th, 4),
                                 round(0.18 * tw, 4), round(0.32 * th, 4)]})
    return out


def build_timeline():
    TRAY = [0.50, 0.50, 0.40, 0.40]
    tray_dets = ([{"label": "托盘", "confidence": 0.9, "bbox": TRAY}]
                 + _sliders_in_tray(*TRAY))
    tiebiao = [{"label": "贴标", "confidence": 0.9, "bbox": [0.05, 0.05, 0.12, 0.12]}]
    fengxiang = [{"label": "封箱", "confidence": 0.9, "bbox": [0.30, 0.10, 0.20, 0.20]}]
    paper = [{"label": "放工单", "confidence": 0.9, "bbox": [0.25, 0.15, 0.15, 0.15]}]
    tl = []

    def seg(f0, f1, dets):
        tl.append({"from": f0, "to": f1, "detections": dets})

    # ── 箱 1 ──
    seg(0, 4, [])
    seg(5, 18, tiebiao)               # 贴标 → 开周期1
    seg(19, 24, [])
    seg(25, 100, tray_dets)           # 托盘+8滑块 稳定在位
    seg(101, 119, [])                 # 托盘进箱 (gone 10帧 → 记账 8)
    seg(120, 134, fengxiang)          # 封箱
    seg(135, 149, [])
    seg(150, 162, paper)              # ⚠️ 提前放工单 (箱1, 对应视频 t≈55s)
    seg(163, 209, [])                 # 周期1按 last_step 结算 → 箱1落账
    # ── 箱 2 (尾箱) ──
    seg(210, 223, tiebiao)            # 贴标 → 开周期2
    seg(224, 229, [])
    seg(230, 305, tray_dets)
    seg(306, 324, [])                 # 记账 8
    seg(325, 339, fengxiang)          # 封箱; 之后【没放工单】
    seg(340, 450, [])                 # 周期2结算 → 尾箱落账 → 该挂等放工单
    return {"name": "sy9_two_box_paper", "fps": FPS, "timeline": tl}


# ───────────────────────── 驱动 ─────────────────────────

class Api:
    def __init__(self, base):
        self.base = base
        self.s = requests.Session()

    def get(self, p, **kw):
        return self.s.get(self.base + p, timeout=15, **kw)

    def post(self, p, **kw):
        return self.s.post(self.base + p, timeout=15, **kw)

    def put(self, p, **kw):
        return self.s.put(self.base + p, timeout=15, **kw)

    def delete(self, p, **kw):
        return self.s.delete(self.base + p, timeout=15, **kw)


def wait_backend(api, secs=90):
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            r = api.get("/api/v1/projects/")
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("后端未就绪")


def cleanup(api):
    """幂等清场: 删同名项目/包装配置, 停检测."""
    try:
        api.post("/api/v1/source/detection/stop?channel=0")
        api.post("/api/v1/test/synthetic/stop?channel=0")
    except Exception:
        pass
    try:
        r = api.get("/api/v1/packaging-flows").json()
        items = r.get("items", r) if isinstance(r, dict) else r
        for c in items or []:
            if c.get("name") == "__repro_sy9_flow":
                api.put(f"/api/v1/packaging-flows/{c['id']}", json={"enabled": False})
                api.delete(f"/api/v1/packaging-flows/{c['id']}")
            elif c.get("enabled"):
                # 播种库里预存的包装配置会抢通道 0, 先禁用
                api.put(f"/api/v1/packaging-flows/{c['id']}", json={"enabled": False})
    except Exception:
        pass


def flow_state(api, cid):
    r = api.get(f"/api/v1/packaging-flows/{cid}/state")
    if r.status_code != 200:
        return None
    j = r.json()
    return j.get("state") if isinstance(j, dict) and "state" in j else j


def last_run_row(api, cid, order_no=None):
    """完成后 state 变 None, 从 DB 回查最近 run."""
    import sqlite3
    con = sqlite3.connect("/tmp/tj_sy9_repro/sql_app.db")
    q = ("select order_no, status, final_result, box_done, box_total, box_details "
         "from packaging_flow_runs where flow_config_id=?")
    params = [cid]
    if order_no:
        q += " and order_no=?"
        params.append(order_no)
    row = con.execute(q + " order by id desc limit 1", params).fetchone()
    con.close()
    if row is None:
        return None
    return {"order_no": row[0], "status": row[1], "final_result": row[2],
            "box_done": row[3], "box_total": row[4],
            "box_details": json.loads(row[5]) if row[5] else []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8005")
    ap.add_argument("--switch", choices=["on", "off"], default="on")
    ap.add_argument("--judge", choices=["timeout", "scan"], default="timeout")
    args = ap.parse_args()
    switch_on = args.switch == "on"

    api = Api(args.base)
    print(f"== SY9 放工单复现 (开关={'开' if switch_on else '关(基线)'}) ==")
    wait_backend(api)
    start_mock_mes()
    cleanup(api)

    # 1) MES 连接 → 本机 mock
    conns = api.get("/api/v1/mes/gateway/connections").json()
    conn = next((c for c in conns if c.get("name") == "__repro_mock_mes"), None)
    pull_cfg = {
        "enabled": True,
        "url": f"http://127.0.0.1:{MOCK_PORT}/api",
        "method": "POST", "content_type": "application/json; charset=UTF-8",
        "request_body_template":
            '{"api":"q","parameters":{"job_no":"{job_no}"}}',
        "success_path": "statusCode", "success_value": 200,
        "array_path": "response.resultData",
        "field_mapping": {"order_no": "job_no", "customer_name": "cust_name",
                          "product_spec": "spec", "planned_qty": "dispatch_qty"},
        "timeout_sec": 5, "retry_count": 0,
    }
    if conn is None:
        r = api.post("/api/v1/mes/gateway/connections", json={
            "name": "__repro_mock_mes", "adapter_type": "rest",
            "enabled": True, "config": {"pull": pull_cfg},
            "push_events": [], "pull_enabled": True})
        assert r.status_code in (200, 201), r.text
        conn = r.json()
    conn_id = conn["id"] if isinstance(conn, dict) and "id" in conn else conn.get("data", {}).get("id")
    print(f"  · MES 连接 id={conn_id} → mock:{MOCK_PORT}")

    # 2) 项目 + 激活 (幂等: 已存在则整体更新)
    payload = project_payload()
    plist = api.get("/api/v1/projects/").json()
    plist = plist.get("items", plist) if isinstance(plist, dict) else plist
    existing = [p for p in plist if p.get("name") == payload["name"]]
    if existing:
        pid = existing[0]["id"]
        r = api.put(f"/api/v1/projects/{pid}", json=payload)
        assert r.status_code in (200, 201), r.text
    else:
        r = api.post("/api/v1/projects/", json=payload)
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
    r = api.post(f"/api/v1/projects/{pid}/activate")
    assert r.status_code in (200, 201), r.text
    print(f"  · 项目 id={pid} 已激活 (custom/sequential+tracking, 放工单=序列外)")

    # 3) 包装结算配置
    r = api.post("/api/v1/packaging-flows",
                 json=flow_payload(conn_id, switch_on, args.judge))
    assert r.status_code in (200, 201), r.text
    cid = r.json()["id"]
    print(f"  · 包装配置 id={cid} (only_after_awaiting={switch_on}, 判定={args.judge})")

    # 4) synthetic 剧本 + 检测启动
    r = api.post("/api/v1/test/synthetic/start",
                 json={"scenario_json": build_timeline(), "channel": 0})
    assert r.status_code == 200, r.text
    r = api.post("/api/v1/source/detection/start?channel=0", json={})
    assert r.status_code == 200, r.text
    print("  · synthetic 已启动, 检测运行中")

    # 5) 虚拟扫一次工单号
    time.sleep(1.0)
    r = api.post("/api/v1/packaging-flows/scan",
                 json={"code": ORDER_NO, "channel_id": 0})
    assert r.status_code == 200, r.text
    st = flow_state(api, cid)
    print(f"  · 已扫工单 {ORDER_NO} → box_total={st and st.get('box_total')} "
          f"status={st and st.get('status')}")
    assert st and st.get("box_total") == 2, f"两箱工单未立起: {st}"

    # 6) 跟踪 60s 时间线 (45s 剧本 + 15s 余量) + 时限判定窗口
    phases = []
    seen = set()
    t0 = time.time()
    deadline = t0 + 45 + PAPER_TIMEOUT_S + 20
    final = None
    next_scanned = False
    while time.time() < deadline:
        st = flow_state(api, cid)
        if (args.judge == "scan" and not next_scanned and st
                and st.get("status") == "awaiting_paper"):
            # 现场判定点: 下一张工单扫码进来 → 判上一单放没放
            time.sleep(1)
            api.post("/api/v1/packaging-flows/scan",
                     json={"code": "SY9NEXT", "channel_id": 0})
            next_scanned = True
            phases.append((round(time.time() - t0, 1), "扫新单", "SY9NEXT (判定点)"))
        if st is None or next_scanned:
            final = last_run_row(api, cid, order_no=ORDER_NO)
            if final and final["status"] in ("completed", "aborted"):
                phases.append((round(time.time() - t0, 1), "工单收尾",
                               f"status={final['status']} final={final['final_result']}"))
                break
        else:
            key = (st.get("status"), st.get("box_done"), st.get("current_box_index"))
            if key not in seen:
                seen.add(key)
                phases.append((round(time.time() - t0, 1), "状态变化",
                               f"status={st.get('status')} box={st.get('current_box_index')}"
                               f"/{st.get('box_total')} done={st.get('box_done')}"))
        time.sleep(0.5)

    print("\n== 相位轨迹 ==")
    for t, k, d in phases:
        print(f"  t+{t:5.1f}s [{k}] {d}")

    if final is None:
        final = last_run_row(api, cid, order_no=ORDER_NO)
    print("\n== 终态 ==")
    print(json.dumps(final, ensure_ascii=False, indent=1))

    # 7) 计数器 (NG 是否被刷)
    r = api.get("/api/v1/source/detection/results?channel=0")
    counters = {}
    if r.status_code == 200:
        j = r.json()
        counters = j.get("counters") or j.get("counter_values") or {}
    print(f"\n== 计数器 == {json.dumps(counters, ensure_ascii=False)}")

    # 8) 停检测
    api.post("/api/v1/source/detection/stop?channel=0")
    api.post("/api/v1/test/synthetic/stop?channel=0")

    # 9) 判定
    print("\n== 判定 ==")
    ok = True
    if switch_on:
        # 扫新单模式下, "扫新单(判定点)"相位只会在观测到 awaiting_paper 后触发,
        # 同样是挂等待的证据
        awaited = any("awaiting_paper" in d or k == "扫新单"
                      for _, k, d in phases)
        if not awaited:
            ok = False
            print("  ✗ B 失败: 尾箱落账后没挂「等放工单收尾」")
        else:
            print("  ✓ B: 尾箱落账后挂了等放工单")
        if final and final["status"] == "completed" and (final["final_result"] or "").upper() == "NG":
            print("  ✓ C: 时限到点判缺工单, 工单 NG 收尾 (箱2缺工单被识别)")
        else:
            ok = False
            print(f"  ✗ C 失败: 终态 {final}")
        boxes = (final or {}).get("box_details") or []
        if all(b.get("result") == "OK" for b in boxes) and len(boxes) == 2:
            print("  ✓ 两箱本身成绩 OK (提前放工单没有污染箱成绩)")
        else:
            ok = False
            print(f"  ✗ 箱成绩异常: {boxes}")
    else:
        if final and final["status"] == "completed" and (final["final_result"] or "").upper() == "OK":
            print("  ✓ A'/B' 基线复现: 箱1误检进账本 → 尾箱没放工单却 OK 收尾 (缺工单漏报)")
        else:
            print(f"  ? 基线终态与预期不同: {final}")
    print("\n结论:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
