# -*- coding: utf-8 -*-
"""v3.48.x 判型表 positional 锁定框可视化 — 可见浏览器 UAT (headless=False).

现场叙事 (对应工程师反馈"没有锁那个框/还没有计数"):
  操作员在 Monitor 看检测: 区A 位置1 装上 → 检测框出现 → 位置锁定 (青色常驻
  框 + "区A #1"), 目标离场后锁框仍在画面上; 位置2 再装 → 锁框 #2; 信息条
  实时显示「判型计数: 区A n · 区B n」; 收尾步骤触发结算 → 命中机型行判 OK,
  机型上屏, 锁框清空进入下一件。

跑法 (先起 backend 8001 [RUNTIME_MODE=test] + frontend 6001):
  python tests/manual_uat/combo_positional_overlay_uat.py [frontend_port]
证据落地: tests/manual_uat/evidence/combo_overlay_<date>/
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

API = "http://localhost:8001"
BASE = f"http://localhost:{sys.argv[1] if len(sys.argv) > 1 else '6001'}"
NAME = "__uat_判型锁框"
CH = 0

EVIDENCE = Path(__file__).parent / "evidence" / f"combo_overlay_{datetime.now():%Y-%m-%d}"
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
    """区A 位置1 → (空档) → 区A 位置2 → 区B → 收尾结算。60fps。
    空档给足 300 帧: 浏览器拉 MJPEG 时 synthetic 帧泵提速 ~2x, 防止两次
    出现被桥接; 空档期正是"锁框常驻"截图窗口。
    """
    return {"name": "combo_overlay_uat", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 129, "detections": [_det("区A", 0.10, 0.15)]},
        {"from": 130, "to": 429, "detections": []},
        {"from": 430, "to": 549, "detections": [_det("区A", 0.55, 0.20)]},
        {"from": 550, "to": 849, "detections": []},
        {"from": 850, "to": 969, "detections": [_det("区B", 0.35, 0.60)]},
        {"from": 970, "to": 1269, "detections": []},
        {"from": 1270, "to": 1389, "detections": [_det("收尾", 0.75, 0.70)]},
        {"from": 1390, "to": 3200, "detections": []},
    ]}


def _results():
    return requests.get(
        f"{API}/api/v1/source/detection/results?channel={CH}", timeout=5).json()


def _locked(body):
    rois = (body.get("combo_verdict") or {}).get("positional_rois") or []
    return [r for r in rois if r.get("state") == "locked"]


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
                "enabled": True, "labels": ["区A", "区B"],
                "count_mode": "positional",
                "tracking": {"iou": 0.4, "ema_alpha": 0.6, "min_consecutive": 3,
                             "pending_ttl": 10, "perish_ticks": 0, "idle_reset_ticks": 0},
                "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "机型X"}],
            },
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [
                {"counter_name": "合格总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [
                {"counter_name": "不良总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1}]},
        ],
        "counters_config": [{"name": "合格总数", "value": 0},
                            {"name": "不良总数", "value": 0},
                            {"name": "总产量", "value": 0}],
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]

    requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=10)
    detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})

        base_ok = 0
        body = _results()
        base_ok = (body.get("counters") or {}).get("合格总数", 0)

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
        requests.post(f"{API}/api/v1/source/detection/start?channel={CH}",
                      json={"conf": 0.25, "iou": 0.45}, timeout=10).raise_for_status()

        page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")
        time.sleep(2)

        # ── 1. 等第 1 个位置锁定, 且当前帧无检测 (锁框常驻的证据窗口) ──
        deadline = time.time() + 60
        got1 = False
        while time.time() < deadline:
            body = _results()
            lk = _locked(body)
            if len(lk) >= 1 and not (body.get("detections") or []):
                got1 = True
                break
            time.sleep(0.3)
        step("位置1 锁定且目标已离场 (锁框应常驻)", got1,
             f"locked={_locked(body)}")
        time.sleep(1.0)
        page.screenshot(path=str(EVIDENCE / "01_locked_persists.png"), full_page=True)
        bar = page.inner_text("body")
        step("信息条显示「判型计数」区A 1", "判型计数" in bar and "区A 1" in bar)

        # ── 2. 等第 2 个位置锁定 ──
        deadline = time.time() + 60
        got2 = False
        while time.time() < deadline:
            body = _results()
            if len(_locked(body)) >= 2:
                got2 = True
                break
            time.sleep(0.3)
        seqs = sorted(r.get("seq") for r in _locked(body))
        step("位置2 锁定, 编号 #1/#2", got2 and seqs == [1, 2], f"seqs={seqs}")
        time.sleep(1.0)
        page.screenshot(path=str(EVIDENCE / "02_two_locked.png"), full_page=True)

        # ── 3. 等收尾结算: OK+机型X, 锁框池清空 ──
        deadline = time.time() + 90
        settled = False
        while time.time() < deadline:
            body = _results()
            if (body.get("counters") or {}).get("合格总数", 0) >= base_ok + 1:
                settled = True
                break
            time.sleep(0.5)
        cv = body.get("combo_verdict") or {}
        step("结算 OK + 机型X", settled and cv.get("last_tag") == "机型X",
             f"counters={body.get('counters')} last_tag={cv.get('last_tag')}")
        step("结算后锁框池清空", len(_locked(body)) == 0,
             f"rois={cv.get('positional_rois')}")
        time.sleep(1.0)
        page.screenshot(path=str(EVIDENCE / "03_settled_cleared.png"), full_page=True)

        browser.close()

    cleanup()
    (EVIDENCE / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'='*50}\nUAT {'PASS' if verdict['pass'] else 'FAIL'} — 证据: {EVIDENCE}")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
