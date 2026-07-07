"""同标签区域拆分（虚拟步骤）+ 工件就位提示 CI E2E 回归 (v3.32)。

覆盖:
  1. 步骤设置 Tab 渲染「同标签区域拆分」+「工件就位提示」两张卡片
  2. 新建拆分规则(四象限模板) → 保存规则 → 虚拟步骤(带拆分徽标)进步骤表
     → 保存配置 → label_splits / split_origin 虚拟步骤 / 序列全部落库
  3. 删除规则 → 虚拟步骤与序列引用级联清理并落库
  4. 就位提示: 开关+锚点+画引导框 → placement_guide 落库
  5. 多轮次规则(v3.32): rounds 段 + 前缀×区域 8 个虚拟步骤落库
  6. 违序即时事件(v3.32): 逻辑设置选事件 → strict_order_violation_event_id 落库

运行时行为(标签改写/状态机/叠加层)由 tests/test_label_split_pipeline.py 与
tests/uat/uat_20260705_label_split.py 覆盖, 本文件只守 UI→落库链路。
资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX


def _mk_project(api_url):
    name = f"{E2E_PREFIX}ls_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={"steps_config": [
        {"id": 1, "label": "打螺丝", "displayLabel": "打螺丝", "enabled": True, "threshold": 50},
        {"id": 2, "label": "前罩", "displayLabel": "前罩", "enabled": True, "threshold": 50},
    ]}, timeout=5).raise_for_status()
    return pid, name


def _open_steps_tab(page, base_url, name):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    # 同 hash URL 二次 goto 不触发真实导航 → 必须显式 reload
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{name}')").first.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
    time.sleep(0.8)


def _fill_creatable_select(page, scope, text):
    """el-select(filterable allow-create): 点开 → 敲字 → 回车创建/选中。"""
    scope.click()
    time.sleep(0.4)
    page.keyboard.type(text, delay=30)
    time.sleep(0.5)
    page.keyboard.press("Enter")
    time.sleep(0.4)


def _create_quadrant_rule(page):
    """新建拆分规则: 原始标签=打螺丝 + 四象限模板 → 保存规则(仅前端内存)。"""
    page.locator("button:has-text('新建拆分规则')").click()
    time.sleep(1.0)
    # 对话框标题随选中标签动态变, 用框内固定文案定位
    dlg = page.locator(".el-dialog:has-text('区域定位方式')")
    assert dlg.count() > 0, "拆分规则编辑器应打开"
    _fill_creatable_select(page, dlg.locator(".el-select").first, "打螺丝")
    dlg.locator("button:has-text('四象限模板')").click()
    time.sleep(0.6)
    dlg.locator("button:has-text('保存规则')").click()
    time.sleep(1.0)


def test_拆分与就位提示卡片渲染(page, base_url, api_url):
    _pid, name = _mk_project(api_url)
    _open_steps_tab(page, base_url, name)
    body = page.evaluate("document.body.innerText")
    assert "同标签区域拆分" in body, "拆分规则卡片应渲染"
    assert "工件就位提示" in body, "就位提示卡片应渲染"
    assert "新建拆分规则" in body


def test_新建四象限规则_虚拟步骤与序列落库(page, base_url, api_url):
    pid, name = _mk_project(api_url)
    _open_steps_tab(page, base_url, name)
    _create_quadrant_rule(page)

    body = page.evaluate("document.body.innerText")
    assert "螺丝1" in body and "拆分" in body, "虚拟步骤(带拆分徽标)应进步骤表"
    assert "固定画面" in body, "规则行应出现在规则表"

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    pc = detail.get("pipeline_config") or {}
    splits = pc.get("label_splits") or []
    assert len(splits) == 1 and splits[0]["source_label"] == "打螺丝"
    assert len(splits[0]["regions"]) == 4
    virt = {s["label"] for s in detail["steps_config"] if s.get("split_origin")}
    assert virt == {f"螺丝{i}" for i in range(1, 5)}, f"虚拟步骤落库不齐: {virt}"
    seq_ids = {it.get("step_id") for it in (pc.get("sequence_order") or [])}
    virt_ids = {s["id"] for s in detail["steps_config"] if s.get("split_origin")}
    assert virt_ids <= seq_ids, "虚拟步骤应自动进序列"


def test_删除规则_级联清理落库(page, base_url, api_url):
    pid, name = _mk_project(api_url)
    _open_steps_tab(page, base_url, name)
    _create_quadrant_rule(page)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    page.locator("tbody tr:has-text('固定画面') button:has-text('删除')").first.click()
    time.sleep(0.8)
    page.locator(".el-message-box button:has-text('删除')").click()
    # 成功 toast 文案含虚拟步骤名, 等它消失再查表
    time.sleep(4.0)
    steps_table = page.locator("div.border.rounded:has-text('标签与检测属性')").first.inner_text()
    assert all(f"螺丝{i}" not in steps_table for i in range(1, 5)), "虚拟步骤应随规则删除消失"

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    pc = detail.get("pipeline_config") or {}
    assert not (pc.get("label_splits") or []), "规则应清空"
    assert not any(s.get("split_origin") for s in detail["steps_config"]), "虚拟步骤应级联移除"
    seq_ids = [it.get("step_id") for it in (pc.get("sequence_order") or [])]
    assert seq_ids in ([], [1], [1, 2], [2, 1]), f"序列不应残留虚拟步骤引用: {seq_ids}"


def test_多轮次规则_八虚拟步骤落库(page, base_url, api_url):
    """四象限 + 多轮次(盖罩×2轮): 前罩/后罩螺丝1~4 共 8 个虚拟步骤落库 + rounds 段持久化。"""
    pid, name = _mk_project(api_url)
    _open_steps_tab(page, base_url, name)

    page.locator("button:has-text('新建拆分规则')").click()
    time.sleep(1.0)
    dlg = page.locator(".el-dialog:has-text('区域定位方式')")
    assert dlg.count() > 0, "拆分规则编辑器应打开"
    _fill_creatable_select(page, dlg.locator(".el-select").first, "打螺丝")
    dlg.locator("button:has-text('四象限模板')").click()
    time.sleep(0.6)
    # 启用多轮次 (对话框里唯一的 el-switch) + 填切换标签, 前缀用默认 前罩/后罩
    dlg.locator(".el-switch").first.click()
    time.sleep(0.5)
    # el-select 的 placeholder 属性在渲染后取不到, 用「多轮次拆分」容器圈定
    rounds_box = dlg.locator("div.border:has-text('多轮次拆分')").last
    _fill_creatable_select(page, rounds_box.locator(".el-select").first, "盖罩")
    dlg.locator("button:has-text('保存规则')").click()
    time.sleep(1.0)

    body = page.evaluate("document.body.innerText")
    assert "前罩螺丝1" in body and "后罩螺丝4" in body, "两轮×4区域的虚拟步骤应进步骤表"

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    pc = detail.get("pipeline_config") or {}
    splits = pc.get("label_splits") or []
    assert len(splits) == 1
    rd = splits[0].get("rounds") or {}
    assert rd.get("enabled") is True and rd.get("trigger_label") == "盖罩"
    assert rd.get("count") == 2 and rd.get("prefixes") == ["前罩", "后罩"]
    virt = {s["label"] for s in detail["steps_config"] if s.get("split_origin")}
    expected = {f"{p}螺丝{i}" for p in ("前罩", "后罩") for i in range(1, 5)}
    assert virt == expected, f"虚拟步骤落库不齐: {virt}"


def test_每轮独立区域落库(page, base_url, api_url):
    """多轮次 + 第2轮独立区域(复制共享再调整): region_overrides 落库 + 虚拟步骤仍按轮展开。"""
    pid, name = _mk_project(api_url)
    _open_steps_tab(page, base_url, name)

    page.locator("button:has-text('新建拆分规则')").click()
    time.sleep(1.0)
    dlg = page.locator(".el-dialog:has-text('区域定位方式')")
    _fill_creatable_select(page, dlg.locator(".el-select").first, "打螺丝")
    dlg.locator("button:has-text('四象限模板')").click()
    time.sleep(0.6)
    dlg.locator(".el-switch").first.click()
    time.sleep(0.5)
    rounds_box = dlg.locator("div.border:has-text('多轮次拆分')").last
    _fill_creatable_select(page, rounds_box.locator(".el-select").first, "盖罩")
    # 切到第2轮作用域 → 复制共享区域生成本轮独立区域
    dlg.locator(".el-radio-button:has-text('第2轮')").first.click()
    time.sleep(0.5)
    dlg.locator("button:has-text('复制共享区域到本轮再调整')").click()
    time.sleep(0.5)
    assert dlg.locator("text=第2轮使用独立区域").count() > 0, "复制后应提示本轮已用独立区域"
    dlg.locator("button:has-text('保存规则')").click()
    time.sleep(1.0)

    body = page.evaluate("document.body.innerText")
    assert "前罩螺丝1" in body and "后罩螺丝4" in body, "虚拟步骤仍应按 2轮×4区域 展开"
    assert "第2轮独立区域" in body, "规则表应标注该规则配了每轮独立区域"

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    splits = (detail.get("pipeline_config") or {}).get("label_splits") or []
    assert len(splits) == 1
    ov = (splits[0].get("rounds") or {}).get("region_overrides") or {}
    assert set(ov.keys()) == {"2"}, f"应只有第2轮的独立区域: {list(ov.keys())}"
    assert {r["name"] for r in ov["2"]} == {"螺丝1", "螺丝2", "螺丝3", "螺丝4"}
    assert all(len(r.get("polygon") or []) >= 3 for r in ov["2"])


def test_锚点跟随规则_抓取标定落库(page, base_url, api_url):
    """锚点跟随模式全 UI 链路: 切定位方式 → 选锚点标签 → 从虚拟画面抓取
    锚点框 → 保存 → mode/anchor_label/anchor_ref 落库。

    需要后端 RUNTIME_MODE=test (synthetic 提供「前罩」检测框供抓取)。
    """
    r = requests.post(f"{api_url}/api/v1/test/synthetic/start", json={
        "scenario_json": {
            "name": "__e2e_anchor_grab", "fps": 30,
            "timeline": [{"from": 0, "to": 30000, "detections": [
                {"label": "前罩", "confidence": 0.97, "bbox": [0.2, 0.2, 0.6, 0.6]},
            ]}],
        },
        "channel": 0, "with_project": False,
    }, timeout=5)
    if r.status_code == 404:
        import pytest
        pytest.skip("后端未开 RUNTIME_MODE=test, synthetic 不可用")
    r.raise_for_status()
    requests.post(f"{api_url}/api/v1/source/detection/start?channel=0",
                  json={"conf": 0.25, "iou": 0.45}, timeout=5).raise_for_status()
    try:
        pid, name = _mk_project(api_url)
        _open_steps_tab(page, base_url, name)

        page.locator("button:has-text('新建拆分规则')").click()
        time.sleep(1.0)
        dlg = page.locator(".el-dialog:has-text('区域定位方式')")
        _fill_creatable_select(page, dlg.locator(".el-select").first, "打螺丝")
        dlg.locator("button:has-text('四象限模板')").click()
        time.sleep(0.6)
        # 切到锚点跟随
        dlg.locator(".el-radio-button:has-text('锚点跟随')").first.click()
        time.sleep(0.5)
        anchor_box = dlg.locator("div.border:has-text('锚点标签')").last
        _fill_creatable_select(page, anchor_box.locator(".el-select").first, "前罩")
        dlg.locator("button:has-text('从当前画面抓取锚点框')").click()
        time.sleep(1.5)
        assert dlg.locator("text=已标定").count() > 0, "抓取后应显示已标定"
        dlg.locator("button:has-text('保存规则')").click()
        time.sleep(1.0)

        body = page.evaluate("document.body.innerText")
        assert "锚点跟随" in body, "规则表应显示锚点跟随定位方式"

        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
        splits = (detail.get("pipeline_config") or {}).get("label_splits") or []
        assert len(splits) == 1
        assert splits[0].get("mode") == "anchor"
        assert splits[0].get("anchor_label") == "前罩"
        ref = splits[0].get("anchor_ref") or {}
        assert ref.get("w", 0) > 0 and ref.get("h", 0) > 0, f"标定框应落库: {ref}"
        assert len(splits[0].get("regions") or []) == 4
    finally:
        requests.post(f"{api_url}/api/v1/source/detection/stop?channel=0", timeout=5)
        requests.post(f"{api_url}/api/v1/test/synthetic/stop?channel=0", timeout=5)


def test_违序即时事件配置落库(page, base_url, api_url):
    """逻辑设置 → 结算方式卡片选违序事件 → strict_order_violation_event_id 落库。"""
    pid, name = _mk_project(api_url)
    _open_steps_tab(page, base_url, name)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(0.8)

    row = page.locator("div:has(> span:has-text('违反严格顺序时立即触发'))").last
    row.locator(".el-select").first.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:has-text('不良(NG)')").last.click()
    time.sleep(0.5)

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    pc = detail.get("pipeline_config") or {}
    assert pc.get("strict_order_violation_event_id") == 2, \
        f"违序事件应落库: {pc.get('strict_order_violation_event_id')}"


def test_就位提示配置落库(page, base_url, api_url):
    pid, name = _mk_project(api_url)
    _open_steps_tab(page, base_url, name)

    guide_card = page.locator("div.border.rounded:has-text('工件就位提示')")
    guide_card.locator(".el-switch").first.click()
    time.sleep(0.5)
    _fill_creatable_select(page, guide_card.locator(".el-select").first, "前罩")
    # 就位后显示策略: 常驻(默认) → 就位后隐藏
    guide_card.locator(".el-select").nth(1).click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:has-text('就位后隐藏')").last.click()
    time.sleep(0.4)
    guide_card.locator("button:has-text('绘制引导框')").click()
    time.sleep(1.2)
    roi_dlg = page.locator(".el-dialog:has-text('绘制工件就位引导框')")
    assert roi_dlg.count() > 0, "就位引导框应复用 ROI 编辑器"
    canvas = roi_dlg.locator("canvas").first
    box = canvas.bounding_box()
    for rx, ry in [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]:
        page.mouse.click(box["x"] + box["width"] * rx, box["y"] + box["height"] * ry)
        time.sleep(0.2)
    roi_dlg.locator("button:has-text('完成绘制')").click()
    time.sleep(0.4)
    roi_dlg.locator("button:has-text('保存 ROI')").click()
    time.sleep(0.8)

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    pg = (detail.get("pipeline_config") or {}).get("placement_guide") or {}
    assert pg.get("enabled") is True
    assert pg.get("anchor_label") == "前罩"
    assert len(pg.get("polygon") or []) >= 3, f"引导框多边形应落库: {pg}"
    assert pg.get("display") == "hide_on_ready", f"显示策略应落库: {pg.get('display')}"
