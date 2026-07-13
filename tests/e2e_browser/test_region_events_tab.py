"""区域事件模式 (logic_mode='region_events') CI E2E 回归 (v3.32+)。

覆盖:
  1. 基础/逻辑设置: 逻辑模式列表出现「区域事件模式」单选且选中态正确
  2. 逻辑设置 Tab 渲染判定规则卡片; region_events 下「同时出现组」卡片隐藏
  3. UI 建规则: overlap(名称/主体/目标) + region_exit(切类型自动回填结算开关)
     + ROI 编辑器画判定区域 → 保存配置 → pipeline_config.region_events 全结构落库
  4. 每类置信度 + 顺序校验开关 → class_conf / sequence_check 落库
  5. 空名规则被保存守门剔除 (前端 _sanitizeRegionEvents)

运行时行为(引擎判定/周期结算/步骤计数)由 tests/test_region_events.py 与
tests/test_region_events_pipeline.py 覆盖; 人眼验收见
tests/uat/uat_20260706_region_events.py。本文件只守 UI→落库链路。
资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX


def _mk_project(api_url):
    name = f"{E2E_PREFIX}re_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "region_events",
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={"steps_config": [
        {"id": 1, "label": "工件", "displayLabel": "工件", "enabled": True, "threshold": 50},
        {"id": 2, "label": "测硬度笔", "displayLabel": "测硬度笔", "enabled": True, "threshold": 50},
        {"id": 3, "label": "扫码枪", "displayLabel": "扫码枪", "enabled": True, "threshold": 50},
    ]}, timeout=5).raise_for_status()
    return pid, name


def _open_logic_tab(page, base_url, name):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    # 同 hash URL 二次 goto 不触发真实导航 → 必须显式 reload
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{name}')").first.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(0.8)


def _fill_creatable_select(page, scope, text):
    """el-select(filterable allow-create): 点开 → 敲字 → 回车创建/选中。"""
    scope.click()
    time.sleep(0.4)
    page.keyboard.type(text, delay=30)
    time.sleep(0.5)
    page.keyboard.press("Enter")
    time.sleep(0.4)
    page.keyboard.press("Escape")
    time.sleep(0.3)


def _rules_card(page):
    return page.locator(".el-card:has-text('区域事件模式 - 动作规则')")


def _add_overlap_rule(page, card, name, subject, obj):
    card.locator("button:has-text('新增动作')").click()
    time.sleep(0.6)
    # 规则块用"动作名输入框"锚定 (顺序校验/结算判定块也含"动作"字样, 文案过滤会串)
    rule = card.locator("div.bg-slate-900").filter(
        has=page.locator("input[placeholder*='动作名']")).last
    rule.locator("input[placeholder*='动作名']").fill(name)
    # 本版 Element Plus 的 placeholder 渲染成 span, 用文案定位 select
    _fill_creatable_select(page, rule.locator(
        ".el-select:has-text('如 测硬度笔')").first, subject)
    _fill_creatable_select(page, rule.locator(
        ".el-select:has-text('如 工件')").first, obj)
    return rule


def test_模式单选与规则卡片渲染(page, base_url, api_url):
    _pid, name = _mk_project(api_url)
    _open_logic_tab(page, base_url, name)

    radio = page.locator("input[type='radio'][value='region_events']")
    assert radio.count() == 1, "逻辑模式列表应出现区域事件模式单选"
    assert radio.is_checked(), "region_events 项目该单选应为选中态"

    assert _rules_card(page).count() == 1, "逻辑设置应渲染判定规则卡片"
    body = page.evaluate("document.body.innerText")
    assert "同时出现组" not in body, "region_events 下步骤级卡片应隐藏"


def test_建两条规则_画区域_全结构落库(page, base_url, api_url):
    pid, name = _mk_project(api_url)
    _open_logic_tab(page, base_url, name)
    card = _rules_card(page)

    rule1 = _add_overlap_rule(page, card, "测硬度", "测硬度笔", "工件")

    # 确认时长秒基门槛 (2026-07 帧率解耦): UI 填 0.3s
    ms_input = rule1.locator(
        "div:has(> label:has-text('确认时长')) .el-input-number input").first
    ms_input.scroll_into_view_if_needed()
    ms_input.fill("0.3")
    ms_input.press("Enter")
    time.sleep(0.4)

    # 规则 2: 切 region_exit → 结算开关自动开 + 画判定区域
    card.locator("button:has-text('新增动作')").click()
    time.sleep(0.6)
    rule2 = card.locator("div.bg-slate-900").filter(has_text="动作 2").first
    rule2.locator("input[placeholder*='动作名']").fill("下工件")
    rule2.locator(".el-select").first.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:has-text('出区消失')").last.click()
    time.sleep(0.6)
    assert rule2.locator(".el-switch.is-checked").count() >= 1, \
        "切到出区消失类型后结算周期开关应自动开启"
    _fill_creatable_select(page, rule2.locator(
        ".el-select:has-text('如 测硬度笔')").first, "工件")

    rule2.locator("button:has-text('绘制区域')").click()
    time.sleep(1.2)
    roi_dlg = page.locator(".el-dialog:has-text('绘制区域事件规则')")
    assert roi_dlg.count() > 0, "规则判定区域应复用 ROI 编辑器"
    canvas = roi_dlg.locator("canvas").first
    box = canvas.bounding_box()
    for rx, ry in [(0.82, 0.05), (0.98, 0.05), (0.98, 0.95), (0.82, 0.95)]:
        page.mouse.click(box["x"] + box["width"] * rx, box["y"] + box["height"] * ry)
        time.sleep(0.2)
    roi_dlg.locator("button:has-text('完成绘制')").click()
    time.sleep(0.4)
    roi_dlg.locator("button:has-text('保存 ROI')").click()
    time.sleep(0.8)
    assert "已标定" in page.evaluate("document.body.innerText"), \
        "区域保存后规则行应显示已标定"

    # 每类置信度: 测硬度笔 0.2
    conf_row = card.locator("div.flex.items-center.gap-2").filter(
        has_text="测硬度笔").first
    conf_input = conf_row.locator("input").first
    conf_input.fill("0.2")
    conf_input.press("Enter")
    time.sleep(0.4)

    # 顺序校验: 开启 + 点动作名标签追加 测硬度→下工件 (tag 式点选 UI)
    seq_block = card.locator("div.bg-slate-900").filter(has_text="流程顺序校验").first
    seq_block.locator(".el-switch").first.click()
    time.sleep(0.5)
    seq_block.locator("button:has-text('+测硬度')").click()
    time.sleep(0.3)
    seq_block.locator("button:has-text('+下工件')").click()
    time.sleep(0.4)

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    re_cfg = (detail.get("pipeline_config") or {}).get("region_events") or {}
    rules = re_cfg.get("rules") or []
    by_name = {r.get("name"): r for r in rules}
    assert set(by_name) == {"测硬度", "下工件"}, f"两条规则应落库: {list(by_name)}"
    hard, exit_ = by_name["测硬度"], by_name["下工件"]
    assert hard["type"] == "overlap"
    assert hard["subject_label"] == "测硬度笔" and hard["object_label"] == "工件"
    assert abs(hard.get("min_seconds", 0) - 0.3) < 1e-6, \
        f"确认时长秒基应落库: {hard.get('min_seconds')}"
    assert exit_["type"] == "region_exit" and exit_["settle"] is True
    assert "min_seconds" not in exit_, "region_exit 规则不应带确认时长键"
    assert len(exit_.get("region") or []) == 4, f"判定区域应落库: {exit_.get('region')}"
    assert abs((re_cfg.get("class_conf") or {}).get("测硬度笔", 0) - 0.2) < 1e-6
    seq = re_cfg.get("sequence_check") or {}
    assert seq.get("enabled") is True and seq.get("order") == ["测硬度", "下工件"]

    # 回显: 刷新后规则完整呈现
    _open_logic_tab(page, base_url, name)
    body = page.evaluate("document.body.innerText")
    assert "测硬度" in body and "下工件" in body and "已标定" in body


def test_结算判定与进区规则落库(page, base_url, api_url):
    """v3.32+ 增量: 结算判定 (自定义缺/重复→事件) + region_enter 规则类型 +
    锚点半截配置被守门剔除, UI→落库全链路。"""
    pid, name = _mk_project(api_url)
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={"events_config": [
        {"id": 1, "name": "合格(OK)", "actions": []},
        {"id": 2, "name": "不合格(NG)", "actions": []},
    ]}, timeout=5).raise_for_status()
    _open_logic_tab(page, base_url, name)
    card = _rules_card(page)

    _add_overlap_rule(page, card, "测硬度", "测硬度笔", "工件")

    # 规则 2: 进区/驻留 类型 + 画区域 + 开锚点但不标定 (保存时应被剔除)
    card.locator("button:has-text('新增动作')").click()
    time.sleep(0.6)
    rule2 = card.locator("div.bg-slate-900").filter(has_text="动作 2").first
    rule2.locator("input[placeholder*='动作名']").fill("上料")
    rule2.locator(".el-select").first.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:has-text('进区/驻留')").last.click()
    time.sleep(0.6)
    assert "驻留超时告警" in page.evaluate("document.body.innerText"), \
        "进区规则应显示驻留告警用法提示"
    _fill_creatable_select(page, rule2.locator(
        ".el-select:has-text('如 测硬度笔')").first, "工件")
    rule2.locator("button:has-text('绘制区域')").click()
    time.sleep(1.2)
    roi_dlg = page.locator(".el-dialog:has-text('绘制区域事件规则')")
    canvas = roi_dlg.locator("canvas").first
    box = canvas.bounding_box()
    for rx, ry in [(0.5, 0.4), (0.9, 0.4), (0.9, 0.95), (0.5, 0.95)]:
        page.mouse.click(box["x"] + box["width"] * rx, box["y"] + box["height"] * ry)
        time.sleep(0.2)
    roi_dlg.locator("button:has-text('完成绘制')").click()
    time.sleep(0.4)
    roi_dlg.locator("button:has-text('保存 ROI')").click()
    time.sleep(0.8)
    # 锚点跟随: 只开开关不抓标定框 → 半截配置
    anchor_row = rule2.locator("label:has-text('区域跟随锚点')")
    anchor_row.locator(".el-switch").first.click()
    time.sleep(0.5)
    assert "未标定（保存时会被忽略）" in page.evaluate("document.body.innerText")

    # 结算判定: 缺"测硬度" → 不合格(NG)
    settle_block = card.locator("div.bg-slate-900").filter(has_text="结算判定").first
    settle_block.locator("button:has-text('新增判定')").click()
    time.sleep(0.6)
    row = settle_block.locator("div.bg-slate-800").first
    # 默认 match=missing; 依次选目标动作 + 结算事件
    row.locator(".el-select:has-text('目标动作')").first.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:has-text('测硬度')").last.click()
    time.sleep(0.4)
    row.locator(".el-select:has-text('选择事件')").first.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:has-text('不合格(NG)')").last.click()
    time.sleep(0.4)

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    re_cfg = (detail.get("pipeline_config") or {}).get("region_events") or {}
    by_name = {r.get("name"): r for r in (re_cfg.get("rules") or [])}
    assert set(by_name) == {"测硬度", "上料"}, f"两条规则应落库: {list(by_name)}"
    enter = by_name["上料"]
    assert enter["type"] == "region_enter" and enter["settle"] is False
    assert len(enter.get("region") or []) == 4, f"进区区域应落库: {enter.get('region')}"
    assert "anchor" not in enter, f"未标定锚点应被守门剔除: {enter.get('anchor')}"
    srules = re_cfg.get("settlement_rules") or []
    assert srules == [{"match": "missing", "target": "测硬度", "event_id": 2}], \
        f"结算判定应落库: {srules}"


def test_空名规则被保存守门剔除(page, base_url, api_url):
    pid, name = _mk_project(api_url)
    _open_logic_tab(page, base_url, name)
    card = _rules_card(page)

    _add_overlap_rule(page, card, "扫码", "扫码枪", "工件")
    # 再加一条不填名字的空规则
    card.locator("button:has-text('新增动作')").click()
    time.sleep(0.6)

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    rules = ((detail.get("pipeline_config") or {}).get("region_events") or {}).get("rules") or []
    assert [r.get("name") for r in rules] == ["扫码"], \
        f"空名规则应被守门剔除: {[r.get('name') for r in rules]}"
