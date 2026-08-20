# -*- coding: utf-8 -*-
"""v3.50 齐件即结算 + 扫码器生命周期 CI E2E 回归 (捷昌二期)。

覆盖 (对应计划 C 部分浏览器六用例):
  1. 逻辑设置: "全部合格立即结算"开关仅 ROI离开/容器策略下显示
  2. 开关开启 → 步骤设置露出"确认放入帧数"列 → 保存 →
     tracking_settle_on_complete + settle_confirm_frames 落库
  3. 开关关闭 → 步骤设置不出现该列 (现状零差异)
  4. 扫码器面板: 三个生命周期新选项渲染 + A 持续模式置灰 / C 模式解锁
  5. 扫码器新字段 UI 保存 → /scanner/devices 落库
  6. 监控页"恢复扫码"按钮: scanner_resume_blocked 时出现, 点击调
     POST /scanner/resume (注入轮询响应验证前端接线)

运行时结算行为由 tests/test_settle_on_complete.py / test_scanner_lifecycle.py
覆盖, 本文件只守 UI→落库/接口 链路。资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import json
import time
import uuid

import requests

from .conftest import E2E_PREFIX


def _mk_tracking_project(api_url, strategy="roi_exit"):
    name = f"{E2E_PREFIX}soc_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "tracking",
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "pipeline_config": {
            "tracking_cycle_strategy": strategy,
            "counting_expected_items": {"螺丝": 2},
        },
        "steps_config": [
            {"id": 1, "label": "螺丝", "displayLabel": "螺丝", "enabled": True,
             "threshold": 50, "count_mode": "track", "expected_count": 2},
        ],
    }, timeout=5).raise_for_status()
    return pid, name


def _open_project(page, base_url, name, tab=None):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{name}')").first.click(timeout=5000)
    time.sleep(0.8)
    if tab:
        page.locator(f".el-tabs__item:has-text('{tab}')").first.click()
        time.sleep(0.8)


SOC_SWITCH = "[data-testid='settle-on-complete-switch']"


# ==================== 1/2/3: 逻辑页开关 + 步骤列 ====================

def test_逻辑页开关仅roi离开与容器可见(page, base_url, api_url):
    _pid, name = _mk_tracking_project(api_url, strategy="roi_exit")
    _open_project(page, base_url, name, tab="逻辑设置")

    assert page.locator(SOC_SWITCH).count() > 0, "ROI离开策略下开关应渲染"
    body = page.evaluate("document.body.innerText")
    assert "全部合格立即结算" in body

    # 切到"全部消失"策略 → 开关消失
    page.locator("label:has-text('全部消失')").first.click()
    time.sleep(0.5)
    assert page.locator(SOC_SWITCH).count() == 0, "全部消失策略下开关不应渲染"

    # 切到"容器模式" → 开关回来
    page.locator("label:has-text('容器模式')").first.click()
    time.sleep(0.5)
    assert page.locator(SOC_SWITCH).count() > 0, "容器策略下开关应渲染"


def test_开关开启_确认放入帧数列露出并落库(page, base_url, api_url):
    pid, name = _mk_tracking_project(api_url, strategy="roi_exit")
    _open_project(page, base_url, name, tab="逻辑设置")

    page.locator(SOC_SWITCH).click()
    time.sleep(0.5)

    # tracking 模式下步骤 tab 显示为"物品设置"
    page.locator(".el-tabs__item:has-text('物品设置')").first.click()
    time.sleep(0.8)
    body = page.evaluate("document.body.innerText")
    assert "确认放入帧数" in body, "开关开启后步骤表应露出确认放入帧数列"

    # data-testid 直取该列输入 (el-input-number 需要键盘输入 + blur 才提交 v-model)
    col_input = page.locator(
        "[data-testid='settle-confirm-frames-input'] input").first
    # Meta+a 全选仅 macOS 生效, Ubuntu CI 上是 no-op → "5"追加到默认"1"变"15" (曾红灯 v3.53.0 发版 CI)。
    # fill() 原生替换值 + Tab blur 提交, 跨平台无竞态。
    col_input.click()
    col_input.fill("5")
    page.keyboard.press("Tab")
    time.sleep(0.4)

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    pc = detail.get("pipeline_config") or {}
    assert pc.get("tracking_settle_on_complete") is True, \
        f"开关应落库: {pc.get('tracking_settle_on_complete')}"
    step = detail["steps_config"][0]
    assert int(step.get("settle_confirm_frames") or 0) == 5, \
        f"确认放入帧数应落库 5: {step.get('settle_confirm_frames')}"


def test_开关关闭_步骤表无确认帧数列(page, base_url, api_url):
    _pid, name = _mk_tracking_project(api_url, strategy="roi_exit")
    _open_project(page, base_url, name, tab="物品设置")
    body = page.evaluate("document.body.innerText")
    assert "确认放入帧数" not in body, "默认关时步骤表不应出现该列 (现状零差异)"


SCAN_GATE_SWITCH = "[data-testid='scan-gate-switch']"


def test_扫码后才计数_开关渲染并落库(page, base_url, api_url):
    """v3.50.1: 跟踪模式通用开关 — 任何策略下都渲染, 开启保存后落 pipeline_config。"""
    pid, name = _mk_tracking_project(api_url, strategy="roi_exit")
    _open_project(page, base_url, name, tab="逻辑设置")

    assert page.locator(SCAN_GATE_SWITCH).count() > 0, "扫码后才计数开关应渲染"
    body = page.evaluate("document.body.innerText")
    assert "扫码后才计数" in body

    # 切到"全部消失"策略 → 开关仍在 (跟踪模式通用, 不随策略隐藏)
    page.locator("label:has-text('全部消失')").first.click()
    time.sleep(0.5)
    assert page.locator(SCAN_GATE_SWITCH).count() > 0, "全部消失策略下开关也应渲染"

    page.locator(SCAN_GATE_SWITCH).click()
    time.sleep(0.5)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    pc = requests.get(f"{api_url}/api/v1/projects/{pid}",
                      timeout=5).json().get("pipeline_config") or {}
    assert pc.get("tracking_scan_gate") is True, \
        f"扫码后才计数应落库 True: {pc.get('tracking_scan_gate')}"


def test_扫码后才计数_默认关落库false(page, base_url, api_url):
    pid, name = _mk_tracking_project(api_url, strategy="container")
    _open_project(page, base_url, name, tab="逻辑设置")
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    pc = requests.get(f"{api_url}/api/v1/projects/{pid}",
                      timeout=5).json().get("pipeline_config") or {}
    assert pc.get("tracking_scan_gate") in (False, None), \
        f"默认应为关: {pc.get('tracking_scan_gate')}"


# ==================== 4/5: 扫码器面板 ====================

def _open_scanner_dialog(page, base_url):
    page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("button:has-text('扫码器')").first.click()
    time.sleep(0.8)
    page.locator("button:has-text('手动添加')").first.click()
    time.sleep(0.8)
    dlg = page.locator(".el-dialog:has-text('添加设备')")
    assert dlg.count() > 0, "添加设备对话框应打开"
    return dlg


def _select_option(page, select_locator, option_text):
    select_locator.click()
    time.sleep(0.5)
    page.locator(f".el-select-dropdown__item:visible:has-text('{option_text}')") \
        .first.click()
    time.sleep(0.4)


def _form_item(dlg, label):
    """按表单 label 精确匹配定位 el-form-item。
    不能用 :has-text(label) 模糊匹配 — 帮助文案里出现同词 (如扫描模式 E 档
    说明含"重新亮灯时机") 会误配到别的表单项。"""
    return dlg.locator(
        f".el-form-item:has(.el-form-item__label:text-is('{label}'))").first


def test_扫码器面板生命周期选项渲染与置灰(page, base_url, api_url):
    dlg = _open_scanner_dialog(page, base_url)
    body = dlg.inner_text()
    assert "重新亮灯时机" in body and "亮灯作废旧码" in body and "强制去重" in body, \
        "三个生命周期选项应渲染"

    # 默认 A 持续模式: 重新亮灯时机/作废旧码 置灰 + 提示文案
    resume_item = _form_item(dlg, "重新亮灯时机")
    assert resume_item.locator(".el-select .is-disabled, .el-select--disabled") \
        .count() > 0 or "is-disabled" in (
            resume_item.locator(".el-select .el-select__wrapper").first
            .get_attribute("class") or ""), "A 模式下重新亮灯时机应置灰"
    assert "仅 LON/LOFF 协议且扫描模式为 C / D / E 时生效" in body

    rearm_item = _form_item(dlg, "亮灯作废旧码")
    assert "is-disabled" in (
        rearm_item.locator(".el-switch").first.get_attribute("class") or ""), \
        "A 模式下亮灯作废旧码应置灰"
    # 强制去重不依赖灯控, 任何模式都可用
    strict_item = _form_item(dlg, "强制去重")
    assert "is-disabled" not in (
        strict_item.locator(".el-switch").first.get_attribute("class") or ""), \
        "强制去重不应置灰"

    # 切扫描模式 C → 解锁
    _select_option(page, _form_item(dlg, "扫描模式").locator(".el-select").first,
                   "C 单次/周期")
    resume_wrapper_cls = (
        resume_item.locator(".el-select .el-select__wrapper").first
        .get_attribute("class") or "")
    assert "is-disabled" not in resume_wrapper_cls, "C 模式下重新亮灯时机应解锁"
    assert "is-disabled" not in (
        rearm_item.locator(".el-switch").first.get_attribute("class") or ""), \
        "C 模式下亮灯作废旧码应解锁"


def test_扫描模式E码合格码_锁定仅合格并落库(page, base_url, api_url):
    """v3.50.1: 选 E 码-合格-码 → 重新亮灯时机自动置 ok_only 且置灰不可改,
    保存后 scan_mode=E + resume_on=ok_only 落库。"""
    dev_name = f"{E2E_PREFIX}E枪_{uuid.uuid4().hex[:6]}"
    dlg = _open_scanner_dialog(page, base_url)
    dlg.locator(".el-form-item:has-text('名称') input").first.fill(dev_name)
    dlg.locator(".el-form-item:has-text('IP 地址') input").first.fill("127.0.0.1")

    _select_option(page, _form_item(dlg, "扫描模式").locator(".el-select").first,
                   "E 码-合格-码")

    body = dlg.inner_text()
    assert "E 码-合格-码模式固定为「仅合格」" in body, "E 模式提示应出现"
    resume_item = _form_item(dlg, "重新亮灯时机")
    assert "is-disabled" in (
        resume_item.locator(".el-select .el-select__wrapper").first
        .get_attribute("class") or ""), "E 模式下重新亮灯时机应置灰锁定"
    assert "仅合格" in resume_item.inner_text(), "应自动切到仅合格"

    dlg.locator("button:has-text('确定'), button:has-text('保存')").last.click()
    time.sleep(2.0)

    devices = requests.get(f"{api_url}/api/v1/scanner/devices", timeout=5).json()
    dev = next((d for d in devices if d.get("name") == dev_name), None)
    try:
        assert dev is not None, f"设备应落库: {dev_name}"
        assert dev["scan_mode"] == "E", f"scan_mode 应为 E: {dev}"
        assert dev["resume_on"] == "ok_only", f"resume_on 应强制 ok_only: {dev}"
    finally:
        if dev is not None:
            requests.delete(
                f"{api_url}/api/v1/scanner/devices/{dev['id']}", timeout=5)


def test_扫码器生命周期字段UI保存落库(page, base_url, api_url):
    dev_name = f"{E2E_PREFIX}枪_{uuid.uuid4().hex[:6]}"
    dlg = _open_scanner_dialog(page, base_url)
    dlg.locator(".el-form-item:has-text('名称') input").first.fill(dev_name)
    dlg.locator(".el-form-item:has-text('IP 地址') input").first.fill("127.0.0.1")

    _select_option(page, _form_item(dlg, "扫描模式").locator(".el-select").first,
                   "C 单次/周期")
    _select_option(page, _form_item(dlg, "重新亮灯时机").locator(".el-select").first,
                   "仅合格")
    _form_item(dlg, "亮灯作废旧码").locator(".el-switch").first.click()
    time.sleep(0.3)
    _form_item(dlg, "强制去重").locator(".el-switch").first.click()
    time.sleep(0.3)

    dlg.locator("button:has-text('确定'), button:has-text('保存')").last.click()
    time.sleep(2.0)

    devices = requests.get(f"{api_url}/api/v1/scanner/devices", timeout=5).json()
    dev = next((d for d in devices if d.get("name") == dev_name), None)
    try:
        assert dev is not None, f"设备应落库: {dev_name}"
        assert dev["resume_on"] == "ok_only", f"resume_on 应落库: {dev}"
        assert dev["rearm_forget_last"] is True, f"rearm_forget_last 应落库: {dev}"
        assert dev["strict_ok_dedup"] is True, f"strict_ok_dedup 应落库: {dev}"
        assert dev["scan_mode"] == "once_per_cycle"
    finally:
        if dev is not None:
            requests.delete(
                f"{api_url}/api/v1/scanner/devices/{dev['id']}", timeout=5)


# ==================== 6: 监控页恢复扫码按钮 ====================

def test_监控页恢复扫码按钮_出现并调用恢复端点(page, base_url, api_url):
    """轮询响应注入 scanner_resume_blocked=True → 按钮出现 → 点击发
    POST /scanner/resume 且后端返回 200 (前端接线 + 端点连通双验证)。"""

    def _inject(route):
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        mes = data.get("mes") or {}
        mes["scanner_resume_blocked"] = True
        data["mes"] = mes
        # 防 v3.40 轮询真相源同步发现 is_running=false 又把轮询停掉
        data["is_running"] = True
        data["is_detecting"] = True
        route.fulfill(
            status=resp.status,
            headers={"content-type": "application/json"},
            body=json.dumps(data),
        )

    def _fake_running(route):
        # 单工位下 startPolling() 只在 is_running=true 时启动;
        # 测试环境没有源在跑, 必须伪造 status 才能让结果轮询发起来
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        data["is_running"] = True
        data["is_detecting"] = True
        data["source_type"] = data.get("source_type") or "video"
        route.fulfill(
            status=resp.status,
            headers={"content-type": "application/json"},
            body=json.dumps(data),
        )

    page.route("**/api/v1/source/status*", _fake_running)
    page.route("**/api/v1/source/detection/results*", _inject)

    resume_calls = []
    page.on("response", lambda r: resume_calls.append(r.status)
            if "/scanner/resume" in r.url else None)

    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    # 150ms 轮询一直在跑, networkidle 永远等不到 → 直接等按钮出现
    btn = page.locator("[data-testid='resume-scanner-btn']")
    btn.wait_for(state="visible", timeout=15000)

    btn.click()
    deadline = time.time() + 8
    while time.time() < deadline and not resume_calls:
        page.wait_for_timeout(300)
    assert resume_calls, "点击后应发起 POST /scanner/resume"
    assert resume_calls[0] == 200, f"恢复端点应 200: {resume_calls[0]}"
