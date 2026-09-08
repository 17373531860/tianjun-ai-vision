"""AI 采样模式 (logic_mode='ocr'|'anomaly') + facing_dwell 朝向驻留 CI E2E (2026-09)。

覆盖:
  1. 项目创建对话框 / 逻辑设置: 出现「OCR 读字」「异常检测」两个新逻辑模式单选
  2. ocr 项目: 逻辑设置渲染「OCR 读字模式 - 读字规则」卡, 同时出现组隐藏;
     UI 建读字规则 (名称/正则/间隔) → 保存配置 → pipeline_config.ocr 落库
  3. anomaly 项目: 渲染「异常检测模式 - 哨兵配置」卡; 改采样间隔/连续帧
     → 保存 → pipeline_config.anomaly 落库
  4. region_events 项目: 「面向仪表点检」模板一键建 facing_dwell 规则,
     类型下拉含「朝向驻留」, 容差/告警取反字段渲染; 已有 target_point 的规则
     经 UI 改容差再保存, target_point 不丢 (序列化白名单守门)
  5. 无 target_point 的 facing_dwell 规则保存时被前端剔除

运行时行为由 tests/test_ai_modes.py 覆盖; 本文件只守 UI→落库链路。
资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX


def _mk_project(api_url, logic_mode):
    name = f"{E2E_PREFIX}ai_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": logic_mode,
    }, timeout=5)
    r.raise_for_status()
    return r.json()["id"], name


def _open_logic_tab(page, base_url, name):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{name}')").first.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(0.8)


def _save(page):
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)


def _pipeline(api_url, pid):
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    return detail.get("pipeline_config") or {}


# ==================== OCR 模式 ====================

def test_ocr模式单选卡片渲染与规则落库(page, base_url, api_url):
    pid, name = _mk_project(api_url, "ocr")
    _open_logic_tab(page, base_url, name)

    radio = page.locator("input[type='radio'][value='ocr']")
    assert radio.count() == 1, "逻辑模式列表应出现 OCR 读字模式单选"
    assert radio.is_checked(), "ocr 项目该单选应为选中态"

    card = page.locator(".el-card:has-text('OCR 读字模式 - 读字规则')")
    assert card.count() == 1, "逻辑设置应渲染 OCR 配置卡"
    body = page.evaluate("document.body.innerText")
    assert "同时出现组" not in body, "ocr 下步骤级卡片应隐藏"

    # 全局参数: 采样间隔 3s / 稳定次数 1
    itv = card.locator(
        "div:has(> label:has-text('采样间隔')) .el-input-number input").first
    itv.fill("3")
    itv.press("Enter")
    time.sleep(0.3)
    stb = card.locator(
        "div:has(> label:has-text('稳定次数')) .el-input-number input").first
    stb.fill("1")
    stb.press("Enter")
    time.sleep(0.3)

    # 建一条规则
    card.locator("button:has-text('新增规则')").click()
    time.sleep(0.6)
    rule = card.locator("div.bg-slate-900").last
    rule.locator("input[placeholder*='规则名']").fill("序列号核对")
    rule.locator("input[placeholder*='SN']").fill(r"^SN\d{8}$")
    time.sleep(0.3)

    _save(page)
    ocr = _pipeline(api_url, pid).get("ocr") or {}
    assert ocr.get("interval_s") == 3, f"采样间隔应落库: {ocr}"
    assert ocr.get("stable_reads") == 1
    rules = ocr.get("rules") or []
    assert len(rules) == 1 and rules[0]["name"] == "序列号核对"
    assert rules[0]["pattern"] == r"^SN\d{8}$"
    assert rules[0].get("settle") is True and rules[0].get("on_change_only") is True


# ==================== 异常检测模式 ====================

def test_anomaly模式卡片渲染与参数落库(page, base_url, api_url):
    pid, name = _mk_project(api_url, "anomaly")
    _open_logic_tab(page, base_url, name)

    radio = page.locator("input[type='radio'][value='anomaly']")
    assert radio.count() == 1 and radio.is_checked()

    card = page.locator(".el-card:has-text('异常检测模式 - 哨兵配置')")
    assert card.count() == 1, "逻辑设置应渲染异常检测配置卡"
    body = page.evaluate("document.body.innerText")
    assert "同时出现组" not in body
    assert "尚未选择记忆库" in body, "未选库时应显示引导提示"

    itv = card.locator(
        "div:has(> label:has-text('采样间隔')) .el-input-number input").first
    itv.fill("5")
    itv.press("Enter")
    time.sleep(0.3)
    csc = card.locator(
        "div:has(> label:has-text('连续超阈值')) .el-input-number input").first
    csc.fill("4")
    csc.press("Enter")
    time.sleep(0.3)

    _save(page)
    ano = _pipeline(api_url, pid).get("anomaly") or {}
    assert ano.get("interval_s") == 5, f"采样间隔应落库: {ano}"
    assert ano.get("consecutive") == 4


# ==================== facing_dwell 朝向驻留 ====================

def _mk_region_project(api_url):
    pid, name = _mk_project(api_url, "region_events")
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={"steps_config": [
        {"id": 1, "label": "人", "displayLabel": "人", "enabled": True, "threshold": 50},
    ]}, timeout=5).raise_for_status()
    return pid, name


def test_facing模板建规则与类型渲染(page, base_url, api_url):
    _pid, name = _mk_region_project(api_url)
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('区域事件模式 - 动作规则')")
    assert card.count() == 1

    card.locator("button:has-text('面向仪表点检')").click()
    time.sleep(0.8)
    body = page.evaluate("document.body.innerText")
    assert "面向仪表点检" in body
    assert "朝向容差" in body, "facing_dwell 规则应渲染朝向容差字段"
    assert "无人面向才告警" in body or "告警" in body
    assert "仪表点" in body
    assert "未标定" in body, "模板建的规则仪表点应显示未标定"

    rule = card.locator("div.bg-slate-900").filter(has_text="朝向容差").first
    assert rule.locator("button:has-text('在画面上点标仪表位置')").count() >= 1, \
        "facing_dwell 规则应有仪表点标定入口"


def test_facing规则改容差保存_target_point不丢(page, base_url, api_url):
    pid, name = _mk_region_project(api_url)
    # 预置一条已标定仪表点的规则 (UI 标点需视频帧, CI 无帧走 API 注入)
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={"pipeline_config": {
        "region_events": {"enabled": True, "rules": [{
            "type": "facing_dwell", "name": "面向真空计", "subject_label": "人",
            "target_point": [0.82, 0.31], "tolerance_deg": 35,
            "min_frames": 3, "min_seconds": 5, "event_id": 1,
        }]},
    }}, timeout=5).raise_for_status()

    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('区域事件模式 - 动作规则')")
    rule = card.locator("div.bg-slate-900").filter(has_text="朝向容差").first
    assert rule.count() == 1, "预置 facing_dwell 规则应渲染"
    assert "已标定" in rule.inner_text(), "已有 target_point 应显示已标定"

    tol = rule.locator(
        "div:has(> label:has-text('朝向容差')) .el-input-number input").first
    tol.fill("45")
    tol.press("Enter")
    time.sleep(0.4)

    _save(page)
    re_cfg = _pipeline(api_url, pid).get("region_events") or {}
    rules = re_cfg.get("rules") or []
    assert len(rules) == 1, f"规则应保留: {rules}"
    r = rules[0]
    assert r["type"] == "facing_dwell" and r["name"] == "面向真空计"
    assert r["tolerance_deg"] == 45, "UI 改的容差应落库"
    assert r["target_point"] == [0.82, 0.31], "target_point 不得在保存中丢失"
    assert r["min_seconds"] == 5


def test_无仪表点的facing规则保存被剔除(page, base_url, api_url):
    pid, name = _mk_region_project(api_url)
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('区域事件模式 - 动作规则')")
    card.locator("button:has-text('面向仪表点检')").click()
    time.sleep(0.8)

    _save(page)
    re_cfg = _pipeline(api_url, pid).get("region_events") or {}
    rules = re_cfg.get("rules") or []
    assert not any(r.get("type") == "facing_dwell" for r in rules), \
        f"未标定仪表点的 facing_dwell 规则应被保存守门剔除: {rules}"
