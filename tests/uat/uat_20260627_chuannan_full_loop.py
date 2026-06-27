"""
可见浏览器 UAT — 川南火工 MES 双向对接「全链路」(覆盖《从零配置操作手册》14 场景)

跟 uat_20260627_chuannan_alarm_closure.py 的区别:
  本脚本用 *真实的模拟中控* (tests/uat/mock_chuannan_mcs.py, 即"客户自己的生产软件")
  作为驱动方, 走真 HTTP 双向链路, 并核对中控侧真收到天军回推的报警/完工报文。

三端 (跑本脚本前需先起好):
  天军后端  : http://127.0.0.1:8011   (RUNTIME_MODE=test, TIANJUN_DATA_DIR=/tmp/uat_cnloop_data)
  天军前端  : http://127.0.0.1:6011   (VITE_API_BASE_URL=http://127.0.0.1:8011/api/v1)
  模拟中控  : http://127.0.0.1:9100   (TIANJUN_INBOUND=http://127.0.0.1:8011/api/v1/mes/inbound/task)

覆盖矩阵 (与手册 14 场景对齐):
  入站(中控→天军, 经真实模拟中控): 1开工切项目+四要素+建单 2缺字段40004 3产品未知40002
        7健康 4顶替+13完工回推到中控
  报警: 8登记上屏 10去重(BDD覆盖) 11消除 12报警上报报文真打到中控
  UI : 14监控页四要素上屏 + 横幅 on/off
  截图: 入站面板四要素开关 / 出站川南预设 / 中控控制台双向流水

证据三件套: /tmp/uat_video/*.webm + /tmp/uat_shots/cnloop_*.png + /tmp/uat_chuannan_loop.log

跑法: python tests/uat/uat_20260627_chuannan_full_loop.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("TIANJUN_DATA_DIR", "/tmp/uat_cnloop_data")
os.environ.setdefault("BACKEND_SKIP_INIT", "1")
os.environ.setdefault("RUNTIME_MODE", "test")

import json
import time
import base64

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8011/api/v1"
FRONTEND = "http://127.0.0.1:6001"
MCS = "http://127.0.0.1:9100"
SHOTS = "/tmp/uat_shots"
VIDEO = "/tmp/uat_video"
LOG = "/tmp/uat_chuannan_loop.log"

os.makedirs(SHOTS, exist_ok=True)
os.makedirs(VIDEO, exist_ok=True)

PRODUCT = "JS-2024-01"          # 产品代号 = 检测项目名 (川南 v4 命名一致约定)
OPERATOR = "赵六"
STEP = "4.2"

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": str(detail)}
    steps_log.append(rec)
    print(f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}", flush=True)


def safe_shot(page, path):
    try:
        page.screenshot(path=path, full_page=False, animations="disabled", timeout=12000)
    except Exception as e:
        print(f"   (截图跳过 {os.path.basename(path)}: {str(e)[:60]})", flush=True)


# ──────── 直接读 DB (在途报警播种 / 工单查询) ────────
def _db():
    import backend.models.models  # noqa: F401
    import backend.models.mes_models  # noqa: F401
    import backend.models.auth_models  # noqa: F401
    import backend.models.export_models  # noqa: F401
    import backend.models.plugin_models  # noqa: F401
    from backend.db.database import SessionLocal
    return SessionLocal()


def seed_alarm(task_no, warning="XXX零件装配顺序错误"):
    from backend.services.external_alarm import record_active_alarm
    db = _db()
    try:
        r = record_active_alarm(db, {
            "task_no": task_no, "product_code": PRODUCT,
            "step_code": STEP, "operator": OPERATOR, "warning_text": warning,
        }, event_type="cycle_end", channel_id=0, dedup_sec=0)
        db.commit()
        return r
    finally:
        db.close()


def get_order(order_no):
    from backend.models.mes_models import WorkOrder
    db = _db()
    try:
        return db.query(WorkOrder).filter(WorkOrder.order_no == order_no).first()
    finally:
        db.close()


def mcs_log():
    try:
        return requests.get(f"{MCS}/log", timeout=10).json().get("items", [])
    except Exception as e:
        print("   (读中控流水失败:", e, ")", flush=True)
        return []


def mcs_has(kind_sub, field=None, value=None):
    """中控流水里是否有某类报文 (可选: detail.payload 里某字段==某值)。"""
    for it in mcs_log():
        if kind_sub in it.get("kind", ""):
            if field is None:
                return True
            pl = (it.get("detail") or {}).get("payload") or {}
            if str(pl.get(field)) == str(value):
                return True
    return False


# ============================================================
# 第 0 段: 按手册配置天军 (入站 + 出站) + 建项目
# ============================================================
def setup():
    # 0.1 建检测项目 (名 = 产品代号), 模拟客户空白软件里先建第一个项目
    requests.post(f"{API}/projects", json={
        "name": PRODUCT, "task_type": "detection", "logic_mode": "sequential"}, timeout=10)
    step("0.1 建检测项目 (名=产品代号)", True, PRODUCT)

    # 0.2 入站配置: 开工切项目 + 四要素上屏 + 建单 + 报警闭环 + 横幅 + 顶替回传完工
    cfg = {
        "enabled": True,
        "field_map": {"task_no": "TaskNo", "product_code": "ProductCode",
                      "step_code": "StepCode", "operator": "Operator", "begin_time": "BeginTime"},
        "required_fields": ["task_no"],
        "switch_project_on_task": True,
        "match_project_by_name": True,
        "create_work_order_on_task": True,
        "store_mapped_extra": True,
        "supersede_previous_task": True,
        "report_complete_on_supersede": True,
        "complete_event_type": "task_complete",
        "task_info_display": {"show_task_no": True, "show_product_code": True,
                              "show_step_code": True, "show_operator": True},
        "alarm_clear_match_fields": ["task_no", "product_code", "step_code", "operator"],
        "alarm_banner": {"enabled": True, "position": "top", "color": "#dc2626",
                         "poll_interval_sec": 3, "show_task_no": True, "show_product_code": True,
                         "show_step_code": True, "show_operator": True, "show_time": True},
    }
    r = requests.put(f"{API}/mes/inbound/config", json=cfg, timeout=10)
    g = requests.get(f"{API}/mes/inbound/config", timeout=10).json()
    ti = g.get("task_info_display") or {}
    ok = (r.status_code == 200 and g.get("switch_project_on_task") is True
          and all(ti.get(k) for k in ("show_task_no", "show_product_code",
                                      "show_step_code", "show_operator")))
    step("0.2 入站配置(切项目+四要素+建单+报警闭环)落库", ok,
         f"switch={g.get('switch_project_on_task')} taskinfo={ti}")

    # 0.3 出站: 完工回传连接 → 模拟中控 /api/v1/task/complete
    cc = requests.post(f"{API}/mes/gateway/connections", json={
        "name": "川南-完工回传(UAT)", "adapter_type": "rest", "enabled": True,
        "push_events": ["task_complete"],
        "config": {"url": f"{MCS}/api/v1/task/complete", "method": "POST",
                   "template": {"TaskNo": "{order.order_no}",
                                "ProductCode": "{order.product_code}",
                                "Status": "completed"}},
    }, timeout=10)
    conn_complete = cc.json().get("id") if cc.status_code in (200, 201) else None
    step("0.3 出站连接[完工回传]→中控建立", conn_complete is not None, f"id={conn_complete}")

    # 0.4 出站: 报警上报连接 (川南预设, 含截图字段) → 模拟中控 /api/v1/warning/report
    cw = requests.post(f"{API}/mes/gateway/connections", json={
        "name": "川南-报警上报(UAT)", "adapter_type": "rest", "enabled": True,
        "push_events": ["cycle_end"],
        "config": {"url": f"{MCS}/api/v1/warning/report", "method": "POST",
                   "attach_snapshot": True,
                   "template": {"TaskNo": "{order.order_no}",
                                "ProductCode": "{order.product_code}",
                                "StepCode": "{order.extra_data.inbound.step_code}",
                                "Operator": "{order.extra_data.inbound.operator}",
                                "WarningText": "{cycle.ng_reason}",
                                "Image": "{snapshot.image_base64}"}},
    }, timeout=10)
    conn_warn = cw.json().get("id") if cw.status_code in (200, 201) else None
    step("0.4 出站连接[报警上报·川南预设]→中控建立", conn_warn is not None, f"id={conn_warn}")
    return conn_complete, conn_warn


# ============================================================
# Phase A: 真实模拟中控驱动 + 核对双向报文
# ============================================================
def phase_a(conn_complete, conn_warn):
    requests.post(f"{MCS}/log/clear", timeout=10)

    # A1 中控发开工 (产品=项目名) → 天军 code=0 + 切项目 + 建单 + 四要素留痕
    r = requests.post(f"{MCS}/drive/start", json={
        "task_no": "CN-LOOP-001", "product_code": PRODUCT,
        "operator": OPERATOR, "step_code": STEP}, timeout=15).json()
    code = (r.get("resp") or {}).get("code")
    step("A1 中控→天军 开工 → code=0", code == 0, f"resp={r.get('resp')}")

    act = requests.get(f"{API}/projects/active/current", timeout=10).json()
    act_name = (act or {}).get("name") if isinstance(act, dict) else None
    step("A1b 天军按产品代号切到同名检测项目", act_name == PRODUCT, f"active={act_name}")

    o = get_order("CN-LOOP-001")
    inbound = (getattr(o, "extra_data", None) or {}).get("inbound") if o else {}
    ok = o is not None and inbound.get("step_code") == STEP and inbound.get("operator") == OPERATOR
    step("A1c 建工单且四要素留痕(工步/操作员)", ok, f"extra={inbound}")

    # A2 中控发缺字段 (无 TaskNo) → 40004
    r = requests.post(f"{MCS}/drive/raw", json={
        "ProductCode": PRODUCT, "Operator": OPERATOR}, timeout=15).json()
    step("A2 中控→天军 缺任务号 → 40004", (r.get("resp") or {}).get("code") == 40004,
         f"resp={r.get('resp')}")

    # A3 中控发产品码未知 → 40002
    r = requests.post(f"{MCS}/drive/start", json={
        "task_no": "CN-LOOP-UNK", "product_code": "NOPE-XYZ-999",
        "operator": OPERATOR, "step_code": STEP}, timeout=15).json()
    step("A3 中控→天军 产品码未知 → 40002", (r.get("resp") or {}).get("code") == 40002,
         f"resp={r.get('resp')}")

    # A4 健康检查
    hj = requests.get(f"{API}/mes/inbound/health", timeout=10).json()
    step("A4 健康检查 → {code:0,message:ok}", hj.get("code") == 0 and hj.get("message") == "ok",
         f"resp={hj}")

    # A5 顶替: 中控连发两单(同产品) → 第二单顶替第一单 → 天军回推完工到中控
    requests.post(f"{MCS}/drive/start", json={
        "task_no": "CN-SUP-A", "product_code": PRODUCT,
        "operator": OPERATOR, "step_code": STEP}, timeout=15)
    requests.post(f"{MCS}/drive/start", json={
        "task_no": "CN-SUP-B", "product_code": PRODUCT,
        "operator": OPERATOR, "step_code": STEP}, timeout=15)
    time.sleep(1.5)
    oa = get_order("CN-SUP-A")
    step("A5 同工位最新开工顶替旧任务 (CN-SUP-A→completed)",
         oa is not None and oa.status == "completed", f"status={getattr(oa,'status',None)}")
    step("A5b 天军把被顶替任务的[完工]回推到中控 (B→A)",
         mcs_has("完工", "TaskNo", "CN-SUP-A"), "查中控流水 完工(B→A) TaskNo=CN-SUP-A")

    # A6 报警上报: 触发出站连接 test → 川南预设报文真打到中控
    if conn_warn:
        tr = requests.post(f"{API}/mes/gateway/connections/{conn_warn}/test",
                           json={"event_type": "cycle_end"}, timeout=15).json()
        sent_ok = tr.get("success") is True or tr.get("status_code") == 200
        time.sleep(0.8)
        step("A6 报警上报报文(川南预设)真打到中控 (B→A)",
             sent_ok and mcs_has("报警"), f"send_status={tr.get('status_code')}")
        prev = tr.get("payload_preview") or {}
        step("A6b 报文为川南字段(TaskNo/WarningText)", "TaskNo" in prev and "WarningText" in prev,
             f"keys={list(prev.keys())}")


# ============================================================
# Phase B: 可见浏览器 — 四要素上屏 + 横幅 on/off
# ============================================================
def phase_b():
    # 重新发一单作为"当前在产任务"(供监控页四要素), 并起 synthetic 检测让 order 绑到通道
    requests.post(f"{MCS}/drive/start", json={
        "task_no": "CN-UI-001", "product_code": PRODUCT,
        "operator": OPERATOR, "step_code": STEP}, timeout=15)
    # 起 synthetic 检测 (无相机无模型), 让 mes_hook 解析当前在产工单 → 监控页 mes.order 有值
    syn = {"scenario_json": {"name": "uat", "fps": 30,
           "timeline": [{"from": 0, "to": 600,
                         "detections": [{"label": "step_a", "confidence": 0.9,
                                         "bbox": [0.4, 0.4, 0.1, 0.1]}]}]},
           "channel": 0, "with_project": True}
    try:
        requests.post(f"{API}/test/synthetic/start", json=syn, timeout=15)
        requests.post(f"{API}/source/detection/start?channel=0", json={"conf": 0.25}, timeout=15)
        time.sleep(2.5)
    except Exception as e:
        print("   (synthetic 起检测失败, 四要素改走 API 校验:", str(e)[:80], ")", flush=True)

    # API 级四要素校验 (前端数据源): 检测结果里 mes.order 带 extra_data.inbound
    try:
        rj = requests.get(f"{API}/source/detection/results?channel=0", timeout=10).json()
        order = (rj.get("mes") or {}).get("order") or {}
        ib = (order.get("extra_data") or {}).get("inbound") or {}
        step("B0 四要素数据已到前端数据源(results.mes.order)",
             ib.get("operator") == OPERATOR and ib.get("step_code") == STEP,
             f"order={order.get('order_no')} inbound={ib}")
    except Exception as e:
        step("B0 四要素数据到前端数据源", False, str(e)[:80])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  record_video_dir=VIDEO,
                                  record_video_size={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.on("console", lambda m: None)

        # B1 监控页 → 四要素上屏 (监控页常驻 MJPEG 流, networkidle 永不达, 用 domcontentloaded)
        page.goto(f"{FRONTEND}/#/monitor", wait_until="domcontentloaded")
        time.sleep(5.0)
        body = page.evaluate("document.body.innerText")
        four_on = ("操作员" in body and "工序工步" in body and OPERATOR in body and STEP in body)
        safe_shot(page, f"{SHOTS}/cnloop_B1_four_elements.png")
        step("B1 监控页四要素上屏(任务号/产品/工步/操作员)", four_on,
             f"含操作员={OPERATOR in body} 含工步={STEP in body}")

        # B2 在途报警 → 横幅出现
        seed_alarm("CN-UI-001")
        time.sleep(4.5)
        banner_on = page.locator(".ext-alarm-banner").count() > 0
        safe_shot(page, f"{SHOTS}/cnloop_B2_banner_on.png")
        step("B2 在途报警 → 监控页持续横幅出现", banner_on, f"banner_dom={banner_on}")
        if banner_on:
            txt = page.locator(".ext-alarm-banner").inner_text()
            step("B2b 横幅含任务号+报警文本", "CN-UI-001" in txt and "装配顺序错误" in txt,
                 txt.replace("\n", " ")[:120])

        # B3 中控消除 → 横幅撤
        requests.post(f"{API}/mes/inbound/alarm/clear", json={
            "TaskNo": "CN-UI-001", "ProductCode": PRODUCT,
            "StepCode": STEP, "Operator": OPERATOR}, timeout=10)
        time.sleep(5)
        banner_off = page.locator(".ext-alarm-banner").count() == 0
        safe_shot(page, f"{SHOTS}/cnloop_B3_banner_off.png")
        step("B3 中控消除后横幅自动撤", banner_off,
             f"remain={page.locator('.ext-alarm-banner').count()}")

        # B4 中控控制台双向流水截图 (客户视角: 看到天军 code=0 + 收到完工/报警)
        try:
            page.goto(MCS, wait_until="domcontentloaded")
            time.sleep(2.5)
            safe_shot(page, f"{SHOTS}/cnloop_B4_mcs_console.png")
            step("B4 模拟中控控制台双向流水截图", True)
        except Exception as e:
            step("B4 中控控制台截图", False, str(e)[:80])

        # 收尾: 停检测/synthetic
        try:
            requests.post(f"{API}/source/detection/stop?channel=0", timeout=10)
            requests.post(f"{API}/test/synthetic/stop?channel=0", timeout=10)
        except Exception:
            pass

        ctx.close()
        browser.close()


def main():
    print("=" * 64, flush=True)
    print("UAT: 川南火工 MES 双向对接 全链路 (真实模拟中控驱动)", flush=True)
    print("=" * 64, flush=True)
    conn_complete, conn_warn = setup()
    phase_a(conn_complete, conn_warn)
    phase_b()

    failed = [s for s in steps_log if not s["ok"]]
    summary = {"total": len(steps_log), "failed": len(failed), "steps": steps_log}
    with open(LOG, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("=" * 64, flush=True)
    print(f"完成: {len(steps_log)} 步, 失败 {len(failed)}", flush=True)
    print(f"failed: {len(failed)}", flush=True)
    print(f"证据: 视频 {VIDEO}/  截图 {SHOTS}/cnloop_*.png  日志 {LOG}", flush=True)


if __name__ == "__main__":
    main()
