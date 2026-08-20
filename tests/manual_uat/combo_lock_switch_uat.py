# -*- coding: utf-8 -*-
"""v3.48.x「监控画锁定框」显示开关 — 可见浏览器 UAT (headless=False).

现场叙事:
  工艺工程师打开 项目管理 → 逻辑设置 → 判型表卡 (positional 已选) →
  看到「监控画锁定框」默认开 → 手点关掉 → 保存 → 落库 false →
  Monitor 实跑: 判型计数条照常涨、画面不画青色锁框 →
  回配置页手点开 → 保存 → Monitor 实跑: 青色锁框回来了。

跑法 (先起 backend 8001 [RUNTIME_MODE=test] + frontend 6001):
  python tests/manual_uat/combo_lock_switch_uat.py [frontend_port]
证据落地: tests/manual_uat/evidence/combo_lock_switch_<date>/
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
NAME = "__uat_锁框开关"
CH = 0

EVIDENCE = Path(__file__).parent / "evidence" / f"combo_lock_switch_{datetime.now():%Y-%m-%d}"
EVIDENCE.mkdir(parents=True, exist_ok=True)

verdict = {"steps": [], "pass": True}

# 与 Monitor COMBO_LOCK_COLOR (#22d3ee) 对齐的 canvas 逐像素扫描
HAS_CYAN_JS = """() => {
  const cv = [...document.querySelectorAll('canvas')]
    .find(c => c.width > 100 && c.height > 100);
  if (!cv) return 'no-canvas';
  const img = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
  for (let i = 0; i < img.length; i += 4) {
    if (img[i + 3] > 200 && Math.abs(img[i] - 34) < 30
        && Math.abs(img[i + 1] - 211) < 30 && Math.abs(img[i + 2] - 238) < 30)
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


def _scenario():
    """区A 出现足够锁定后离场, 不结算 —— 锁框独立在画面上, 专测画不画。
    (目标在场时绿色检测框与锁框坐标重合, 逐像素扫青色会被绿框盖掉)"""
    return {"name": "lock_switch_uat", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 369, "detections": [
            {"label": "区A", "confidence": 0.92, "bbox": [0.2, 0.2, 0.2, 0.2]}]},
        {"from": 370, "to": 7200, "detections": []},
    ]}


def _open_logic_tab(page):
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
    return card


def _run_monitor_and_probe(page, pid, detail):
    """激活项目 + synthetic 起跑 → Monitor → 等锁定生效 → 返回 (计数条可见, 画没画青色)。"""
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
    # 等后端锁定至少 1 个位置
    deadline = time.time() + 40
    locked = []
    while time.time() < deadline:
        body = requests.get(
            f"{API}/api/v1/source/detection/results?channel={CH}", timeout=5).json()
        locked = [r for r in (body.get("combo_verdict") or {}).get("positional_rois") or []
                  if r.get("state") == "locked"]
        # 等到锁定发生且目标已离场 (锁框独立在画面上, 不被绿色检测框覆盖)
        if locked and not (body.get("detections") or []):
            break
        time.sleep(0.3)
    time.sleep(2.0)  # 留几个轮询拍让前端画/不画稳定
    bar_visible = "判型计数" in page.inner_text("body")
    cyan = page.evaluate(HAS_CYAN_JS)
    requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=5)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel={CH}", timeout=5)
    return bool(locked), bar_visible, cyan


def main() -> int:
    cleanup()
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": NAME, "task_type": "detection", "logic_mode": "detection",
        "steps_config": [
            {"id": 1, "label": "区A", "name": "区域A", "enabled": True, "min_frames": 1, "threshold": 30},
            {"id": 2, "label": "收尾", "name": "收尾", "enabled": True, "min_frames": 1, "threshold": 30},
        ],
        "pipeline_config": {
            "settlement_mode": "last_step", "detection_steps": [1, 2],
            "combo_table": {
                "enabled": True, "labels": ["区A"], "count_mode": "positional",
                "tracking": {"iou": 0.4, "ema_alpha": 0.6, "min_consecutive": 3,
                             "pending_ttl": 10, "perish_ticks": 0, "idle_reset_ticks": 0},
                "rows": [{"counts": [1], "verdict": "OK", "tag": "机型X"}],
            },
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [{"counter_name": "合格总数", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [{"counter_name": "不良总数", "delta": 1}]},
        ],
        "counters_config": [{"name": "合格总数", "value": 0}, {"name": "不良总数", "value": 0}],
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=10)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})

        # ── 1. 配置页: 开关露出且默认开 ──
        card = _open_logic_tab(page)
        sw = card.locator(".combo-lock-overlay-switch").first
        step("「监控画锁定框」开关露出", sw.count() == 1)
        step("默认状态 = 开", "is-checked" in (sw.get_attribute("class") or ""))
        page.screenshot(path=str(EVIDENCE / "01_switch_default_on.png"), full_page=True)

        # ── 2. 手点关 → 保存 → 落库 false ──
        sw.click()
        time.sleep(0.3)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        cmb = ((requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
                .get("pipeline_config") or {}).get("combo_table") or {})
        step("T5 落库 show_lock_overlay=false", cmb.get("show_lock_overlay") is False,
             json.dumps({k: cmb.get(k) for k in ("show_lock_overlay", "count_mode")},
                        ensure_ascii=False))
        page.screenshot(path=str(EVIDENCE / "02_switch_off_saved.png"), full_page=True)

        # ── 3. Monitor 实跑: 计数照常, 不画锁框 ──
        detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
        locked, bar, cyan = _run_monitor_and_probe(page, pid, detail)
        step("关: 后端锁定照常发生", locked)
        step("关: 判型计数条照常显示", bar)
        step("关: 画面不画青色锁框", cyan is not True, f"cyan={cyan}")
        page.screenshot(path=str(EVIDENCE / "03_monitor_no_lockbox.png"), full_page=True)

        # ── 4. 回配置页手点开 → 保存 → Monitor 锁框回来 ──
        card = _open_logic_tab(page)
        card.locator(".combo-lock-overlay-switch").first.click()
        time.sleep(0.3)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        cmb = ((requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
                .get("pipeline_config") or {}).get("combo_table") or {})
        step("重开落库 show_lock_overlay=true", cmb.get("show_lock_overlay") is True)
        detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
        locked, bar, cyan = _run_monitor_and_probe(page, pid, detail)
        step("开: 青色锁框回来", locked and cyan is True, f"locked={locked} cyan={cyan}")
        page.screenshot(path=str(EVIDENCE / "04_monitor_lockbox_back.png"), full_page=True)

        browser.close()

    cleanup()
    (EVIDENCE / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'='*50}\nUAT {'PASS' if verdict['pass'] else 'FAIL'} — 证据: {EVIDENCE}")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
