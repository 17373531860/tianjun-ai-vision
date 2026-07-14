"""UAT (可见浏览器): 区域事件 overlap 规则「目标框扩边 object_margin」.

覆盖 2026-07-13 TP #35 修复面 (扫工件下沿条码, 枪框不压进工件框):
  1. overlap 规则卡渲染「目标框扩边」输入 (region_exit 规则不渲染)
  2. UI 改值 → 保存配置 → GET 项目核对落库 (双向验证)
  3. 刷新回显

前置: backend 8001 + frontend 6001 已启动。
产物: /tmp/uat_re_margin/*.png
"""
from __future__ import annotations

import os
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

BASE = "http://localhost:6001"
API = "http://localhost:8001"
OUT = "/tmp/uat_re_margin"
AB = [[0.5, 0.4], [0.9, 0.4], [0.9, 1.0], [0.5, 1.0]]
C = [[0.85, 0.0], [1.0, 0.0], [1.0, 1.0], [0.85, 1.0]]

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")


def make_project():
    name = f"__uat_margin_{uuid.uuid4().hex[:6]}"
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


def open_logic_tab(page, name):
    page.wait_for_selector("text=项目管理", timeout=15000)
    # 注: 不等 networkidle — Navbar 常驻轮询使网络永不空闲, 用选择器锚定即可
    page.wait_for_selector("input[placeholder*='搜索项目']", timeout=15000)
    time.sleep(1.0)
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
        open_logic_tab(page, name)

        # 1) overlap 规则卡渲染扩边输入; 数一下出现次数 = 1
        #    (只有 overlap 类型的「扫码」渲染, region_exit 的「下工件」不渲染)
        body = page.evaluate("document.body.innerText")
        check("目标框扩边输入渲染", "目标框扩边" in body)
        n_margin = page.locator("label:has-text('目标框扩边')").count()
        check("仅 overlap 规则渲染 (1 处)", n_margin == 1, f"count={n_margin}")
        inp = _num_input(page, "目标框扩边")
        inp.scroll_into_view_if_needed()
        check("默认值 0", inp.input_value() in ("0", "0.00"),
              f"value={inp.input_value()}")
        page.screenshot(path=f"{OUT}/01_margin_input.png", full_page=False)

        # 2) 改值 0.02 → 保存 → 落库验证
        inp.fill("0.02")
        inp.press("Enter")
        time.sleep(0.4)
        page.screenshot(path=f"{OUT}/02_value_filled.png", full_page=False)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
        rules = ((detail.get("pipeline_config") or {})
                 .get("region_events") or {}).get("rules") or []
        scan = next((r for r in rules if r.get("name") == "扫码"), {})
        unload = next((r for r in rules if r.get("name") == "下工件"), {})
        check("object_margin 落库 0.02",
              abs((scan.get("object_margin") or 0) - 0.02) < 1e-6,
              f"object_margin={scan.get('object_margin')}")
        check("region_exit 规则不带该键",
              not unload.get("object_margin"),
              f"unload.object_margin={unload.get('object_margin')}")

        # 3) 刷新回显
        page.reload(wait_until="domcontentloaded")
        open_logic_tab(page, name)
        val = _num_input(page, "目标框扩边").input_value()
        check("刷新后回显 0.02", val == "0.02", f"value={val}")
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
