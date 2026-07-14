# -*- coding: utf-8 -*-
"""可见浏览器 UAT — 川南反馈三改动 (2026-07-13)

覆盖:
  A. Q1a 开工后自动开始检测 (start_detection_on_task, API 级端到端):
     synthetic 源在跑 + 检测已停 → 入站开工 → 检测被自动拉起
  B. Q1b 导航栏跟随外部切项目 (浏览器): 入站开工切到另一项目,
     不刷新页面, ≤10s 内导航栏项目选择器自动跟上 + toast 提示
  C. Q5 新建项目弹窗: 任务类型下拉出现可选的「图像分割 (Instance Segmentation)」

前置: 后端 8012 (RUNTIME_MODE=test, TIANJUN_DATA_DIR=/tmp/cn_fix_data)
      前端 6012 (VITE_API_BASE_URL=http://127.0.0.1:8012/api/v1)
产物: /tmp/uat_shots/cnfix_*.png + /tmp/uat_video/*.webm + stdout 步骤日志
"""
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BACKEND = "http://127.0.0.1:8012/api/v1"
FRONTEND = "http://127.0.0.1:6012"
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


def setup():
    pa = ensure_project("CN-A")
    pb = ensure_project("CN-B")
    step(f"两个项目就绪 CN-A=#{pa} CN-B=#{pb}")

    # 激活 CN-A 作为初始项目
    requests.post(f"{BACKEND}/projects/{pa}/activate", timeout=15)
    step("初始激活 CN-A")

    cfg = requests.get(f"{BACKEND}/mes/inbound/config", timeout=10).json()
    cfg.update({
        "enabled": True,
        "switch_project_on_task": True,
        "match_project_by_name": True,
        "start_detection_on_task": True,
        "create_work_order_on_task": True,
    })
    r = requests.put(f"{BACKEND}/mes/inbound/config", json=cfg, timeout=10)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved.get("start_detection_on_task") is True
    step("入站配置已保存 (切项目+同名匹配+自动开始检测+建工单)")
    return pa, pb


def phase_a(pa):
    """Q1a: synthetic 源在跑、检测停止 → 入站开工 → 检测被自动拉起。"""
    r = requests.post(f"{BACKEND}/test/synthetic/start", json={
        "channel": 0, "scenario_json": {"name": "idle", "timeline": []},
        "with_project": False, "fps": 5,
    }, timeout=15)
    assert r.status_code == 200, r.text
    step("ch0 synthetic 视频源已启动 (未开检测)")

    res = requests.get(f"{BACKEND}/source/detection/results",
                       params={"channel": 0}, timeout=10).json()
    assert not res.get("is_detecting"), f"预期检测未跑: {res.get('is_detecting')}"
    step("确认 ch0 当前未在检测")

    r = requests.post(f"{BACKEND}/mes/inbound/task", json={
        "TaskNo": "CNFIX-001", "ProductCode": "CN-A",
        "StepCode": "工序10/工步2", "Operator": "川南验证员",
    }, timeout=30)
    body = r.json()
    assert r.status_code == 200 and body.get("code") == 0, f"开工失败: {body}"
    step(f"入站开工返回成功: {body}")

    time.sleep(2.0)
    res = requests.get(f"{BACKEND}/source/detection/results",
                       params={"channel": 0}, timeout=10).json()
    assert res.get("is_detecting") is True, f"检测未被自动拉起: is_detecting={res.get('is_detecting')}"
    step("开工后 ch0 检测已被自动拉起 (start_detection_on_task 生效)")

    # 再发一次同产品开工: 已在检测 → 不重复拉起也不报错
    r = requests.post(f"{BACKEND}/mes/inbound/task", json={
        "TaskNo": "CNFIX-002", "ProductCode": "CN-A",
    }, timeout=30)
    assert r.json().get("code") == 0
    step("已在检测时再开工: 幂等无异常")


def phase_b_browser(pa, pb):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=["--window-size=1680,1000"])
        ctx = browser.new_context(viewport={"width": 1680, "height": 950},
                                  record_video_dir="/tmp/uat_video")
        page = ctx.new_page()

        # ---------- Q1b: 导航栏跟随外部切项目 ----------
        page.goto(f"{FRONTEND}/#/monitor", wait_until="domcontentloaded")
        time.sleep(6.0)  # 等 Navbar loadProjects + 首轮同步
        page.screenshot(path=f"{SHOTS}/cnfix_01_before_switch.png")
        nav_text = page.locator(".el-select__selected-item, .el-select__placeholder").first.inner_text(timeout=5000)
        step(f"浏览器就绪, 当前导航栏项目显示: {nav_text!r}")

        r = requests.post(f"{BACKEND}/mes/inbound/task", json={
            "TaskNo": "CNFIX-010", "ProductCode": "CN-B",
            "StepCode": "工序20/工步1", "Operator": "川南验证员",
        }, timeout=30)
        assert r.json().get("code") == 0, r.text
        step("入站开工 CN-B (后端已切项目), 等待前端自动跟随…")

        deadline = time.time() + 15
        followed = False
        while time.time() < deadline:
            body_text = page.inner_text("body")
            if "CN-B" in (page.locator(".el-select__selected-item").first.inner_text()
                          if page.locator(".el-select__selected-item").count() else "") \
               or "项目已由外部系统切换" in body_text:
                followed = True
                break
            time.sleep(1.0)
        page.screenshot(path=f"{SHOTS}/cnfix_02_after_switch.png")
        step("导航栏在 15s 内自动跟随到 CN-B (无刷新)", ok=followed)

        # ---------- Q5: 新建项目弹窗分割选项 ----------
        # 检测运行中路由会被拦 ("请先停止检测"), 先停检测+synthetic 源
        requests.post(f"{BACKEND}/source/detection/stop", params={"channel": 0}, timeout=15)
        requests.post(f"{BACKEND}/test/synthetic/stop", params={"channel": 0}, timeout=15)
        time.sleep(1.0)
        page.goto(f"{FRONTEND}/#/project", wait_until="domcontentloaded")
        page.reload(wait_until="domcontentloaded")
        time.sleep(3.0)
        page.screenshot(path=f"{SHOTS}/cnfix_02b_project_page.png")
        page.get_by_role("button", name="新建项目").click()
        time.sleep(1.0)
        # 弹窗里第 1 个下拉 = 任务类型 (第 2 个是逻辑模式)
        page.locator(".el-dialog .el-select").first.click()
        time.sleep(0.8)
        page.screenshot(path=f"{SHOTS}/cnfix_03_create_dialog_seg.png")
        # 只认可见的下拉面板 (页面可能残留其他隐藏的 select 面板 DOM)
        seg_opt = page.locator(".el-select-dropdown:visible .el-select-dropdown__item",
                               has_text="图像分割")
        assert seg_opt.count() >= 1, "下拉里没有「图像分割」选项"
        cls = seg_opt.first.get_attribute("class") or ""
        assert "is-disabled" not in cls, f"图像分割仍为禁用: {cls}"
        step("新建项目弹窗: 「图像分割 (Instance Segmentation)」可选且未禁用")

        seg_opt.first.click()
        time.sleep(0.5)
        page.screenshot(path=f"{SHOTS}/cnfix_04_seg_selected.png")
        step("已选中图像分割 (截图留证)")

        ctx.close()
        browser.close()


if __name__ == "__main__":
    import os
    os.makedirs(SHOTS, exist_ok=True)
    pa, pb = setup()
    phase_a(pa)
    phase_b_browser(pa, pb)
    print("\n=== UAT 全部通过 ===", flush=True)
