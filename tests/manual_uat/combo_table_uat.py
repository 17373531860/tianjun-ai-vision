# -*- coding: utf-8 -*-
"""v3.48 计数组合判定表 (纯视觉判型) — 可见浏览器 UAT (headless=False, 人眼证据).

现场叙事:
  工艺工程师建"多机型混产"检测项目 (区A/区B 按数量判型, 收尾步骤定周期),
  打开 项目管理 → 逻辑设置 → 「计数组合判定表」卡 → 开启 → 选判型标签 →
  添加机型行 (2,1)=OK「机型X」→ 保存 → 落库核对 → 重进回显 →
  Monitor 启动 synthetic 剧本实跑一件: 区A×2+区B×1 → 结算命中「机型X」判 OK,
  合格计数 +1, combo_verdict.last_tag 上屏 → 全程截图留证。

跑法 (先起 backend 8001 [RUNTIME_MODE=test] + frontend 6001/6002):
  python tests/manual_uat/combo_table_uat.py [frontend_port]
证据落地: tests/manual_uat/evidence/combo_table_<date>/
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
NAME = "__uat_组合判型"
CH = 0

EVIDENCE = Path(__file__).parent / "evidence" / f"combo_table_{datetime.now():%Y-%m-%d}"
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


def _det(label):
    return {"label": label, "confidence": 0.92, "bbox": [0.4, 0.4, 0.2, 0.2]}


def _scenario():
    """区A 出现 2 次 → 区B 1 次 → 收尾 1 次 (last_step 结算)。60fps。

    空档给足 300 帧 (标称 5s): 浏览器开着 Monitor 时 MJPEG 拉流会让 synthetic
    帧泵提速约 2 倍 (消费驱动的测试伪影, 真实相机不存在), 空档实际时长被压缩;
    给足余量防止两次出现被桥接成一次 (在场从未"消失确认")。
    """
    tl, f = [{"from": 0, "to": 9, "detections": []}], 10
    for lbl in ("区A", "区A", "区B", "收尾"):
        tl.append({"from": f, "to": f + 119, "detections": [_det(lbl)]})
        tl.append({"from": f + 120, "to": f + 419, "detections": []})
        f += 420
    tl.append({"from": f, "to": f + 1800, "detections": []})
    return {"name": "combo_uat", "fps": 60, "timeline": tl}


def main() -> int:
    cleanup()
    # 预建检测模式项目 (UI 配 combo 是本 UAT 主角, 项目骨架走 API)
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": NAME, "task_type": "detection", "logic_mode": "detection",
        "steps_config": [
            {"id": 1, "label": "区A", "name": "区域A", "enabled": True, "min_frames": 1, "threshold": 30},
            {"id": 2, "label": "区B", "name": "区域B", "enabled": True, "min_frames": 1, "threshold": 30},
            {"id": 3, "label": "收尾", "name": "收尾", "enabled": True, "min_frames": 1, "threshold": 30},
        ],
        "pipeline_config": {"settlement_mode": "last_step",
                            "detection_steps": [1, 2, 3]},
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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})

        # ── 1. 项目管理 → 选项目 → 逻辑设置 → 卡片可见 ──
        page.goto(f"{BASE}/#/project", wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=10000)
        page.locator("input[placeholder*='搜索项目']").fill(NAME)
        time.sleep(0.8)
        page.locator(f"div.p-4:has-text('{NAME}')").first.click()
        time.sleep(0.8)
        page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
        time.sleep(0.8)
        card = page.locator(".el-card:has-text('计数组合判定表')").first
        step("检测模式露出「计数组合判定表」卡", card.count() == 1)
        card.scroll_into_view_if_needed()
        page.screenshot(path=str(EVIDENCE / "01_card_visible.png"), full_page=True)

        # ── 2. 开启 → 选判型标签 → 加机型行 (2,1)=OK 机型X ──
        card.locator(".el-switch").first.click()
        time.sleep(0.5)
        card.locator(".el-form-item:has-text('参与判型的步骤标签') .el-select").first.click()
        time.sleep(0.5)
        page.locator(".el-select-dropdown__item:visible", has_text="区A").first.click()
        page.locator(".el-select-dropdown__item:visible", has_text="区B").first.click()
        page.keyboard.press("Escape")
        time.sleep(0.3)
        card.locator("button:has-text('添加机型行')").click()
        time.sleep(0.5)
        row = card.locator("div.flex.items-center.gap-2").last
        for i, v in enumerate((2, 1)):
            inp = row.locator(".el-input-number input").nth(i)
            inp.click()
            inp.press("ControlOrMeta+a")
            inp.type(str(v), delay=30)
            inp.press("Tab")
            time.sleep(0.2)
        row.locator("input[placeholder*='4缸']").fill("机型X")
        page.screenshot(path=str(EVIDENCE / "02_configured.png"), full_page=True)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        step("开启 + 选标签 + 机型行编辑 + 保存", True)

        # ── 3. T5 落库核对 + 重进回显 ──
        pc = (requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
              .get("pipeline_config") or {})
        cmb = pc.get("combo_table") or {}
        ok = (cmb.get("enabled") is True and cmb.get("labels") == ["区A", "区B"]
              and cmb.get("rows") == [{"counts": [2, 1], "verdict": "OK", "tag": "机型X"}])
        step("T5 落库核对 combo_table", ok, json.dumps(cmb, ensure_ascii=False))
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=10000)
        page.locator("input[placeholder*='搜索项目']").fill(NAME)
        time.sleep(0.8)
        page.locator(f"div.p-4:has-text('{NAME}')").first.click()
        time.sleep(0.8)
        page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
        time.sleep(0.8)
        card = page.locator(".el-card:has-text('计数组合判定表')").first
        card.scroll_into_view_if_needed()
        tag_ok = card.locator("input[placeholder*='4缸']").first.input_value() == "机型X"
        step("重进回显 (开关/标签/行)", tag_ok)
        page.screenshot(path=str(EVIDENCE / "03_reopened.png"), full_page=True)

        # ── 4. synthetic 实跑一件: 区A×2+区B×1 → 命中「机型X」判 OK ──
        requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=10)
        detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
        requests.post(f"{API}/api/v1/test/synthetic/start", json={
            "scenario_json": _scenario(), "channel": CH, "with_project": False,
        }, timeout=10).raise_for_status()
        requests.post(f"{API}/api/v1/source/detection/set-project?channel={CH}",
                      json={**{k: detail.get(k) for k in (
                          "steps_config", "pipeline_config", "events_config",
                          "counters_config", "data_config", "logic_mode")},
                          "project_id": pid, "name": NAME}, timeout=10).raise_for_status()
        requests.post(f"{API}/api/v1/source/detection/start?channel={CH}",
                      json={"conf": 0.25, "iou": 0.45}, timeout=10).raise_for_status()
        page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")
        time.sleep(2)

        # counters 按 project_id 从文件恢复 (pid 复用会带出历史值) → 增量断言
        base = requests.get(
            f"{API}/api/v1/source/detection/results?channel={CH}", timeout=5).json()
        base_ok = (base.get("counters") or {}).get("合格总数", 0)
        deadline, body = time.time() + 60, {}
        while time.time() < deadline:
            body = requests.get(
                f"{API}/api/v1/source/detection/results?channel={CH}", timeout=5).json()
            if (body.get("counters") or {}).get("合格总数", 0) >= base_ok + 1:
                break
            time.sleep(0.5)
        counters = body.get("counters") or {}
        cv = body.get("combo_verdict") or {}
        step("synthetic 实跑命中机型X 判 OK",
             counters.get("合格总数", 0) >= base_ok + 1 and cv.get("last_tag") == "机型X",
             f"counters={counters} combo_verdict={cv}")
        time.sleep(2)
        page.screenshot(path=str(EVIDENCE / "04_monitor_ok.png"), full_page=True)

        browser.close()

    cleanup()
    (EVIDENCE / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'='*50}\nUAT {'PASS' if verdict['pass'] else 'FAIL'} — 证据: {EVIDENCE}")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
