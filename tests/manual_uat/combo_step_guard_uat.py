# -*- coding: utf-8 -*-
"""v3.49「切步数量门」— 可见浏览器 UAT (headless=False).

现场叙事 (缸体判型, 工程师原话"没达到五或者超过五了就报警, 根据步骤来算"):
  工艺工程师打开 项目管理 → 逻辑设置 → 判型表卡 → 打开「切步数量门」→
  选提示事件 → 保存落库。Monitor 实跑三幕:
    幕1 hint·少装切步: 区A 只装 1 个 (机型行要求 2/3) 就去装区B →
        切步那一刻红色横幅 + 警告计数 +1, 周期不被提前结算;
    幕2 hint·超装即时: 区A 锁到第 4 个 (候选上限 3) → 不等切步当场报;
    幕3 instant_ng: 同幕1 场景 → 当场结算 NG (不良 +1, 不等收尾步)。

跑法 (先起 backend 8001 [RUNTIME_MODE=test] + frontend 6001):
  python tests/manual_uat/combo_step_guard_uat.py [frontend_port]
证据落地: tests/manual_uat/evidence/combo_step_guard_<date>/
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
NAME = "__uat_数量门"
CH = 0

EVIDENCE = Path(__file__).parent / "evidence" / f"combo_step_guard_{datetime.now():%Y-%m-%d}"
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


def _scenario_under():
    """区A 只在 1 个位置锁定 → 区B 出现 (切步) → 长空尾 (不给收尾, 周期保持打开)。"""
    return {"name": "guard_under", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 129, "detections": [_det("区A", 0.1, 0.1)]},
        {"from": 130, "to": 149, "detections": []},
        {"from": 150, "to": 269, "detections": [_det("区B", 0.55, 0.55)]},
        {"from": 270, "to": 7200, "detections": []},
    ]}


def _scenario_over():
    """区A 依次在 4 个不同位置锁定 (候选行上限 3) → 第 4 个当场超装, 不出区B。"""
    tl = [{"from": 0, "to": 9, "detections": []}]
    pos = [(0.1, 0.1), (0.35, 0.1), (0.6, 0.1), (0.1, 0.4)]
    f = 10
    for x, y in pos:
        tl.append({"from": f, "to": f + 89, "detections": [_det("区A", x, y)]})
        tl.append({"from": f + 90, "to": f + 109, "detections": []})
        f += 110
    tl.append({"from": f, "to": 7200, "detections": []})
    return {"name": "guard_over", "fps": 60, "timeline": tl}


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


def _counters():
    body = requests.get(f"{API}/api/v1/source/detection/results?channel={CH}",
                        timeout=5).json()
    return body.get("counters") or {}, body


def _run_scenario(page, pid, scenario, wait_pred, timeout=45):
    """激活 + synthetic 起跑 → Monitor → 等断言条件 → 返回最后一次 results body。"""
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
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        _, body = _counters()
        try:
            if wait_pred(body):
                break
        except Exception:
            pass
        time.sleep(0.4)
    time.sleep(2.0)  # 留轮询拍让前端横幅渲染
    return body


def _stop():
    requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=5)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel={CH}", timeout=5)
    time.sleep(0.5)


def _guard_last(body):
    return ((body.get("combo_verdict") or {}).get("step_guard") or {}).get("last") or {}


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
                "rows": [
                    {"counts": [2, 1], "verdict": "OK", "tag": "机型X", "plc_code": "4"},
                    {"counts": [3, 1], "verdict": "OK", "tag": "机型Y", "plc_code": "6"},
                ],
            },
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [{"counter_name": "合格总数", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [{"counter_name": "不良总数", "delta": 1}]},
            {"id": 3, "name": "数量门警告", "actions": [{"counter_name": "数量门警告数", "delta": 1}]},
        ],
        "counters_config": [{"name": "合格总数", "value": 0}, {"name": "不良总数", "value": 0},
                            {"name": "数量门警告数", "value": 0}],
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=10)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})

        # ── 幕0. 配置页: 手点开数量门 + 选提示事件 → 保存落库 ──
        card = _open_logic_tab(page)
        sw = card.locator(".combo-guard-switch").first
        step("「切步数量门」开关露出且默认关", sw.count() == 1
             and "is-checked" not in (sw.get_attribute("class") or ""))
        sw.click()
        time.sleep(0.5)
        txt = card.inner_text()
        step("配置区展开 (少装/超装/收窄/期望依据/处置)", all(
            k in txt for k in ("少装/数量不符", "超装", "按已装数量收窄机型",
                               "期望数量依据", "违规处置")))
        card.locator(".combo-guard-event").click()
        page.locator(".el-select-dropdown:visible .el-select-dropdown__item"
                     ":has-text('数量门警告')").first.click()
        time.sleep(0.3)
        page.screenshot(path=str(EVIDENCE / "00_guard_config_on.png"), full_page=True)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        cmb = ((requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
                .get("pipeline_config") or {}).get("combo_table") or {})
        sg = cmb.get("step_guard") or {}
        step("T5 落库 step_guard (hint + 事件3)", sg.get("enabled") is True
             and sg.get("action") == "hint" and sg.get("event_id") == 3,
             json.dumps(sg, ensure_ascii=False))

        # ── 幕1. hint·少装切步: 区A×1 就出区B → 横幅 + 警告 +1, 不提前结算 ──
        base, _ = _counters()
        base_warn, base_ng = base.get("数量门警告数", 0), base.get("不良总数", 0)
        body = _run_scenario(page, pid, _scenario_under(),
                             lambda b: _guard_last(b).get("kind") == "transition")
        last = _guard_last(body)
        step("幕1 切步那一刻报少装 (transition 区A=1)", last.get("kind") == "transition"
             and last.get("label") == "区A" and last.get("actual") == 1,
             json.dumps(last, ensure_ascii=False))
        cnt, body = _counters()
        step("幕1 警告事件计数 +1", cnt.get("数量门警告数", 0) >= base_warn + 1,
             f"警告 {base_warn}→{cnt.get('数量门警告数', 0)}")
        step("幕1 hint 不提前结算 (不良不变)", cnt.get("不良总数", 0) == base_ng,
             f"不良 {base_ng}→{cnt.get('不良总数', 0)}")
        banner = "切步数量不符" in page.inner_text("body")
        step("幕1 Monitor 红色横幅可见", banner)
        page.screenshot(path=str(EVIDENCE / "01_hint_under_banner.png"), full_page=True)
        _stop()

        # ── 幕2. hint·超装即时: 区A 第 4 个位置当场报, 不等切步 ──
        body = _run_scenario(page, pid, _scenario_over(),
                             lambda b: _guard_last(b).get("kind") == "over")
        last = _guard_last(body)
        step("幕2 第4个位置当场报超装 (over, 上限3)", last.get("kind") == "over"
             and last.get("actual") == 4 and last.get("expected") == [2, 3],
             json.dumps(last, ensure_ascii=False))
        page.screenshot(path=str(EVIDENCE / "02_hint_over_banner.png"), full_page=True)
        _stop()

        # ── 幕3. instant_ng: 同幕1 场景 → 当场结算 NG ──
        pc = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()["pipeline_config"]
        pc["combo_table"]["step_guard"]["action"] = "instant_ng"
        requests.put(f"{API}/api/v1/projects/{pid}",
                     json={"pipeline_config": pc}, timeout=5).raise_for_status()
        base, _ = _counters()
        base_ng = base.get("不良总数", 0)
        body = _run_scenario(page, pid, _scenario_under(),
                             lambda b: (b.get("counters") or {}).get("不良总数", 0) >= base_ng + 1)
        cnt = body.get("counters") or {}
        step("幕3 instant_ng 当场结算 (不良 +1, 未出收尾步)",
             cnt.get("不良总数", 0) >= base_ng + 1,
             f"不良 {base_ng}→{cnt.get('不良总数', 0)}")
        last = _guard_last(body)
        step("幕3 违规透出 action=instant_ng", last.get("action") == "instant_ng",
             json.dumps(last, ensure_ascii=False))
        page.screenshot(path=str(EVIDENCE / "03_instant_ng_settled.png"), full_page=True)
        _stop()

        browser.close()

    cleanup()
    (EVIDENCE / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'='*50}\nUAT {'PASS' if verdict['pass'] else 'FAIL'} — 证据: {EVIDENCE}")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
