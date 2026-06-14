"""可见浏览器 UAT — 外部 MES 工单拉取面板 (v3.20).

走完整客户路径:
  打开 MES → 工单拉取 Tab → 新建配置 → 上银模板一键填充 → 改地址指向 mock
  → 测试连接(结构识别+字段映射自动填) → 保存 → 列表出现
  → 试同步(不落库) → 立即同步(落库) → sqlite 验证工单真入库

mock 一个本地接口返回上银 HIWIN 结构, 后端 8009 去拉它。
前端 5173 (baseURL 经 .env.development.local 指向 8009)。
隔离 DB: /tmp/tianjun_uat_pull/sql_app.db (UAT 后整目录销毁)。
"""
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:5173"
MOCK_PORT = 8888
DB_PATH = "/tmp/tianjun_uat_pull/sql_app.db"
SHOTS = "/tmp/tianjun_uat_pull_shots"

import os
os.makedirs(SHOTS, exist_ok=True)

_results = []
def step(name, ok):
    _results.append((name, ok))
    print(("  ✓ " if ok else "  ✗ ") + name, flush=True)


# ──────── mock 上银接口 ────────
class _MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        _ = self.rfile.read(ln)
        body = {
            "statusCode": 200,
            "resultData": [
                {"job_no": "UAT-WO-001", "cust_name": "雷鸟UAT", "dispatch_qty": 88, "spec": "UAT-SPEC-A"},
                {"job_no": "UAT-WO-002", "cust_name": "雷鸟UAT", "dispatch_qty": 200, "spec": "UAT-SPEC-B"},
            ],
        }
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_mock():
    srv = HTTPServer(("0.0.0.0", MOCK_PORT), _MockHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run():
    start_mock()
    mock_url = f"http://localhost:{MOCK_PORT}/api"
    print(f"[UAT] mock 上银接口: {mock_url}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir="/tmp/uat_video",
            record_video_size={"width": 1600, "height": 1000},
            ignore_https_errors=True,
        )
        page = ctx.new_page()
        try:
            # 1) 进 MES 模块 (hash 路由)
            page.goto(f"{FRONTEND}/#/mes")
            page.wait_for_load_state("networkidle"); time.sleep(1.5)
            page.screenshot(path=f"{SHOTS}/01_mes.png")

            # 2) 切到 工单拉取 Tab
            page.get_by_role("button", name="工单拉取").click()
            page.wait_for_load_state("networkidle"); time.sleep(1.0)
            body = page.evaluate("document.body.innerText")
            step("01 工单拉取 Tab 渲染", "外部 MES 工单拉取" in body)
            page.screenshot(path=f"{SHOTS}/02_pull_tab.png")

            # 3) 新建配置对话框
            page.get_by_role("button", name="新建拉取配置").click()
            time.sleep(0.8)
            dlg = page.get_by_role("dialog")
            step("02 新建对话框打开", dlg.is_visible())

            # 4) 上银模板一键填充
            page.get_by_role("button", name="上银 HIWIN").click()
            time.sleep(0.8)
            name_val = dlg.locator("input").first.input_value()
            step("03 上银模板填充配置名称", "上银" in name_val)
            page.screenshot(path=f"{SHOTS}/03_template_filled.png")

            # 5) 把地址改成 mock (清空再填)
            url_input = dlg.get_by_placeholder("上银给你的查询接口网址")
            url_input.fill(mock_url)
            step("04 地址已填 mock", dlg.get_by_placeholder("上银给你的查询接口网址").input_value() == mock_url)

            # 6) 测试连接 → 结构识别 + 字段映射自动填
            dlg.get_by_role("button", name="测试连接").click()
            time.sleep(2.0)
            dbody = dlg.inner_text()
            step("05 测试连接成功提示", "成功" in dbody)
            step("06 自动识别返回字段", "job_no" in dbody and "dispatch_qty" in dbody)
            page.screenshot(path=f"{SHOTS}/04_test_ok.png")

            # 7) 保存
            page.locator(".el-dialog button:has-text('保存')").first.click()
            page.wait_for_load_state("networkidle"); time.sleep(1.5)
            body = page.evaluate("document.body.innerText")
            step("07 保存后列表出现配置", "上银 HIWIN MES" in body)
            page.screenshot(path=f"{SHOTS}/05_saved_list.png")

            # 8) 试同步 (dry_run, 不落库)
            page.get_by_role("button", name="试同步").first.click()
            time.sleep(2.0)
            rbody = page.evaluate("document.body.innerText")
            step("08 试同步结果弹窗出现", "同步结果" in rbody and "拉回" in rbody)
            page.screenshot(path=f"{SHOTS}/06_dryrun.png")
            # 关弹窗
            page.keyboard.press("Escape"); time.sleep(0.6)
            # 验证试同步没真落库
            cnt_dry = _count_orders()
            step("09 试同步未落库 (工单数=0)", cnt_dry == 0)

            # 9) 立即同步 (真落库)
            page.get_by_role("button", name="立即同步").first.click()
            time.sleep(2.5)
            rbody = page.evaluate("document.body.innerText")
            step("10 立即同步结果显示新建", "新建" in rbody)
            page.screenshot(path=f"{SHOTS}/07_realsync.png")
            page.keyboard.press("Escape"); time.sleep(0.6)

            # 10) sqlite 验证工单真入库
            time.sleep(1.0)
            rows = _fetch_orders()
            step("11 工单真入库 (2 条)", len(rows) == 2)
            ok_fields = any(r["order_no"] == "UAT-WO-001" and r["customer_name"] == "雷鸟UAT"
                            and r["planned_qty"] == 88 and r["product_spec"] == "UAT-SPEC-A"
                            and r["source"] == "external" for r in rows)
            step("12 入库字段映射正确", ok_fields)
            print("  入库工单:", rows, flush=True)

        finally:
            page.screenshot(path=f"{SHOTS}/99_final.png")
            ctx.close(); browser.close()

    print("\n===== UAT 汇总 =====", flush=True)
    passed = sum(1 for _, ok in _results if ok)
    for n, ok in _results:
        print(("PASS " if ok else "FAIL ") + n, flush=True)
    print(f"\n{passed}/{len(_results)} 通过  截图: {SHOTS}", flush=True)
    return passed == len(_results)


def _count_orders():
    try:
        con = sqlite3.connect(DB_PATH)
        n = con.execute("SELECT COUNT(*) FROM work_orders WHERE order_no LIKE 'UAT-WO-%'").fetchone()[0]
        con.close()
        return n
    except Exception as e:
        print("  [count err]", e, flush=True)
        return -1


def _fetch_orders():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT order_no, customer_name, planned_qty, product_spec, product_name, source "
        "FROM work_orders WHERE order_no LIKE 'UAT-WO-%' ORDER BY order_no"
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    ok = run()
    raise SystemExit(0 if ok else 1)
