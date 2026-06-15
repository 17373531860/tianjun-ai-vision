"""可见浏览器 UAT — 包装箱结算 (上银包装线, v3.21 M6).

客户现场叙事:
  工人扫工单标签开工 → 拿第一箱标签扫一下开始装箱 → 往箱里放若干托盘
  (每托盘检测滑块数达标=合格) → 封箱后扫下一张标签结算上一箱 → 往复.
  Monitor 实时显示当前工单 / 第几箱 / 托盘进度; 不启用配置时整块不显示 (零差异).

证据链 (路径 H 三件套):
  Phase A  API 契约 + 真挂接 — 扫码驱动状态机 + 经 synthetic 真检测路径触发托盘结算
           (★ A4 是核心: 若 source 的 on_cycle_settled 挂接没接好, 托盘计数永远 0)
  Phase B  可见浏览器 — 启用时 Monitor 显示包装进度卡; 禁用后刷新卡干净消失 (零差异)
  产物     视频 /tmp/uat_pkg_video/*.webm + 截图 /tmp/uat_pkg_shots/*.png + 日志 /tmp/uat_pkg_run.log

跑法 (后端 8001 RUNTIME_MODE=test + 前端 6001 已起):
  python tests/uat/uat_20260616_packaging_flow.py
"""
import json
import os
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8001"
FRONTEND = "http://127.0.0.1:6001"
SHOTS = "/tmp/uat_pkg_shots"
VIDEO = "/tmp/uat_pkg_video"
RUNLOG = "/tmp/uat_pkg_run.log"

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": str(detail)}
    steps_log.append(rec)
    print(f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}")


def _scan(code, channel_id=0):
    r = requests.post(f"{API}/api/v1/packaging-flows/scan",
                      json={"code": code, "channel_id": channel_id}, timeout=10)
    return r.json() if r.status_code == 200 else {"_http": r.status_code, "_body": r.text}


# ──────── 环境准备 ────────
def setup():
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(VIDEO, exist_ok=True)
    # 清理上轮 UAT 残留配置 (enabled 的先关再删)
    _purge_uat_configs()
    step("setup 清理旧 UAT 配置", True)


def _purge_uat_configs():
    r = requests.get(f"{API}/api/v1/packaging-flows", timeout=10)
    items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
    for c in items:
        if str(c.get("name", "")).startswith("__uat_"):
            if c.get("enabled"):
                requests.put(f"{API}/api/v1/packaging-flows/{c['id']}",
                             json={"enabled": False}, timeout=10)
            requests.delete(f"{API}/api/v1/packaging-flows/{c['id']}", timeout=10)


# ──────── Phase A: API 契约 + 真挂接 ────────
def phase_a():
    name = f"__uat_pkg_{uuid.uuid4().hex[:6]}"

    # A0 零差异: 还没启用配置时, scan 返回 handled=False (前端据此回退默认行为)
    pre = _scan("ANY-001")
    step("A0 无配置时 scan 不接管 (handled=False)",
         pre.get("handled") is False, f"reason={pre.get('reason')}")

    # A1 建启用配置 (channel 0, 每箱 2 托盘; 不配 MES → 不限箱数, 主链路照跑)
    r = requests.post(f"{API}/api/v1/packaging-flows", json={
        "name": name, "enabled": True, "channel_id": 0,
        "trays_per_box_fixed": 2, "label_match": "strip_hyphen",
        "on_mes_fail": "offline",
    }, timeout=10)
    ok_create = r.status_code == 201
    cid = r.json()["id"] if ok_create else None
    step("A1 建启用包装配置", ok_create, f"id={cid} http={r.status_code}")
    if not ok_create:
        return None

    # A2 扫工单标签开工 (带连字符, 应归一化为 ORD001)
    s2 = _scan("ORD-001")
    st2 = s2.get("state") or {}
    step("A2 扫工单 → 开工单 (去连字符归一化)",
         s2.get("handled") and st2.get("order_no") == "ORD001",
         f"order_no={st2.get('order_no')} status={st2.get('status')}")

    # A3 扫同号箱标签 → 开第 1 箱
    s3 = _scan("ORD001")
    st3 = s3.get("state") or {}
    step("A3 扫同号 → 开第 1 箱",
         st3.get("current_box_index") == 1,
         f"current_box_index={st3.get('current_box_index')} status={st3.get('status')}")

    # A4 ★核心: 经 synthetic 真检测路径跑一个 OK 周期 → on_cycle_settled 挂接 → 托盘计数 +1
    # 用两轮完整 A-B-C 内联剧本: 第二轮首步出现时 first_step 模式结算第一轮 OK 周期.
    def _seg(a, b, label=None):
        det = ([{"label": label, "confidence": 0.95, "bbox": [0.4, 0.4, 0.1, 0.1]}]
               if label else [])
        return {"from": a, "to": b, "detections": det}
    inline = {"name": "uat_pkg_tray", "fps": 60, "timeline": [
        _seg(0, 9), _seg(10, 39, "step_a"), _seg(40, 49),
        _seg(50, 79, "step_b"), _seg(80, 89),
        _seg(90, 119, "step_c"), _seg(120, 139),
        _seg(140, 169, "step_a"), _seg(170, 400),
    ]}
    requests.post(f"{API}/api/v1/test/synthetic/start",
                  json={"scenario_json": inline, "with_project": True,
                        "logic_mode": "sequential", "channel": 0}, timeout=10)
    requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                  json={"conf": 0.25, "iou": 0.45}, timeout=10)
    trays = 0
    for _ in range(60):  # 最多等 ~30s
        time.sleep(0.5)
        rs = requests.get(f"{API}/api/v1/packaging-flows/{cid}/state", timeout=10)
        if rs.status_code == 200:
            st = (rs.json() or {}).get("state") or {}
            trays = st.get("current_box_trays", 0)
            if trays >= 1:
                break
    step("A4 ★真检测路径触发托盘结算 (on_cycle_settled 挂接)",
         trays >= 1, f"current_box_trays={trays} (=0 说明挂接没通)")

    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)

    # A6 扫新工单标签 → 上一单结算, 切到新工单
    s6 = _scan("ORD-002")
    st6 = s6.get("state") or {}
    step("A6 扫新工单 → 结算上一单 + 切新工单",
         st6.get("order_no") == "ORD002",
         f"order_no={st6.get('order_no')}")

    return cid


# ──────── Phase B: 可见浏览器 (启用显示 / 禁用零差异) ────────
def phase_b(cid):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250,
                                     args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 1000},
            ignore_https_errors=True,
        )
        page = ctx.new_page()

        # B1 启用配置 → Monitor 应显示包装进度卡
        page.goto(f"{FRONTEND}/#/monitor")
        page.wait_for_load_state("networkidle")
        time.sleep(2.5)  # 给 Monitor onMounted 拉配置 + polling 留时间
        page.screenshot(path=f"{SHOTS}/B1_monitor_with_card.png", full_page=True)
        body1 = page.evaluate("document.body.innerText")
        has_card = page.locator(".packaging-card").count() > 0
        step("B1 启用时 Monitor 显示包装结算卡",
             has_card and "包装箱结算" in body1,
             f"card_count={page.locator('.packaging-card').count()}")

        # B2 禁用配置 → 刷新 → 卡应干净消失 (零差异验证)
        requests.put(f"{API}/api/v1/packaging-flows/{cid}",
                     json={"enabled": False}, timeout=10)
        page.reload()
        page.wait_for_load_state("networkidle")
        time.sleep(2.5)
        page.screenshot(path=f"{SHOTS}/B2_monitor_no_card.png", full_page=True)
        no_card = page.locator(".packaging-card").count() == 0
        step("B2 禁用后刷新卡消失 (零差异)",
             no_card, f"card_count={page.locator('.packaging-card').count()}")

        ctx.close()   # 先 close ctx 才会 flush 视频
        browser.close()


# ──────── 清理 + 总结 ────────
def cleanup_and_report():
    _purge_uat_configs()
    failed = [s for s in steps_log if not s["ok"]]
    summary = {"total": len(steps_log), "failed": len(failed), "steps": steps_log}
    with open(RUNLOG, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write(f"\n\nfailed: {len(failed)}\n")
    print("\n" + "=" * 50)
    print(f"UAT 包装结算: {len(steps_log) - len(failed)}/{len(steps_log)} OK, failed: {len(failed)}")
    print(f"视频: {VIDEO}/  截图: {SHOTS}/  日志: {RUNLOG}")
    print("=" * 50)


if __name__ == "__main__":
    setup()
    cid = phase_a()
    if cid:
        phase_b(cid)
    cleanup_and_report()
