# -*- coding: utf-8 -*-
"""UAT: 组⑧「箱标签扫码授权 + 标签取本箱数量」配置 UI 真浏览器验证 (T4+T5).

路径: 设置页 → 包装箱结算 tab → 新建配置 → 切滑块口径 → 展开组⑧ →
     开全套开关填值 → 保存 → GET 回读 + 直查 DB 双向核对 → 清理.

证据: /home/qianqian/uat_box_label_scan/ (截图 + run.log)
"""
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8001"
WEB = "http://localhost:6001"
OUT = Path("/home/qianqian/uat_box_label_scan")
NAME = "__uat_label_scan"

_log_fh = None


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_fh:
        _log_fh.write(line + "\n")
        _log_fh.flush()


def cleanup():
    r = requests.get(f"{API}/api/v1/packaging-flows", timeout=10).json()
    for it in r.get("items", []):
        if it["name"] == NAME:
            requests.put(f"{API}/api/v1/packaging-flows/{it['id']}",
                         json={"enabled": False}, timeout=10)
            requests.delete(f"{API}/api/v1/packaging-flows/{it['id']}", timeout=10)


def main():
    global _log_fh
    OUT.mkdir(parents=True, exist_ok=True)
    _log_fh = open(OUT / "run.log", "w", encoding="utf-8")
    cleanup()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_context(viewport={"width": 1600, "height": 950}).new_page()
        page.goto(f"{WEB}/#/settings", wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)

        page.click("text=包装箱结算")
        time.sleep(1)
        page.screenshot(path=str(OUT / "01_panel.png"), full_page=True)

        page.click("button:has-text('新建配置')")
        time.sleep(1)
        dlg = page.locator(".el-dialog").last
        dlg.locator("input").first.fill(NAME)
        # 切滑块口径 (组⑧ 仅 sliders 显示)
        dlg.locator("text=按滑块 (上银)").first.click()
        time.sleep(0.5)
        # 展开组⑧
        dlg.locator("text=⑧ 箱标签扫码").click()
        time.sleep(0.5)
        page.screenshot(path=str(OUT / "02_group8_collapsed_open.png"), full_page=True)

        # 开总开关 → 子项显现
        row = dlg.locator(".el-form-item", has_text="每箱必须扫箱标签").first
        row.locator(".el-switch").click()
        time.sleep(0.4)
        # 开取量
        dlg.locator(".el-form-item", has_text="从标签取本箱数量").first \
           .locator(".el-switch").click()
        time.sleep(0.4)
        # 数量段号 3 → 4 (验证落库)
        seg = dlg.locator(".el-form-item", has_text="数量在第几段").first \
                 .locator("input").first
        seg.fill("4")
        # 正则
        dlg.locator(".el-form-item", has_text="数量段识别正则").first \
           .locator("input").first.fill(r"\d+\.\d+")
        # 重扫 update 档
        dlg.locator(".el-form-item", has_text="已放行后重扫标签").first \
           .locator("text=更新本箱目标").click()
        # 未扫做完整箱 book 档
        dlg.locator(".el-form-item", has_text="未扫标签做完整箱").first \
           .locator("text=报警后照常落账").click()
        # 收尾对账
        dlg.locator(".el-form-item", has_text="收尾数量对账").first \
           .locator(".el-switch").click()
        time.sleep(0.4)
        page.screenshot(path=str(OUT / "03_group8_filled.png"), full_page=True)

        dlg.locator("button:has-text('保存')").click()
        time.sleep(1.5)
        page.screenshot(path=str(OUT / "04_saved.png"), full_page=True)
        browser.close()

    # ==================== T5: GET 回读 + DB 直查双向核对 ====================
    items = requests.get(f"{API}/api/v1/packaging-flows", timeout=10).json()["items"]
    mine = [it for it in items if it["name"] == NAME]
    assert mine, "保存后 GET 未见新配置"
    c = mine[0]
    log(f"GET 回读: {c['id']} box_label_scan_required={c['box_label_scan_required']} "
        f"label_qty_enabled={c['label_qty_enabled']} segment={c['label_qty_segment']} "
        f"pattern={c['label_qty_pattern']!r} rescan={c['label_rescan_action']} "
        f"unauth={c['unauthorized_cycle_action']} total_check={c['label_total_check']}")
    assert c["box_label_scan_required"] is True
    assert c["label_qty_enabled"] is True
    assert c["label_qty_segment"] == 4
    assert c["label_qty_pattern"] == r"\d+\.\d+"
    assert c["label_rescan_action"] == "update"
    assert c["unauthorized_cycle_action"] == "book"
    assert c["label_total_check"] is True

    import sqlite3
    db = sqlite3.connect("/home/qianqian/桌面/word/tianjun-main/backend/sql_app.db")
    row = db.execute(
        "SELECT box_label_scan_required, label_qty_enabled, label_qty_segment,"
        " label_qty_pattern, label_rescan_action, unauthorized_cycle_action,"
        " label_total_check FROM packaging_flow_configs WHERE name=?", (NAME,)
    ).fetchone()
    log(f"DB 直查: {row}")
    assert row == (1, 1, 4, r"\d+\.\d+", "update", "book", 1), f"DB 落库不符: {row}"

    cleanup()
    log("PASS: 组⑧ UI → API → DB 双向验证全通过")
    _log_fh.close()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        cleanup()
        raise
