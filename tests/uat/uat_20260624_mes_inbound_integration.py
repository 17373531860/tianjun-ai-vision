#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M6 全链路 UAT —— 天军入站对接 × 川南中控模拟系统。

前置(本脚本不负责启动, 由调用方起好):
  - 隔离后端 8011 (鉴权关, 临时库 /tmp/uat_mcs_data)
  - 川南中控模拟系统 9100 (tests/uat/mock_chuannan_mcs.py, 入站指向 8011)
  - 前端 6011 (指向 8011, 仅 Phase B 浏览器需要)

两阶段:
  Phase A  API 契约 + 真传数据闭环(经模拟系统收发, 不是脚本直拼)
  Phase B  可见浏览器逐一确认「工单接收」面板所有新 UI 控件真渲染 + 保存往返

产物: /tmp/uat_shots/*.png  /tmp/uat_video/*.webm  /tmp/uat_run.log
"""
import os, sys, time, json, datetime
import requests

API = os.environ.get("UAT_API", "http://127.0.0.1:8011")
MCS = os.environ.get("UAT_MCS", "http://127.0.0.1:9100")
FE = os.environ.get("UAT_FE", "http://127.0.0.1:6011")
SHOTS = "/tmp/uat_shots"
VIDEO = "/tmp/uat_video"
os.makedirs(SHOTS, exist_ok=True)
os.makedirs(VIDEO, exist_ok=True)

PROD_A = "PROD-X9-02"   # → 项目 1
PROD_B = "PROD-Y3-01"   # → 项目 3
PROJ_A = 1
PROJ_B = 3

_log = []
def step(label, ok, detail=""):
    _log.append({"i": len(_log)+1, "label": label, "ok": bool(ok), "detail": str(detail)})
    print(f"[{'OK' if ok else '!!'}] {len(_log):02d}. {label}  {detail}", flush=True)

def _active_project():
    try:
        r = requests.get(f"{API}/api/v1/projects/active/current", timeout=5)
        if r.status_code == 200 and r.text.strip() not in ("", "null"):
            return r.json()
    except Exception:
        pass
    return None

def _find_order(task_no):
    r = requests.get(f"{API}/api/v1/mes/orders", params={"limit": 50}, timeout=5)
    items = r.json().get("items", []) if r.status_code == 200 else []
    for o in items:
        if o.get("order_no") == task_no:
            return o
    return None

def _mock_log():
    return requests.get(f"{MCS}/log", timeout=5).json().get("items", [])


# ════════════════ Phase A: API 契约 + 数据闭环 ════════════════
def phase_a():
    print("\n========== Phase A: API 契约 + 真传数据闭环 ==========\n", flush=True)

    # A0. 入站配置: 按川南协议(字段+错误码) 全量配置
    cfg = {
        "enabled": True,
        "field_map": {
            "task_no": "TaskNo", "product_code": "ProductCode",
            "step_code": "StepCode", "operator": "Operator",
            "begin_time": "BeginTime", "is_complete": "IsComplete",
        },
        "required_fields": ["task_no", "product_code"],
        "response": {
            "code_field": "code", "message_field": "message",
            "success_code": 0, "success_message": "success",
            "codes": {
                "missing_field": 40004, "duplicate": 40001,
                "unknown_product": 40002, "activate_failed": 40003,
                "internal_error": 40006, "bad_request": 40005,
            },
        },
        "switch_project_on_task": True,
        "product_project_map": {PROD_A: PROJ_A, PROD_B: PROJ_B},
        "create_work_order_on_task": True,
        "order_binding": "project",
        "store_mapped_extra": True,
        "supersede_previous_task": True,
        "supersede_scope": "project",
        "report_complete_on_supersede": True,
        "complete_event_type": "task_complete",
        "reject_duplicate_task": False,
        "complete_field": "is_complete",
        "complete_match_field": "task_no",
        "complete_match_column": "order_no",
    }
    r = requests.put(f"{API}/api/v1/mes/inbound/config", json=cfg, timeout=10)
    step("A0 入站配置已保存(川南字段+错误码)", r.status_code == 200, f"http={r.status_code}")

    # 幂等: 先删旧 __uat_ 出站连接, 避免重复推送
    try:
        for c in requests.get(f"{API}/api/v1/mes/gateway/connections", timeout=5).json():
            if "__uat_" in (c.get("name") or ""):
                requests.delete(f"{API}/api/v1/mes/gateway/connections/{c['id']}", timeout=5)
    except Exception:
        pass

    # A1. 出站网关连接: 订阅 task_complete → 推给模拟系统 /api/v1/task/complete
    conn = {
        "name": "__uat_川南完工回传",
        "adapter_type": "rest",
        "enabled": True,
        "push_events": ["task_complete"],
        "retry_count": 0,
        "config": {
            "url": f"{MCS}/api/v1/task/complete",
            "method": "POST",
            "template": {
                "TaskNo": "{order.order_no}",
                "ProductCode": "{order.product_code}",
                "StepCode": "{order.extra_data.inbound.step_code}",
                "Operator": "{order.extra_data.inbound.operator}",
                "IsComplete": True,
                "BeginTime": "{timestamp}",
            },
        },
    }
    r = requests.post(f"{API}/api/v1/mes/gateway/connections", json=conn, timeout=10)
    conn_id = None
    if r.status_code in (200, 201):
        try: conn_id = r.json().get("id")
        except Exception: pass
    step("A1 出站连接已建(task_complete→模拟系统)", r.status_code in (200, 201), f"http={r.status_code} id={conn_id}")

    requests.post(f"{MCS}/log/clear", timeout=5)

    # A2. 模拟系统「发开工 #1」(产品A) —— 经模拟系统 POST 给我方
    t1 = f"__uat_T1_{datetime.datetime.now():%H%M%S}"
    r = requests.post(f"{MCS}/drive/start",
                      json={"task_no": t1, "product_code": PROD_A, "operator": "张三", "step_code": "1.1"},
                      timeout=15)
    resp1 = r.json().get("resp", {})
    step("A2 开工#1 → 我方响应 code=0", resp1.get("code") == 0,
         f"http={resp1.get('http')} code={resp1.get('code')} msg={resp1.get('message')}")

    time.sleep(1.0)
    ap = _active_project()
    step("A3 按产品码已切到项目A", ap and ap.get("id") == PROJ_A,
         f"active={ap.get('id') if ap else None} name={ap.get('name') if ap else None}")
    o1 = _find_order(t1)
    step("A4 开工#1 已建工单 in_progress", o1 and o1.get("status") == "in_progress",
         f"order={o1.get('order_no') if o1 else None} status={o1.get('status') if o1 else None}")

    # A5. 模拟系统「缺字段」(无 TaskNo) → 应回 40004
    r = requests.post(f"{MCS}/drive/raw",
                      json={"ProductCode": PROD_A, "Operator": "张三"}, timeout=15)
    resp_bad = r.json().get("resp", {})
    step("A5 缺字段 → 我方响应 40004", resp_bad.get("code") == 40004,
         f"code={resp_bad.get('code')} msg={resp_bad.get('message')}")

    # A6. 模拟系统「发开工 #2」(同产品A, 新任务) → 顶替旧任务 #1 + 回推完工
    requests.post(f"{MCS}/log/clear", timeout=5)
    t2 = f"__uat_T2_{datetime.datetime.now():%H%M%S}"
    r = requests.post(f"{MCS}/drive/start",
                      json={"task_no": t2, "product_code": PROD_A, "operator": "李四", "step_code": "2.1"},
                      timeout=15)
    resp2 = r.json().get("resp", {})
    step("A6 开工#2 → 我方响应 code=0", resp2.get("code") == 0,
         f"code={resp2.get('code')} msg={resp2.get('message')}")

    time.sleep(1.5)
    o1b = _find_order(t1)
    step("A7 旧任务#1 已被顶替为 completed", o1b and o1b.get("status") == "completed",
         f"order#1 status={o1b.get('status') if o1b else None}")
    o2 = _find_order(t2)
    step("A8 新任务#2 当前 in_progress", o2 and o2.get("status") == "in_progress",
         f"order#2 status={o2.get('status') if o2 else None}")

    # A9. 模拟系统应收到我方回推的「完工」(针对被顶替的 #1)
    got_complete = None
    for _ in range(10):
        for e in _mock_log():
            if e.get("kind", "").startswith("完工"):
                pl = e.get("detail", {}).get("payload", {})
                if pl.get("TaskNo") == t1:
                    got_complete = pl; break
        if got_complete: break
        time.sleep(0.6)
    step("A9 模拟系统收到我方回推完工(针对#1)", got_complete is not None,
         f"完工报文={json.dumps(got_complete, ensure_ascii=False) if got_complete else '未收到'}")

    # A10. 模拟系统「完工信号」收尾 #2 (is_complete=true, 匹配 task_no)
    r = requests.post(f"{MCS}/drive/raw",
                      json={"TaskNo": t2, "ProductCode": PROD_A, "IsComplete": True}, timeout=15)
    resp_c = r.json().get("resp", {})
    time.sleep(1.0)
    o2b = _find_order(t2)
    step("A10 完工信号收尾#2 → completed", o2b and o2b.get("status") == "completed",
         f"resp code={resp_c.get('code')} order#2 status={o2b.get('status') if o2b else None}")

    return {"conn_id": conn_id, "t1": t1, "t2": t2}


# ════════════════ Phase B: 可见浏览器验 UI ════════════════
def phase_b():
    print("\n========== Phase B: 可见浏览器逐一确认「工单接收」UI ==========\n", flush=True)
    from playwright.sync_api import sync_playwright

    # 重置成"全部开关关"的确定态, 这样下面每个开关 OFF→ON 都能确定地验出条件控件。
    reset = {
        "enabled": False,
        "field_map": {"task_no": "TaskNo", "product_code": "ProductCode"},
        "required_fields": ["task_no", "product_code"],
        "switch_project_on_task": False, "create_work_order_on_task": False,
        "reject_duplicate_task": False, "supersede_previous_task": False,
        "complete_field": "", "input_format": "auto", "merge_query_params": True,
        "auth": {"enabled": False}, "response": {"format": "json"},
    }
    try:
        requests.put(f"{API}/api/v1/mes/inbound/config", json=reset, timeout=10)
    except Exception:
        pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  record_video_dir=VIDEO,
                                  record_video_size={"width": 1600, "height": 1000})
        page = ctx.new_page()

        def switch_on(label_text):
            """按精确 label 文本定位其所在 el-form-item, 确保开关处于 ON。
            用精确 label 避免子串误命中(如某项说明里含别项 label 文字)。"""
            lab = page.get_by_text(label_text, exact=True).first
            fi = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
            sw = fi.locator(".el-switch").first
            cls = sw.get_attribute("class") or ""
            if "is-checked" not in cls:
                sw.click()
            time.sleep(0.8)

        try:
            page.goto(f"{FE}/#/mes", wait_until="networkidle")
            time.sleep(2.0)
            # 进 工单接收 tab
            page.get_by_text("工单接收", exact=True).first.click()
            time.sleep(1.5)
            page.screenshot(path=f"{SHOTS}/B1_inbound_panel_top.png", full_page=True)
            body = page.evaluate("document.body.innerText")

            # B1 常驻控件存在性(这就是上次漏测的「UI 真渲染了吗」)
            base_labels = ["字段映射", "必填字段", "按产品码切项目", "开工即建工单",
                           "拒绝重复任务", "最新开工为准", "完工信号字段",
                           "响应体格式", "业务码字段", "各类失败码", "自定义响应模板"]
            missing = [x for x in base_labels if x not in body]
            step("B1 入站面板常驻控件全部渲染", not missing,
                 "缺失=" + (",".join(missing) if missing else "无"))

            # B2 条件控件: 开「最新开工为准」→ 顶替范围/顶替即回传完工 出现
            try:
                switch_on("最新开工为准")
                body2 = page.evaluate("document.body.innerText")
                ok2 = ("顶替范围" in body2) and ("顶替即回传完工" in body2)
                page.screenshot(path=f"{SHOTS}/B2_supersede_expanded.png", full_page=True)
                step("B2 开顶替→顶替范围/回传完工 条件控件出现", ok2,
                     f"顶替范围={'顶替范围' in body2} 回传完工={'顶替即回传完工' in body2}")
            except Exception as e:
                step("B2 顶替条件控件", False, f"异常 {e}")

            # B3 条件控件: 开「按产品码切项目」→ 产品代号→项目 对照表出现
            try:
                switch_on("按产品码切项目")
                body3 = page.evaluate("document.body.innerText")
                step("B3 开切项目→产品代号→项目 对照出现", "产品代号 → 项目" in body3 or "加一行对照" in body3,
                     f"found={'加一行对照' in body3}")
            except Exception as e:
                step("B3 切项目对照", False, f"异常 {e}")

            # B4 高级设置: 展开 → 入站编码格式 / 并入URL参数 / 失败→HTTP状态码 / 来源校验
            try:
                page.get_by_text("展开高级设置", exact=True).first.click()
                time.sleep(1.0)
                page.screenshot(path=f"{SHOTS}/B3_advanced_expanded.png", full_page=True)
                body4 = page.evaluate("document.body.innerText")
                adv = ["入站编码格式", "并入 URL 参数", "失败→HTTP状态码", "请求体上限", "开启来源校验"]
                miss4 = [x for x in adv if x not in body4]
                step("B4 高级设置控件全部渲染", not miss4, "缺失=" + (",".join(miss4) if miss4 else "无"))
            except Exception as e:
                step("B4 高级设置", False, f"异常 {e}")

            # B5 开来源校验 → 共享密钥头/IP白名单 出现
            try:
                switch_on("开启来源校验")
                body5 = page.evaluate("document.body.innerText")
                step("B5 开来源校验→密钥头/IP白名单 出现",
                     ("共享密钥头" in body5) and ("IP 白名单" in body5),
                     f"密钥头={'共享密钥头' in body5} IP={'IP 白名单' in body5}")
            except Exception as e:
                step("B5 来源校验控件", False, f"异常 {e}")

            # B6 响应体格式切 XML → 根标签/带?xml?头 出现
            try:
                fi = page.locator(".el-form-item", has_text="响应体格式").first
                fi.get_by_text("XML / SOAP", exact=False).first.click()
                time.sleep(0.8)
                body6 = page.evaluate("document.body.innerText")
                step("B6 响应体切XML→根标签/xml头 出现", "根标签" in body6,
                     f"根标签={'根标签' in body6}")
            except Exception as e:
                step("B6 XML响应控件", False, f"异常 {e}")

            # B7 保存往返: 点保存配置 → 成功提示
            try:
                # 切回 JSON 避免存成 XML 影响 Phase A 已验证的契约
                fi = page.locator(".el-form-item", has_text="响应体格式").first
                fi.get_by_text("JSON", exact=False).first.click()
                time.sleep(0.5)
                page.get_by_role("button", name="保存配置").first.click()
                page.wait_for_selector(".el-message--success", timeout=6000)
                page.screenshot(path=f"{SHOTS}/B4_after_save.png", full_page=True)
                step("B7 点「保存配置」→ 成功提示", True, "el-message--success 出现")
            except Exception as e:
                page.screenshot(path=f"{SHOTS}/B4_after_save.png", full_page=True)
                step("B7 保存配置", False, f"未见成功提示 {e}")

        finally:
            ctx.close()   # flush 视频
            browser.close()


def main():
    do_b = "--no-browser" not in sys.argv
    a = phase_a()
    if do_b:
        try:
            phase_b()
        except Exception as e:
            step("Phase B 整体异常", False, str(e))

    failed = [s for s in _log if not s["ok"]]
    summary = {"total": len(_log), "passed": len(_log)-len(failed), "failed": len(failed),
               "failed_steps": [s["label"] for s in failed]}
    with open("/tmp/uat_run.log", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "steps": _log}, f, ensure_ascii=False, indent=2)
    print("\n========== 汇总 ==========", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"failed: {len(failed)}", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
