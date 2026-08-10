"""正式 UAT: 真视频 + 真模型 验证「只认收尾后放的工单」(SY9 v3.46).

素材: tests/uat/assets/sy9_20260805_two_boxes.mkv (150s, 两箱工单现场录像)
      tests/uat/assets/sy9_packing_best.pt   (v33-groove 系, 12 类)

时间线 (模型密扫实测):
  t≈7s   箱1贴标   → 开周期1
  t≈25-48 三盘滑块进箱1 (容器逐盘记账)
  t≈46-48 箱1封箱  → last_step 结算 ≈t51 → 箱1落账
  t≈51-62 ⚠️ 工人把工单纸塞进箱1 (提前放! 模型报「工单纸」, 「放工单」动作类全程零检出)
  t≈84s  箱2贴标
  t≈114-126 滑块进箱2
  t≈143-144 箱2封箱 → 结算 ≈t147 → 尾箱落账 → 挂「等放工单收尾」
  t=150  视频结束 (EOF 自动停检测; 配 on_forced_stop=keep 保住等待态, 判定权归时限)
  t≈162  时限(15s)到点 → 判缺工单 → 工单 NG 收尾

判定 (--switch on):
  A. 箱1 提前放工单被丢弃: debug 有「按开关丢弃」, 无 early_paper 报警
  B. 尾箱落账后 status=awaiting_paper
  C. 时限到点 missing_paper 报警 + 工单 NG 收尾 (箱2缺工单被识别)
基线 (--switch off): 复现现场毛病 — early_paper 报警(共用NG档) + 尾箱靠历史账本
  误命中箱1检出 → 工单 OK 收尾 (缺工单漏报).

跑法 (先起 RUNTIME_MODE=test 后端 8005):
  python tests/uat/uat_sy9_video_real.py --base http://127.0.0.1:8005 --switch on
"""
import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

ASSETS = Path(__file__).parent / "assets"
VIDEO = str(ASSETS / "sy9_20260805_two_boxes.mkv")
MODEL = str(ASSETS / "sy9_packing_best.pt")
CH = 0
# 每轮唯一工单号: 避免上一轮遗留的进行中 run 在扫码时复活 (重扫拦截只拦已完成OK)
ORDER_NO = f"SY9T2B{time.strftime('%H%M%S')}"
ITEMS_PER_BOX = 96          # 现场规格: 每箱 4 托盘 x 24 滑块
DISPATCH_QTY = ITEMS_PER_BOX * 2
PAPER_TIMEOUT_S = 15
MOCK_PORT = 19081
VIDEO_LEN_S = 150
SITE_PROJECT_ID = 41        # SY8 现场真配置 (容器四盘/AND双门/peak_cap/去重)
MODEL_CONF = 0.10           # 模型层置信度拉低, 步骤阈值仍按项目配置把关


class _MesHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        job_no = str(((body.get("parameters") or {}).get("job_no")) or "").strip()
        rows = ([{"job_no": ORDER_NO, "cust_name": "上银(UAT)",
                  "dispatch_qty": DISPATCH_QTY, "spec": "SY9T"}]
                if job_no == ORDER_NO else [])
        data = json.dumps({"statusCode": 200, "success": True,
                           "response": {"resultData": rows}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def project_runtime_config(site_project):
    """项目 41 (SY8 现场真配置) 原样下发运行时, 只做两处本视频适配:
    1. 序列摘掉「放油嘴包」— 本录像该动作最高置信度仅 0.51 零星闪现,
       留着会缺步挂起 (探路跑实证: 周期不结算 + 防呆提示);
    2. 加「工单纸」序列外步骤 — 「放工单」动作类本录像全程最高 conf 0.053
       (conf=0.01 逐帧扫也不出), 放纸的真实信号是工单纸物体类 (51-62s, 0.56-0.84);
    3. NG 处置挂起改即时结算 — 本录像是**真短装样片** (凹槽类实证每盘 21-22/24),
       现场配置 short_count=hold 会把短装箱挂 120s 等人工处置, 挡住放工单链路;
       UAT 关注放工单判定, 短装箱直接按成绩 NG 落账即可."""
    steps = [dict(s) for s in (site_project.get("steps_config") or [])]
    if not any(s.get("label") == "工单纸" for s in steps):
        steps.append({"id": 10, "label": "工单纸", "enabled": True,
                      "threshold": 50, "min_frames": 5,
                      "detection_type": "dynamic", "join_cycle": True})
    oil_ids = {s["id"] for s in steps if s.get("label") == "放油嘴包"}
    pc = dict(site_project.get("pipeline_config") or {})
    def _keep(x):
        sid = x.get("step_id") if isinstance(x, dict) else x
        return sid not in oil_ids

    for key in ("sequence_order", "custom_sequence_order"):
        if pc.get(key):
            pc[key] = [x for x in pc[key] if _keep(x)]
    ng = dict(pc.get("ng_handling") or {})
    ng.update({"missing_step": "ng", "short_count": "ng", "gate_enabled": False})
    pc["ng_handling"] = ng
    # 适配 4: 事件全关 require_ack — 现场事件 2/3/4 都要人工点确认框
    # (ack_timeout=0 无限等), 无人值守跑视频会把检测线定格锁死
    events = [dict(e) for e in (site_project.get("events_config") or [])]
    for e in events:
        e["require_ack"] = False
    return {
        "project_id": site_project["id"], "name": site_project["name"],
        "task_type": site_project["task_type"],
        "logic_mode": site_project["logic_mode"],
        "pipeline_config": pc, "steps_config": steps,
        "events_config": events,
        "counters_config": site_project.get("counters_config") or [],
        "data_config": site_project.get("data_config") or {},
    }


def flow_payload(conn_id, switch_on):
    return {
        "name": "__uat_sy9_video_flow", "enabled": True, "channel_id": CH,
        "pull_conn_id": conn_id,
        "count_unit": "sliders",
        "items_per_box_source": "config",
        "items_per_box_fixed": ITEMS_PER_BOX,
        "slider_total_field": "dispatch_qty",
        "box_count_source": "field", "box_count_field": "dispatch_qty",
        "label_match": "exact", "label_len": 0,
        "on_mes_fail": "block", "on_label_mismatch": "off", "on_short_box": "redo",
        # EOF 自动停检测不许豁免放工单收尾, 判定权归时限 Timer
        "on_forced_stop": "keep",
        "tail_paper_order_required": True,
        "tail_paper_step_label": "工单纸",
        "tail_paper_as_close_action": True,
        "tail_paper_only_after_awaiting": bool(switch_on),
        "tail_paper_scan_alarm": False,
        "tail_paper_timeout_s": PAPER_TIMEOUT_S,
        # 缺工单(含提前放)=不良NG(2); 箱NG/短装映射「防呆提示」(4) 而非
        # 「需人工确认」(3) — 事件3会把检测线定格锁死 (manual-ack blocked),
        # 本录像是真短装样片, 每箱都触发, 定格会冻住整条链路
        "event_missing_paper": 2,
        "event_short_box": 4, "event_box_ng": 4, "event_mes_fail": 4,
        "auto_switch_project": False,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8005")
    ap.add_argument("--switch", choices=["on", "off"], default="on")
    args = ap.parse_args()
    switch_on = args.switch == "on"
    api = args.base

    def call(method, path, **kw):
        r = getattr(requests, method)(f"{api}{path}", timeout=20, **kw)
        r.raise_for_status()
        return r.json()

    def results():
        try:
            r = requests.get(
                f"{api}/api/v1/source/detection/results?channel={CH}", timeout=10)
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}

    def flow_state(cid):
        r = requests.get(f"{api}/api/v1/packaging-flows/{cid}/state", timeout=10)
        if r.status_code != 200:
            return None
        j = r.json()
        return j.get("state") if isinstance(j, dict) and "state" in j else j

    def pkg_logs():
        try:
            r = requests.get(
                f"{api}/api/v1/debug/logs?category=backend.packaging&limit=300",
                timeout=10)
            j = r.json()
            return [l for l in (j.get("logs") or j.get("items") or j)
                    if isinstance(l, dict)]
        except Exception:
            return []

    def last_run(order_no):
        import sqlite3
        con = sqlite3.connect("/tmp/tj_sy9_repro/sql_app.db")
        row = con.execute(
            "select order_no, status, final_result, box_done, box_total, box_details"
            " from packaging_flow_runs where order_no=? order by id desc limit 1",
            (order_no,)).fetchone()
        con.close()
        if row is None:
            return None
        return {"order_no": row[0], "status": row[1], "final_result": row[2],
                "box_done": row[3], "box_total": row[4],
                "box_details": json.loads(row[5]) if row[5] else []}

    print(f"== SY9 真视频 UAT (开关={'开' if switch_on else '关(基线)'}) ==")

    # mock MES
    srv = HTTPServer(("127.0.0.1", MOCK_PORT), _MesHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # 清场 (含清人工确认定格 — 上一轮若留了 manual-ack 冻结会锁死本轮检测)
    for ep in ("/api/v1/source/detection/ack-event",
               "/api/v1/source/detection/stop", "/api/v1/source/video/stop",
               "/api/v1/test/synthetic/stop"):
        try:
            requests.post(f"{api}{ep}?channel={CH}", json={}, timeout=15)
        except Exception:
            pass
    time.sleep(1.5)
    requests.post(f"{api}/api/v1/debug/logs/clear", timeout=10)

    # MES 连接 (幂等)
    conns = call("get", "/api/v1/mes/gateway/connections")
    conn = next((c for c in conns if c.get("name") == "__uat_mock_mes"), None)
    pull_cfg = {
        "enabled": True, "url": f"http://127.0.0.1:{MOCK_PORT}/api",
        "method": "POST", "content_type": "application/json; charset=UTF-8",
        "request_body_template": '{"api":"q","parameters":{"job_no":"{job_no}"}}',
        "success_path": "statusCode", "success_value": 200,
        "array_path": "response.resultData",
        "field_mapping": {"order_no": "job_no", "customer_name": "cust_name",
                          "product_spec": "spec", "planned_qty": "dispatch_qty"},
        "timeout_sec": 5, "retry_count": 0,
    }
    if conn is None:
        conn = call("post", "/api/v1/mes/gateway/connections", json={
            "name": "__uat_mock_mes", "adapter_type": "rest",
            "enabled": True, "config": {"pull": pull_cfg},
            "push_events": [], "pull_enabled": True})
    conn_id = conn["id"]

    # 包装配置 (幂等: 其他配置全禁用, 本配置删了重建保证开关值干净)
    r = call("get", "/api/v1/packaging-flows")
    for c in (r.get("items", r) if isinstance(r, dict) else r) or []:
        if c.get("name") == "__uat_sy9_video_flow":
            requests.put(f"{api}/api/v1/packaging-flows/{c['id']}",
                         json={"enabled": False}, timeout=10)
            requests.delete(f"{api}/api/v1/packaging-flows/{c['id']}", timeout=10)
        elif c.get("enabled"):
            requests.put(f"{api}/api/v1/packaging-flows/{c['id']}",
                         json={"enabled": False}, timeout=10)
    cfg_row = call("post", "/api/v1/packaging-flows",
                   json=flow_payload(conn_id, switch_on))
    cid = cfg_row["id"]
    # 清遗留 run: 配置 id 可能复用, 上一轮 keep 策略保下的进行中工单会复活
    st0 = flow_state(cid)
    if st0 and st0.get("order_no"):
        r = requests.post(f"{api}/api/v1/packaging-flows/{cid}/force-settle",
                          json={"reason": "UAT 开跑清场"}, timeout=10)
        print(f"  · 清掉遗留工单 {st0.get('order_no')} http={r.status_code}")
    print(f"  · 包装配置 id={cid} only_after_awaiting={switch_on} "
          f"label=工单纸 时限={PAPER_TIMEOUT_S}s on_forced_stop=keep")

    # 项目 41 现场真配置推运行时 + 视频 + 模型
    site_project = call("get", f"/api/v1/projects/{SITE_PROJECT_ID}")
    call("post", f"/api/v1/source/detection/set-project?channel={CH}",
         json=project_runtime_config(site_project))
    print(f"  · 运行项目: {site_project['name']}(id={site_project['id']}) "
          f"[摘放油嘴包 +工单纸步骤]")
    call("post", f"/api/v1/source/video/start?channel={CH}",
         json={"file_path": VIDEO, "speed": 1.0})
    call("post", f"/api/v1/source/detection/start?channel={CH}",
         json={"session_name": f"uat_sy9_paper_{args.switch}", "models": [
             {"name": "main", "model_path": MODEL, "conf": MODEL_CONF,
              "iou": 0.45, "priority": 100}]})
    print(f"  · 视频+真模型检测已启动 (模型conf={MODEL_CONF})")

    # 虚拟扫一次工单号
    time.sleep(2.0)
    call("post", "/api/v1/packaging-flows/scan",
         json={"code": ORDER_NO, "channel_id": CH})
    st = flow_state(cid)
    print(f"  · 已扫工单 {ORDER_NO} → box_total={st and st.get('box_total')}")
    assert st and st.get("box_total") == 2, f"两箱工单未立起: {st}"

    # 跟踪: 视频 150s + 时限 + 余量; 同时抓容器逐盘记账做对账
    t0 = time.time()
    phases, seen = [], set()
    final = None
    tray_books = []      # (t, 盘明细) 全程逐盘
    last_done_len = 0
    while time.time() - t0 < VIDEO_LEN_S + PAPER_TIMEOUT_S + 60:
        el = round(time.time() - t0, 1)
        cont = ((results().get("custom_mix_state") or {}).get("container") or {})
        done = cont.get("done_detail") or []
        if len(done) > last_done_len:
            for tr in done[last_done_len:]:
                tray_books.append((el, tr))
                print(f"  t+{el:6.1f}s 盘进箱: {json.dumps(tr, ensure_ascii=False)}")
        last_done_len = len(done) if len(done) >= last_done_len else 0
        st = flow_state(cid)
        if st is None:
            final = last_run(ORDER_NO)
            if final and final["status"] in ("completed", "aborted"):
                phases.append((el, "工单收尾",
                               f"status={final['status']} final={final['final_result']}"))
                break
        else:
            # 短装挂起 → 模拟现场主管「照实落账」处置 (本录像是真短装样片)
            if st.get("status") == "pending_remediation":
                pend = st.get("pending_box") or {}
                sc = pend.get("sliders")
                r = requests.post(
                    f"{api}/api/v1/packaging-flows/{cid}/supplement-sliders",
                    json={"target_count": sc, "reason": "UAT: 短装照实落账"},
                    timeout=10)
                phases.append((el, "人工处置",
                               f"箱{pend.get('box')} 短装 {sc}/{pend.get('target')} "
                               f"照实落账 http={r.status_code}"))
                time.sleep(0.5)
                st = flow_state(cid) or st
            key = (st.get("status"), st.get("box_done"),
                   st.get("current_box_index"), st.get("current_box_sliders"))
            if key[:3] not in seen:
                seen.add(key[:3])
                phases.append((el, "状态", f"status={st.get('status')} "
                               f"box={st.get('current_box_index')}/{st.get('box_total')} "
                               f"done={st.get('box_done')}"))
        time.sleep(1.0)

    print("\n== 相位轨迹 ==")
    for t, k, d in phases:
        print(f"  t+{t:6.1f}s [{k}] {d}")
    if final is None:
        final = last_run(ORDER_NO)
    print("\n== 终态 ==", json.dumps(final, ensure_ascii=False))
    print(f"\n== 记账对账 == 全程盘进箱 {len(tray_books)} 次 (期望 8 = 两箱x四盘)")
    for el, tr in tray_books:
        print(f"  t+{el:6.1f}s {json.dumps(tr, ensure_ascii=False)}")
    for b in (final or {}).get("box_details") or []:
        print(f"  箱{b['box']}: 记账滑块 {b['sliders']}/{b['target']} "
              f"{'尾箱' if b.get('is_tail') else ''} 箱成绩={b['result']}")

    logs = pkg_logs()
    drops = [l for l in logs if "丢弃" in (l.get("action") or "")]
    early = [l for l in logs if "early_paper" in (l.get("action") or "") + (l.get("detail") or "")
             or "提前放工单" in (l.get("action") or "")]
    missing = [l for l in logs if "missing_paper" in (l.get("action") or "") + (l.get("detail") or "")
               or "未放工单" in (l.get("action") or "")]
    awaiting_log = [l for l in logs if "挂等放工单" in (l.get("action") or "")]
    print(f"\n== 关键日志 == 丢弃x{len(drops)} 提前放报警x{len(early)} "
          f"缺工单x{len(missing)} 挂等待x{len(awaiting_log)}")
    for l in drops[:3] + early[:3] + awaiting_log[:2] + missing[:3]:
        print(f"  {l.get('ts','')} {l.get('action','')} | {(l.get('detail') or '')[:90]}")

    # 收尾清场
    requests.post(f"{api}/api/v1/source/detection/stop?channel={CH}", timeout=15)
    requests.post(f"{api}/api/v1/source/video/stop?channel={CH}", timeout=15)

    print("\n== 判定 ==")
    ok = True
    if switch_on:
        if drops and not early:
            print(f"  ✓ A: 箱1 提前放工单被丢弃 ({len(drops)} 次检出全拦), 无提前放报警")
        else:
            ok = False
            print(f"  ✗ A 失败: 丢弃x{len(drops)} 提前放报警x{len(early)}")
        if awaiting_log or any("awaiting" in d for _, _, d in phases):
            print("  ✓ B: 尾箱落账后挂「等放工单收尾」")
        else:
            ok = False
            print("  ✗ B 失败: 未见等待态")
        if (final and final["status"] == "completed"
                and (final["final_result"] or "").upper() == "NG" and missing):
            print("  ✓ C: 时限到点判缺工单, 工单 NG 收尾 (箱2缺工单被识别)")
        else:
            ok = False
            print(f"  ✗ C 失败: 终态={final} 缺工单日志x{len(missing)}")
    else:
        hit_early = bool(early)
        # 漏报判据: 工单直接收尾, 全程没挂等待、没报缺工单 (历史账本误命中).
        # 终态 OK/NG 不作判据 — 箱滑块计数是否达标与放工单无关.
        leaked = (final and final["status"] == "completed"
                  and not missing and not awaiting_log
                  and not any("awaiting" in d for _, _, d in phases))
        print(f"  {'✓' if hit_early else '?'} A': 提前放工单报警(共用NG档) x{len(early)}")
        print(f"  {'✓' if leaked else '?'} B': 尾箱未挂等待/未报缺工单, "
              f"靠历史账本误命中直接收尾 (缺工单漏报)")
        ok = hit_early and leaked
    print("\n结论:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
