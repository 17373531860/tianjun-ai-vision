"""UAT (可见浏览器): 区域事件模式 — 确认时长秒基门槛 min_seconds.

覆盖 2026-07-09 帧率解耦改造:
  1. 规则卡片渲染「确认时长（秒，0=按帧数）」输入框
  2. UI 改值 → 保存配置 → GET 项目核对落库 (双向验证)
  3. 刷新回显 + 0 值不落库 (老配置语义不受污染)

前置: backend 8001 + frontend 6001 已启动。
产物: /tmp/uat_re_min_seconds/*.png
"""
from __future__ import annotations

import os
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

BASE = "http://localhost:6001"
API = "http://localhost:8001"
OUT = "/tmp/uat_re_min_seconds"
AB = [[0.5, 0.4], [0.9, 0.4], [0.9, 1.0], [0.5, 1.0]]
C = [[0.85, 0.0], [1.0, 0.0], [1.0, 1.0], [0.85, 1.0]]

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")


def make_project():
    name = f"__uat_re_ms_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "region_events"})
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/api/v1/projects/{pid}", json={
        "pipeline_config": {"region_events": {
            "enabled": True,
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
    return page.locator(
        f"div:has(> label:has-text('{label_text}')) .el-input-number input").first


def _open_logic_tab(page, name):
    page.wait_for_selector("text=项目管理", timeout=15000)
    page.wait_for_load_state("networkidle", timeout=15000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.8)
    page.locator(f"div.p-4:has-text('{name}')").first.click()
    time.sleep(1.0)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(1.2)


def main():
    os.makedirs(OUT, exist_ok=True)
    pid, name = make_project()
    print(f"项目 {name} (id={pid})")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})
        page.goto(f"{BASE}/#/project", wait_until="domcontentloaded")
        _open_logic_tab(page, name)

        # 1) 新参数渲染 (overlap 规则有, region_exit 规则没有)
        body = page.evaluate("document.body.innerText")
        check("确认时长输入渲染", "确认时长" in body)
        inp = _num_input(page, "确认时长")
        check("默认值 0 (按帧数)", inp.input_value() == "0.0",
              f"value={inp.input_value()}")
        inp.scroll_into_view_if_needed()
        page.screenshot(path=f"{OUT}/01_field_rendered.png", full_page=False)

        # 2) 改值 0.3s → 保存 → 落库
        inp.fill("0.3")
        inp.press("Enter")
        time.sleep(0.4)
        page.screenshot(path=f"{OUT}/02_value_filled.png", full_page=False)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)

        detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
        re_cfg = (detail.get("pipeline_config") or {}).get("region_events") or {}
        scan = next((r for r in (re_cfg.get("rules") or [])
                     if r.get("name") == "扫码"), {})
        exit_rule = next((r for r in (re_cfg.get("rules") or [])
                          if r.get("name") == "下工件"), {})
        check("min_seconds 落库", abs(scan.get("min_seconds", 0) - 0.3) < 1e-6,
              f"min_seconds={scan.get('min_seconds')}")
        check("region_exit 规则不带该键", "min_seconds" not in exit_rule)

        # 3) 刷新回显
        page.reload(wait_until="domcontentloaded")
        _open_logic_tab(page, name)
        ms = _num_input(page, "确认时长").input_value()
        check("刷新后确认时长回显", ms == "0.3", f"value={ms}")
        page.screenshot(path=f"{OUT}/03_reload_echo.png", full_page=False)

        # 4) 归零 → 保存 → 键从库里剥离 (0 不落库)
        inp = _num_input(page, "确认时长")
        inp.fill("0")
        inp.press("Enter")
        time.sleep(0.4)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
        re_cfg = (detail.get("pipeline_config") or {}).get("region_events") or {}
        scan = next((r for r in (re_cfg.get("rules") or [])
                     if r.get("name") == "扫码"), {})
        check("归零后键不落库", "min_seconds" not in scan,
              f"rule_keys={sorted(scan.keys())}")

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
