"""逻辑设置 Tab（LogicConfigTab.vue 外置组件, 拆分批次 P-4）CI E2E 回归。

覆盖:
  1. 五种逻辑模式各自的专属卡片按 v-if 条件渲染（外置组件五分支守门未破坏）
  2. sequential 加序列行选步骤 → 保存 → pipeline_config.sequence_order 落库
  3. custom 新增周期性规则 + 快捷建事件绑定 → 保存 → 落库（emit/平移函数双向）

资源用 __e2e_ 前缀, conftest 自动清理。
"""
from __future__ import annotations

import time
import uuid

import requests

from .conftest import E2E_PREFIX

MODE_CARDS = {
    "sequential": ["结算方式", "超时结算", "顺序模式 - 步骤排序", "NG 判定与处置", "结算冷却"],
    "detection": ["检测模式 - 需检测的步骤", "超时结算", "NG 判定与处置"],
    "custom": ["自定义模式 - 条件配置", "周期性强制动作", "NG 判定与处置"],
    "tracking": ["跟踪模式 - 物品清点配置", "结算冷却"],
    "per_item": ["逐件覆盖 — 通用参数", "逐件覆盖 — 每个步骤的角色"],
}


def _mk_project(api_url, mode, steps=None):
    name = f"{E2E_PREFIX}lg_{mode[:4]}_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/projects", json={
        "name": name, "task_type": "detection", "logic_mode": mode,
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    if steps:
        requests.put(f"{api_url}/api/v1/projects/{pid}",
                     json={"steps_config": steps}, timeout=5).raise_for_status()
    return pid, name


def _open_logic_tab(page, base_url, name, tab="逻辑设置"):
    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=15000)
    # 同 hash URL 二次 goto 不触发真实导航 → 项目列表停留旧快照, 必须显式 reload
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    # 项目多时新建卡片可能在侧栏可视区外 → 先用搜索框过滤
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    card = page.locator(f"div.p-4:has-text('{name}')").first
    if card.count() == 0:
        card = page.get_by_text(name, exact=False).first
    card.click(timeout=5000)
    time.sleep(0.8)
    page.locator(f".el-tabs__item:has-text('{tab}')").first.click()
    time.sleep(0.8)
    return page.evaluate("document.body.innerText")


def test_五模式专属卡片按条件渲染(page, base_url, api_url):
    for mode, cards in MODE_CARDS.items():
        _pid, name = _mk_project(api_url, mode)
        body = _open_logic_tab(page, base_url, name)
        for c in cards:
            assert c in body, f"{mode} 模式应渲染卡片「{c}」"


def test_检测模式超时结算_可见可配_保存落库(page, base_url, api_url):
    """2026-08-21 现场缺口回归: 空闲/周期超时此前挂在仅顺序模式渲染的
    「结算方式」卡里, 检测模式(缸体判型)无从配置。拆出「超时结算」独立卡后,
    检测模式必须可见可改且保存落库到 pipeline_config。"""
    pid, name = _mk_project(api_url, "detection", steps=[
        {"id": 1, "label": "区A", "name": "区域A", "enabled": True},
    ])
    body = _open_logic_tab(page, base_url, name)
    assert "超时结算" in body, "检测模式应渲染「超时结算」卡"
    page.screenshot(path="/tmp/t4_timeout_card.png")
    card = page.locator(".el-card:has-text('超时结算')").first
    inputs = card.locator(".el-input-number input")
    inputs.nth(0).fill("30")     # 空闲超时
    inputs.nth(1).fill("300")    # 周期超时
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    pc = detail.get("pipeline_config") or {}
    assert pc.get("idle_timeout_seconds") == 30, \
        f"空闲超时应落库 30, 实际 {pc.get('idle_timeout_seconds')}"
    assert pc.get("cycle_max_duration") == 300, \
        f"周期超时应落库 300, 实际 {pc.get('cycle_max_duration')}"


def test_sequential序列编辑_保存落库(page, base_url, api_url):
    pid, name = _mk_project(api_url, "sequential", steps=[
        {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
    ])
    _open_logic_tab(page, base_url, name)
    page.locator("button:has-text('添加步骤')").first.click()
    time.sleep(0.5)
    page.locator(".el-card:has-text('步骤排序') .el-select").last.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:visible", has_text="step_a").first.click()
    time.sleep(0.5)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    seq = (detail.get("pipeline_config") or {}).get("sequence_order") or []
    assert seq and seq[-1].get("step_id") == 1, f"序列应落库, 实际 {seq}"


def test_custom周期规则快捷建事件_落库(page, base_url, api_url):
    pid, name = _mk_project(api_url, "custom")
    _open_logic_tab(page, base_url, name)
    page.locator("button:has-text('+ 新增规则')").click()
    time.sleep(0.5)
    page.locator("button:has-text('+ 新建事件')").first.click()
    time.sleep(0.8)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    detail = requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
    pas = (detail.get("pipeline_config") or {}).get("periodic_actions") or []
    evs = detail.get("events_config") or []
    assert len(pas) == 1, f"周期性规则应落库, 实际 {len(pas)}"
    bound = pas[0].get("due_warning_event_id")
    assert bound and any(e.get("id") == bound for e in evs), "快捷事件应创建并绑定"


def _fill_row_number(page, row_text, nth, value):
    """含 row_text 的行里第 nth 个数字输入框真实键入 value (不走 API 写侧)."""
    row = page.locator(f"div.flex:has-text('{row_text}')").last
    inp = row.locator(".el-input-number input").nth(nth)
    inp.click()
    inp.press("ControlOrMeta+a")
    inp.type(str(value), delay=20)
    inp.press("Tab")
    time.sleep(0.3)


def test_容器动作门槛三参数_可见可改_保存落库(page, base_url, api_url):
    """v3.43.1 治"放托盘一次动作结算两次": 动作出现/消失确认帧 + 进箱最小间隔(不应期)
    在进箱确认卡直配 (此前借用步骤字段, 该模式无 UI 入口调不到)。入口可见 → 真实键入 →
    保存 → pipeline 三键落库 → 重进回显。
    信息架构重构后容器装箱块在独立「装箱清点」Tab（混合跟踪时出现）。"""
    pid, name = _mk_project(api_url, "custom")
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "pipeline_config": {
            "custom_based_on": "sequential",
            "custom_mixed_with": "tracking",
            "custom_mix_container_label": "托盘",
            "custom_mix_container_confirm_by_action": True,
            "custom_mix_container_action_label": "放托盘",
        },
        "steps_config": [
            {"id": 1, "label": "放托盘", "enabled": True, "min_frames": 2},
            {"id": 2, "label": "滑块", "enabled": True, "detect_role": "item",
             "count_mode": "track", "expected_count": 24},
        ],
    }, timeout=5).raise_for_status()
    body = _open_logic_tab(page, base_url, name, tab="装箱清点")
    for label in ("动作出现确认帧", "动作消失确认帧", "进箱最小间隔"):
        assert label in body, f"入口断言失败: 找不到「{label}」"
    _fill_row_number(page, "动作出现确认帧", 0, 4)
    _fill_row_number(page, "动作出现确认帧", 1, 20)
    _fill_row_number(page, "进箱最小间隔", 0, 3.5)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    pc = (requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
          .get("pipeline_config") or {})
    got = (pc.get("custom_mix_container_action_min_frames"),
           pc.get("custom_mix_container_action_gone_frames"),
           pc.get("custom_mix_container_action_cooldown_s"))
    assert got == (4, 20, 3.5), f"三参数应落库, 实际 {got}"
    # 重进回显 (水合断言): 不配则出现/消失帧显 0(跟随步骤), 不应期缺省 2
    _open_logic_tab(page, base_url, name, tab="装箱清点")
    row = page.locator("div.flex:has-text('动作出现确认帧')").last
    assert row.locator(".el-input-number input").nth(0).input_value() == "4"
    assert row.locator(".el-input-number input").nth(1).input_value() == "20"
    row2 = page.locator("div.flex:has-text('进箱最小间隔')").last
    assert row2.locator(".el-input-number input").nth(0).input_value() == "3.5"


def test_NG判定与处置卡_可见可配_保存落库(page, base_url, api_url):
    """v3.44 NG 判定与处置统一卡: custom-sequential 露出全部四行场景。
    缺步骤选「挂起等补做」+ 开数量门选收尾步骤 → 保存 →
    pipeline_config.ng_handling 落库 → 重进回显档位。"""
    pid, name = _mk_project(api_url, "custom")
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "pipeline_config": {"custom_based_on": "sequential"},
        "steps_config": [
            {"id": 1, "label": "放油嘴包", "enabled": True},
            {"id": 2, "label": "封箱", "enabled": True},
        ],
    }, timeout=5).raise_for_status()
    body = _open_logic_tab(page, base_url, name)
    assert "NG 判定与处置" in body, "custom-sequential 应渲染「NG 判定与处置」卡"
    assert "结算冷却" in body, "「结算冷却」合并卡应渲染"
    card = page.locator(".el-card:has-text('NG 判定与处置')").first
    # 缺步骤 → 挂起等补做
    card.locator("div.flex:has-text('结算缺步骤时') .el-select").first.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:visible", has_text="挂起等补做").first.click()
    time.sleep(0.3)
    # 数量门开关 + 选收尾步骤
    card.locator("div.flex:has-text('数量不足禁收尾') .el-switch").first.click()
    time.sleep(0.3)
    card.locator("div.flex:has-text('数量不足禁收尾') .el-select").first.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:visible", has_text="放油嘴包").first.click()
    page.keyboard.press("Escape")
    time.sleep(0.3)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    pc = (requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
          .get("pipeline_config") or {})
    ngh = pc.get("ng_handling") or {}
    assert ngh.get("missing_step") == "hold", f"缺步骤档位应落库 hold, 实际 {ngh}"
    assert ngh.get("gate_enabled") is True, f"数量门应落库开, 实际 {ngh}"
    assert ngh.get("gate_steps") == ["放油嘴包"], f"门步骤应落库, 实际 {ngh}"
    # 重进回显: 挂起档 + 数量门开
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('NG 判定与处置')").first
    assert "挂起等补做" in card.inner_text(), "缺步骤档位应回显挂起"
    assert card.locator(".el-switch.is-checked").count() >= 1, "数量门开关应回显开"


def test_计数组合判定表_可见可配_保存落库回显(page, base_url, api_url):
    """v3.48 计数组合判定表 (检测模式纯视觉判型): detection 项目露出卡片 →
    开启 → 选两个判型标签 → 添加机型行填数量/tag → 保存 →
    pipeline_config.combo_table 落库 → 重进回显。"""
    pid, name = _mk_project(api_url, "detection", steps=[
        {"id": 1, "label": "区A", "name": "区域A", "enabled": True},
        {"id": 2, "label": "区B", "name": "区域B", "enabled": True},
        {"id": 3, "label": "收尾", "name": "收尾", "enabled": True},
    ])
    body = _open_logic_tab(page, base_url, name)
    assert "计数组合判定表" in body, "detection 模式应渲染「计数组合判定表」卡"
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    # 开启开关 → 展开配置区
    card.locator(".el-switch").first.click()
    time.sleep(0.5)
    # 选参与判型标签 区A/区B
    card.locator(".el-form-item:has-text('参与判型的步骤标签') .el-select").first.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:visible", has_text="区A").first.click()
    page.locator(".el-select-dropdown__item:visible", has_text="区B").first.click()
    page.keyboard.press("Escape")
    time.sleep(0.3)
    # 添加机型行: counts (5, 4), verdict 默认 OK, tag
    card.locator("button:has-text('添加机型行')").click()
    time.sleep(0.5)
    row = card.locator("div.flex.items-center.gap-2").last
    for i, v in enumerate((5, 4)):
        inp = row.locator(".el-input-number input").nth(i)
        inp.click()
        inp.press("ControlOrMeta+a")
        inp.type(str(v), delay=20)
        inp.press("Tab")
        time.sleep(0.2)
    row.locator("input[placeholder*='4缸']").fill("4缸-含挺柱")
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    pc = (requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
          .get("pipeline_config") or {})
    cmb = pc.get("combo_table") or {}
    assert cmb.get("enabled") is True, f"combo_table 应落库开, 实际 {cmb}"
    assert cmb.get("labels") == ["区A", "区B"], f"labels 应落库, 实际 {cmb}"
    assert cmb.get("rows") == [{"counts": [5, 4], "verdict": "OK",
                                "tag": "4缸-含挺柱", "plc_code": ""}], \
        f"行应落库, 实际 {cmb}"
    # 重进回显
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    assert card.locator(".el-switch.is-checked").count() >= 1, "开关应回显开"
    assert "区A" in card.inner_text(), "参与判型标签应回显"
    assert card.locator("input[placeholder*='4缸']").first.input_value() == "4缸-含挺柱", \
        "机型 tag 应回显"
    row = card.locator("div.flex.items-center.gap-2").last
    assert row.locator(".el-input-number input").nth(0).input_value() == "5"
    assert row.locator(".el-input-number input").nth(1).input_value() == "4"


def test_计数口径positional_可切可配_保存落库回显(page, base_url, api_url):
    """v3.48.x 计数口径: detection 项目 combo 卡露出「计数口径」radio →
    切「按位置去重」→ 追踪参数网格展开 → 改 IoU → 保存 →
    combo_table.count_mode/tracking 落库 → 重进回显。"""
    pid, name = _mk_project(api_url, "detection", steps=[
        {"id": 1, "label": "区A", "name": "区域A", "enabled": True},
        {"id": 2, "label": "收尾", "name": "收尾", "enabled": True},
    ])
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "pipeline_config": {"combo_table": {
            "enabled": True, "labels": ["区A"],
            "rows": [{"counts": [5], "verdict": "OK", "tag": "T"}],
        }},
    }, timeout=5).raise_for_status()
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    card.scroll_into_view_if_needed()
    assert "计数口径" in card.inner_text(), "combo 卡应露出「计数口径」"
    card.locator(".el-radio-button:has-text('按位置去重')").click()
    time.sleep(0.5)
    body = card.inner_text()
    for k in ("同位置判定 IoU", "新位置确认帧数", "候选保留节拍"):
        assert k in body, f"positional 追踪参数「{k}」应展开"
    trk = card.locator(".combo-tracking").first
    inp = trk.locator(".el-form-item:has-text('同位置判定 IoU') .el-input-number input").first
    inp.click()
    inp.press("ControlOrMeta+a")
    inp.type("0.5", delay=20)
    inp.press("Tab")
    time.sleep(0.3)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    cmb = ((requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
            .get("pipeline_config") or {}).get("combo_table") or {})
    assert cmb.get("count_mode") == "positional", f"count_mode 应落库, 实际 {cmb}"
    assert (cmb.get("tracking") or {}).get("iou") == 0.5, f"tracking.iou 应落库, 实际 {cmb}"
    # 重进回显
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    card.scroll_into_view_if_needed()
    assert card.locator(".el-radio-button.is-active:has-text('按位置去重')").count() >= 1, \
        "计数口径应回显 positional"
    got = card.locator(
        ".el-form-item:has-text('同位置判定 IoU') .el-input-number input").first.input_value()
    assert got == "0.5", f"IoU 应回显 0.5, 实际 {got}"


def test_监控画锁定框开关_默认开_关掉落库回显(page, base_url, api_url):
    """v3.48.x 锁定框显示开关: positional 展开后露出「监控画锁定框」且默认开 →
    关掉 → 保存 → combo_table.show_lock_overlay=false 落库 → 重进回显关。"""
    pid, name = _mk_project(api_url, "detection", steps=[
        {"id": 1, "label": "区A", "name": "区域A", "enabled": True},
        {"id": 2, "label": "收尾", "name": "收尾", "enabled": True},
    ])
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "pipeline_config": {"combo_table": {
            "enabled": True, "labels": ["区A"], "count_mode": "positional",
            "rows": [{"counts": [5], "verdict": "OK", "tag": "T"}],
        }},
    }, timeout=5).raise_for_status()
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    card.scroll_into_view_if_needed()
    sw = card.locator(".combo-lock-overlay-switch").first
    assert sw.count() == 1, "positional 展开后应露出「监控画锁定框」开关"
    assert "is-checked" in (sw.get_attribute("class") or ""), "锁定框显示开关应默认开"
    sw.click()
    time.sleep(0.3)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    cmb = ((requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
            .get("pipeline_config") or {}).get("combo_table") or {})
    assert cmb.get("show_lock_overlay") is False, f"show_lock_overlay 应落库 false, 实际 {cmb}"
    # 重进回显关
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    card.scroll_into_view_if_needed()
    sw = card.locator(".combo-lock-overlay-switch").first
    assert "is-checked" not in (sw.get_attribute("class") or ""), "重进应回显关"


def test_切步数量门开关_默认关_打开落库回显(page, base_url, api_url):
    """v3.49 切步数量门: 判定表卡露出开关且默认关 → 打开 + 选提示事件 + 填 PLC 码
    → 保存落库 step_guard.enabled/action/event_id + 行 plc_code → 重进回显开。"""
    pid, name = _mk_project(api_url, "detection", steps=[
        {"id": 1, "label": "区A", "name": "区域A", "enabled": True},
        {"id": 2, "label": "区B", "name": "区域B", "enabled": True},
        {"id": 3, "label": "收尾", "name": "收尾", "enabled": True},
    ])
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": []},
            {"id": 2, "name": "不合格(NG)", "actions": []},
            {"id": 3, "name": "数量门警告", "actions": []},
        ],
        "pipeline_config": {"combo_table": {
            "enabled": True, "labels": ["区A", "区B"], "count_mode": "positional",
            "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "机型X"}],
        }},
    }, timeout=5).raise_for_status()
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    card.scroll_into_view_if_needed()
    sw = card.locator(".combo-guard-switch").first
    assert sw.count() == 1, "应露出「切步数量门」开关"
    assert "is-checked" not in (sw.get_attribute("class") or ""), "数量门应默认关"
    sw.click()
    time.sleep(0.5)
    txt = card.inner_text()
    assert all(k in txt for k in ("少装/数量不符", "超装", "按已装数量收窄机型",
                                  "期望数量依据", "违规处置")), f"配置区未展开: {txt[:300]}"
    # 填行级 PLC 码
    card.locator("input[placeholder='如 4']").first.fill("4")
    card.locator(".combo-guard-event").click()
    time.sleep(0.4)
    # 2026-08-12 产品决策: 系统 合格(OK)/不合格(NG) 结算事件不得出现在提示事件下拉
    dd_items = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
    dd_texts = [dd_items.nth(i).inner_text() for i in range(dd_items.count())]
    assert not any("合格" in t for t in dd_texts), \
        f"提示事件下拉不得含系统 合格/不合格 结算事件, 实际 {dd_texts}"
    page.locator(".el-select-dropdown:visible .el-select-dropdown__item"
                 ":has-text('数量门警告')").first.click()
    time.sleep(0.3)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    cmb = ((requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
            .get("pipeline_config") or {}).get("combo_table") or {})
    sg = cmb.get("step_guard") or {}
    assert sg.get("enabled") is True and sg.get("action") == "hint", f"实际 {sg}"
    assert sg.get("event_id") == 3, f"event_id 应落库 3, 实际 {sg}"
    assert (cmb.get("rows") or [{}])[0].get("plc_code") == "4", f"行 plc_code 应落库, 实际 {cmb}"
    # 重进回显开
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    sw = card.locator(".combo-guard-switch").first
    assert "is-checked" in (sw.get_attribute("class") or ""), "重进应回显数量门开"


def test_二期新配置_顺序检查挂起大字卡按标签覆盖_落库回显(page, base_url, api_url):
    """v3.49 二期: 数量门卡露出 顺序检查/已补齐事件/结算处置档, combo 卡露出
    大字实时卡 + 按标签覆盖折叠表 → 全部配置 → 保存落库全部新键 → 重进回显。"""
    pid, name = _mk_project(api_url, "detection", steps=[
        {"id": 1, "label": "区A", "name": "区域A", "enabled": True},
        {"id": 2, "label": "区B", "name": "区域B", "enabled": True},
        {"id": 3, "label": "收尾", "name": "收尾", "enabled": True},
    ])
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": []},
            {"id": 2, "name": "不合格(NG)", "actions": []},
            {"id": 3, "name": "补齐提示", "actions": []},
        ],
        "pipeline_config": {"combo_table": {
            "enabled": True, "labels": ["区A", "区B"], "count_mode": "positional",
            "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "机型X"}],
            "step_guard": {"enabled": True},
        }},
    }, timeout=5).raise_for_status()
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    card.scroll_into_view_if_needed()
    # --- 数量门: 顺序检查默认关 → 打开 ---
    osw = card.locator(".combo-guard-order-switch").first
    assert osw.count() == 1, "应露出「工序顺序检查」开关"
    assert "is-checked" not in (osw.get_attribute("class") or ""), "顺序检查应默认关"
    osw.click()
    time.sleep(0.3)
    # --- 已补齐事件 ---
    card.locator(".combo-guard-resolved-event").click()
    page.locator(".el-select-dropdown:visible .el-select-dropdown__item"
                 ":has-text('补齐提示')").first.click()
    time.sleep(0.3)
    # --- 结算处置档 → 挂起等补 + 超时 60 ---
    card.locator(".combo-guard-settle-mismatch").click()
    page.locator(".el-select-dropdown:visible .el-select-dropdown__item"
                 ":has-text('挂起等补')").first.click()
    time.sleep(0.3)
    tmo = card.locator(".combo-guard-hold-timeout input").first
    tmo.click()
    tmo.press("ControlOrMeta+a")
    tmo.type("60", delay=20)
    tmo.press("Tab")
    time.sleep(0.3)
    # --- 大字实时卡: 默认关 → 打开 + 普通字号 (2026-08-13 停靠看板化后无「位置」项) ---
    lsw = card.locator(".combo-live-switch").first
    assert lsw.count() == 1, "应露出「监控大字实时卡」开关"
    assert "is-checked" not in (lsw.get_attribute("class") or ""), "大字卡应默认关"
    lsw.click()
    time.sleep(0.4)
    card.locator(".el-radio-button:has-text('普通')").click()
    assert card.locator(".el-radio-button:has-text('画面下方')").count() == 0, \
        "停靠看板化后不应再有「位置」选项"
    time.sleep(0.3)
    # --- 按标签覆盖: 展开 → 区A 确认帧数填 1 ---
    tpl = card.locator(".combo-tpl").first
    assert tpl.count() == 1, "positional 下应露出「按标签单独覆盖追踪参数」"
    tpl.locator("text=展开").first.click()
    time.sleep(0.4)
    row_a = tpl.locator("div.flex.items-center.gap-2:has-text('区A')").first
    inp = row_a.locator(".el-input-number input").nth(2)  # 第3列=确认帧数
    inp.click()
    inp.type("1", delay=20)
    inp.press("Tab")
    time.sleep(0.3)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    cmb = ((requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
            .get("pipeline_config") or {}).get("combo_table") or {})
    sg = cmb.get("step_guard") or {}
    assert sg.get("check_order") is True, f"check_order 应落库, 实际 {sg}"
    assert sg.get("resolved_event_id") == 3, f"resolved_event_id 应落库 3, 实际 {sg}"
    assert sg.get("on_settle_mismatch") == "hold", f"结算处置档应落库 hold, 实际 {sg}"
    assert sg.get("hold_timeout_s") == 60, f"挂起超时应落库 60, 实际 {sg}"
    ld = cmb.get("live_display") or {}
    assert ld.get("enabled") is True and ld.get("size") == "normal", \
        f"live_display 应落库, 实际 {ld}"
    tplj = cmb.get("tracking_per_label") or {}
    assert tplj.get("区A") == {"min_consecutive": 1}, \
        f"按标签覆盖应只存有效键, 实际 {tplj}"
    # 重进回显
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    card.scroll_into_view_if_needed()
    assert "is-checked" in (card.locator(".combo-guard-order-switch").first
                            .get_attribute("class") or ""), "顺序检查应回显开"
    assert "is-checked" in (card.locator(".combo-live-switch").first
                            .get_attribute("class") or ""), "大字卡应回显开"
    tpl = card.locator(".combo-tpl").first
    tpl.locator("text=展开").first.click()
    time.sleep(0.4)
    row_a = tpl.locator("div.flex.items-center.gap-2:has-text('区A')").first
    assert row_a.locator(".el-input-number input").nth(2).input_value() == "1", \
        "区A 确认帧数覆盖应回显 1"


def test_数量门PLC连接下拉_列出已建连接并选中落库(page, base_url, api_url):
    """v3.49 二期现场修复回归: 后端 GET /plc/connections 返回 {"connections": [...]},
    下拉曾因前端误取 data.items 永远 No data (PLC 已连接也选不了)。
    验证: 建 mock 连接 → 下拉能列出 → 选中 → 保存落库 plc_connection_id。"""
    conn_name = f"{E2E_PREFIX}plc_combo_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{api_url}/api/v1/plc/connections", json={
        "name": conn_name, "driver": "mock", "enabled": False,
        "conn_params": {"store_id": conn_name, "poll_interval_ms": 100},
        "points": [{"key": "cyl_type", "addr": "m0", "type": "int16", "dir": "read"}],
        "read_rules": [], "write_rules": [], "options": {},
    }, timeout=10)
    assert r.status_code == 200, f"建 mock PLC 连接失败: {r.text}"
    conn_id = r.json()["id"]
    try:
        pid, name = _mk_project(api_url, "detection", steps=[
            {"id": 1, "label": "区A", "name": "区域A", "enabled": True},
            {"id": 2, "label": "收尾", "name": "收尾", "enabled": True},
        ])
        requests.put(f"{api_url}/api/v1/projects/{pid}", json={
            "pipeline_config": {"combo_table": {
                "enabled": True, "labels": ["区A"], "count_mode": "positional",
                "rows": [{"counts": [2], "verdict": "OK", "tag": "机型X"}],
                "step_guard": {"enabled": True, "expected_source": "auto"},
            }},
        }, timeout=5).raise_for_status()
        _open_logic_tab(page, base_url, name)
        card = page.locator(".el-card:has-text('计数组合判定表')").first
        card.scroll_into_view_if_needed()
        sel = card.locator(".combo-guard-plc-conn").first
        assert sel.count() == 1, "期望数量依据非 table 时应露出「PLC 连接」下拉"
        sel.click()
        time.sleep(0.8)
        opt = page.locator(".el-select-dropdown:visible .el-select-dropdown__item"
                           f":has-text('{conn_name}')").first
        assert opt.count() == 1, \
            "PLC 连接下拉应列出已建连接 (回归: data.connections 而非 data.items)"
        opt.click()
        time.sleep(0.3)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        cmb = ((requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
                .get("pipeline_config") or {}).get("combo_table") or {})
        sg = cmb.get("step_guard") or {}
        assert sg.get("plc_connection_id") == conn_id, \
            f"plc_connection_id 应落库 {conn_id}, 实际 {sg}"
    finally:
        requests.delete(f"{api_url}/api/v1/plc/connections/{conn_id}", timeout=5)


def test_legacy收尾防呆键_迁移为ng_handling回显(page, base_url, api_url):
    """v3.44 老键 (closing_guard/ng_remediation) 项目 → 前端加载合成 ng_handling,
    统一卡回显等价档位; 保存后新块落库。"""
    pid, name = _mk_project(api_url, "custom")
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "pipeline_config": {
            "custom_based_on": "sequential",
            "closing_guard": {"gate_enabled": True, "gate_steps": ["封箱"],
                              "hold_enabled": True, "hold_timeout_s": 60, "event_id": 4},
            "ng_remediation": {"enabled": True, "allow_step": True, "allow_count": True},
        },
        "steps_config": [{"id": 1, "label": "封箱", "enabled": True}],
    }, timeout=5).raise_for_status()
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('NG 判定与处置')").first
    txt = card.inner_text()
    assert "挂起等补做" in txt, f"legacy hold 应迁为挂起档, 卡内容: {txt[:200]}"
    assert card.locator(".el-switch.is-checked").count() >= 1, "legacy 数量门应迁移回显开"
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    pc = (requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
          .get("pipeline_config") or {})
    ngh = pc.get("ng_handling") or {}
    assert ngh.get("missing_step") == "hold" and ngh.get("hold_timeout_s") == 60, f"实际 {ngh}"
    assert ngh.get("gate_enabled") is True and ngh.get("gate_steps") == ["封箱"], f"实际 {ngh}"
    assert ngh.get("short_count") == "ack", f"legacy 补数量应迁为 ack, 实际 {ngh}"
    assert ngh.get("gate_event_id") == 4 and ngh.get("hold_event_id") == 4, f"实际 {ngh}"


def test_缺步提前发现_挂起档可见_保存落库(page, base_url, api_url):
    """SY8 收编: 缺步骤=挂起等补做时露出「缺步提前发现」开关, 保存进 ng_handling。"""
    pid, name = _mk_project(api_url, "custom")
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "pipeline_config": {"custom_based_on": "sequential"},
        "steps_config": [
            {"id": 1, "label": "放油嘴包", "enabled": True},
            {"id": 2, "label": "封箱", "enabled": True},
        ],
    }, timeout=5).raise_for_status()
    _open_logic_tab(page, base_url, name)
    card = page.locator(".el-card:has-text('NG 判定与处置')").first
    card.locator("div.flex:has-text('结算缺步骤时') .el-select").first.click()
    time.sleep(0.5)
    page.locator(".el-select-dropdown__item:visible", has_text="挂起等补做").first.click()
    time.sleep(0.4)
    assert "缺步提前发现" in card.inner_text(), "挂起档应露出「缺步提前发现」"
    row = card.locator("div.flex:has-text('缺步提前发现')").first
    row.locator(".el-switch").first.click()
    time.sleep(0.3)
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    pc = (requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
          .get("pipeline_config") or {})
    ngh = pc.get("ng_handling") or {}
    assert ngh.get("missing_step") == "hold"
    assert ngh.get("missing_step_early") is True, f"缺步提前发现应落库 True, 实际 {ngh}"


def test_装箱清点取值三开关与虚拟步骤_可见_保存落库(page, base_url, api_url):
    """SY8 收编: 取值锚点 / max-latest 口径 / 看全下修 / 整箱虚拟步骤。"""
    pid, name = _mk_project(api_url, "custom")
    requests.put(f"{api_url}/api/v1/projects/{pid}", json={
        "pipeline_config": {
            "custom_based_on": "sequential",
            "custom_mixed_with": "tracking",
            "custom_mix_container_label": "托盘",
            "custom_mix_container_confirm_by_action": True,
            "custom_mix_container_action_label": "放托盘",
            "custom_mix_container_stable_min_frames": 6,
            "custom_mix_container_slot_check_label": "空槽",
            "custom_mix_container_slot_total": 24,
        },
        "steps_config": [
            {"id": 1, "label": "放托盘", "enabled": True},
            {"id": 2, "label": "滑块", "enabled": True, "detect_role": "item"},
            {"id": 3, "label": "托盘", "enabled": True},
            {"id": 4, "label": "空槽", "enabled": True},
        ],
    }, timeout=5).raise_for_status()
    body = _open_logic_tab(page, base_url, name, tab="装箱清点")
    page.screenshot(path="/tmp/t4_mix_box_tab.png")
    for label in ("取值锚点", "稳定值取值口径", "看全下修", "整箱装箱合并为一步"):
        assert label in body, f"装箱清点应渲染「{label}」"
    _fill_row_number(page, "取值锚点", 0, 1.5)
    page.locator("div.flex:has-text('稳定值取值口径') .el-radio-button:has-text('取最近')").first.click()
    for label in ("看全下修", "整箱装箱合并为一步"):
        row = page.locator(f".mix-row:has-text('{label}')").first
        row.evaluate("el => el.scrollIntoView({block:'center'})")
        row.locator(".el-switch").first.evaluate("el => el.click()")
    time.sleep(0.3)
    page.locator("input[placeholder*='放滑块']").fill("放滑块")
    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    pc = (requests.get(f"{api_url}/api/v1/projects/{pid}", timeout=5).json()
          .get("pipeline_config") or {})
    assert pc.get("custom_mix_container_stable_anchor_s") == 1.5, \
        f"取值锚点应落库 1.5, 实际 {pc.get('custom_mix_container_stable_anchor_s')}"
    assert pc.get("custom_mix_container_stable_pick") == "latest", \
        f"口径应落库 latest, 实际 {pc.get('custom_mix_container_stable_pick')}"
    assert pc.get("custom_mix_container_slot_verified_drop") is True, \
        f"看全下修应落库 True, 实际 {pc.get('custom_mix_container_slot_verified_drop')}"
    assert pc.get("custom_mix_container_virtual_step") is True
    assert pc.get("custom_mix_container_virtual_step_label") == "放滑块", \
        f"虚拟步骤名应落库, 实际 {pc.get('custom_mix_container_virtual_step_label')}"
