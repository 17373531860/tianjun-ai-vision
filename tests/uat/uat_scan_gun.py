"""USB 扫码枪 可见浏览器 UAT (v3.20)

验证: 一把 USB 键盘式扫码枪, 扫到的码按用途自动路由 ——
  - 扫 JOB 工单码 → 去外部 MES (mock) 拉对应工单 → 入库
  - 扫工件码      → 注入扫码绑定链路 (/scanner/simulate)

内嵌 mock 上银 server (真实 response.resultData 两层结构)。
模拟扫码枪 = playwright 极快键盘输入 + Enter (满足全局捕获的速度判定)。

前置: 隔离后端 8009 + 前端 dev 5173 已起 (本脚本只跑浏览器)。
"""
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from playwright.sync_api import sync_playwright

BACKEND = "http://localhost:8009/api/v1"
FRONTEND = "http://localhost:5173"
MOCK_PORT = 18909
SHOT_DIR = "/home/qianqian/桌面/word/tianjun-main/tests/uat"

JOB_NO = f"JOB{uuid.uuid4().hex[:9].upper()}"
PART_NO = f"SZ{uuid.uuid4().hex[:6].upper()}"  # 工件码, 不匹配 ^(JOB|ORD)

_passed, _failed = 0, 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {name}")
    else:
        _failed += 1
        print(f"  ✗ {name}")


# ============================================================
# mock 上银 server: 任何查询都回真实嵌套结构, 回显请求的 job_no
# ============================================================
class MockHIWIN(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(ln) or b"{}")
        except Exception:
            req = {}
        job = (req.get("parameters") or {}).get("job_no") or JOB_NO
        body = {
            "error": None, "statusCode": 200, "success": True,
            "response": {"pageNo": 1, "numberOfPerPage": 200, "resultData": [
                {"job_no": job, "cust_name": "UAT客户", "dispatch_qty": 96, "spec": "HGL15CA ZAC"},
            ], "i18nInfo": None},
        }
        out = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def start_mock():
    srv = HTTPServer(("0.0.0.0", MOCK_PORT), MockHIWIN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def make_pull_connection():
    """直接用后端 API 建一条指向 mock 的拉取连接, 省去 UI 建连接步骤。"""
    cfg = {
        "pull": {
            "enabled": True,
            "url": f"http://localhost:{MOCK_PORT}/api",
            "method": "POST",
            "content_type": "application/json; charset=UTF-8",
            "request_body_template": '{"api":"hiwin/x/query","parameters":{"job_no":"{job_no}"}}',
            "success_path": "statusCode", "success_value": 200,
            "array_path": "response.resultData",
            "field_mapping": {
                "order_no": "job_no", "customer_name": "cust_name",
                "product_spec": "spec", "planned_qty": "dispatch_qty", "product_name": "spec",
            },
            "import_mode": "upsert", "retry_count": 0, "verify_ssl": False,
            "triggers": {"manual": True, "scheduled": False},
        }
    }
    r = requests.post(f"{BACKEND}/mes/gateway/connections", json={
        "name": "UAT-ScanGun", "adapter_type": "rest", "enabled": False,
        "pull_enabled": True, "config": cfg,
    }, timeout=10)
    r.raise_for_status()
    return (r.json().get("data") or r.json()).get("id")


def scan(page, code):
    """模拟 USB 扫码枪: 极快逐字符输入 + 回车 (满足全局捕获的速度判定)。"""
    page.mouse.click(5, 5)  # 让任何 input 失焦, 焦点回 body
    page.wait_for_timeout(100)
    page.keyboard.type(code, delay=10)
    page.keyboard.press("Enter")


def main():
    start_mock()
    print(f"[mock] 上银 mock 已起 :{MOCK_PORT}")
    conn_id = make_pull_connection()
    print(f"[setup] 拉取连接 id={conn_id}, JOB={JOB_NO}, PART={PART_NO}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = browser.new_context(no_viewport=True, record_video_dir=SHOT_DIR)
        page = ctx.new_page()

        # 进 MES → 扫码器 → 点「添加 USB 扫码枪」(USB 现在是扫码器设备的一种, 进设备列表)
        page.goto(f"{FRONTEND}/#/mes", wait_until="networkidle")
        page.wait_for_timeout(1500)
        page.get_by_text("扫码器", exact=True).first.click()
        page.wait_for_timeout(800)
        page.get_by_role("button", name="添加 USB 扫码枪").click()
        page.wait_for_timeout(800)
        page.screenshot(path=f"{SHOT_DIR}/scangun_01_dialog.png")
        check("01 USB 扫码枪编辑框弹出", page.get_by_text("扫到的码用来").count() > 0)

        dlg = page.locator(".el-dialog", has_text="添加 USB 扫码枪")
        # 名称
        dlg.locator(".el-form-item", has_text="名称").locator("input").fill("UAT-USB枪")
        page.wait_for_timeout(200)
        # 用途=两者 (enabled 默认已开)
        dlg.get_by_text("两者(按规则区分)").click()
        page.wait_for_timeout(300)
        # 选拉取连接
        dlg.locator(".el-form-item", has_text="拉工单用哪条连接").locator(".el-select").click()
        page.wait_for_timeout(500)
        page.get_by_text("UAT-ScanGun").last.click()
        page.wait_for_timeout(300)
        page.screenshot(path=f"{SHOT_DIR}/scangun_02_config.png")
        check("02 配置项齐全(规则+连接)", dlg.get_by_text("工单号识别规则").count() > 0)

        # 保存 → 设备进列表
        dlg.get_by_role("button", name="保存").click()
        page.wait_for_timeout(1200)
        page.screenshot(path=f"{SHOT_DIR}/scangun_02b_saved.png")
        check("02b USB 枪进设备列表", page.get_by_text("USB扫码枪").count() > 0)
        # 后端确认 usb_hid 设备已落库
        devs = requests.get(f"{BACKEND}/scanner/devices", timeout=10).json()
        dev_list = devs if isinstance(devs, list) else (devs.get("data") or devs.get("items") or [])
        usb_devs = [d for d in dev_list if isinstance(d, dict) and d.get("device_type") == "usb_hid"]
        check("02c 后端落库 usb_hid 设备", len(usb_devs) > 0)

        # —— 路 A: 扫 JOB 工单码 → 拉工单 ——
        scan(page, JOB_NO)
        page.wait_for_timeout(2500)
        page.screenshot(path=f"{SHOT_DIR}/scangun_03_pull.png")
        pull_ok = page.get_by_text("扫码拉工单成功").count() > 0
        check("03 扫工单码→拉工单成功通知", pull_ok)
        # 后端确认工单入库
        time.sleep(0.5)
        got = requests.get(f"{BACKEND}/mes/orders", params={"limit": 200}, timeout=10).json()
        orders = got.get("items") or got.get("data") or []
        order_nos = [o.get("order_no") for o in orders if isinstance(o, dict)]
        check("04 工单已入库(后端核对)", JOB_NO in order_nos)

        page.wait_for_timeout(1200)  # 等通知消失

        # —— 路 B: 扫工件码 → 绑工件 ——
        scan(page, PART_NO)
        page.wait_for_timeout(2000)
        page.screenshot(path=f"{SHOT_DIR}/scangun_04_bind.png")
        bind_ok = page.get_by_text("扫码绑定工件成功").count() > 0
        check("05 扫工件码→绑工件成功通知", bind_ok)
        # 后端确认 simulate 落了扫码日志
        logs = requests.get(f"{BACKEND}/scanner/logs", params={"limit": 50}, timeout=10).json()
        log_list = logs.get("data") or logs.get("items") or (logs if isinstance(logs, list) else [])
        raws = [l.get("raw_data") for l in (log_list if isinstance(log_list, list) else [])]
        check("06 工件码进了扫码链路(后端核对)", PART_NO in raws)

        page.wait_for_timeout(1000)
        ctx.close()
        browser.close()

    print(f"\n结果: {_passed} 通过 / {_failed} 失败")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
