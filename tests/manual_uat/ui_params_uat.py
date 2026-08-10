# -*- coding: utf-8 -*-
"""UI 参数补齐 UAT（2026-08-10）: 可见浏览器手点验证 + 截图证据。

覆盖两块新 UI:
  A. 逻辑设置 → 计数组合判定表: 计数口径 (steps/positional) + positional 六追踪参数
     → 保存落库 → GET 双向核对 → 重进回显
  B. 系统设置 → 触发中心: pixel_region 补 ref_drift/ref_drift_alpha/invert,
     serial_pattern 补 4 串口参数+编码, timer 补每日定点, http 补 IP 白名单/变量提取
     → 模板建触发源改缓漂步长 → 保存 → GET 核对

证据: tests/manual_uat/evidence/ui_params_2026-08-10/
运行: 前端 http://localhost:6003 + 后端 8001 须在跑。
"""
import json
import pathlib
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:6003"
API = "http://localhost:8001/api/v1"
EV = pathlib.Path(__file__).parent / "evidence" / "ui_params_2026-08-10"
EV.mkdir(parents=True, exist_ok=True)

verdict = {"steps": [], "pass": True}


def check(name, ok, detail=""):
    verdict["steps"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("✅" if ok else "❌"), name, detail if not ok else "")
    if not ok:
        verdict["pass"] = False


def mk_demo_project():
    """建演示项目: 4 步骤 + combo 表(默认 steps 口径), UI 里再切 positional。"""
    r = requests.post(f"{API}/projects", json={
        "name": "缸体判型演示", "task_type": "detection", "logic_mode": "detection",
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/projects/{pid}", json={
        "steps_config": [
            {"id": 1, "label": "擦拭缸体", "name": "擦拭缸体", "enabled": True,
             "threshold": 0.35, "min_frames": 1, "disappear_delay": 4.0},
            {"id": 2, "label": "装轴承座瓦", "name": "装轴承座瓦", "enabled": True,
             "threshold": 0.35, "min_frames": 2, "disappear_delay": 0.35},
            {"id": 3, "label": "装轴承盖瓦", "name": "装轴承盖瓦", "enabled": True,
             "threshold": 0.35, "min_frames": 2, "disappear_delay": 0.35},
            {"id": 4, "label": "放置挺柱", "name": "放置挺柱", "enabled": True,
             "threshold": 0.35, "min_frames": 2, "disappear_delay": 0.35},
        ],
        "pipeline_config": {
            "combo_table": {
                "enabled": True,
                "labels": ["装轴承座瓦", "装轴承盖瓦", "放置挺柱"],
                "rows": [
                    {"counts": [5, 5, 0], "verdict": "OK", "tag": "4缸-无挺柱"},
                    {"counts": [5, 5, 4], "verdict": "OK", "tag": "4缸-含挺柱"},
                    {"counts": [7, 7, 0], "verdict": "OK", "tag": "6缸-无挺柱"},
                    {"counts": [7, 7, 6], "verdict": "OK", "tag": "6缸-含挺柱"},
                ],
            },
        },
    }, timeout=5).raise_for_status()
    return pid


def open_logic_tab(page, name):
    page.goto(f"{BASE_URL}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{name}')").first.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(0.8)


def set_number(scope, label, value):
    item = scope.locator(f".el-form-item:has-text('{label}')").first
    inp = item.locator(".el-input-number input").first
    inp.click()
    inp.press("ControlOrMeta+a")
    inp.type(str(value), delay=15)
    inp.press("Tab")
    time.sleep(0.3)


def part_a_combo(page, pid, name):
    open_logic_tab(page, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    card.scroll_into_view_if_needed()
    time.sleep(0.4)
    body = card.inner_text()
    check("A1 计数口径入口可见", "计数口径" in body and "按位置去重" in body, body[:150])

    # 切到 positional → 追踪参数网格展开
    card.locator(".el-radio-button:has-text('按位置去重')").click()
    time.sleep(0.5)
    body = card.inner_text()
    ok = all(k in body for k in ("同位置判定 IoU", "位置平滑步长", "新位置确认帧数",
                                 "候选保留节拍", "单位置消失超时", "整类空闲重置"))
    check("A2 positional 六参数展开", ok, body[-400:])
    trk = card.locator(".combo-tracking").first
    set_number(trk, "同位置判定 IoU", 0.45)
    set_number(trk, "新位置确认帧数", 2)
    card.screenshot(path=str(EV / "01_combo_positional_card.png"))

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    pc = requests.get(f"{API}/projects/{pid}", timeout=5).json().get("pipeline_config") or {}
    cmb = pc.get("combo_table") or {}
    trk_db = cmb.get("tracking") or {}
    check("A3 count_mode 落库", cmb.get("count_mode") == "positional", cmb)
    check("A4 tracking 落库", trk_db.get("iou") == 0.45 and trk_db.get("min_consecutive") == 2
          and trk_db.get("ema_alpha") == 0.6, trk_db)

    # 重进回显
    open_logic_tab(page, name)
    card = page.locator(".el-card:has-text('计数组合判定表')").first
    card.scroll_into_view_if_needed()
    time.sleep(0.4)
    radio_on = card.locator(".el-radio-button.is-active:has-text('按位置去重')").count()
    iou_val = card.locator(".el-form-item:has-text('同位置判定 IoU') .el-input-number input").first.input_value()
    check("A5 重进回显", radio_on >= 1 and iou_val == "0.45", f"radio={radio_on} iou={iou_val}")
    card.screenshot(path=str(EV / "02_combo_reopen.png"))


def open_trigger_tab(page):
    page.goto(f"{BASE_URL}/#/settings", wait_until="networkidle")
    page.get_by_text("触发中心", exact=True).first.click()
    page.wait_for_selector("text=触发源", timeout=8000)
    time.sleep(0.5)


def dlg(page):
    return page.locator(".el-dialog:visible").first


def part_b_triggers(page):
    # 清理旧演示触发源
    for trg in (requests.get(f"{API}/triggers/channels", timeout=5).json() or {}).get("triggers", []):
        if (trg.get("name") or "").startswith("演示-"):
            requests.delete(f"{API}/triggers/channels/{trg['id']}", timeout=5)

    open_trigger_tab(page)
    # 走模板路径 (自带 region 缺省)
    page.locator("button:has-text('模板')").first.click()
    time.sleep(0.5)
    page.locator(".el-dropdown-menu__item:visible, .el-select-dropdown__item:visible").first.click()
    time.sleep(0.8)
    d = dlg(page)
    body = d.inner_text()
    ok = all(k in body for k in ("参考帧缓漂", "缓漂步长", "反相触发"))
    check("B1 pixel_region 新参数可见", ok, body[:300])
    # 改名 + 改缓漂步长
    d.locator(".el-form-item:has-text('名称') input").first.fill("演示-虚拟按钮")
    set_number(d, "缓漂步长", 0.05)
    d.screenshot(path=str(EV / "03_pixel_region_dialog.png"))
    d.locator("button:has-text('保 存'), button:has-text('保存')").last.click()
    time.sleep(1.5)
    trgs = (requests.get(f"{API}/triggers/channels", timeout=5).json() or {}).get("triggers", [])
    mine = next((t for t in trgs if t.get("name") == "演示-虚拟按钮"), None)
    p = (mine or {}).get("params") or {}
    check("B2 ref_drift_alpha 落库", mine is not None and p.get("ref_drift_alpha") == 0.05, p)

    # serial_pattern 字段可见 (截图后取消)
    page.locator("button:has-text('添加触发源'), button:has-text('新增')").first.click()
    time.sleep(0.6)
    d = dlg(page)
    d.locator(".el-form-item:has-text('类型') .el-select").first.click()
    time.sleep(0.4)
    page.locator(".el-select-dropdown__item:visible", has_text="串口报文").first.click()
    time.sleep(0.5)
    body = d.inner_text()
    ok = all(k in body for k in ("数据位", "校验位", "停止位", "行分隔符", "编码"))
    check("B3 serial_pattern 新参数可见", ok, body[:300])
    d.screenshot(path=str(EV / "04_serial_dialog.png"))

    # timer 每日定点
    d.locator(".el-form-item:has-text('类型') .el-select").first.click()
    time.sleep(0.4)
    page.locator(".el-select-dropdown__item:visible", has_text="定时").first.click()
    time.sleep(0.5)
    body = d.inner_text()
    check("B4 timer 每日定点可见", "每日定点" in body, body[:200])
    d.screenshot(path=str(EV / "05_timer_dialog.png"))

    # http 白名单/变量提取
    d.locator(".el-form-item:has-text('类型') .el-select").first.click()
    time.sleep(0.4)
    page.locator(".el-select-dropdown__item:visible", has_text="HTTP").first.click()
    time.sleep(0.5)
    body = d.inner_text()
    check("B5 http 白名单/提取可见", "IP 白名单" in body and "变量提取" in body, body[:200])
    d.screenshot(path=str(EV / "06_http_dialog.png"))
    page.keyboard.press("Escape")
    time.sleep(0.5)

    # 面板总览截图 (留 PDF 用)
    page.screenshot(path=str(EV / "07_trigger_panel.png"), full_page=True)


def main():
    # 清理旧演示项目
    items = requests.get(f"{API}/projects", timeout=5).json().get("items", [])
    for p in items:
        if p["name"] == "缸体判型演示":
            requests.delete(f"{API}/projects/{p['id']}", timeout=5)
    pid = mk_demo_project()
    name = "缸体判型演示"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=80)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        try:
            part_a_combo(page, pid, name)
            part_b_triggers(page)
        finally:
            (EV / "verdict.json").write_text(
                json.dumps(verdict, ensure_ascii=False, indent=2))
            browser.close()

    print("\n=== 总判定:", "PASS" if verdict["pass"] else "FAIL", "===")
    sys.exit(0 if verdict["pass"] else 1)


if __name__ == "__main__":
    main()
