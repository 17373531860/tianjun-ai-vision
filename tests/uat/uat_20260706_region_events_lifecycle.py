"""UAT (可见浏览器): 区域事件模式 v3 增量 — 动作生命周期三件套.

覆盖 2026-07-06 客户反馈 (TP 现场第一周期误报复检) 的修复面:
  1. 术语: 卡片/按钮/占位符「事件」→「动作」
  2. 新参数 UI 回显与可编辑: 位移门槛 min_move / 消失确认秒数 gone_seconds /
     重叠深度 min_overlap_ratio / 全局「连续同动作去重」开关
  3. UI 改值 → 保存配置 → GET 项目核对落库 (双向验证, 不是只看 toast)

前置: backend 8001 + frontend 6001 已启动。
产物: /tmp/uat_re_lifecycle/*.png
"""
from __future__ import annotations

import os
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

BASE = "http://localhost:6001"
API = "http://localhost:8001"
OUT = "/tmp/uat_re_lifecycle"
AB = [[0.5, 0.4], [0.9, 0.4], [0.9, 1.0], [0.5, 1.0]]
C = [[0.85, 0.0], [1.0, 0.0], [1.0, 1.0], [0.85, 1.0]]

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")


def make_project():
    name = f"__uat_re3_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "region_events"})
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/api/v1/projects/{pid}", json={
        "pipeline_config": {"region_events": {
            "enabled": True,
            "class_conf": {"扫码枪": 0.6},
            "gap_tolerance_frames": 6,
            "rules": [
                {"id": "r1", "name": "扫码", "type": "overlap",
                 "subject_label": "扫码枪", "object_label": "工件",
                 "region": AB, "region_mode": "and", "min_frames": 8,
                 "settle": False, "event_id": None},
                {"id": "r2", "name": "下工件", "type": "region_exit",
                 "subject_label": "工件", "region": C, "min_frames": 1,
                 "gone_frames": 8, "match_iou": 0.2, "settle": True,
                 "event_id": None},
            ],
            "sequence_check": {"enabled": False, "order": [], "event_id": None},
            "settlement_rules": [],
        }},
    }).raise_for_status()
    return pid, name


def _num_input(page, label_text):
    """按 label 文案定位同格的 el-input-number 内部 input。"""
    return page.locator(
        f"div:has(> label:has-text('{label_text}')) .el-input-number input").first


def main():
    os.makedirs(OUT, exist_ok=True)
    pid, name = make_project()
    print(f"项目 {name} (id={pid})")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})
        page.goto(f"{BASE}/#/project", wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=15000)
        page.locator("input[placeholder*='搜索项目']").fill(name)
        time.sleep(0.8)
        page.locator(f"div.p-4:has-text('{name}')").first.click()
        time.sleep(1.0)
        page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
        time.sleep(1.2)

        # 1) 术语「动作」
        card = page.locator(".el-card:has-text('区域事件模式 - 动作规则')")
        check("卡片标题改「动作规则」", card.count() == 1)
        body = page.evaluate("document.body.innerText")
        check("新增按钮改「新增动作」", "+ 新增动作" in body)

        # 2) 新参数 UI 渲染
        check("位移门槛输入渲染", "位移门槛" in body)
        check("消失确认输入渲染", "消失确认" in body)
        check("重叠深度输入渲染", "重叠深度下限" in body)
        check("连续同动作去重开关渲染", "连续同动作去重" in body)
        card.first.scroll_into_view_if_needed()
        page.screenshot(path=f"{OUT}/01_new_params.png", full_page=False)

        # 3) 改值: 扫码规则 位移门槛 0.04 + 消失确认 1.5s + 重叠深度 0.1
        for label, value in (("位移门槛", "0.04"), ("消失确认", "1.5"),
                             ("重叠深度下限", "0.1")):
            inp = _num_input(page, label)
            inp.scroll_into_view_if_needed()
            inp.fill(value)
            inp.press("Enter")
            time.sleep(0.4)
        page.screenshot(path=f"{OUT}/02_values_filled.png", full_page=False)

        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)

        # 4) 落库双向验证
        detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
        re_cfg = (detail.get("pipeline_config") or {}).get("region_events") or {}
        rule = next((r for r in (re_cfg.get("rules") or [])
                     if r.get("name") == "扫码"), {})
        check("min_move 落库", abs(rule.get("min_move", 0) - 0.04) < 1e-6,
              f"min_move={rule.get('min_move')}")
        check("gone_seconds 落库", abs(rule.get("gone_seconds", 0) - 1.5) < 1e-6,
              f"gone_seconds={rule.get('gone_seconds')}")
        check("min_overlap_ratio 落库",
              abs(rule.get("min_overlap_ratio", 0) - 0.1) < 1e-6,
              f"min_overlap_ratio={rule.get('min_overlap_ratio')}")
        check("dedup_consecutive 默认开落库",
              re_cfg.get("dedup_consecutive") is True,
              f"dedup={re_cfg.get('dedup_consecutive')}")

        # 5) 刷新回显
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=15000)
        page.locator("input[placeholder*='搜索项目']").fill(name)
        time.sleep(0.8)
        page.locator(f"div.p-4:has-text('{name}')").first.click()
        time.sleep(1.0)
        page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
        time.sleep(1.2)
        mm = _num_input(page, "位移门槛").input_value()
        gs = _num_input(page, "消失确认").input_value()
        check("刷新后位移门槛回显", mm == "0.04", f"value={mm}")
        check("刷新后消失确认回显", gs == "1.5", f"value={gs}")
        page.screenshot(path=f"{OUT}/03_reload_echo.png", full_page=False)

        browser.close()
    requests.delete(f"{API}/api/v1/projects/{pid}")
    fails = [r for r in results if not r[1]]
    print(f"\n结果: {len(results) - len(fails)}/{len(results)} 通过")
    if fails:
        for n, _, d in fails:
            print(f"  FAIL: {n} {d}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
