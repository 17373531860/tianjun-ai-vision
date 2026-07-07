"""
可见浏览器 UAT — 川南火工 报警闭环 + 监控页持续横幅 (v3.28.x 对接增强)

覆盖客户场景:
  1) 入站配置 (报警闭环 + 横幅外观) 保存后真落库可读回         [配置可自填可改]
  2) 外部系统开工 (POST /mes/inbound/task) → 回 code=0          [开工入站]
  3) 在途报警登记 → 监控页持续横幅出现 (人眼 + 截图 + 视频)     [横幅显示]
  4) 外部回推消除 (POST /mes/inbound/alarm/clear) → 横幅自动撤   [报警闭环消除]
  5) 消除命令匹配不到 → 回 40007 (alarm_not_found)             [错误码]
  6) UI 改横幅停靠位置并保存 → GET 配置确认已变                 [UI→后端双向]

证据三件套: /tmp/uat_video/*.webm + /tmp/uat_shots/*.png + /tmp/uat_chuannan.log

前置: 隔离栈 后端 8011 (RUNTIME_MODE=test, TIANJUN_DATA_DIR=/tmp/uat_chuannan_data)
      前端 6002 (VITE_API_BASE_URL=http://127.0.0.1:8011/api/v1)

跑法: python tests/uat/uat_20260627_chuannan_alarm_closure.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("TIANJUN_DATA_DIR", "/tmp/uat_chuannan_data")
os.environ.setdefault("BACKEND_SKIP_INIT", "1")
os.environ.setdefault("RUNTIME_MODE", "test")

import json
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8011/api/v1"
FRONTEND = "http://localhost:6001"
SHOTS = "/tmp/uat_shots"
VIDEO = "/tmp/uat_video"
LOG = "/tmp/uat_chuannan.log"

os.makedirs(SHOTS, exist_ok=True)
os.makedirs(VIDEO, exist_ok=True)

ALARM = {
    "task_no": "TASK-UAT-001",
    "product_code": "PROD-X9-02",
    "step_code": "1.1",
    "operator": "张三",
    "warning_text": "XXX零件装配顺序错误",
}

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": str(detail)}
    steps_log.append(rec)
    print(f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}", flush=True)


def safe_shot(page, path):
    """监控页有常驻 MJPEG/canvas 动画, 整页截图易超时; 视口 + 关动画 + 短超时 + 不致命。"""
    try:
        page.screenshot(path=path, full_page=False, animations="disabled", timeout=12000)
    except Exception as e:
        print(f"   (截图跳过 {os.path.basename(path)}: {str(e)[:60]})", flush=True)


# ──────── DB 直写: 模拟"外部系统已收到报警并登记在途" ────────
def seed_alarm():
    import backend.models.models  # noqa: F401  注册全部 ORM 映射, 否则 WorkOrder→Project 关系解析失败
    import backend.models.mes_models  # noqa: F401
    import backend.models.auth_models  # noqa: F401
    import backend.models.export_models  # noqa: F401
    import backend.models.plugin_models  # noqa: F401
    from backend.db.database import SessionLocal
    from backend.services.external_alarm import record_active_alarm
    db = SessionLocal()
    try:
        r = record_active_alarm(db, ALARM, event_type="cycle_end", channel_id=0, dedup_sec=0)
        db.commit()
        return r
    finally:
        db.close()


# ──────── Phase A: API 契约 ────────
def phase_a_api():
    cfg = {
        "enabled": True,
        "field_map": {
            "task_no": "TaskNo", "product_code": "ProductCode",
            "step_code": "StepCode", "operator": "Operator", "begin_time": "BeginTime",
        },
        "required_fields": ["task_no"],
        "create_work_order_on_task": True,
        "alarm_event_name": ["cycle_end"],
        "alarm_record_on_results": ["NG"],
        "alarm_dedup_sec": 5,
        "alarm_clear_match_fields": ["task_no", "product_code", "step_code", "operator"],
        "alarm_banner": {
            "enabled": True, "position": "top", "color": "#dc2626",
            "poll_interval_sec": 3, "show_task_no": True, "show_product_code": True,
            "show_step_code": True, "show_operator": True, "show_time": True,
        },
    }
    r = requests.put(f"{API}/mes/inbound/config", json=cfg, timeout=10)
    step("A1 PUT 入站配置(报警闭环+横幅)", r.status_code == 200, f"http={r.status_code}")

    g = requests.get(f"{API}/mes/inbound/config", timeout=10).json()
    ok = (g.get("alarm_event_name") == ["cycle_end"]
          and (g.get("alarm_banner") or {}).get("color") == "#dc2626"
          and (g.get("alarm_banner") or {}).get("position") == "top")
    step("A2 GET 配置确认已落库(可读回可改)", ok,
         f"event={g.get('alarm_event_name')} banner={g.get('alarm_banner', {}).get('position')}")

    # 开工入站
    body = {"TaskNo": ALARM["task_no"], "ProductCode": ALARM["product_code"],
            "StepCode": ALARM["step_code"], "Operator": ALARM["operator"],
            "BeginTime": "2026-06-27T08:30:00"}
    r = requests.post(f"{API}/mes/inbound/task", json=body, timeout=10)
    rj = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    step("A3 外部开工 POST /inbound/task → code=0", rj.get("code") == 0, f"resp={rj}")

    # 清掉历史在途, 再登记一条
    requests.post(f"{API}/mes/inbound/alarm/clear", json=body, timeout=10)
    seed_alarm()
    lst = requests.get(f"{API}/mes/inbound/active-alarms", timeout=10).json()
    found = any(a.get("task_no") == ALARM["task_no"] for a in lst)
    step("A4 登记在途报警 → GET active-alarms 可见", found, f"count={len(lst)}")

    # 消除匹配不到 → 40007
    nomatch = {"TaskNo": "NOPE-999", "ProductCode": "X", "StepCode": "Y", "Operator": "Z"}
    r = requests.post(f"{API}/mes/inbound/alarm/clear", json=nomatch, timeout=10)
    rj = r.json()
    step("A5 消除匹配不到 → 40007", rj.get("code") == 40007, f"resp={rj}")

    # 正确消除 → code=0, 台账清空
    r = requests.post(f"{API}/mes/inbound/alarm/clear", json=body, timeout=10)
    rj = r.json()
    lst2 = requests.get(f"{API}/mes/inbound/active-alarms", timeout=10).json()
    step("A6 正确消除 → code=0 且台账清空", rj.get("code") == 0 and len(lst2) == 0,
         f"resp={rj} remain={len(lst2)}")

    # A7 健康检查 (川南协议 GET .../health → {code:0,message:ok})
    hj = requests.get(f"{API}/mes/inbound/health", timeout=10).json()
    step("A7 健康检查 → {code:0,message:ok}", hj.get("code") == 0 and hj.get("message") == "ok",
         f"resp={hj}")


# ──────── Phase B/C: 可见浏览器 ────────
def phase_browser():
    # 重新播种一条, 供横幅显示
    requests.post(f"{API}/mes/inbound/alarm/clear",
                  json={"TaskNo": ALARM["task_no"], "ProductCode": ALARM["product_code"],
                        "StepCode": ALARM["step_code"], "Operator": ALARM["operator"]}, timeout=10)
    seed_alarm()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  record_video_dir=VIDEO,
                                  record_video_size={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.on("console", lambda m: None)

        # B1 监控页 → 横幅出现
        page.goto(f"{FRONTEND}/#/monitor")
        page.wait_for_load_state("networkidle")
        time.sleep(4.5)  # 给横幅两个轮询周期 (3s) 拉到在途报警
        banner_on = page.locator(".ext-alarm-banner").count() > 0
        safe_shot(page, f"{SHOTS}/B1_monitor_banner_on.png")
        step("B1 监控页持续横幅出现(在途报警)", banner_on,
             f"banner_dom={banner_on}")
        if banner_on:
            txt = page.locator(".ext-alarm-banner").inner_text()
            step("B1b 横幅含报警文本+任务号", ALARM["task_no"] in txt and "装配顺序错误" in txt,
                 detail=txt.replace("\n", " ")[:120])

        # B2 外部消除 → 横幅撤
        requests.post(f"{API}/mes/inbound/alarm/clear",
                      json={"TaskNo": ALARM["task_no"], "ProductCode": ALARM["product_code"],
                            "StepCode": ALARM["step_code"], "Operator": ALARM["operator"]}, timeout=10)
        time.sleep(5)  # 等横幅下一轮轮询撤
        banner_off = page.locator(".ext-alarm-banner").count() == 0
        safe_shot(page, f"{SHOTS}/B2_monitor_banner_off.png")
        step("B2 外部消除后横幅自动撤", banner_off, f"banner_dom={page.locator('.ext-alarm-banner').count()}")

        # C 配置面板截图 (供教程) + UI→后端双向
        # 用 domcontentloaded (监控页 MJPEG 流让 networkidle 永不达)
        page.goto(f"{FRONTEND}/#/mes", wait_until="domcontentloaded")
        time.sleep(3)
        try:
            page.wait_for_selector("button:has-text('工单接收')", timeout=15000)
            page.locator("button:has-text('工单接收')").first.click()
            time.sleep(1.5)
            safe_shot(page, f"{SHOTS}/C1a_inbound_top.png")
            # 滚到"报警闭环"分隔区, 截报警闭环 + 横幅配置
            try:
                page.get_by_text("报警闭环", exact=False).first.scroll_into_view_if_needed()
                time.sleep(0.6)
                safe_shot(page, f"{SHOTS}/C1b_alarm_closure.png")
                page.get_by_text("监控页报警横幅", exact=False).first.scroll_into_view_if_needed()
                time.sleep(0.6)
                safe_shot(page, f"{SHOTS}/C1c_banner_config.png")
            except Exception:
                pass
            # C1d 展开"按产品码切项目" → 露出"按项目名自动匹配"开关 + 对照表 (截图给教程)
            try:
                sw = page.locator(".el-form-item:has-text('按产品码切项目') .el-switch").first
                sw.scroll_into_view_if_needed()
                time.sleep(0.4)
                sw.click()
                time.sleep(0.8)
                page.get_by_text("按项目名自动匹配", exact=False).first.scroll_into_view_if_needed()
                time.sleep(0.5)
                safe_shot(page, f"{SHOTS}/C1d_project_match.png")
                name_match_visible = page.get_by_text("按项目名自动匹配", exact=False).count() > 0
                step("C1d 产品码切项目+按项目名自动匹配开关可见", name_match_visible)
                sw.click()  # 还原, 不污染后续保存
                time.sleep(0.4)
            except Exception as e:
                step("C1d 按项目名自动匹配开关截图", False, str(e)[:80])
            step("C1 入站接收面板(含报警闭环+横幅配置)截图", True)
        except Exception as e:
            step("C1 入站接收面板截图", False, str(e)[:80])

        # C2 UI 改横幅停靠=底部 → 保存 → GET 确认
        try:
            page.locator("label.el-radio:has-text('底部')").first.click()
            time.sleep(0.5)
            page.locator("button:has-text('保存配置')").first.click()
            time.sleep(2)
            g = requests.get(f"{API}/mes/inbound/config", timeout=10).json()
            pos = (g.get("alarm_banner") or {}).get("position")
            step("C2 UI改横幅位置=底部→保存→后端确认", pos == "bottom", f"position={pos}")
            safe_shot(page, f"{SHOTS}/C2_banner_pos_saved.png")
        except Exception as e:
            step("C2 UI改横幅位置双向验证", False, str(e)[:80])

        # C3 外部对接 → 新建连接 → 川南报警预设 → 截图(截图开关+模板)
        try:
            # 重新进 MES 页 (清掉上一步保存后的瞬时态), 再切外部对接
            page.goto(f"{FRONTEND}/#/mes", wait_until="domcontentloaded")
            time.sleep(3)
            page.wait_for_selector("button:has-text('外部对接')", timeout=15000)
            page.locator("button:has-text('外部对接')").first.click()
            time.sleep(1.2)
            page.locator("button:has-text('新建连接')").first.click()
            time.sleep(1.0)
            # 选预设模板
            page.locator(".el-select").filter(has_text="选择预设模板").first.click()
            time.sleep(0.6)
            page.locator(".el-select-dropdown__item", has_text="川南报警上报").first.click()
            time.sleep(1.0)
            tpl = page.locator(".template-editor textarea").input_value()
            ok = "TaskNo" in tpl and "Image" in tpl and "snapshot.image_base64" in tpl
            step("C3 出站新建连接套用川南报警预设(含截图字段)", ok, detail=tpl.replace("\n", " ")[:120])
            safe_shot(page, f"{SHOTS}/C3_gateway_chuannan_preset.png")
        except Exception as e:
            step("C3 出站川南报警预设截图", False, str(e)[:120])

        ctx.close()
        browser.close()


def main():
    print("=" * 60, flush=True)
    print("UAT: 川南报警闭环 + 监控页横幅", flush=True)
    print("=" * 60, flush=True)
    phase_a_api()
    phase_browser()

    failed = [s for s in steps_log if not s["ok"]]
    summary = {"total": len(steps_log), "failed": len(failed), "steps": steps_log}
    with open(LOG, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("=" * 60, flush=True)
    print(f"完成: {len(steps_log)} 步, 失败 {len(failed)}", flush=True)
    print(f"failed: {len(failed)}", flush=True)
    print(f"证据: 视频 {VIDEO}/  截图 {SHOTS}/  日志 {LOG}", flush=True)


if __name__ == "__main__":
    main()
