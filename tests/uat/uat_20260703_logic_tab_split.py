# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 项目页逻辑设置 Tab 外置为 LogicConfigTab.vue（拆分批次 P-4）。

现场叙事: 五种逻辑模式的项目逐个打开「逻辑设置」Tab, 各自的专属卡片必须齐全:
  sequential → 结算方式 + 步骤排序（加一行序列并选步骤 → 保存落库）
  detection  → 需检测的步骤
  custom     → 条件配置 + 周期性强制动作（新增规则+快捷建事件绑定 → 保存落库）
  tracking   → 物品清点配置 + ROI 设置按钮（点开外置 RoiEditorDialog, emit 链路通）
  per_item   → 通用参数 + 每步角色
拆分是"只搬不改", 五种模式的条件渲染与交互必须与拆分前一致。

前置: 后端 8001 + 前端 6001 已启动。测试项目 __uat_ 前缀, 收尾删除。
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
SUF = uuid.uuid4().hex[:5]
MODES = ["sequential", "detection", "custom", "tracking", "per_item"]
PNAMES = {m: f"__uat_lg_{m[:4]}_{SUF}" for m in MODES}

run = UatRun("logic_tab_split")
pids = {}


def _select_and_open_logic(page, name):
    card = page.locator(f"div.p-4:has-text('{name}')").first
    if card.count() == 0:
        card = page.get_by_text(name, exact=False).first
    card.click()
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(0.8)
    return page.evaluate("document.body.innerText")


try:
    for m in MODES:
        r = requests.post(f"{API}/projects", json={
            "name": PNAMES[m], "task_type": "detection", "logic_mode": m,
        }, timeout=10)
        r.raise_for_status()
        pids[m] = r.json()["id"]
    # sequential 项目给两个步骤, 供序列编辑器选
    requests.put(f"{API}/projects/{pids['sequential']}", json={
        "steps_config": [
            {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
            {"id": 2, "label": "step_b", "name": "步骤B", "enabled": True},
        ],
    }, timeout=10).raise_for_status()
    print(f">>> 五模式测试项目就绪: {pids}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        time.sleep(1.5)

        # ---- 1. sequential: 结算方式 + 步骤排序 + 序列编辑落库 ----
        body = _select_and_open_logic(page, PNAMES["sequential"])
        run.step("A1 sequential: 结算方式+步骤排序卡片在",
                 "结算方式" in body and "顺序模式 - 步骤排序" in body)
        page.locator("button:has-text('添加步骤')").first.click()
        time.sleep(0.5)
        sel = page.locator(".el-card:has-text('步骤排序') .el-select").last
        sel.click()
        time.sleep(0.5)
        # 序列下拉项文案 = displayLabel || label（步骤没配 displayLabel 时显示原始 label）
        page.locator(".el-select-dropdown__item:visible", has_text="step_a").first.click()
        time.sleep(0.5)
        run.shot(page, "01_sequential_logic")
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        detail = requests.get(f"{API}/projects/{pids['sequential']}", timeout=10).json()
        # 序列持久化在 pipeline_config.sequence_order（顶层 sequence_order 是前端加载期注入的视图字段）
        seq = (detail.get("pipeline_config") or {}).get("sequence_order") or []
        run.step("A2 落库: sequence_order 三行(默认两行+新增 step_a)",
                 len(seq) == 3 and seq[-1].get("step_id") == 1, f"seq={seq}")

        # ---- 2. detection ----
        body = _select_and_open_logic(page, PNAMES["detection"])
        run.step("B1 detection: 需检测的步骤卡片在", "检测模式 - 需检测的步骤" in body)

        # ---- 3. custom: 条件配置 + 周期性强制动作(新增规则+快捷建事件) ----
        body = _select_and_open_logic(page, PNAMES["custom"])
        run.step("C1 custom: 条件配置+周期性强制动作卡片在",
                 "自定义模式 - 条件配置" in body and "周期性强制动作" in body)
        page.locator("button:has-text('+ 新增规则')").click()
        time.sleep(0.6)
        # 规则名在 el-input 里, innerText 取不到 → 以"暂无规则"占位消失 + 事件快捷按钮出现为准
        body_after = page.evaluate("document.body.innerText")
        run.step("C2 新增规则行出现", "暂无规则" not in body_after and "到期提醒事件" in body_after)
        # 快捷新建事件并绑定(addEventAndBindToRule 平移后仍工作)
        page.locator("button:has-text('+ 新建事件')").first.click()
        time.sleep(0.8)
        run.shot(page, "02_custom_periodic")
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        detail = requests.get(f"{API}/projects/{pids['custom']}", timeout=10).json()
        # 周期性规则持久化在 pipeline_config.periodic_actions（同 sequence_order, 顶层是视图字段）
        pas = (detail.get("pipeline_config") or {}).get("periodic_actions") or []
        evs = detail.get("events_config") or []
        bound = pas and (pas[0].get("due_warning_event_id") or pas[0].get("overdue_event_id"))
        run.step("C3 落库: 周期性规则 + 快捷事件已绑定",
                 len(pas) == 1 and bool(bound) and any(e.get("id") == bound for e in evs),
                 f"rules={len(pas)} bound={bound} 事件数={len(evs)}")

        # ---- 4. tracking: 物品清点 + ROI 按钮拉起外置编辑器 ----
        body = _select_and_open_logic(page, PNAMES["tracking"])
        run.step("D1 tracking: 物品清点配置卡片在", "跟踪模式 - 物品清点配置" in body)
        page.locator("button:has-text('设置区域')").first.click()
        time.sleep(2.0)
        canvas = page.locator("canvas.cursor-crosshair").first
        box = canvas.bounding_box()
        run.shot(page, "03_tracking_roi_dialog")
        run.step("D2 emit 链路: 点设置区域拉起外置 ROI 编辑器",
                 box is not None and box["width"] > 100)
        page.keyboard.press("Escape")
        time.sleep(0.6)

        # ---- 5. per_item ----
        body = _select_and_open_logic(page, PNAMES["per_item"])
        run.step("E1 per_item: 通用参数+每步角色卡片在",
                 "逐件覆盖 — 通用参数" in body and "逐件覆盖 — 每个步骤的角色" in body)
        run.shot(page, "04_per_item_logic")

        real = filter_console_errors(cerrs)
        run.step("F1 控制台无前端逻辑报错", not real,
                 f"真报错={real[:3]}" if real else f"噪声{len(cerrs)}条")

        ctx.close()
        browser.close()
finally:
    for m, pid in pids.items():
        requests.delete(f"{API}/projects/{pid}", timeout=10)
    print(f">>> 清理: 已删 {len(pids)} 个测试项目")

sys.exit(run.finish())
