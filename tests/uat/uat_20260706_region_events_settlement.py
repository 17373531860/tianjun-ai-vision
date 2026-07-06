"""UAT (可见浏览器): 区域事件模式 v2 增量 — 结算判定 + 进区/驻留规则 + 锚点跟随.

路径 H 人眼证据: headless=False 真开浏览器, 用 API 预置一个带
结算判定(缺/重复/兜底) + region_enter 规则 + 锚点配置的项目,
打开 项目页-逻辑设置 核对回显, 逐块截图。

前置: backend 8001 + frontend 6001 已启动。
产物: /tmp/uat_re_settlement/*.png
"""
from __future__ import annotations

import os
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

BASE = "http://localhost:6001"
API = "http://localhost:8001"
OUT = "/tmp/uat_re_settlement"
AB = [[0.5, 0.4], [0.9, 0.4], [0.9, 1.0], [0.5, 1.0]]
C = [[0.85, 0.0], [1.0, 0.0], [1.0, 1.0], [0.85, 1.0]]

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")


def make_project():
    name = f"__uat_re2_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "region_events"})
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/api/v1/projects/{pid}", json={
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": []},
            {"id": 2, "name": "不合格(NG)", "actions": []},
            {"id": 3, "name": "滞留警告", "actions": []},
        ],
        "pipeline_config": {"region_events": {
            "enabled": True,
            "class_conf": {"测硬度笔": 0.25},
            "gap_tolerance_frames": 6,
            "rules": [
                {"id": "r1", "name": "测硬度", "type": "overlap",
                 "subject_label": "测硬度笔", "object_label": "工件",
                 "region": AB, "region_mode": "and", "min_frames": 10,
                 "require_label": "手", "settle": False, "event_id": None},
                {"id": "r2", "name": "滞留告警", "type": "region_enter",
                 "subject_label": "工件", "region": AB, "min_frames": 240,
                 "settle": False, "event_id": 3,
                 "anchor": {"enabled": True, "label": "工件",
                            "ref": {"x": 0.55, "y": 0.55, "w": 0.2, "h": 0.2},
                            "hold_seconds": 3.0}},
                {"id": "r3", "name": "下工件", "type": "region_exit",
                 "subject_label": "工件", "region": C, "min_frames": 1,
                 "gone_frames": 8, "match_iou": 0.2, "settle": True,
                 "event_id": None},
            ],
            "sequence_check": {"enabled": False, "order": [], "event_id": None},
            "settlement_rules": [
                {"match": "exact", "sequence": ["测硬度", "下工件"], "event_id": 1},
                {"match": "missing", "target": "测硬度", "event_id": 2},
                {"match": "repeated", "target": "测硬度", "min_count": 2, "event_id": 2},
                {"match": "always", "event_id": 2},
            ],
        }},
    }).raise_for_status()
    return pid, name


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

        card = page.locator(".el-card:has-text('区域事件模式 - 动作规则')")
        check("规则卡片渲染", card.count() == 1)
        # 规则名是 el-input 的 value, 不在 innerText 里 — 直接读输入框值
        rule_names = page.eval_on_selector_all(
            "input[placeholder*='动作名']", "els => els.map(e => e.value)")
        check("三条规则回显", set(rule_names) == {"测硬度", "滞留告警", "下工件"},
              f"names={rule_names}")
        body = page.evaluate("document.body.innerText")
        check("进区/驻留类型回显", "进区/驻留" in body)
        check("驻留告警提示语", "驻留超时告警" in body)
        check("锚点已标定回显", "已标定 x=0.55" in body.replace(" ", " "))
        check("结算判定块渲染", "结算判定" in body and "先匹配先赢" in body)
        check("四条判定回显", body.count("判定 ") >= 4 or all(
            t in body for t in ("序列完全匹配", "缺某动作", "某动作重复", "其余全部")))
        card.first.scroll_into_view_if_needed()
        page.screenshot(path=f"{OUT}/01_rules_top.png", full_page=False)
        # :has-text 会连外层容器一起匹配, 取最内层 (文档序最后) 才是结算判定块
        settle_blk = page.locator("div.bg-slate-900:has-text('新增判定')").last

        def _srows():
            return settle_blk.locator("div.bg-slate-800").filter(has_text="结算为")

        check("四条判定行渲染", _srows().count() == 4, f"rows={_srows().count()}")
        _srows().first.scroll_into_view_if_needed()
        time.sleep(0.5)
        page.screenshot(path=f"{OUT}/02_settlement.png", full_page=False)

        # 交互: 新增一条判定再删除 (UI 可操作性)
        settle_blk.locator("button:has-text('新增判定')").click()
        time.sleep(0.6)
        check("新增判定成第5行", _srows().count() == 5, f"rows={_srows().count()}")
        _srows().last.scroll_into_view_if_needed()
        time.sleep(0.4)
        _srows().last.locator("button:has-text('删除')").click()
        time.sleep(0.5)
        check("删除后回到4行", _srows().count() == 4)
        page.screenshot(path=f"{OUT}/03_after_interaction.png", full_page=False)

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
