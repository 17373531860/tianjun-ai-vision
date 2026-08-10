# -*- coding: utf-8 -*-
"""边界复现: 尾箱「封箱后、落账前」放工单 — 用户语义 vs 实现语义.

用户语义 (2026-08-10 口述): 放工单严格模式 = 放工单是**最后一个动作**;
尾箱封箱**前**的放工单检出全部过滤, 封箱**后**放的要算数 (没放则扫下一单判 NG).

实现语义 (v3.46 only_after_awaiting): 过滤右边界是尾箱**落账** (封箱步骤消失
确认 + disappear_delay 之后), 非等待态检出无记忆直接丢 —— 「封箱后~落账前」
存在几秒真空窗, 工人贴完箱马上放纸属现场常态动作, 恰落此窗.

变体 (单箱工单, 该箱即尾箱, 扫新单判定=现场默认):
  c1: 纸在封箱结束后 0.6s 出现, **跨过落账持续可见** (纸留在箱面上)
  c2: 纸在封箱结束后 0.6s 出现, 2s 后消失 (**落账前**工人已把箱搬走)
  c3: 拉宽缝隙 (封箱 disappear_delay 3→8s) + 纸只闪 1.5s 且**落账前已离场**
      — 模拟放工单动作类短爆发 + 工人手快, 检出完全落在缝里
三个变体按用户语义都该 OK 收尾; 若判 NG = 实现与语义有缝.

跑法: python tests/uat/repro_sy9_paper_boundary.py --variant c1 [--base ...]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import repro_sy9_paper_synth as R  # noqa: E402


def build_timeline(variant):
    TRAY = [0.50, 0.50, 0.40, 0.40]
    tray_dets = ([{"label": "托盘", "confidence": 0.9, "bbox": TRAY}]
                 + R._sliders_in_tray(*TRAY))
    tiebiao = [{"label": "贴标", "confidence": 0.9, "bbox": [0.05, 0.05, 0.12, 0.12]}]
    fengxiang = [{"label": "封箱", "confidence": 0.9, "bbox": [0.30, 0.10, 0.20, 0.20]}]
    paper = [{"label": "放工单", "confidence": 0.9, "bbox": [0.25, 0.15, 0.15, 0.15]}]
    tl = []

    def seg(f0, f1, dets):
        tl.append({"from": f0, "to": f1, "detections": dets})

    seg(0, 4, [])
    seg(5, 18, tiebiao)            # 贴标 → 开周期 (唯一箱 = 尾箱)
    seg(19, 24, [])
    seg(25, 100, tray_dets)        # 托盘+8滑块 稳定在位
    seg(101, 119, [])              # 托盘 gone → 记账 8
    seg(120, 134, fengxiang)       # 封箱 (f134 结束)
    seg(135, 139, [])
    # 封箱结束后 0.6s 放纸
    if variant == "c1":
        seg(140, 450, paper)       # 跨落账持续可见
    elif variant == "c2":
        seg(140, 160, paper)       # 2s 后消失 (实测落账 ≈ f140±, 正好擦边)
        seg(161, 450, [])
    else:                          # c3: 缝拉宽到 8s, 纸 1.5s 短爆发后离场
        seg(140, 154, paper)       # 落账 ≈ f134+确认+80帧 ≈ f220, 纸远在其前
        seg(155, 450, [])
    return {"name": f"sy9_paper_boundary_{variant}", "fps": R.FPS, "timeline": tl}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8005")
    ap.add_argument("--variant", choices=["c1", "c2", "c3"], default="c1")
    args = ap.parse_args()

    # 单箱工单: 8 滑块 ÷ 8/箱 = 1 箱 (该箱即尾箱)
    R.DISPATCH_QTY = 8
    order = R.ORDER_NO

    api = R.Api(args.base)
    R.wait_backend(api)
    R.start_mock_mes()
    R.cleanup(api)

    # MES 连接 (幂等)
    conns = api.get("/api/v1/mes/gateway/connections").json()
    conn = next((c for c in conns if c.get("name") == "__repro_mock_mes"), None)
    if conn is None:
        pull_cfg = {
            "enabled": True, "url": f"http://127.0.0.1:{R.MOCK_PORT}/api",
            "method": "POST",
            "content_type": "application/json; charset=UTF-8",
            "request_body_template": '{"api":"q","parameters":{"job_no":"{job_no}"}}',
            "success_path": "statusCode", "success_value": 200,
            "array_path": "response.resultData",
            "field_mapping": {"order_no": "job_no", "customer_name": "cust_name",
                              "product_spec": "spec", "planned_qty": "dispatch_qty"},
            "timeout_sec": 5, "retry_count": 0,
        }
        r = api.post("/api/v1/mes/gateway/connections", json={
            "name": "__repro_mock_mes", "adapter_type": "rest",
            "enabled": True, "config": {"pull": pull_cfg},
            "push_events": [], "pull_enabled": True})
        conn = r.json()
    conn_id = conn["id"]

    # 项目 (幂等: 存在则 PUT); c3 把封箱 disappear_delay 拉到 8s 加宽缝隙
    pj = R.project_payload()
    if args.variant == "c3":
        for s in pj["steps_config"]:
            if s.get("label") == "封箱":
                s["disappear_delay"] = 8
    r = api.get("/api/v1/projects/").json()
    items = r.get("items", r) if isinstance(r, dict) else r
    old = next((p for p in items or [] if p.get("name") == pj["name"]), None)
    if old:
        pid = old["id"]
        api.put(f"/api/v1/projects/{pid}", json=pj)
    else:
        pid = api.post("/api/v1/projects/", json=pj).json()["id"]
    api.post(f"/api/v1/projects/{pid}/activate")

    # 包装配置: 开关开 + 扫新单判定 (现场默认)
    cfg = api.post("/api/v1/packaging-flows",
                   json=R.flow_payload(conn_id, switch_on=True, judge="scan")).json()
    cid = cfg["id"]
    print(f"变体={args.variant} 配置 id={cid} (开关开, 扫新单判定, 单箱工单)")

    # 起 synthetic + 检测 + 扫工单
    api.post("/api/v1/debug/logs/clear")
    r = api.post("/api/v1/test/synthetic/start",
                 json={"scenario_json": build_timeline(args.variant), "channel": 0})
    assert r.status_code == 200, r.text
    r = api.post("/api/v1/source/detection/start?channel=0", json={})
    assert r.status_code == 200, r.text
    time.sleep(1.0)
    api.post("/api/v1/packaging-flows/scan", json={"code": order, "channel_id": 0})
    st = R.flow_state(api, cid)
    assert st and st.get("box_total") == 1, f"单箱工单未立起: {st}"
    print(f"已扫 {order} box_total=1 (即尾箱)")

    # 跟踪: 挂等待就扫新单触发判定 (现场动作); 收尾即止
    t0 = time.time()
    seen = set()
    closed = None
    next_scanned = False
    while time.time() - t0 < 70:
        st = R.flow_state(api, cid)
        if st is None:
            closed = R.last_run_row(api, cid, order)
            if closed and closed["status"] == "completed":
                print(f"t+{time.time()-t0:5.1f}s 工单已收尾: "
                      f"final={closed['final_result']}")
                break
        else:
            key = (st.get("status"), st.get("box_done"))
            if key not in seen:
                seen.add(key)
                print(f"t+{time.time()-t0:5.1f}s status={st.get('status')} "
                      f"done={st.get('box_done')}/1")
            if st.get("status") == "awaiting_paper" and not next_scanned:
                time.sleep(1)
                print(f"t+{time.time()-t0:5.1f}s 扫新单 SY9NEXT (判定点)")
                api.post("/api/v1/packaging-flows/scan",
                         json={"code": "SY9NEXT", "channel_id": 0})
                next_scanned = True
        time.sleep(0.5)
    if closed is None:
        closed = R.last_run_row(api, cid, order)

    print("\n终态:", json.dumps(closed, ensure_ascii=False))
    logs = api.get("/api/v1/debug/logs?category=backend.packaging&limit=200").json()
    lines = [l for l in (logs.get("logs") or logs.get("items") or logs)
             if isinstance(l, dict)]
    for l in lines:
        a = l.get("action") or ""
        if any(k in a for k in ("丢弃", "即时收尾", "挂等放工单", "未放工单",
                                "报警", "判定")):
            print(f"  {l.get('ts','')} {a} | {(l.get('detail') or '')[:80]}")

    api.post("/api/v1/test/synthetic/stop?channel=0")
    api.post("/api/v1/source/detection/stop?channel=0")

    ok = (closed and closed["status"] == "completed"
          and (closed["final_result"] or "").upper() == "OK")
    print(f"\n判定 (用户语义: 封箱后放的纸要算数 → 应 OK 收尾): "
          f"{'PASS' if ok else 'FAIL — 封箱后~落账前的放纸被误丢, 误判缺工单'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
