# -*- coding: utf-8 -*-
"""可见浏览器 UAT — 川南生产级全链路 (2026-07-13 第二轮)

与客户现场同构的三端联动:
  mock 川南中控 (9100)  →  天军后端 (8012)  →  天军前端 (6012, 真浏览器)

流程 (全部走生产路径, 不直捅内部 API):
  1. 浏览器进 MES → 工单接收: 手动勾开关(启用/切项目/同名匹配/自动开始检测/建工单/
     四要素全勾) → 点"保存配置" → GET 配置验证真实落库   [T5 UI→后端闭环]
  2. synthetic 视频源在跑、检测停止 (模拟现场设备就绪待机)
  3. 中控 /drive/start 推开工 (客户 Postman 的等价物) → 验证:
     a. 中控收到 code=0
     b. 检测被自动拉起 (start_detection_on_task)
     c. 浏览器不刷新, 导航栏自动跟随切到目标项目
     d. 检测中心四要素上屏 (任务号/产品代号/工序工步/操作员)
  4. 截图留证 + 中控通讯台账截图

前置: 后端 8012 (RUNTIME_MODE=test, TIANJUN_DATA_DIR=/tmp/cn_fix_data)
      前端 6012 (VITE_API_BASE_URL=http://127.0.0.1:8012/api/v1)
      中控 9100 (TIANJUN_INBOUND=http://127.0.0.1:8012/api/v1/mes/inbound/task)
"""
import datetime
import os
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BACKEND = "http://127.0.0.1:8012/api/v1"
FRONTEND = "http://127.0.0.1:6012"
MCS = "http://127.0.0.1:9100"
SHOTS = "/tmp/uat_shots"

_step_n = 0


def step(msg, ok=True):
    global _step_n
    _step_n += 1
    print(f"[{'PASS' if ok else 'FAIL'}] step{_step_n}: {msg}", flush=True)
    if not ok:
        sys.exit(1)


def ensure_project(name):
    r = requests.get(f"{BACKEND}/projects", timeout=10)
    for p in r.json().get("items", []):
        if p["name"] == name:
            return p["id"]
    r = requests.post(f"{BACKEND}/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def reset_inbound_config_all_off():
    """把入站配置重置为全关, 让浏览器里的勾选保存成为唯一配置来源。"""
    cfg = requests.get(f"{BACKEND}/mes/inbound/config", timeout=10).json()
    cfg.update({
        "enabled": False,
        "switch_project_on_task": False,
        "match_project_by_name": False,
        "start_detection_on_task": False,
        "create_work_order_on_task": False,
        "supersede_previous_task": False,
        "task_info_display": {"show_task_no": False, "show_product_code": False,
                              "show_step_code": False, "show_operator": False},
    })
    r = requests.put(f"{BACKEND}/mes/inbound/config", json=cfg, timeout=10)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["start_detection_on_task"] is False
    step("入站配置已重置为全关 (浏览器勾选将成为唯一配置来源)")


def wipe_stale_orders():
    """收掉历史测试留下的在产工单, 模拟客户现场干净初始态。"""
    r = requests.get(f"{BACKEND}/mes/orders", params={"status": "in_progress",
                                                      "page_size": 100}, timeout=10)
    items = r.json().get("items") or []
    for o in items:
        requests.post(f"{BACKEND}/mes/orders/{o['id']}/status",
                      json={"status": "completed"}, timeout=10)
    step(f"清理历史在产工单 {len(items)} 张 (干净初始态)")


def switch_by_label(page, label):
    """点 el-form-item 标签对应的开关。"""
    page.locator(".el-form-item", has=page.locator(
        f".el-form-item__label:text-is('{label}')")).first \
        .locator(".el-switch").first.click()
    time.sleep(0.3)


def main():
    os.makedirs(SHOTS, exist_ok=True)

    pa = ensure_project("CN-A")
    pb = ensure_project("CN-B")
    requests.post(f"{BACKEND}/projects/{pa}/activate", timeout=15)
    step(f"项目就绪并激活 CN-A (CN-A=#{pa}, CN-B=#{pb})")

    reset_inbound_config_all_off()
    wipe_stale_orders()

    r = requests.get(f"{MCS}/health", timeout=5)
    assert r.status_code == 200
    step("mock 川南中控在线 (9100)")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=["--window-size=1680,1000"])
        ctx = browser.new_context(viewport={"width": 1680, "height": 950},
                                  record_video_dir="/tmp/uat_video")
        page = ctx.new_page()

        # ---------- 1. 浏览器里配置入站 (UI→后端闭环) ----------
        page.goto(f"{FRONTEND}/#/mes", wait_until="domcontentloaded")
        time.sleep(4.0)
        page.get_by_text("工单接收", exact=False).first.click()
        time.sleep(2.0)
        page.screenshot(path=f"{SHOTS}/cnprod_01_inbound_panel.png")

        # 顶部启用总开关 (面板头部第一个 switch)
        page.locator(".el-switch").first.click()
        time.sleep(0.3)
        switch_by_label(page, "按产品码切项目")
        switch_by_label(page, "按项目名自动匹配")
        switch_by_label(page, "开工后自动开始检测")
        switch_by_label(page, "开工即建工单")
        switch_by_label(page, "最新开工为准")
        # 四要素全勾
        for txt in ("任务号", "产品代号", "工序工步", "操作员"):
            item = page.locator(".el-form-item", has=page.locator(
                ".el-form-item__label:text-is('持续显示要素')")).first
            item.locator(".el-checkbox", has_text=txt).first.click()
            time.sleep(0.2)
        page.screenshot(path=f"{SHOTS}/cnprod_02_switches_on.png")
        page.get_by_role("button", name="保存配置").click()
        time.sleep(1.5)
        step("浏览器已勾选全部开关并点保存")

        cfg = requests.get(f"{BACKEND}/mes/inbound/config", timeout=10).json()
        assert cfg["enabled"] is True, f"enabled 未落库: {cfg['enabled']}"
        assert cfg["switch_project_on_task"] is True
        assert cfg["match_project_by_name"] is True
        assert cfg["start_detection_on_task"] is True, "自动开始检测开关未落库"
        assert cfg["create_work_order_on_task"] is True
        assert cfg["supersede_previous_task"] is True, "最新开工为准未落库"
        tinfo = cfg["task_info_display"]
        assert all(tinfo[k] for k in ("show_task_no", "show_product_code",
                                      "show_step_code", "show_operator")), tinfo
        step("GET 配置回读: 全部开关真实落库 (含自动开始检测+四要素)")

        # ---------- 2. 现场态: 源在跑、检测停 ----------
        r = requests.post(f"{BACKEND}/test/synthetic/start", json={
            "channel": 0, "scenario_json": {"name": "idle", "timeline": []},
            "with_project": False, "fps": 5,
        }, timeout=15)
        assert r.status_code == 200, r.text
        requests.post(f"{BACKEND}/source/detection/stop",
                      params={"channel": 0}, timeout=15)
        time.sleep(1.0)
        res = requests.get(f"{BACKEND}/source/detection/results",
                           params={"channel": 0}, timeout=10).json()
        assert not res.get("is_detecting")
        step("现场态就绪: ch0 视频源在跑、检测停止")

        # 浏览器回检测中心待命
        page.goto(f"{FRONTEND}/#/monitor", wait_until="domcontentloaded")
        time.sleep(5.0)
        page.screenshot(path=f"{SHOTS}/cnprod_03_monitor_idle.png")

        # ---------- 3. 中控推开工 (生产路径) ----------
        # 任务号带时间戳: 现场任务号不重复; completed 终态单按业务约定不复活
        task_no = f"CNPROD-{datetime.datetime.now():%H%M%S}"
        r = requests.post(f"{MCS}/drive/start", json={
            "task_no": task_no, "product_code": "CN-B",
            "step_code": "工序30/工步2", "operator": "川南操作员",
        }, timeout=30)
        body = r.json()
        assert body["resp"]["http"] == 200 and body["resp"]["code"] == 0, \
            f"中控推开工失败: {body}"
        step(f"中控推开工成功, 天军回码 {body['resp']['code']}/{body['resp']['message']}")

        time.sleep(2.5)
        res = requests.get(f"{BACKEND}/source/detection/results",
                           params={"channel": 0}, timeout=10).json()
        assert res.get("is_detecting") is True, "开工后检测未被自动拉起"
        step("检测已被开工自动拉起 (start_detection_on_task 生产路径生效)")

        # c. 导航栏无刷新跟随 (取 textContent, inner_text 对该控件返回空串)
        deadline = time.time() + 15
        followed = False
        while time.time() < deadline:
            txt = page.evaluate(
                "document.querySelector('.el-select__selection')?.textContent || ''")
            if "CN-B" in txt:
                followed = True
                break
            time.sleep(1.0)
        step("导航栏无刷新自动跟随到 CN-B", ok=followed)

        # d. 四要素上屏
        deadline = time.time() + 20
        four_ok = False
        while time.time() < deadline:
            body_text = page.inner_text("body")
            if all(s in body_text for s in
                   (task_no, "CN-B", "工序30/工步2", "川南操作员")):
                four_ok = True
                break
            time.sleep(1.0)
        page.screenshot(path=f"{SHOTS}/cnprod_04_four_elements.png")
        step("检测中心四要素上屏 (任务号/产品代号/工序工步/操作员)", ok=four_ok)

        # ---------- 4. 中控台账留证 ----------
        page.goto(f"{MCS}/", wait_until="domcontentloaded")
        time.sleep(2.0)
        page.screenshot(path=f"{SHOTS}/cnprod_05_mcs_console.png")
        log = requests.get(f"{MCS}/log", timeout=5).json()
        assert any(i["kind"].startswith("开工") for i in log["items"])
        step("中控通讯台账已记录本次开工往来")

        ctx.close()
        browser.close()

    # 收尾: 停检测/源
    requests.post(f"{BACKEND}/source/detection/stop", params={"channel": 0}, timeout=15)
    requests.post(f"{BACKEND}/test/synthetic/stop", params={"channel": 0}, timeout=15)
    print("\n=== 生产级全链路 UAT 全部通过 ===", flush=True)


if __name__ == "__main__":
    main()
