# -*- coding: utf-8 -*-
"""川南 2026-07-17 · 可见浏览器 UAT (v3.40 复用工单重绑 + 周期结果过滤 修复验收)。

现场叙事 (客户原话 + Postman/推送记录截图钉死的两个 bug):
  1. 客户每次测试都发同一个任务号 task-pro。这张工单第一次创建时绑的项目
     和现在跑的项目不同 → 之后每次开工都走"复用工单"分支, 老代码不刷新绑定
     → 工位永远挂不上单 → 信息条不显示(问题1) + 完工/报警推送里
     TaskNo/ProductCode/StepCode/Operator 全 null(问题2)。
  2. 报警连接勾了"仅 NG", 合格周期照样推 (WarningText="顺序正确完成"):
     老过滤只读顶层结果字段, 周期事件的结果在里层, 过滤从未生效(问题3)。

本 UAT 完整复刻:
  - 项目 A 上建单 → 切到项目 B → 同任务号再开工(复用) → 信息条四要素上屏
  - 本地 mock 中控收推送: 完工报文四要素非 null;
    合格周期不再打到报警接口; 不合格周期报警报文四要素非 null

跑法(前置: 后端 8013 RUNTIME_MODE=test + 前端 dev 6003 已起):
  cd tests/uat && UAT_API=http://127.0.0.1:8013 UAT_FRONT=http://127.0.0.1:6003 \
    UAT_DB=/tmp/uat_cn_seq/sql_app.db python uat_20260717_cn_order_reuse_null.py
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, launch_browser, filter_console_errors

API = os.environ.get("UAT_API", "http://127.0.0.1:8013")
FRONT = os.environ.get("UAT_FRONT", "http://127.0.0.1:6003")
MOCK_PORT = 18922
TASK_NO = "task-pro-uat"          # 复刻客户: 固定任务号反复开工
FPS = 30

run = UatRun("cn_order_reuse_null")


# ==================== 本地 mock 中控 (收 /warning /complete) ====================
RECEIVED = {"warning": [], "complete": []}


class _MockMCS(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if "/warning" in self.path:
            RECEIVED["warning"].append(body)
        elif "/complete" in self.path:
            RECEIVED["complete"].append(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"code": 0}')

    def log_message(self, *a):
        pass


def start_mock():
    srv = HTTPServer(("127.0.0.1", MOCK_PORT), _MockMCS)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    run.step("mock 中控已起", True, f"port={MOCK_PORT}")
    return srv


# ==================== 场景搭建 ====================
def fr(sec):
    return int(sec * FPS)


def seg(t0, t1, labels):
    return {"from": fr(t0), "to": fr(t1), "detections": [
        {"label": l, "confidence": 0.95,
         "bbox": [0.1 + 0.2 * i, 0.1, 0.25 + 0.2 * i, 0.35]}
        for i, l in enumerate(labels)]}


# 剧本: 前 15s 静默 (留给"开工前无单检查 + POST 开工 + 回填绑定"), 之后:
# 周期1 A,B,C 齐(OK) → 周期2 A,C 漏 B → 周期3 的 A 触发周期2 结算 NG(缺B) → 静默
# 所有周期都在重新开工之后结算, 推送报文应带上复用单重绑后的四要素。
TIMELINE = [
    seg(0, 15, []),
    seg(15, 17, ["A"]), seg(17, 17.5, []),
    seg(17.5, 19.5, ["B"]), seg(19.5, 20, []),
    seg(20, 22, ["C"]), seg(22, 24, []),
    seg(24, 26, ["A"]), seg(26, 26.5, []),     # 结算周期1 OK, 周期2 开始
    seg(26.5, 28.5, ["C"]), seg(28.5, 30, []),  # 周期2 漏 B 直接 C
    seg(30, 32, ["A"]), seg(32, 90, []),       # 结算周期2 NG(缺B)
]


def mk_project(name):
    steps = [{"id": f"s{i+1}", "label": l, "threshold": 25, "min_frames": 3,
              "enabled": True, "color": "#1976d2"} for i, l in enumerate("ABC")]
    pipeline = {"sequence_order": [{"step_id": s["id"]} for s in steps],
                "settlement_mode": "first_step", "settle_dedup": False}
    events = [
        {"id": 1, "name": "合格(OK)", "actions": [{"counter_name": "总产量", "delta": 1}]},
        {"id": 2, "name": "不合格(NG)", "actions": [{"counter_name": "总产量", "delta": 1}]},
    ]
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "sequential",
        "steps_config": steps, "pipeline_config": pipeline,
        "events_config": events}, timeout=15)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def setup():
    r = requests.get(f"{API}/api/v1/projects", timeout=10)
    run.step("后端就绪", r.status_code == 200, f"HTTP {r.status_code}")
    run.step("前端就绪", requests.get(FRONT, timeout=10).status_code == 200)

    # 清理: 同名项目/工单/旧连接 (可重复跑)
    items = (r.json() or {}).get("items") or r.json()
    for p in items:
        if str(p.get("name", "")).startswith("CNRU-"):
            requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=10)
    for c in requests.get(f"{API}/api/v1/mes/gateway/connections", timeout=10).json():
        if str(c.get("name", "")).startswith("cnru-"):
            requests.delete(f"{API}/api/v1/mes/gateway/connections/{c['id']}", timeout=10)
    for o in requests.get(f"{API}/api/v1/mes/orders", timeout=10).json().get("items", []):
        if o.get("order_no") == TASK_NO:
            requests.delete(f"{API}/api/v1/mes/orders/{o['id']}", timeout=10)
    run.step("历史数据已清", True)

    pa = mk_project(f"CNRU-A-{time.strftime('%H%M%S')}")
    pb = mk_project(f"CNRU-B-{time.strftime('%H%M%S')}")
    run.step("建项目 A/B", True, f"A=#{pa} B=#{pb}")

    # 入站: 建单 + 四要素全勾 (不开切项目, 项目切换由我们手动控制以钉死"复用跨项目")
    cfg = requests.get(f"{API}/api/v1/mes/inbound/config", timeout=10).json()
    cfg.update({
        "enabled": True,
        "create_work_order_on_task": True,
        "start_detection_on_task": False,
        "switch_project_on_task": False,
        "supersede_previous_task": False,
        "task_info_display": {"show_task_no": True, "show_product_code": True,
                              "show_step_code": True, "show_operator": True},
    })
    ur = requests.put(f"{API}/api/v1/mes/inbound/config", json=cfg, timeout=10)
    run.step("入站配置就绪", ur.status_code == 200, f"HTTP {ur.status_code}")

    # 两条出站连接, 模板与川南预设同款 ({order.*} 路径)
    common_tpl = {
        "TaskNo": "{order.order_no}",
        "ProductCode": "{order.product_code}",
        "StepCode": "{order.extra_data.inbound.step_code}",
        "Operator": "{order.extra_data.inbound.operator}",
    }
    wr = requests.post(f"{API}/api/v1/mes/gateway/connections", json={
        "name": "cnru-warning", "adapter_type": "rest", "enabled": True,
        "push_events": ["cycle_end"], "retry_count": 0,
        "config": {"url": f"http://127.0.0.1:{MOCK_PORT}/warning/report",
                   "method": "POST", "push_on_result": ["NG"],
                   "template": {**common_tpl, "WarningText": "{cycle.ng_reason}"}},
    }, timeout=10)
    cr = requests.post(f"{API}/api/v1/mes/gateway/connections", json={
        "name": "cnru-complete", "adapter_type": "rest", "enabled": True,
        "push_events": ["cycle_end"], "retry_count": 0,
        "config": {"url": f"http://127.0.0.1:{MOCK_PORT}/task/complete",
                   "method": "POST",
                   "template": {**common_tpl, "IsComplete": True,
                                "Result": "{cycle.result}"}},
    }, timeout=10)
    run.step("出站连接就绪(报警仅NG + 完工全推)",
             wr.status_code == 200 and cr.status_code == 200,
             f"warn={wr.status_code} comp={cr.status_code}")
    return pa, pb


def reproduce_stale_order(pa, pb):
    """复刻客户历史: 单子建在项目 A 上且已被结案, 然后现场切到项目 B。

    07-17 现场钉死的完整陷阱 = 绑定过期 + 状态终态两层叠加:
    单子第一次建在别的项目上, 之后又被顶替/手工结案成 completed,
    老代码复用时既不重绑项目、也不复活状态, 响应还回"已就绪"。
    """
    ar = requests.post(f"{API}/api/v1/projects/{pa}/activate", timeout=30)
    run.step("激活项目 A", ar.status_code == 200, f"HTTP {ar.status_code}")
    r = requests.post(f"{API}/api/v1/mes/inbound/task", json={
        "TaskNo": TASK_NO, "ProductCode": "rocket",
        "StepCode": "0.9", "Operator": "old-op"}, timeout=10)
    ok = r.status_code in (200, 201) and r.json().get("code") == 0
    run.step("项目A时期建单(历史遗留态)", ok, f"body={r.text[:100]}")

    # 现场等价操作: 这张单被结案 (被别的任务顶替 / 手工完工)
    orders = requests.get(f"{API}/api/v1/mes/orders", timeout=10).json().get("items", [])
    oid = next((o["id"] for o in orders if o.get("order_no") == TASK_NO), None)
    cr = requests.post(f"{API}/api/v1/mes/orders/{oid}/status",
                       json={"status": "completed"}, timeout=10)
    run.step("工单被结案(复刻终态陷阱)", cr.status_code == 200,
             f"order_id={oid} HTTP {cr.status_code}")

    ar = requests.post(f"{API}/api/v1/projects/{pb}/activate", timeout=30)
    run.step("切到项目 B(复刻现场换型)", ar.status_code == 200, f"HTTP {ar.status_code}")


def start_detection_on_b(pb):
    r = requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario_json": {"name": "cn-reuse", "fps": FPS, "timeline": TIMELINE},
        "channel": 0, "with_project": False}, timeout=15)
    run.step("起 synthetic 剧本(OK周期+缺B周期)", r.status_code == 200,
             f"HTTP {r.status_code}")
    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25}, timeout=30)
    run.step("项目 B 上开始检测", r.status_code == 200, f"HTTP {r.status_code}")
    time.sleep(2)
    mes = requests.get(f"{API}/api/v1/source/detection/results?channel=0",
                       timeout=5).json().get("mes") or {}
    run.step("开工前工位未挂单(客户症状前置: 复用单绑定过期挂不上)",
             not mes.get("order"), f"order={bool(mes.get('order'))}")


def visible_post_and_verify():
    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4)
        run.shot(page, "01_开工前_无四要素")

        # 同任务号再开工 → 复用分支 (修复点)
        r = requests.post(f"{API}/api/v1/mes/inbound/task", json={
            "TaskNo": TASK_NO, "ProductCode": "rocket",
            "StepCode": "1.1", "Operator": "wyf"}, timeout=10)
        ok = r.status_code in (200, 201) and r.json().get("code") == 0
        run.step("同任务号再开工(复用工单)", ok, f"body={r.text[:160]}")
        run.step("响应说明终态单已复活", "重新开工" in r.text, f"body={r.text[:160]}")

        # UI 会把长值截断显示 (如 "task-pr..."), 匹配用前缀而非全串
        probe = TASK_NO[:7]
        deadline, appeared, txt = time.time() + 15, False, ""
        while time.time() < deadline:
            txt = page.evaluate("document.body.innerText")
            if probe in txt and "任务号" in txt:
                appeared = True
                break
            time.sleep(0.5)
        run.shot(page, "02_开工后_四要素上屏")
        run.step("复用单重绑后四要素上屏(问题1修复)", appeared,
                 f"任务号标签={'任务号' in txt} 值前缀={probe in txt}")
        if appeared:
            run.step("工序工步为最新报文值 1.1", "1.1" in txt)
            run.step("操作员为最新报文值 wyf", "wyf" in txt)

        # 等两个周期结算完 (剧本 24s 处结算周期1 OK, 30s 处结算周期2 NG)
        t0 = time.time()
        while time.time() - t0 < 45:
            if RECEIVED["warning"] and len(RECEIVED["complete"]) >= 2:
                break
            time.sleep(1)
        run.shot(page, "03_周期结算后")

        real = filter_console_errors(console_errs)
        run.step("控制台无前端逻辑报错", not real, f"真报错={real[:3]}")
        ctx.close()
        browser.close()


def verify_pushes():
    comp, warn = RECEIVED["complete"], RECEIVED["warning"]
    run.step("完工推送已收到", len(comp) >= 1, f"n={len(comp)}")
    if comp:
        c = comp[0]
        four_ok = (c.get("TaskNo") == TASK_NO and c.get("ProductCode") == "rocket"
                   and c.get("StepCode") == "1.1" and c.get("Operator") == "wyf")
        run.step("完工报文四要素非 null(问题2修复)", four_ok, json.dumps(c, ensure_ascii=False)[:200])

    ok_warned = [w for w in warn if str(w.get("WarningText", "")) == "顺序正确完成"]
    run.step("合格周期未打到报警接口(问题3修复)", not ok_warned,
             f"warning n={len(warn)} 其中合格文案={len(ok_warned)}")
    ng_warns = [w for w in warn if w.get("WarningText") and w.get("WarningText") != "顺序正确完成"]
    run.step("NG 周期报警已推且带原因", len(ng_warns) >= 1,
             json.dumps(ng_warns[:1], ensure_ascii=False)[:200])
    if ng_warns:
        w = ng_warns[0]
        run.step("报警报文四要素非 null(问题2修复)",
                 w.get("TaskNo") == TASK_NO and w.get("Operator") == "wyf",
                 json.dumps(w, ensure_ascii=False)[:200])


def cleanup():
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)
    for c in requests.get(f"{API}/api/v1/mes/gateway/connections", timeout=10).json():
        if str(c.get("name", "")).startswith("cnru-"):
            requests.delete(f"{API}/api/v1/mes/gateway/connections/{c['id']}", timeout=10)
    run.step("清理完成", True)


def main():
    srv = start_mock()
    try:
        pa, pb = setup()
        reproduce_stale_order(pa, pb)
        start_detection_on_b(pb)
        visible_post_and_verify()
        verify_pushes()
        cleanup()
    finally:
        srv.shutdown()
    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
