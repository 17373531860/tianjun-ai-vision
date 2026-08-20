# -*- coding: utf-8 -*-
"""虚拟按钮: 触发区域上屏 + 步骤类模式手动结算 — 可见浏览器 UAT (headless=False).

背景 (2026-08-13 现场反馈, 缸体判型):
  1. 工程师标定完虚拟按钮的触发区域后, 监控画面上看不见按钮在哪;
  2. (排查中同时发现) manual_settle 动作只支持 per_item 模式 —— 判型现场
     (detection + combo_table) 按了虚拟按钮根本不会结算, 日志报
     "未启用 per_item 模式"。

本 UAT 验证两条修复的真实链路 (真后端 + 真触发中心实例 + synthetic 剧本):
  1. Monitor 画面出现琥珀虚线触发区域框 (#fbbf24, 名称+边框);
  2. 周期打开、自动结算不来 (first_step 结算模式, 首步不复现) → 试触发
     (POST /channels/{id}/test 直接执行动作) → manual_settle 请求 → 推理
     线程消费 → 按判定表真实结算 OK (合格总数 0→1);
  3. 禁用触发源 → 刷新页面 → 区域框消失 (显示跟随启用状态)。

跑法 (先起 backend 8002 + frontend 6002):
  python tests/manual_uat/trigger_zone_settle_uat.py
证据落地: tests/manual_uat/evidence/trigger_zone_settle_<date>/
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

API = "http://localhost:8002"
BASE = f"http://localhost:{sys.argv[1] if len(sys.argv) > 1 else '6002'}"
NAME = "__uat_虚拟按钮结算"
TRIGGER_NAME = "__uat_虚拟结算按钮"
CH = 0

EVIDENCE = Path(__file__).parent / "evidence" / f"trigger_zone_settle_{datetime.now():%Y-%m-%d}"
EVIDENCE.mkdir(parents=True, exist_ok=True)

verdict = {"steps": [], "pass": True}

# 琥珀 #fbbf24 — 与 Monitor/index.vue TRIGGER_ZONE_COLOR 对齐
_HAS_AMBER_JS = """() => {
  const cv = [...document.querySelectorAll('canvas')]
    .find(c => c.width > 100 && c.height > 100);
  if (!cv) return 'no-canvas';
  const ctx = cv.getContext('2d');
  const img = ctx.getImageData(0, 0, cv.width, cv.height).data;
  for (let i = 0; i < img.length; i += 4) {
    if (img[i + 3] > 200 && Math.abs(img[i] - 251) < 25
        && Math.abs(img[i + 1] - 191) < 25 && Math.abs(img[i + 2] - 36) < 40)
      return true;
  }
  return false;
}"""


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
    trs = requests.get(f"{API}/api/v1/triggers/channels", timeout=5).json()
    for t in trs.get("triggers") or []:
        if (t.get("name") or "").startswith("__uat_"):
            requests.delete(f"{API}/api/v1/triggers/channels/{t['id']}", timeout=5)


def _det(label, x, y):
    return {"label": label, "confidence": 0.92, "bbox": [x, y, 0.15, 0.15]}


def _scn():
    """区A 两位置 + 区B + 收尾 各出现一段后画面清空。

    first_step 结算模式下首步 (区A) 不复现 → 周期一直开着等结算,
    唯一的结算出口就是虚拟按钮 (manual_settle)。60fps。
    """
    return {"name": "zone_settle", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 129, "detections": [_det("区A", 0.1, 0.1)]},
        {"from": 130, "to": 149, "detections": []},
        {"from": 150, "to": 269, "detections": [_det("区A", 0.4, 0.1)]},
        {"from": 270, "to": 289, "detections": []},
        {"from": 290, "to": 409, "detections": [_det("区B", 0.7, 0.5)]},
        {"from": 410, "to": 429, "detections": []},
        {"from": 430, "to": 549, "detections": [_det("收尾", 0.4, 0.7)]},
        {"from": 550, "to": 28800, "detections": []},
    ]}


def _results():
    return requests.get(f"{API}/api/v1/source/detection/results?channel={CH}",
                        timeout=5).json()


def _wait(pred, timeout=60):
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        body = _results()
        try:
            if pred(body):
                break
        except Exception:
            pass
        time.sleep(0.4)
    time.sleep(1.2)  # 留轮询拍给前端渲染
    return body


def _wait_amber(page, timeout_s=15.0):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = page.evaluate(_HAS_AMBER_JS)
        if last is True:
            return True, last
        time.sleep(0.4)
    return False, last


def main() -> int:
    cleanup()

    # ---- 项目: 判定表 + first_step 结算 (首步不复现 → 只能手动结算) ----
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": NAME, "task_type": "detection", "logic_mode": "detection",
        "steps_config": [
            {"id": 1, "label": "区A", "name": "区域A", "enabled": True, "min_frames": 1, "threshold": 30},
            {"id": 2, "label": "区B", "name": "区域B", "enabled": True, "min_frames": 1, "threshold": 30},
            {"id": 3, "label": "收尾", "name": "收尾", "enabled": True, "min_frames": 1, "threshold": 30},
        ],
        "pipeline_config": {
            "settlement_mode": "first_step", "detection_steps": [1, 2, 3],
            "combo_table": {
                "enabled": True, "labels": ["区A", "区B"], "count_mode": "positional",
                "tracking": {"iou": 0.4, "ema_alpha": 0.6, "min_consecutive": 3,
                             "pending_ttl": 10, "perish_ticks": 0, "idle_reset_ticks": 0},
                "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "机型X"}],
            },
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [
                {"counter_name": "合格总数", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [
                {"counter_name": "不良总数", "delta": 1}]},
        ],
        "counters_config": [{"name": "合格总数", "value": 0},
                            {"name": "不良总数", "value": 0}],
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=10)

    # ---- 触发中心: 虚拟结算按钮 (pixel_region + manual_settle) ----
    r = requests.post(f"{API}/api/v1/triggers/channels", json={
        "name": TRIGGER_NAME, "type": "pixel_region", "enabled": True,
        "params": {"channel": CH, "region": [900, 90, 1150, 280],
                   "mode": "ref_diff", "threshold": 40, "sample_ms": 150},
        "rules": [{"name": "遮挡结算", "when": [{"trigger": "rising"}],
                   "actions": [{"do": "manual_settle"}]}],
        "options": {"default_channel": CH},
    }, timeout=10)
    r.raise_for_status()
    tid = r.json()["id"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})

        # ---- 启动剧本 + 检测 ----
        detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
        requests.post(f"{API}/api/v1/test/synthetic/start", json={
            "scenario_json": _scn(), "channel": CH, "with_project": False,
        }, timeout=10).raise_for_status()
        sp = {k: detail.get(k) for k in (
            "steps_config", "pipeline_config", "events_config",
            "counters_config", "data_config", "logic_mode")}
        sp["data_config"] = sp.get("data_config") or {}
        requests.post(f"{API}/api/v1/source/detection/set-project?channel={CH}",
                      json={**sp, "project_id": pid, "name": NAME},
                      timeout=10).raise_for_status()
        requests.post(f"{API}/api/v1/source/detection/reset-stats?channel={CH}",
                      timeout=5)
        requests.post(f"{API}/api/v1/source/detection/start?channel={CH}",
                      json={"conf": 0.25, "iou": 0.45}, timeout=10).raise_for_status()
        page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")

        # ---- 1. 触发区域琥珀框上屏 ----
        ok, last = _wait_amber(page)
        step("监控画面画出虚拟按钮触发区域 (琥珀框)", ok, f"last={last}")
        page.screenshot(path=str(EVIDENCE / "01_zone_overlay.png"), full_page=False)

        # ---- 2. 剧本走完, 周期保持打开 (自动结算不来) ----
        body = _wait(lambda b: (
            ((b.get("combo_verdict") or {}).get("positional_counts") or {}).get("区A") == 2
            and ((b.get("combo_verdict") or {}).get("positional_counts") or {}).get("区B") == 1),
            timeout=60)
        pc = (body.get("combo_verdict") or {}).get("positional_counts") or {}
        step("计数就绪 (区A=2, 区B=1)", pc.get("区A") == 2 and pc.get("区B") == 1,
             json.dumps(pc, ensure_ascii=False))
        time.sleep(4)  # 越过收尾窗口 (剧本 60fps, 收尾在 ~7-9s), 确认没自动结算
        counters = _results().get("counters") or {}
        step("按钮未按前不结算 (合格总数=0)",
             int(counters.get("合格总数") or 0) == 0,
             json.dumps(counters, ensure_ascii=False))

        # ---- 3. 试触发按钮 → manual_settle → 推理线程消费 → 按表结算 OK ----
        requests.post(f"{API}/api/v1/triggers/channels/{tid}/test",
                      json={"rule_index": 0}, timeout=10).raise_for_status()
        body = _wait(lambda b: int((b.get("counters") or {}).get("合格总数") or 0) == 1,
                     timeout=20)
        counters = body.get("counters") or {}
        step("按钮触发后按判定表结算 OK (合格总数=1, 不良=0)",
             int(counters.get("合格总数") or 0) == 1
             and int(counters.get("不良总数") or 0) == 0,
             json.dumps(counters, ensure_ascii=False))
        logs = requests.get(f"{API}/api/v1/triggers/channels/{tid}/logs",
                            timeout=5).json()
        log_txt = json.dumps(logs, ensure_ascii=False)
        step("触发源日志记录了结算请求", "结算" in log_txt,
             log_txt[-300:])
        page.screenshot(path=str(EVIDENCE / "02_after_settle.png"), full_page=False)

        # ---- 4. 禁用触发源 → 区域框消失 ----
        requests.post(f"{API}/api/v1/triggers/channels/{tid}/enable",
                      json={"enabled": False}, timeout=5).raise_for_status()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("canvas", timeout=10000)
        time.sleep(3)
        gone = page.evaluate(_HAS_AMBER_JS) is not True
        step("禁用触发源后区域框消失", gone, "")
        page.screenshot(path=str(EVIDENCE / "03_disabled_gone.png"), full_page=False)

        browser.close()

    cleanup()

    (EVIDENCE / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 50}\n总判定: {'PASS' if verdict['pass'] else 'FAIL'}"
          f"  (证据: {EVIDENCE})")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
