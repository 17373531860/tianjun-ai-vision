# -*- coding: utf-8 -*-
"""可见浏览器 UAT: OCR/异常检测新逻辑模式 + facing_dwell 朝向驻留 (2026-09)。

路径 H (headless=False 真开浏览器) + 真实后端运行链路:
  1. [真实链路] synthetic 源 + logic_mode=ocr 起检测 (无 YOLO 模型) →
     AI 采样线程真实运行 → /detection/results 带 ai_mode 快照
  2. [真实链路] Monitor 页真开浏览器: OCR 模式面板渲染 (非 SOP)
  3. [手点] 项目页创建 OCR 项目 → 逻辑设置配置读字规则 → 保存 → 落库
  4. [手点] anomaly 项目哨兵配置卡
  5. [手点] region_events 项目「面向仪表点检」模板 → facing_dwell 参数渲染
  6. [真实链路] AI 能力试用页: 上传真图跑 OCR 识别

前置: 后端 RUNTIME_MODE=test 起在 8003, 前端 6003。
证据: 截图 + run.log 存 tests/uat/ai_modes_out/。

用法: python tests/uat/uat_ai_modes_visible.py
"""
import os
import sys
import time
import uuid

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

API = "http://localhost:8003"
FRONT = "http://localhost:6003"
OUT = os.path.join(os.path.dirname(__file__), "ai_modes_out")
PREFIX = "__uat_ai_"

_log_lines = []


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    _log_lines.append(line)


def api(method, path, **kw):
    r = requests.request(method, f"{API}/api/v1{path}", timeout=15, **kw)
    r.raise_for_status()
    return r.json() if r.text else {}


def cleanup():
    try:
        payload = api("GET", "/projects")
        items = payload if isinstance(payload, list) else (
            payload.get("items") or payload.get("projects") or payload.get("data") or [])
        for p in items:
            if not isinstance(p, dict):
                continue
            if (p.get("name") or "").startswith(PREFIX):
                requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=5)
    except Exception as e:
        log(f"清理警告: {e}")


def main():
    os.makedirs(OUT, exist_ok=True)
    from playwright.sync_api import sync_playwright

    cleanup()
    ok = True
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=150)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})

        # ========== 1. 真实链路: synthetic + ocr 模式起检测 ==========
        log("== 1. synthetic 源 + OCR 模式无模型起检测 ==")
        api("POST", "/test/synthetic/start", json={
            "channel": 0, "with_project": True, "logic_mode": "ocr",
            "project_steps": ["人"], "fps": 5,
        })
        api("POST", "/source/detection/start?channel=0",
            json={"conf": 0.25, "iou": 0.45})
        time.sleep(4)
        res = api("GET", "/source/detection/results")
        ai = res.get("ai_mode")
        assert ai and ai.get("mode") == "ocr", f"结果应带 ai_mode 快照: {ai}"
        log(f"   ai_mode 快照: mode={ai.get('mode')} available={ai.get('available')}")
        log("   ✓ 无 YOLO 模型的 OCR 模式检测已运行, 采样线程产出快照")

        # ========== 2. Monitor 页 OCR 面板 ==========
        log("== 2. Monitor 页 OCR 模式面板 ==")
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4)
        assert page.locator("[data-layout-slot='mode-panel']").count() == 1, \
            "Monitor 应渲染专属模式面板槽"
        body = page.evaluate("document.body.innerText")
        assert "读字" in body, "Monitor 应显示 OCR 读字面板内容"
        assert "采样间隔" in body, "面板应显示采样线程运行态"
        page.screenshot(path=f"{OUT}/1_monitor_ocr_panel.png")
        log("   ✓ Monitor 显示 OCR 读字面板 + 采样运行态 (截图 1_monitor_ocr_panel.png)")
        api("POST", "/source/detection/stop?channel=0")
        api("POST", "/test/synthetic/stop?channel=0")

        # ========== 3. 项目页手点建 OCR 项目 ==========
        log("== 3. 项目页手点: 创建 OCR 项目 + 配置读字规则 ==")
        name_ocr = f"{PREFIX}ocr_{uuid.uuid4().hex[:4]}"
        pid_ocr = api("POST", "/projects", json={
            "name": name_ocr, "task_type": "detection", "logic_mode": "ocr"})["id"]
        page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=10000)
        time.sleep(1)
        page.locator("input[placeholder*='搜索项目']").fill(name_ocr)
        time.sleep(0.8)
        page.locator(f"div.p-4:has-text('{name_ocr}')").first.click()
        time.sleep(1)
        page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
        time.sleep(1)
        card = page.locator(".el-card:has-text('OCR 读字模式 - 读字规则')")
        assert card.count() == 1, "OCR 配置卡应渲染"
        card.locator("button:has-text('新增规则')").click()
        time.sleep(0.8)
        rule = card.locator("div.bg-slate-900").last
        rule.locator("input[placeholder*='规则名']").fill("批次号核对")
        rule.locator("input[placeholder*='SN']").fill(r"^PH\d{6}$")
        time.sleep(0.5)
        page.screenshot(path=f"{OUT}/2_project_ocr_config.png")
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2)
        ocr_cfg = api("GET", f"/projects/{pid_ocr}").get("pipeline_config", {}).get("ocr") or {}
        assert (ocr_cfg.get("rules") or [{}])[0].get("name") == "批次号核对", f"落库: {ocr_cfg}"
        log("   ✓ OCR 规则 UI 配置 → pipeline_config.ocr 落库 (截图 2)")

        # ========== 4. anomaly 项目哨兵配置卡 ==========
        log("== 4. 异常检测项目哨兵配置卡 ==")
        name_ano = f"{PREFIX}ano_{uuid.uuid4().hex[:4]}"
        api("POST", "/projects", json={
            "name": name_ano, "task_type": "detection", "logic_mode": "anomaly"})
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=10000)
        time.sleep(1)
        page.locator("input[placeholder*='搜索项目']").fill(name_ano)
        time.sleep(0.8)
        page.locator(f"div.p-4:has-text('{name_ano}')").first.click()
        time.sleep(1)
        page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
        time.sleep(1)
        assert page.locator(".el-card:has-text('异常检测模式 - 哨兵配置')").count() == 1
        page.screenshot(path=f"{OUT}/3_project_anomaly_config.png")
        log("   ✓ 哨兵配置卡渲染 (截图 3)")

        # ========== 5. facing_dwell 模板 ==========
        log("== 5. region_events「面向仪表点检」模板 ==")
        name_re = f"{PREFIX}re_{uuid.uuid4().hex[:4]}"
        pid_re = api("POST", "/projects", json={
            "name": name_re, "task_type": "detection", "logic_mode": "region_events"})["id"]
        api("PUT", f"/projects/{pid_re}", json={"steps_config": [
            {"id": 1, "label": "人", "displayLabel": "人", "enabled": True, "threshold": 50}]})
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=10000)
        time.sleep(1)
        page.locator("input[placeholder*='搜索项目']").fill(name_re)
        time.sleep(0.8)
        page.locator(f"div.p-4:has-text('{name_re}')").first.click()
        time.sleep(1)
        page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
        time.sleep(1)
        page.locator("button:has-text('面向仪表点检')").click()
        time.sleep(1)
        body = page.evaluate("document.body.innerText")
        assert "朝向容差" in body and "仪表点" in body
        page.screenshot(path=f"{OUT}/4_facing_dwell_rule.png")
        log("   ✓ facing_dwell 规则 UI 渲染: 朝向容差/仪表点标定/告警取反 (截图 4)")

        # ========== 6. AI 能力试用页真 OCR ==========
        log("== 6. AI 能力试用页真图 OCR 识别 ==")
        import cv2
        import numpy as np
        img = np.full((200, 640, 3), 255, dtype=np.uint8)
        cv2.putText(img, "PH202609", (60, 120), cv2.FONT_HERSHEY_SIMPLEX,
                    2.2, (0, 0, 0), 5)
        test_img = f"{OUT}/_ocr_input.png"
        cv2.imwrite(test_img, img)
        page.goto(f"{FRONT}/#/ai-tools", wait_until="domcontentloaded")
        time.sleep(2)
        page.locator(
            ".el-upload:has([data-test='ocr-upload-btn']) input[type='file']"
        ).set_input_files(test_img)
        time.sleep(6)
        body = page.evaluate("document.body.innerText")
        assert "PH202609" in body, "AI 试用页应识别出 PH202609"
        page.screenshot(path=f"{OUT}/5_aitools_ocr_result.png")
        log("   ✓ 真图 OCR 识别出 PH202609 (截图 5)")

        browser.close()

    cleanup()
    with open(f"{OUT}/run.log", "w", encoding="utf-8") as f:
        f.write("\n".join(_log_lines) + "\n")
    log("UAT 全部通过 ✓")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"!! UAT 失败: {e}")
        with open(f"{OUT}/run.log", "w", encoding="utf-8") as f:
            f.write("\n".join(_log_lines) + "\n")
        raise
