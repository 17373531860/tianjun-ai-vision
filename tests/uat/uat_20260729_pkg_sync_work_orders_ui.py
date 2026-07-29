# -*- coding: utf-8 -*-
"""UAT: 包装工单镜像进工单管理 (v3.45.1) — 真浏览器验证 (T4+T5).

两段:
  A. 设置页 → 包装箱结算 → 新建配置 → 组⑤「同步到工单管理」开关默认开 →
     关掉 → 保存 → GET 回读 + DB 直查双向核对 → 清理.
  B. 工单表插一条 source=packaging 的镜像单 → MES管理 → 工单页 →
     核对来源标签显示「包装扫码」→ 清理.

证据: /home/qianqian/uat_pkg_wo_sync/ (截图 + run.log)
"""
import sys
import time
import sqlite3
from pathlib import Path

import requests

API = "http://localhost:8001"
WEB = "http://localhost:5173"
DB_PATH = "/home/qianqian/桌面/word/tianjun-main/backend/sql_app.db"
OUT = Path("/home/qianqian/uat_pkg_wo_sync")
NAME = "__uat_wo_sync"
WO_NO = "__UAT_PKG_WO_1"

_log_fh = None


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_fh:
        _log_fh.write(line + "\n")
        _log_fh.flush()


def cleanup():
    try:
        r = requests.get(f"{API}/api/v1/packaging-flows", timeout=10).json()
        for it in r.get("items", []):
            if it["name"] == NAME:
                requests.put(f"{API}/api/v1/packaging-flows/{it['id']}",
                             json={"enabled": False}, timeout=10)
                requests.delete(f"{API}/api/v1/packaging-flows/{it['id']}",
                                timeout=10)
    except Exception:
        pass
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.execute("DELETE FROM work_orders WHERE order_no=?", (WO_NO,))
    db.commit()
    db.close()


def main():
    global _log_fh
    OUT.mkdir(parents=True, exist_ok=True)
    _log_fh = open(OUT / "run.log", "w", encoding="utf-8")
    cleanup()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_context(
            viewport={"width": 1600, "height": 950}).new_page()

        # ============ A. 包装面板开关 ============
        page.goto(f"{WEB}/#/settings", wait_until="domcontentloaded",
                  timeout=20000)
        time.sleep(3)
        page.click("text=包装箱结算")
        time.sleep(1)
        page.click("button:has-text('新建配置')")
        time.sleep(1)
        dlg = page.locator(".el-dialog").last
        dlg.locator("input").first.fill(NAME)
        dlg.locator("text=⑤ 收尾与回推").click()
        time.sleep(0.5)
        row = dlg.locator(".el-form-item", has_text="同步到工单管理").first
        sw = row.locator(".el-switch").first
        default_on = "is-checked" in (sw.get_attribute("class") or "")
        log(f"开关默认态: {'开' if default_on else '关'}")
        assert default_on, "「同步到工单管理」默认应为开"
        page.screenshot(path=str(OUT / "01_switch_default_on.png"),
                        full_page=True)
        sw.click()   # 关掉, 验证落库
        time.sleep(0.4)
        page.screenshot(path=str(OUT / "02_switch_off.png"), full_page=True)
        dlg.locator("button:has-text('保存')").click()
        time.sleep(1.5)
        page.screenshot(path=str(OUT / "03_saved.png"), full_page=True)

        # T5: GET 回读 + DB 直查
        items = requests.get(f"{API}/api/v1/packaging-flows",
                             timeout=10).json()["items"]
        mine = [it for it in items if it["name"] == NAME]
        assert mine, "保存后 GET 未见新配置"
        log(f"GET 回读: sync_work_orders={mine[0]['sync_work_orders']}")
        assert mine[0]["sync_work_orders"] is False
        db = sqlite3.connect(DB_PATH)
        row_db = db.execute(
            "SELECT sync_work_orders FROM packaging_flow_configs WHERE name=?",
            (NAME,)).fetchone()
        db.close()
        log(f"DB 直查: sync_work_orders={row_db}")
        assert row_db == (0,), f"DB 落库不符: {row_db}"

        # ============ B. 工单页来源标签 ============
        db = sqlite3.connect(DB_PATH, timeout=15)
        db.execute(
            "INSERT INTO work_orders (order_no, product_name, planned_qty,"
            " completed_qty, good_qty, ng_qty, rework_qty, scrap_qty,"
            " priority, status, source, binding_scope, created_at)"
            " VALUES (?, '包装工单', 4, 2, 2, 0, 0, 0, 5, 'in_progress',"
            " 'packaging', 'order', datetime('now'))", (WO_NO,))
        db.commit()
        db.close()
        page.goto(f"{WEB}/#/mes", wait_until="domcontentloaded",
                  timeout=20000)
        time.sleep(3)
        page.click("text=工单管理")
        time.sleep(2)
        wo_row = page.locator("tr", has_text=WO_NO).first
        wo_row.wait_for(timeout=10000)
        tag_txt = wo_row.locator(".el-tag").first.inner_text()
        log(f"工单页来源标签: {tag_txt!r}")
        assert "包装扫码" in tag_txt, f"来源标签应显示'包装扫码', 实际: {tag_txt!r}"
        page.screenshot(path=str(OUT / "04_wo_page_packaging_tag.png"),
                        full_page=True)
        browser.close()

    cleanup()
    log("PASS: 同步开关 UI→API→DB 双向 + 工单页'包装扫码'标签 全通过")
    _log_fh.close()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        cleanup()
        raise
