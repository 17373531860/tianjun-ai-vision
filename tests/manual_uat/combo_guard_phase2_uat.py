# -*- coding: utf-8 -*-
"""v3.49 二期「缸体判型客户诉求全覆盖」— 可见浏览器 UAT (headless=False).

四幕 (对应 2026-08-11 客户反馈):
  幕1 大字卡+实时缸型: live_display 开 → Monitor 大字计数卡 + 缸型;
      mock PLC 点位 cyl_type 4→6, 缸型标记 机型X→机型Y 即时切换;
  幕2 乱序报警: check_order 开, 区B 已开始后区A 又新增 → 红幅「工序乱序」;
  幕3 补救闭环: 区A 少装切步红幅 → 回头补第 2 件 → 绿幅「已补齐」+
      补齐事件计数 +1 → 收尾正常结算 OK;
  幕4 结算挂起: on_settle_mismatch=hold → 收尾时 (1,1) 未命中 → 琥珀横幅
      挂起不结算 → 4a 补齐自动按 OK 落账 / 4b 超时按 NG 落账。

跑法 (先起 backend 8001 [RUNTIME_MODE=test] + frontend 6001):
  python tests/manual_uat/combo_guard_phase2_uat.py [frontend_port]
证据落地: tests/manual_uat/evidence/combo_guard_phase2_<date>/
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

API = os.environ.get("UAT_API", "http://localhost:8001")
BASE = f"http://localhost:{sys.argv[1] if len(sys.argv) > 1 else '6001'}"
NAME = "__uat_判型二期"
PLC_NAME = "__uat_虚拟PLC二期"
CH = 0

EVIDENCE = Path(__file__).parent / "evidence" / f"combo_guard_phase2_{datetime.now():%Y-%m-%d}"
EVIDENCE.mkdir(parents=True, exist_ok=True)

verdict = {"steps": [], "pass": True}


def step(name: str, ok: bool, detail: str = ""):
    verdict["steps"].append({"name": name, "ok": ok, "detail": detail})
    verdict["pass"] = verdict["pass"] and ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def cleanup():
    requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=5)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel={CH}", timeout=5)
    payload = requests.get(f"{API}/api/v1/projects", timeout=5).json()
    for p in (payload.get("items") if isinstance(payload, dict) else payload) or []:
        if (p.get("name") or "").startswith("__uat_"):
            requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=5)
    conns = requests.get(f"{API}/api/v1/plc/connections", timeout=5).json()
    for c in conns.get("connections") or []:
        if (c.get("name") or "").startswith("__uat_"):
            requests.delete(f"{API}/api/v1/plc/connections/{c['id']}", timeout=5)


def _det(label, x, y):
    return {"label": label, "confidence": 0.92, "bbox": [x, y, 0.15, 0.15]}


# ---------------- 剧本 ----------------

def _scn_live():
    """幕1: 区A 两个位置锁定后长驻 (大字卡计数 2, 不结算, 留时间切缸型)。"""
    return {"name": "p2_live", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 129, "detections": [_det("区A", 0.1, 0.1)]},
        {"from": 130, "to": 149, "detections": []},
        {"from": 150, "to": 269, "detections": [_det("区A", 0.4, 0.1)]},
        {"from": 270, "to": 14400, "detections": []},
    ]}


def _scn_order():
    """幕2: 区A×2 (合法) → 区B×1 → 又回头装区A 第 3 件 = 乱序 (数量本身合法)。"""
    return {"name": "p2_order", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 129, "detections": [_det("区A", 0.1, 0.1)]},
        {"from": 130, "to": 149, "detections": []},
        {"from": 150, "to": 269, "detections": [_det("区A", 0.4, 0.1)]},
        {"from": 270, "to": 289, "detections": []},
        {"from": 290, "to": 409, "detections": [_det("区B", 0.7, 0.5)]},
        {"from": 410, "to": 429, "detections": []},
        {"from": 430, "to": 549, "detections": [_det("区A", 0.1, 0.4)]},
        {"from": 550, "to": 14400, "detections": []},
    ]}


def _scn_remedy():
    """幕3: 区A×1 → 区B (少装红幅) → 补区A 第 2 件 (绿幅消警) → 收尾结算 OK。"""
    return {"name": "p2_remedy", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 129, "detections": [_det("区A", 0.1, 0.1)]},
        {"from": 130, "to": 149, "detections": []},
        {"from": 150, "to": 269, "detections": [_det("区B", 0.7, 0.5)]},
        {"from": 270, "to": 289, "detections": []},
        {"from": 290, "to": 409, "detections": [_det("区A", 0.4, 0.1)]},
        {"from": 410, "to": 429, "detections": []},
        {"from": 430, "to": 549, "detections": [_det("收尾", 0.5, 0.8)]},
        {"from": 550, "to": 14400, "detections": []},
    ]}


def _scn_hold(with_remedy: bool):
    """幕4: 区A×1 → 区B → 收尾 (查表 (1,1) 未命中 → 挂起);
    with_remedy=True 时挂起后补区A 第 2 件 → 自动按 (2,1) OK 落账。"""
    tl = [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 129, "detections": [_det("区A", 0.1, 0.1)]},
        {"from": 130, "to": 149, "detections": []},
        {"from": 150, "to": 269, "detections": [_det("区B", 0.7, 0.5)]},
        {"from": 270, "to": 289, "detections": []},
        {"from": 290, "to": 409, "detections": [_det("收尾", 0.5, 0.8)]},
        {"from": 410, "to": 469, "detections": []},
    ]
    if with_remedy:
        tl.append({"from": 470, "to": 589, "detections": [_det("区A", 0.4, 0.1)]})
        tl.append({"from": 590, "to": 14400, "detections": []})
    else:
        tl.append({"from": 470, "to": 14400, "detections": []})
    return {"name": f"p2_hold_{'remedy' if with_remedy else 'timeout'}",
            "fps": 60, "timeline": tl}


# ---------------- 基座 ----------------

def _counters():
    body = requests.get(f"{API}/api/v1/source/detection/results?channel={CH}",
                        timeout=5).json()
    return body.get("counters") or {}, body


def _cv(body):
    return body.get("combo_verdict") or {}


def _guard_last(body):
    return (_cv(body).get("step_guard") or {}).get("last") or {}


def _run_scenario(page, pid, scenario, wait_pred, timeout=60):
    detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
    requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario_json": scenario, "channel": CH, "with_project": False,
    }, timeout=10).raise_for_status()
    sp = {k: detail.get(k) for k in (
        "steps_config", "pipeline_config", "events_config",
        "counters_config", "data_config", "logic_mode")}
    sp["data_config"] = sp.get("data_config") or {}
    requests.post(f"{API}/api/v1/source/detection/set-project?channel={CH}",
                  json={**sp, "project_id": pid, "name": NAME},
                  timeout=10).raise_for_status()
    requests.post(f"{API}/api/v1/source/detection/start?channel={CH}",
                  json={"conf": 0.25, "iou": 0.45}, timeout=10).raise_for_status()
    page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")
    return _wait(wait_pred, timeout)


def _wait(pred, timeout=60):
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        _, body = _counters()
        try:
            if pred(body):
                break
        except Exception:
            pass
        time.sleep(0.4)
    time.sleep(1.0)  # 留轮询拍给前端渲染
    return body


def _stop():
    requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=5)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel={CH}", timeout=5)
    time.sleep(0.5)


def _patch_guard(pid, **kv):
    pc = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()["pipeline_config"]
    pc["combo_table"]["step_guard"].update(kv)
    requests.put(f"{API}/api/v1/projects/{pid}",
                 json={"pipeline_config": pc}, timeout=5).raise_for_status()


def main() -> int:
    cleanup()

    # ---- mock PLC 连接 (缸型点位 cyl_type) ----
    r = requests.post(f"{API}/api/v1/plc/connections", json={
        "name": PLC_NAME, "driver": "mock", "enabled": True,
        "conn_params": {"store_id": "uat_phase2", "poll_interval_ms": 50},
        "points": [{"key": "cyl_type", "addr": "m2", "type": "int16", "dir": "read"}],
        "read_rules": [], "write_rules": [], "options": {},
    }, timeout=10)
    r.raise_for_status()
    plc_id = r.json()["id"]
    requests.post(f"{API}/api/v1/plc/connections/{plc_id}/mock-set",
                  json={"point": "cyl_type", "value": 4}, timeout=5).raise_for_status()

    # ---- 项目: 判型表 + 全部二期开关 ----
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": NAME, "task_type": "detection", "logic_mode": "detection",
        "steps_config": [
            {"id": 1, "label": "区A", "name": "区域A", "enabled": True, "min_frames": 1, "threshold": 30},
            {"id": 2, "label": "区B", "name": "区域B", "enabled": True, "min_frames": 1, "threshold": 30},
            {"id": 3, "label": "收尾", "name": "收尾", "enabled": True, "min_frames": 1, "threshold": 30},
        ],
        "pipeline_config": {
            "settlement_mode": "last_step", "detection_steps": [1, 2, 3],
            "combo_table": {
                "enabled": True, "labels": ["区A", "区B"], "count_mode": "positional",
                "tracking": {"iou": 0.4, "ema_alpha": 0.6, "min_consecutive": 3,
                             "pending_ttl": 10, "perish_ticks": 0, "idle_reset_ticks": 0},
                "rows": [
                    {"counts": [2, 1], "verdict": "OK", "tag": "机型X", "plc_code": "4"},
                    {"counts": [3, 1], "verdict": "OK", "tag": "机型Y", "plc_code": "6"},
                ],
                "live_display": {"enabled": True, "size": "large",
                                 "show_plc_type": True, "position": "top"},
                "plc_display": {"connection_id": plc_id, "point": "cyl_type"},
                "step_guard": {
                    "enabled": True, "check_under": True, "check_over": True,
                    "check_order": True, "narrow_by_progress": True,
                    "action": "hint", "event_id": 3, "resolved_event_id": 4,
                    "expected_source": "table", "plc_unavailable": "table",
                    "on_settle_mismatch": "ng", "hold_timeout_s": 120,
                },
            },
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [{"counter_name": "合格总数", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [{"counter_name": "不良总数", "delta": 1}]},
            {"id": 3, "name": "数量门警告", "actions": [{"counter_name": "数量门警告数", "delta": 1}]},
            {"id": 4, "name": "已补齐提示", "actions": [{"counter_name": "补齐数", "delta": 1}]},
        ],
        "counters_config": [{"name": "合格总数", "value": 0}, {"name": "不良总数", "value": 0},
                            {"name": "数量门警告数", "value": 0}, {"name": "补齐数", "value": 0}],
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=10)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})

        # ── 幕0. 配置页人眼验证: 二期配置区渲染 + 回显 ──
        page.goto(f"{BASE}/#/project", wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=10000)
        page.locator("input[placeholder*='搜索项目']").fill(NAME)
        time.sleep(0.8)
        page.locator(f"div.p-4:has-text('{NAME}')").first.click()
        time.sleep(0.8)
        page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
        time.sleep(0.8)
        card = page.locator(".el-card:has-text('计数组合判定表')").first
        card.scroll_into_view_if_needed()
        txt = card.inner_text()
        step("幕0 二期配置区渲染 (顺序检查/已补齐/结算处置/大字卡/按标签覆盖)", all(
            k in txt for k in ("工序顺序检查", "已补齐事件", "结算数量不符时",
                               "监控大字实时卡", "按标签单独覆盖追踪参数")))
        step("幕0 顺序检查/大字卡开关回显开",
             "is-checked" in (card.locator(".combo-guard-order-switch").first
                              .get_attribute("class") or "")
             and "is-checked" in (card.locator(".combo-live-switch").first
                                  .get_attribute("class") or ""))
        page.screenshot(path=str(EVIDENCE / "00_phase2_config.png"), full_page=True)

        # ── 幕1. 大字卡 + 缸型 4→6 即时切换 ──
        body = _run_scenario(page, pid, _scn_live(),
                             lambda b: (_cv(b).get("positional_counts") or {}).get("区A") == 2)
        pt = _cv(body).get("plc_type") or {}
        step("幕1 API 透出缸型 (value=4 → 机型X)",
             str(pt.get("value")) == "4" and pt.get("tag") == "机型X",
             json.dumps(pt, ensure_ascii=False))
        ptext = page.inner_text("body")
        step("幕1 Monitor 大字卡可见 (缸型 + 机型X)",
             "缸型" in ptext and "机型X" in ptext)
        page.screenshot(path=str(EVIDENCE / "01_bigcard_plc4.png"), full_page=True)
        requests.post(f"{API}/api/v1/plc/connections/{plc_id}/mock-set",
                      json={"point": "cyl_type", "value": 6}, timeout=5)
        body = _wait(lambda b: (_cv(b).get("plc_type") or {}).get("tag") == "机型Y",
                     timeout=15)
        step("幕1 mock 切缸型 4→6, 标记即时变 机型Y",
             (_cv(body).get("plc_type") or {}).get("tag") == "机型Y",
             json.dumps(_cv(body).get("plc_type"), ensure_ascii=False))
        time.sleep(1.0)
        step("幕1 Monitor 缸型显示跟随切换", "机型Y" in page.inner_text("body"))
        page.screenshot(path=str(EVIDENCE / "02_bigcard_plc6.png"), full_page=True)
        _stop()
        requests.post(f"{API}/api/v1/plc/connections/{plc_id}/mock-set",
                      json={"point": "cyl_type", "value": 4}, timeout=5)

        # ── 幕2. 乱序报警 ──
        base, _ = _counters()
        base_warn = base.get("数量门警告数", 0)
        body = _run_scenario(page, pid, _scn_order(),
                             lambda b: _guard_last(b).get("kind") == "order")
        last = _guard_last(body)
        step("幕2 区B 开始后回头加区A → 乱序违规 (order)",
             last.get("kind") == "order" and last.get("label") == "区A",
             json.dumps(last, ensure_ascii=False))
        cnt, _ = _counters()
        step("幕2 警告事件计数 +1", cnt.get("数量门警告数", 0) >= base_warn + 1,
             f"警告 {base_warn}→{cnt.get('数量门警告数', 0)}")
        step("幕2 Monitor 乱序红幅可见", "乱序" in page.inner_text("body"))
        page.screenshot(path=str(EVIDENCE / "03_order_banner.png"), full_page=True)
        _stop()

        # ── 幕3. 补救闭环: 少装红幅 → 补齐绿幅 + 事件 → 结算 OK ──
        base, _ = _counters()
        base_ok, base_fix = base.get("合格总数", 0), base.get("补齐数", 0)
        body = _run_scenario(page, pid, _scn_remedy(),
                             lambda b: _guard_last(b).get("kind") == "transition")
        step("幕3 少装切步红幅 (transition 区A=1)",
             _guard_last(body).get("kind") == "transition",
             json.dumps(_guard_last(body), ensure_ascii=False))
        page.screenshot(path=str(EVIDENCE / "04_under_red.png"), full_page=True)
        body = _wait(lambda b: _guard_last(b).get("kind") == "resolved", timeout=30)
        last = _guard_last(body)
        step("幕3 补第 2 件后自动消警 (resolved)", last.get("kind") == "resolved"
             and last.get("actual") == 2, json.dumps(last, ensure_ascii=False))
        step("幕3 Monitor 绿幅「已补齐」可见", "已补齐" in page.inner_text("body"))
        page.screenshot(path=str(EVIDENCE / "05_resolved_green.png"), full_page=True)
        body = _wait(lambda b: (b.get("counters") or {}).get("合格总数", 0) >= base_ok + 1,
                     timeout=30)
        cnt = body.get("counters") or {}
        step("幕3 补齐后收尾正常结算 OK (+1) + 补齐事件 +1",
             cnt.get("合格总数", 0) >= base_ok + 1
             and cnt.get("补齐数", 0) >= base_fix + 1,
             f"合格 {base_ok}→{cnt.get('合格总数', 0)}, 补齐 {base_fix}→{cnt.get('补齐数', 0)}")
        step("幕3 结算后绿幅消失 (guard_last 清空)", not _guard_last(body),
             json.dumps(_guard_last(body), ensure_ascii=False))
        page.screenshot(path=str(EVIDENCE / "06_settle_ok.png"), full_page=True)
        _stop()

        # ── 幕4a. 结算挂起 → 补齐 → 自动 OK 落账 ──
        _patch_guard(pid, on_settle_mismatch="hold", hold_timeout_s=60)
        base, _ = _counters()
        base_ok, base_ng = base.get("合格总数", 0), base.get("不良总数", 0)
        body = _run_scenario(page, pid, _scn_hold(with_remedy=True),
                             lambda b: (_cv(b).get("settle_hold") or {}).get("active"))
        hold = _cv(body).get("settle_hold") or {}
        step("幕4a 查表未命中 → 挂起等补 (不结算)", hold.get("active") is True
             and "未匹配" in (hold.get("reason") or ""),
             json.dumps(hold, ensure_ascii=False))
        step("幕4a Monitor 琥珀横幅「挂起等补」可见", "挂起等补" in page.inner_text("body"))
        page.screenshot(path=str(EVIDENCE / "07_hold_amber.png"), full_page=True)
        body = _wait(lambda b: (b.get("counters") or {}).get("合格总数", 0) >= base_ok + 1,
                     timeout=40)
        cnt = body.get("counters") or {}
        step("幕4a 补齐后自动按 OK 落账 (+1, 无 NG)",
             cnt.get("合格总数", 0) >= base_ok + 1 and cnt.get("不良总数", 0) == base_ng,
             f"合格 {base_ok}→{cnt.get('合格总数', 0)}")
        step("幕4a 落账后挂起解除", not (_cv(body).get("settle_hold") or {}),
             json.dumps(_cv(body).get("settle_hold"), ensure_ascii=False))
        page.screenshot(path=str(EVIDENCE / "08_hold_ok.png"), full_page=True)
        _stop()

        # ── 幕4b. 结算挂起 → 超时 → NG 落账 ──
        _patch_guard(pid, hold_timeout_s=6)
        base, _ = _counters()
        base_ng = base.get("不良总数", 0)
        body = _run_scenario(page, pid, _scn_hold(with_remedy=False),
                             lambda b: (_cv(b).get("settle_hold") or {}).get("active"))
        step("幕4b 挂起进入 (超时 6s 档)",
             (_cv(body).get("settle_hold") or {}).get("active") is True)
        body = _wait(lambda b: (b.get("counters") or {}).get("不良总数", 0) >= base_ng + 1,
                     timeout=40)
        cnt = body.get("counters") or {}
        step("幕4b 超时按原因 NG 落账 (+1)", cnt.get("不良总数", 0) >= base_ng + 1,
             f"不良 {base_ng}→{cnt.get('不良总数', 0)}")
        step("幕4b 落账后挂起解除", not (_cv(body).get("settle_hold") or {}))
        page.screenshot(path=str(EVIDENCE / "09_hold_timeout_ng.png"), full_page=True)
        _stop()

        browser.close()

    cleanup()
    (EVIDENCE / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'='*50}\nUAT {'PASS' if verdict['pass'] else 'FAIL'} — 证据: {EVIDENCE}")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
