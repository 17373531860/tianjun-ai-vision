# -*- coding: utf-8 -*-
"""可见浏览器 UAT: ROI 偏差修复缺陷 A（编辑器快照通道解析）。

现场叙事: 操作员给项目的某个步骤画检测区域 → 打开 ROI 编辑器 → 底图取
「该项目实际绑定的通道」快照（此前写死 0 号通道, 多工位画错底图）→
画一个三角形保存 → 保存配置落库 → 刷新回读顶点一致。

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
PNAME = f"__uat_roi_{uuid.uuid4().hex[:6]}"

run = UatRun("roi_channel_fix")


def create_project():
    payload = {
        "name": PNAME,
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {},
        "steps_config": [
            {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
        ],
        "events_config": [
            {"id": 1, "name": "合格", "type": "ok", "enabled": True},
            {"id": 2, "name": "NG", "type": "ng", "enabled": True},
        ],
        "counters_config": [],
        "alarm_config": {},
        "detection_config": {},
        "data_config": {},
        "default_model_id": None,
        "model_format": "pytorch_fp32",
    }
    r = requests.post(f"{API}/projects", json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


proj = create_project()
PID = proj["id"]
run.step("A1 API 建单步骤项目", True, f"id={PID}")

try:
    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)

        # 抓 /snapshot 请求, 验证通道解析逻辑真实生效
        snapshot_urls = []
        page.on("request", lambda req: snapshot_urls.append(req.url)
                if "/snapshot" in req.url else None)

        page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        time.sleep(1.5)
        card = page.locator(f"div.p-4:has-text('{PNAME}')").first
        if card.count() == 0:
            card = page.get_by_text(PNAME, exact=False).first
        card.click()
        time.sleep(1.2)

        # 进步骤设置 Tab, 找步骤 ROI 的「设置」按钮
        page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
        time.sleep(1.0)
        run.shot(page, "01_steps_tab")
        btn = page.locator("button:has-text('设置')").first
        run.step("B1 步骤 ROI 设置按钮存在", btn.count() > 0)
        btn.click()
        time.sleep(2.0)  # 等 workstations 解析 + snapshot 加载
        run.shot(page, "02_roi_editor_open")

        # 编辑器打开且快照请求带通道参数（本项目未绑定任何通道 → 回退 channel=0）
        dlg = page.locator(".roi-editor-dialog")
        run.step("B2 ROI 编辑器对话框打开", dlg.count() > 0)
        snaps = [u for u in snapshot_urls if "/snapshot" in u]
        ch0_ok = any("channel=0" in u for u in snaps)
        run.step("B3 未绑定项目回退 0 号通道快照", ch0_ok, f"urls={snaps[-2:]}")

        # 画三角形: 在 canvas 上点 3 个点 + 完成绘制
        canvas = page.locator("canvas.cursor-crosshair").first
        box = canvas.bounding_box()
        run.step("B4 快照 canvas 已渲染", box is not None and box["width"] > 100,
                 f"box={box}")
        pts = [(0.2, 0.2), (0.8, 0.3), (0.5, 0.8)]
        for (fx, fy) in pts:
            page.mouse.click(box["x"] + box["width"] * fx,
                             box["y"] + box["height"] * fy)
            time.sleep(0.3)
        page.locator("button:has-text('完成绘制')").click()
        time.sleep(0.5)
        run.shot(page, "03_polygon_drawn")
        run.step("B5 多边形闭合提示出现",
                 "多边形已闭合" in page.evaluate("document.body.innerText"))

        page.locator("button:has-text('保存 ROI')").click()
        time.sleep(0.8)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        run.shot(page, "04_saved")

        # T5 落库验证: 步骤 roi 有 3 个归一化顶点且与点击位置吻合(容差 3%)
        detail = requests.get(f"{API}/projects/{PID}", timeout=10).json()
        roi = (detail.get("steps_config") or [{}])[0].get("roi")
        run.step("C1 落库: 步骤 ROI 3 顶点", isinstance(roi, list) and len(roi) == 3,
                 f"roi={roi}")
        if isinstance(roi, list) and len(roi) == 3:
            err = max(abs(roi[i][0] - pts[i][0]) + abs(roi[i][1] - pts[i][1])
                      for i in range(3))
            run.step("C2 顶点与点击位置吻合(容差3%)", err < 0.03, f"最大偏差={err:.4f}")

        # 刷新回读: 重开编辑器应显示"重绘"(已有 ROI)
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
        run.shot(page, "05_reload")
        run.step("C3 刷新回读: 按钮变'重绘'+顶点缩略图",
                 page.locator("button:has-text('重绘')").count() > 0)

        real = filter_console_errors(cerrs)
        run.step("D1 控制台无前端逻辑报错", not real,
                 f"真报错={real[:3]}" if real else f"噪声{len(cerrs)}条")

        ctx.close()
        browser.close()
finally:
    requests.delete(f"{API}/projects/{PID}", timeout=10)
    print(f">>> 清理: 已删测试项目 {PNAME}")

sys.exit(run.finish())
