"""可见浏览器 UAT: 数量门「PLC 连接」下拉修复验证 (2026-08-12 现场反馈).

现场症状: PLC 已连接, 但逻辑设置 → 切步数量门 → PLC 连接下拉恒为 No data。
根因: loadPlcConns() 误取 data.items, 后端实际返回 {"connections": [...]}。

跑法 (需 8002 后端 + 6002 前端已起):
  E2E_API_URL=http://localhost:8002 E2E_BASE_URL=http://localhost:6002 \
    python tests/manual_uat/combo_plc_conn_dropdown_uat.py
"""
import os
import time
import uuid
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

API = os.environ.get("E2E_API_URL", "http://localhost:8002")
BASE = os.environ.get("E2E_BASE_URL", "http://localhost:6002")
EVID = Path(__file__).parent / "evidence" / "combo_plc_conn_dropdown_2026-08-12"
EVID.mkdir(parents=True, exist_ok=True)


def main():
    suffix = uuid.uuid4().hex[:6]
    conn_name = f"机加线1号PLC-UAT{suffix}"
    r = requests.post(f"{API}/api/v1/plc/connections", json={
        "name": conn_name, "driver": "mock", "enabled": False,
        "conn_params": {"store_id": conn_name, "poll_interval_ms": 100},
        "points": [{"key": "cyl_type", "addr": "m0", "type": "int16", "dir": "read"}],
        "read_rules": [], "write_rules": [], "options": {},
    }, timeout=10)
    r.raise_for_status()
    conn_id = r.json()["id"]

    proj_name = f"__e2e_uat_plcdd_{suffix}"
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": proj_name, "task_type": "detection", "logic_mode": "detection",
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/api/v1/projects/{pid}", json={
        "steps_config": [
            {"id": 1, "label": "区A", "name": "区域A", "enabled": True},
            {"id": 2, "label": "收尾", "name": "收尾", "enabled": True},
        ],
        "pipeline_config": {"combo_table": {
            "enabled": True, "labels": ["区A"], "count_mode": "positional",
            "rows": [{"counts": [2], "verdict": "OK", "tag": "机型X"}],
            "step_guard": {"enabled": True, "expected_source": "auto"},
        }},
    }, timeout=5).raise_for_status()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False, slow_mo=250)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{BASE}/#/project", wait_until="domcontentloaded")
            page.wait_for_selector("text=项目管理", timeout=10000)
            page.locator("input[placeholder*='搜索项目']").fill(proj_name)
            time.sleep(0.8)
            page.locator(f"div.p-4:has-text('{proj_name}')").first.click()
            time.sleep(0.8)
            page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
            time.sleep(0.8)
            card = page.locator(".el-card:has-text('计数组合判定表')").first
            card.scroll_into_view_if_needed()
            sel = card.locator(".combo-guard-plc-conn").first
            sel.scroll_into_view_if_needed()
            sel.click()
            time.sleep(1.0)
            page.screenshot(path=str(EVID / "01_dropdown_lists_connection.png"))
            opt = page.locator(".el-select-dropdown:visible .el-select-dropdown__item"
                               f":has-text('{conn_name}')").first
            assert opt.count() == 1, "下拉应列出连接"
            opt.click()
            time.sleep(0.5)
            page.screenshot(path=str(EVID / "02_selected.png"))
            page.locator("button:has-text('保存配置')").click()
            time.sleep(2.0)
            page.screenshot(path=str(EVID / "03_saved.png"))
            browser.close()

        cmb = ((requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
                .get("pipeline_config") or {}).get("combo_table") or {})
        sg = cmb.get("step_guard") or {}
        assert sg.get("plc_connection_id") == conn_id, f"落库不对: {sg}"
        print(f"UAT PASS: 下拉列出连接 + 选中 + 落库 plc_connection_id={conn_id}")
        print(f"证据: {EVID}")
    finally:
        requests.delete(f"{API}/api/v1/projects/{pid}", timeout=5)
        requests.delete(f"{API}/api/v1/plc/connections/{conn_id}", timeout=5)


if __name__ == "__main__":
    main()
