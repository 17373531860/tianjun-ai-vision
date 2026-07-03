# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 项目页四个对话框外置为独立组件（拆分批次 P-2）。

现场叙事: 操作员点「新建项目」→ 对话框(外置 CreateProjectDialog)填名建项目 →
点「选择模型」→ 模型列表对话框(外置 ModelSelectDialog)正常弹出 →
点推理格式入口 → 格式对话框(外置 FormatSelectDialog)列出格式与推荐标签 →
给步骤画 ROI → 编辑器(外置 RoiEditorDialog)取快照画三角形 → 保存落库回读。
拆分是"只搬不改", 四个对话框行为必须与拆分前一致。

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
PNAME = f"__uat_dlg_{uuid.uuid4().hex[:6]}"

run = UatRun("project_dialogs_split")
created_pid = None

try:
    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)

        page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        time.sleep(1.5)

        # ---- 1. 新建项目对话框 ----
        page.locator("button:has-text('新建项目')").first.click()
        time.sleep(0.8)
        dlg_title = page.locator(".el-dialog__title:has-text('新建项目')")
        run.step("A1 新建项目对话框弹出(外置组件)", dlg_title.count() > 0)
        page.locator("input[placeholder*='手机壳外观检测']").fill(PNAME)
        run.shot(page, "01_create_dialog")
        page.locator("button:has-text('创建')").click()
        time.sleep(2.0)
        r = requests.get(f"{API}/projects", timeout=10).json()
        hit = [x for x in r.get("items", []) if x["name"] == PNAME]
        created_pid = hit[0]["id"] if hit else None
        run.step("A2 创建落库(项目出现在列表)", bool(hit), f"id={created_pid}")

        # 选中新项目
        card = page.locator(f"div.p-4:has-text('{PNAME}')").first
        if card.count() == 0:
            card = page.get_by_text(PNAME, exact=False).first
        card.click()
        time.sleep(1.0)

        # ---- 2. 模型选择对话框 ----
        page.locator("button:has-text('选择模型')").first.click()
        time.sleep(1.0)
        body = page.evaluate("document.body.innerText")
        model_dlg_ok = "选择模型" in body and ("个类别" in body or "暂无可用模型" in body)
        run.shot(page, "02_model_select_dialog")
        run.step("B1 模型选择对话框弹出且渲染列表/空态", model_dlg_ok)

        # ---- 3. 点第一个模型 → 父级 selectModel → 自动弹出推理格式对话框 ----
        model_row = page.locator(".el-dialog .p-3.bg-slate-800").first
        run.step("B2 模型列表有条目可选", model_row.count() > 0)
        model_row.click()
        time.sleep(2.0)
        body = page.evaluate("document.body.innerText")
        fmt_dlg_ok = "选择推理格式" in body and "PyTorch FP32" in body
        run.shot(page, "03_format_select_dialog")
        run.step("B3 选模型后自动弹出格式对话框并列出格式(选择链路未被拆分打断)", fmt_dlg_ok)
        page.keyboard.press("Escape")
        time.sleep(0.6)

        # ---- 4. ROI 编辑器对话框(先给项目加一个步骤) ----
        requests.put(f"{API}/projects/{created_pid}", json={
            "steps_config": [{"id": 1, "label": "step_a", "name": "步骤A", "enabled": True}],
        }, timeout=10).raise_for_status()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        time.sleep(1.5)
        card = page.locator(f"div.p-4:has-text('{PNAME}')").first
        if card.count() == 0:
            card = page.get_by_text(PNAME, exact=False).first
        card.click()
        time.sleep(1.0)
        page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
        time.sleep(1.0)
        page.locator("button:has-text('设置')").first.click()
        time.sleep(2.0)
        canvas = page.locator("canvas.cursor-crosshair").first
        box = canvas.bounding_box()
        run.step("C1 ROI 编辑器弹出且快照 canvas 渲染", box is not None and box["width"] > 100)
        pts = [(0.25, 0.25), (0.75, 0.3), (0.5, 0.75)]
        for (fx, fy) in pts:
            page.mouse.click(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
            time.sleep(0.3)
        page.locator("button:has-text('完成绘制')").click()
        time.sleep(0.5)
        run.shot(page, "04_roi_drawn")
        run.step("C2 多边形闭合", "多边形已闭合" in page.evaluate("document.body.innerText"))
        page.locator("button:has-text('保存 ROI')").click()
        time.sleep(0.8)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)

        # ---- T5 落库 + 回读 ----
        detail = requests.get(f"{API}/projects/{created_pid}", timeout=10).json()
        roi = (detail.get("steps_config") or [{}])[0].get("roi")
        run.step("D1 落库: 步骤 ROI 3 顶点", isinstance(roi, list) and len(roi) == 3, f"roi={roi}")
        if isinstance(roi, list) and len(roi) == 3:
            err = max(abs(roi[i][0] - pts[i][0]) + abs(roi[i][1] - pts[i][1]) for i in range(3))
            run.step("D2 顶点与点击位置吻合(容差3%)", err < 0.03, f"最大偏差={err:.4f}")

        # 重开编辑器: 已有多边形应被预加载(子组件 load(existing) 链路)
        page.locator("button:has-text('重绘')").first.click()
        time.sleep(2.0)
        body = page.evaluate("document.body.innerText")
        run.shot(page, "05_roi_reopen_preload")
        run.step("D3 重开编辑器预加载已有多边形(顶点数3+已闭合)",
                 "顶点数: 3" in body and "多边形已闭合" in body)
        page.keyboard.press("Escape")
        time.sleep(0.5)

        real = filter_console_errors(cerrs)
        run.step("E1 控制台无前端逻辑报错", not real,
                 f"真报错={real[:3]}" if real else f"噪声{len(cerrs)}条")

        ctx.close()
        browser.close()
finally:
    if created_pid:
        requests.delete(f"{API}/projects/{created_pid}", timeout=10)
        print(f">>> 清理: 已删测试项目 {PNAME}")

sys.exit(run.finish())
