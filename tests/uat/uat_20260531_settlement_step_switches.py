#!/usr/bin/env python3
"""UAT: API 建项目缺 sequence_order → 结算步开关应不可用 (金龙踩坑回归).

客户现场叙事:
  1. 工程师用脚本/API 创建顺序模式项目 (如金龙 GW1), 只写步骤表、不写步骤顺序
  2. 进项目页, 结算方式选「最后一步结算」
  3. 预期: 序列最后一步的「严格顺序」「单次接受」变灰不可点
  4. 修复前: 顺序列表为空, 末步开关仍可编辑 → FAIL
  5. 修复后: 创建/加载时自动补顺序, 末步开关禁用 → PASS

证据输出:
  /tmp/uat_settlement_switches/run.log
  /tmp/uat_settlement_switches/*.png (Playwright 可用时)
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

API = os.environ.get("TIANJUN_API", "http://localhost:8001/api/v1")
OUT = Path("/tmp/uat_settlement_switches")
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "run.log"

passed = 0
failed = 0


def log(msg: str, ok: bool | None = None):
    global passed, failed
    if ok is True:
        passed += 1
        line = f"[OK] {msg}"
    elif ok is False:
        failed += 1
        line = f"[FAIL] {msg}"
    else:
        line = f"[..] {msg}"
    print(line)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def make_steps(labels):
    return [
        {"id": f"s{i}", "label": lbl, "enabled": True, "threshold": 50, "min_frames": 1,
         "strict_order": True, "accept_once": True}
        for i, lbl in enumerate(labels, start=1)
    ]


def api_create_project(name: str, settlement_mode: str):
    payload = {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {
            "logic_mode": "sequential",
            "settlement_mode": settlement_mode,
        },
        "steps_config": make_steps(["上料", "压墨", "吹干"]),
        "events_config": [
            {"id": 1, "name": "OK", "actions": [], "show_notification": True},
            {"id": 2, "name": "NG", "actions": [], "show_notification": True},
        ],
    }
    r = requests.post(f"{API}/projects", json=payload, timeout=15)
    return r


def main():
    LOG.write_text("", encoding="utf-8")
    log(f"API base: {API}")

    # --- 1. 后端 API: 创建后 sequence_order 必须非空 ---
    suffix = uuid.uuid4().hex[:8]
    name = f"uat-settle-{suffix}"
    r = api_create_project(name, "last_step")
    if r.status_code not in (200, 201):
        log(f"POST /projects status={r.status_code} body={r.text[:200]}", False)
        return 1
    proj = r.json()
    pid = proj["id"]
    seq = (proj.get("pipeline_config") or {}).get("sequence_order") or []
    log(f"创建项目 id={pid}, sequence_order 长度={len(seq)}", len(seq) == 3)
    if seq:
        log(f"末步 step_id={seq[-1].get('step_id')}", seq[-1].get("step_id") == "s3")

    # --- 2. GET 回读 ---
    r2 = requests.get(f"{API}/projects/{pid}", timeout=10)
    seq2 = (r2.json().get("pipeline_config") or {}).get("sequence_order") or []
    log(f"GET /projects/{pid} sequence_order 长度={len(seq2)}", len(seq2) == 3)

    # --- 3. Playwright: 项目页末步开关 disabled (可选) ---
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("Playwright 未安装, 跳过 UI 验证 (仅 API 层)", None)
        log(f"汇总: passed={passed} failed={failed}", None)
        return 0 if failed == 0 else 1

    frontend = os.environ.get("TIANJUN_FRONTEND", "http://localhost:6001")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.goto(f"{frontend}/#/project", wait_until="networkidle", timeout=60000)
        time.sleep(1)
        page.screenshot(path=str(OUT / "01_project_list.png"))

        # 点选刚创建的项目卡片 (按名称)
        card = page.locator(f"text={name}").first
        if card.count() == 0:
            log(f"项目页找不到 {name}, 可能需刷新列表", False)
        else:
            card.click()
            time.sleep(1.5)
            page.screenshot(path=str(OUT / "02_project_opened.png"))

            # 逻辑设置 Tab — 确认结算方式为「最后一步结算」
            page.locator('#tab-logic, .el-tabs__item:has-text("逻辑设置")').first.click()
            time.sleep(0.8)
            last_step_checked = page.locator(
                'label.el-radio.is-checked:has-text("最后一步结算")'
            ).count() > 0
            log(f"逻辑设置 Tab: 最后一步结算已选中={last_step_checked}", last_step_checked)
            page.screenshot(path=str(OUT / "02b_logic_tab.png"))

            # 步骤设置 Tab — 末步开关应 disabled
            page.locator('#tab-steps, .el-tabs__item:has-text("步骤设置")').first.click()
            time.sleep(0.8)

            row = page.locator("tr").filter(has_text="吹干").first
            switches = row.locator(".el-switch")
            count = switches.count()
            disabled_count = 0
            for i in range(count):
                cls = switches.nth(i).get_attribute("class") or ""
                aria = switches.nth(i).get_attribute("aria-disabled") or ""
                if "is-disabled" in cls or aria == "true":
                    disabled_count += 1
            page.screenshot(path=str(OUT / "03_last_step_switches.png"))
            log(
                f"末步「吹干」开关 disabled 数={disabled_count}/{count} (期望 2/2)",
                disabled_count >= 2 and count >= 2,
            )

        browser.close()

    log(f"汇总: passed={passed} failed={failed}", None)
    log(f"截图目录: {OUT}", None)
    log(f"日志: {LOG}", None)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
