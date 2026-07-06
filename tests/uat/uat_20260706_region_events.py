# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 区域事件模式 (TP 工位流程监测, v3.32+ 新 logic_mode)。

现场叙事: TP 工厂工位, 模型只检测"物"(测硬度笔/扫码枪/工件/手), 动作事件由
时序规则产出。客户在项目页选「区域事件模式」, 逻辑设置页配两条规则:
「测硬度」= 笔与工件重叠 N 帧;「下工件」= 工件进下料出口区后消失 → 结算周期。
监控页步骤统计按事件名(而非模型类别)计数, 一个工位循环 = 一个 OK 周期。

覆盖:
  A: 项目页 — 逻辑模式单选出现「区域事件模式」; 逻辑设置页规则卡片可增删配置
  B: 规则配置 — overlap 规则(名称/主体/目标) + region_exit 规则(切类型自动回填
     结算开关) + ROI 编辑器画判定区域 + 每类置信度 + 顺序校验开关
  C: 落库双向 — 保存配置 → GET 复核 pipeline_config.region_events 全结构
  D: 运行时 — synthetic 剧本注入 TP 循环 → 事件计数/OK 周期通过 API 出口;
     监控页步骤表显示事件名并计数 (人眼证据 = 截图/录像)

前置: main 栈 — 后端 8001 (RUNTIME_MODE=test) + 前端 6001。
测试项目 __uat_ 前缀, 收尾删除。
"""
from __future__ import annotations

import sys
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"
PNAME = f"__uat_re_{uuid.uuid4().hex[:5]}"
CH = 0

run = UatRun("region_events")
pid = None

STEPS = [
    {"id": 1, "label": "工件", "displayLabel": "工件", "enabled": True, "threshold": 50},
    {"id": 2, "label": "测硬度笔", "displayLabel": "测硬度笔", "enabled": True, "threshold": 50},
    {"id": 3, "label": "扫码枪", "displayLabel": "扫码枪", "enabled": True, "threshold": 50},
    {"id": 4, "label": "手", "displayLabel": "手", "enabled": True, "threshold": 50},
]

EVENTS = [
    {"id": 1, "name": "合格(OK)", "actions": [
        {"counter_name": "合格总数", "delta": 1},
        {"counter_name": "总产量", "delta": 1}]},
    {"id": 2, "name": "不合格(NG)", "actions": [
        {"counter_name": "不良总数", "delta": 1},
        {"counter_name": "总产量", "delta": 1}]},
]

COUNTERS = [
    {"name": "合格总数", "value": 0},
    {"name": "不良总数", "value": 0},
    {"name": "总产量", "value": 0},
]

# 与 tests/test_region_events_pipeline.py 同款 TP 循环剧本 (去掉扫码步骤):
# 测硬度(30帧重叠) → 空档 → 工件进下料出口(10帧) → 消失 → 结算 OK
WORK_ON_TABLE = {"label": "工件", "confidence": 0.9, "bbox": [0.5, 0.4, 0.2, 0.2]}
PEN_ON_WORK = {"label": "测硬度笔", "confidence": 0.6, "bbox": [0.58, 0.48, 0.04, 0.04]}
WORK_IN_C = {"label": "工件", "confidence": 0.9, "bbox": [0.87, 0.4, 0.1, 0.12]}


def _scenario(loops=8):
    """每轮 80 帧 (~1.3s@60fps): 上件→测硬度 30 帧→空档→进出口 10 帧→消失 20 帧。
    多轮循环让监控页有持续活动可看。"""
    tl = []
    f = 0
    for _ in range(loops):
        tl.append({"from": f, "to": f + 9, "detections": [WORK_ON_TABLE]})
        tl.append({"from": f + 10, "to": f + 39,
                   "detections": [WORK_ON_TABLE, PEN_ON_WORK]})
        tl.append({"from": f + 40, "to": f + 49, "detections": [WORK_ON_TABLE]})
        tl.append({"from": f + 50, "to": f + 59, "detections": [WORK_IN_C]})
        tl.append({"from": f + 60, "to": f + 79, "detections": []})
        f += 80
    tl.append({"from": f, "to": f + 1200, "detections": []})
    return {"name": "uat_region_events_tp", "fps": 60, "timeline": tl}


def _open_logic_tab(page):
    page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
    page.wait_for_selector("text=项目管理", timeout=15000)
    time.sleep(1.2)
    page.locator("input[placeholder*='搜索项目']").fill(PNAME)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{PNAME}')").first.click()
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(0.8)


def _fill_creatable_select(page, scope, text):
    scope.click()
    time.sleep(0.4)
    page.keyboard.type(text, delay=40)
    time.sleep(0.5)
    page.keyboard.press("Enter")
    time.sleep(0.4)
    page.keyboard.press("Escape")
    time.sleep(0.3)


try:
    # ---- 0. API 建项目 (region_events + 4 类别步骤 + 标准事件/计数器) ----
    r = requests.post(f"{API}/projects", json={
        "name": PNAME, "task_type": "detection", "logic_mode": "region_events",
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/projects/{pid}", json={
        "steps_config": STEPS, "events_config": EVENTS, "counters_config": COUNTERS,
    }, timeout=10).raise_for_status()
    run.step("A0 API 建 region_events 项目", True, f"id={pid}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)

        # ---- A. 逻辑设置页: 新逻辑模式单选存在且选中 (逻辑模式面板挂在该 tab 左栏) ----
        _open_logic_tab(page)
        radio = page.locator("input[type='radio'][value='region_events']")
        run.step("A1 逻辑模式列表出现「区域事件模式」单选", radio.count() == 1)
        run.step("A2 region_events 单选处于选中态", radio.count() == 1 and radio.is_checked())
        run.shot(page, "01_logic_mode_radio")

        # ---- B. 逻辑设置页: 规则卡片 + 两条规则 ----
        card = page.locator(".el-card:has-text('区域事件模式 - 判定规则')")
        run.step("B1 逻辑设置页出现区域事件规则卡片", card.count() == 1)
        # region_events 下步骤级卡片(同时出现组)应隐藏
        body = page.evaluate("document.body.innerText")
        run.step("B2 同时出现组卡片已隐藏(不适用本模式)", "同时出现组" not in body)

        # 规则 1: overlap 测硬度 (笔 × 工件, 不限区域)
        card.locator("button:has-text('新增规则')").click()
        time.sleep(0.6)
        rule1 = card.locator("div.bg-slate-900").filter(has_text="规则 1").first
        rule1.locator("input[placeholder*='事件名']").fill("测硬度")
        # 本版 Element Plus 的 placeholder 渲染成 span, 用文案定位 select
        _fill_creatable_select(page, rule1.locator(
            ".el-select:has-text('如 测硬度笔')").first, "测硬度笔")
        _fill_creatable_select(page, rule1.locator(
            ".el-select:has-text('如 工件')").first, "工件")
        run.shot(page, "02_rule1_overlap")

        # 规则 2: region_exit 下工件 (切类型 → 自动回填结算开关)
        card.locator("button:has-text('新增规则')").click()
        time.sleep(0.6)
        rule2 = card.locator("div.bg-slate-900").filter(has_text="规则 2").first
        rule2.locator("input[placeholder*='事件名']").fill("下工件")
        rule2.locator(".el-select").first.click()  # 头行类型选择器
        time.sleep(0.5)
        page.locator(".el-select-dropdown__item:has-text('出区消失')").last.click()
        time.sleep(0.6)
        settle_on = rule2.locator(".el-switch.is-checked").count() >= 1
        run.step("B3 切到出区消失类型后「结算周期」自动开启", settle_on)
        _fill_creatable_select(page, rule2.locator(
            ".el-select:has-text('如 测硬度笔')").first, "工件")

        # 画判定区域 (右缘竖带 = 下料出口): 复用 ROI 编辑器, 无相机时落兜底画布
        rule2.locator("button:has-text('绘制区域')").click()
        time.sleep(1.5)
        roi_dlg = page.locator(".el-dialog:has-text('绘制区域事件规则')")
        run.step("B4 规则判定区域编辑器打开(复用 ROI 编辑器)", roi_dlg.count() > 0)
        canvas = roi_dlg.locator("canvas").first
        box = canvas.bounding_box()
        for rx, ry in [(0.82, 0.05), (0.98, 0.05), (0.98, 0.95), (0.82, 0.95)]:
            page.mouse.click(box["x"] + box["width"] * rx, box["y"] + box["height"] * ry)
            time.sleep(0.25)
        roi_dlg.locator("button:has-text('完成绘制')").click()
        time.sleep(0.5)
        run.shot(page, "03_exit_region_drawn")
        roi_dlg.locator("button:has-text('保存 ROI')").click()
        time.sleep(0.8)
        run.step("B5 区域保存后规则行显示已标定",
                 "已标定" in page.evaluate("document.body.innerText"))

        # 每类置信度: 测硬度笔 = 0.2 (小目标放低)
        conf_row = card.locator("div.flex.items-center.gap-2").filter(
            has_text="测硬度笔").first
        conf_input = conf_row.locator("input").first
        conf_input.fill("0.2")
        conf_input.press("Enter")
        time.sleep(0.4)

        # 顺序校验: 开启 + 期望顺序 测硬度→下工件
        seq_block = card.locator("div.bg-slate-900").filter(has_text="流程顺序校验").first
        seq_block.locator(".el-switch").first.click()
        time.sleep(0.5)
        order_sel = seq_block.locator(".el-select").first
        order_sel.click()
        time.sleep(0.5)
        page.locator(".el-select-dropdown__item:has-text('测硬度')").last.click()
        time.sleep(0.3)
        page.locator(".el-select-dropdown__item:has-text('下工件')").last.click()
        time.sleep(0.3)
        page.keyboard.press("Escape")
        time.sleep(0.4)
        run.shot(page, "04_rules_configured")

        # ---- C. 保存配置 → GET 复核落库 ----
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.5)
        detail = requests.get(f"{API}/projects/{pid}", timeout=10).json()
        re_cfg = (detail.get("pipeline_config") or {}).get("region_events") or {}
        rules = re_cfg.get("rules") or []
        by_name = {r0.get("name"): r0 for r0 in rules}
        r_hard, r_exit = by_name.get("测硬度") or {}, by_name.get("下工件") or {}
        run.step("C1 落库: 两条规则齐全(测硬度 overlap / 下工件 region_exit)",
                 r_hard.get("type") == "overlap" and r_exit.get("type") == "region_exit",
                 f"rules={[(x.get('name'), x.get('type')) for x in rules]}")
        run.step("C2 落库: overlap 主体/目标类别正确",
                 r_hard.get("subject_label") == "测硬度笔"
                 and r_hard.get("object_label") == "工件",
                 f"subject={r_hard.get('subject_label')} object={r_hard.get('object_label')}")
        run.step("C3 落库: 出区规则 结算开 + 区域 4 顶点",
                 r_exit.get("settle") is True and len(r_exit.get("region") or []) == 4,
                 f"settle={r_exit.get('settle')} region_pts={len(r_exit.get('region') or [])}")
        run.step("C4 落库: 每类置信度 测硬度笔=0.2",
                 abs((re_cfg.get("class_conf") or {}).get("测硬度笔", 0) - 0.2) < 1e-6,
                 f"class_conf={re_cfg.get('class_conf')}")
        seq = re_cfg.get("sequence_check") or {}
        run.step("C5 落库: 顺序校验开启 + 期望顺序 测硬度→下工件",
                 seq.get("enabled") is True and seq.get("order") == ["测硬度", "下工件"],
                 f"seq={seq}")

        # 刷新回显: 重进逻辑设置页, 规则应完整回显
        _open_logic_tab(page)
        body = page.evaluate("document.body.innerText")
        run.step("C6 回显: 两条规则 + 已标定区域在刷新后完整呈现",
                 "测硬度" in body and "下工件" in body and "已标定" in body)
        run.shot(page, "05_rules_echo_after_reload")

        # ---- D. 运行时: synthetic 剧本 + 监控页 ----
        requests.post(f"{API}/projects/{pid}/activate", timeout=15)
        requests.post(f"{API}/test/synthetic/start", json={
            "scenario_json": _scenario(), "channel": CH, "with_project": False,
        }, timeout=10).raise_for_status()
        detail = requests.get(f"{API}/projects/{pid}", timeout=10).json()
        requests.post(f"{API}/source/detection/set-project?channel={CH}", json={
            "project_id": pid, "name": detail["name"], "task_type": detail["task_type"],
            "logic_mode": detail["logic_mode"], "steps_config": detail["steps_config"],
            "pipeline_config": detail["pipeline_config"],
            "events_config": detail.get("events_config") or [],
            "counters_config": detail.get("counters_config") or [],
        }, timeout=10).raise_for_status()
        r = requests.post(f"{API}/source/detection/start?channel={CH}",
                          json={"conf": 0.2, "iou": 0.45}, timeout=15)
        run.step("D0 synthetic 剧本 + 检测已启动", r.status_code == 200,
                 f"status={r.status_code}")

        # 计数器按项目跨运行持久化 → 全部断言用相对基线增量
        b0 = requests.get(f"{API}/source/detection/results?channel={CH}",
                          timeout=5).json()
        base_ok = (b0.get("counters") or {}).get("合格总数", 0)
        base_sc = dict(b0.get("step_counts") or {})

        deadline = time.time() + 35
        sc, counters = {}, {}
        while time.time() < deadline:
            b = requests.get(f"{API}/source/detection/results?channel={CH}",
                             timeout=5).json()
            sc = b.get("step_counts") or {}
            counters = b.get("counters") or {}
            if (counters.get("合格总数", 0) >= base_ok + 1
                    and sc.get("测硬度", 0) >= base_sc.get("测硬度", 0) + 1):
                break
            time.sleep(0.5)
        run.step("D1 事件按规则名计数(测硬度/下工件)",
                 sc.get("测硬度", 0) >= base_sc.get("测硬度", 0) + 1
                 and sc.get("下工件", 0) >= base_sc.get("下工件", 0) + 1,
                 f"step_counts={sc} base={base_sc}")
        run.step("D2 出区消失事件结算出 OK 周期",
                 counters.get("合格总数", 0) >= base_ok + 1,
                 f"counters={counters} base_ok={base_ok}")

        # 监控页: 步骤统计按事件名展示 (整页刷新让 Navbar 按 is_active 选中项目;
        # 先清路由记忆, 否则冷启动恢复会把 reload 弹回 /project)
        page.evaluate("localStorage.removeItem('tianjun:lastRoute')")
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        page.reload(wait_until="domcontentloaded")
        time.sleep(8)
        m_body = page.evaluate("document.body.innerText")
        run.step("D3 监控页接上运行态并显示事件名步骤(测硬度/下工件)",
                 PNAME in m_body and "测硬度" in m_body and "下工件" in m_body)
        # 步骤统计表里模型类别不该出现为步骤行 (扫码枪未配规则, 不应成行)
        run.step("D4 未配规则的模型类别(扫码枪)不出现在步骤统计",
                 "扫码枪" not in m_body)
        run.shot(page, "06_monitor_event_steps")

        errs = filter_console_errors(cerrs)
        run.step("E1 无前端 console 错误", len(errs) == 0, f"errs={errs[:3]}")

        ctx.close()
        browser.close()
except Exception:  # noqa: BLE001 — 主流程异常必须显式记录
    import traceback
    _tb = traceback.format_exc()
    print(_tb, flush=True)
    run.step("EXCEPTION 主流程异常中断", False, _tb.strip().splitlines()[-1][:200])
finally:
    try:
        requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)
    except Exception:
        pass
    if pid is not None:
        try:
            requests.delete(f"{API}/projects/{pid}", timeout=10)
        except Exception:
            pass
    raise SystemExit(run.finish())
