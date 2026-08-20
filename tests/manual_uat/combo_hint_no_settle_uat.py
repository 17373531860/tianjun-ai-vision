# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 数量门提示档「只报警不结算」修复验证 (2026-08-12 现场反馈).

现场症状: 违规处置=当场提示(报警不结算) + 提示事件=不良(NG), 切步违规瞬间
直接 NG 结算落账 (检测次数+1 / 饼图变红 / NG TOP3 落账)。
两层修复:
  ① 通道修正: hint/挂起提示/补齐消警从结算通道 _trigger_event (无条件
     end_cycle) 改走响应面通道 fire_external_event_response;
  ② 产品决策 (2026-08-12): 系统 合格(1)/不合格(2) 是结算事件, 提示/消警
     禁止借用 — 前端下拉不给选, 后端 parse 把旧配置洗成 None。

三幕 (按新规矩配专用事件: 提示=数量门警告(3), 已补齐=已补齐提示(4);
      系统 OK/NG 事件被 parse 拒收的口径由单测 test_combo_table.py 覆盖):
  幕1 切步少装 → 红幅 + 专用警告事件响应面计数, OK/NG 统计不动, 不结算;
  幕2 回头补件 → 绿幅已补齐 + 专用补齐事件计数, 依然不结算;
  幕3 收尾 → 查表命中正常 OK 结算, 全程唯一一次结算, NG 统计始终为 0。

跑法 (先起 backend 8002 [RUNTIME_MODE=test] + frontend 6002):
  E2E_API_URL=http://localhost:8002 E2E_BASE_URL=http://localhost:6002 \
    python tests/manual_uat/combo_hint_no_settle_uat.py
证据: tests/manual_uat/evidence/combo_hint_no_settle_<date>/
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

API = os.environ.get("E2E_API_URL", "http://localhost:8002")
BASE = os.environ.get("E2E_BASE_URL", "http://localhost:6002")
NAME = "__uat_提示不结算"
CH = 0

EVIDENCE = Path(__file__).parent / "evidence" / f"combo_hint_no_settle_{datetime.now():%Y-%m-%d}"
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


def _det(label, x, y):
    return {"label": label, "confidence": 0.92, "bbox": [x, y, 0.15, 0.15]}


def _scenario():
    """区A×1 → 区B (切步少装红幅) → 补区A 第 2 件 (绿幅) → 收尾 OK 结算。"""
    return {"name": "hint_no_settle", "fps": 60, "timeline": [
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


def _body():
    return requests.get(f"{API}/api/v1/source/detection/results?channel={CH}",
                        timeout=5).json()


def _guard_last(body):
    return ((body.get("combo_verdict") or {}).get("step_guard") or {}).get("last") or {}


def _wait(pred, timeout=60):
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        body = _body()
        try:
            if pred(body):
                break
        except Exception:
            pass
        time.sleep(0.4)
    time.sleep(1.0)
    return body


def main() -> int:
    cleanup()

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
                "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "机型X"}],
                # 新规矩: 提示/已补齐配专用事件 (系统 OK/NG 被后端 parse 拒收)
                "step_guard": {
                    "enabled": True, "check_under": True, "check_over": True,
                    "action": "hint", "event_id": 3, "resolved_event_id": 4,
                    "expected_source": "table", "plc_unavailable": "table",
                    "on_settle_mismatch": "ng", "hold_timeout_s": 120,
                },
            },
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [{"counter_name": "合格总数", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [{"counter_name": "不良总数", "delta": 1}],
             "show_notification": True},
            {"id": 3, "name": "数量门警告", "actions": [{"counter_name": "数量门警告数", "delta": 1}],
             "show_notification": True},
            {"id": 4, "name": "已补齐提示", "actions": [{"counter_name": "补齐数", "delta": 1}]},
        ],
        "counters_config": [{"name": "合格总数", "value": 0}, {"name": "不良总数", "value": 0},
                            {"name": "数量门警告数", "value": 0}, {"name": "补齐数", "value": 0}],
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=10)

    detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
    requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario_json": _scenario(), "channel": CH, "with_project": False,
    }, timeout=10).raise_for_status()
    sp = {k: detail.get(k) for k in (
        "steps_config", "pipeline_config", "events_config",
        "counters_config", "data_config", "logic_mode")}
    sp["data_config"] = sp.get("data_config") or {}
    requests.post(f"{API}/api/v1/source/detection/set-project?channel={CH}",
                  json={**sp, "project_id": pid, "name": NAME},
                  timeout=10).raise_for_status()
    # 清掉上一轮残留计数 (计数器按名字跨项目持久化), 保证断言从 0 起算
    requests.post(f"{API}/api/v1/source/detection/reset-stats?channel={CH}", timeout=10)
    requests.post(f"{API}/api/v1/source/detection/start?channel={CH}",
                  json={"conf": 0.25, "iou": 0.45}, timeout=10).raise_for_status()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})
        page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")

        # ── 幕1: 切步少装 → 红幅提示, 但周期必须还活着 (不结算) ──
        body = _wait(lambda b: _guard_last(b).get("kind") == "transition")
        gl = _guard_last(body)
        step("幕1 切步少装违规产生 (hint 档)",
             gl.get("kind") == "transition" and gl.get("action") == "hint",
             json.dumps(gl, ensure_ascii=False)[:120])
        counters = body.get("counters") or {}
        cyc = body.get("current_cycle_steps") or []
        step("幕1 专用警告事件响应面计数 (数量门警告数=1)",
             counters.get("数量门警告数") == 1, f"counters={counters}")
        step("幕1 ⚠️ 周期未被结算 (current_cycle_steps 仍非空)",
             len(cyc) >= 1, f"cycle_steps={cyc}")
        step("幕1 OK/NG 统计全干净 (合格=0 不良=0)",
             counters.get("合格总数") == 0 and counters.get("不良总数") == 0,
             f"{counters}")
        time.sleep(0.5)
        page.screenshot(path=str(EVIDENCE / "01_hint_red_banner_not_settled.png"))

        # ── 幕2: 补第 2 件 → 绿幅已补齐 + 专用补齐事件, 依然不结算 ──
        body = _wait(lambda b: _guard_last(b).get("kind") == "resolved")
        gl = _guard_last(body)
        counters = body.get("counters") or {}
        cyc = body.get("current_cycle_steps") or []
        step("幕2 补齐消警 (resolved 绿幅)", gl.get("action") == "resolved",
             json.dumps(gl, ensure_ascii=False)[:120])
        step("幕2 专用补齐事件计数 (补齐数=1) 且周期仍未结算, OK/NG 仍为 0",
             counters.get("补齐数") == 1 and len(cyc) >= 2
             and counters.get("合格总数") == 0 and counters.get("不良总数") == 0,
             f"counters={counters} cycle={cyc}")
        time.sleep(0.5)
        page.screenshot(path=str(EVIDENCE / "02_resolved_green_still_open.png"))

        # ── 幕3: 收尾 → 全程唯一一次真结算 (OK) ──
        body = _wait(lambda b: (b.get("counters") or {}).get("合格总数") == 1)
        counters = body.get("counters") or {}
        cyc = body.get("current_cycle_steps") or []
        step("幕3 收尾正常 OK 结算 (合格总数 0→1, 周期清空)",
             counters.get("合格总数") == 1 and len(cyc) == 0,
             f"counters={counters} cycle={cyc}")
        step("幕3 全程无 NG 结算 (不良总数=0)",
             counters.get("不良总数") == 0, f"{counters}")
        time.sleep(0.5)
        page.screenshot(path=str(EVIDENCE / "03_settled_ok_once.png"))
        browser.close()

    requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=5)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel={CH}", timeout=5)
    cleanup()

    (EVIDENCE / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'ALL PASS' if verdict['pass'] else 'FAILED'} — 证据: {EVIDENCE}")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
