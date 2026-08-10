# -*- coding: utf-8 -*-
"""SY9 吸收补账 UAT（2026-08-10）: 可见浏览器手点验证 + 截图证据。

覆盖 SY9 分支合入 main 后的两块 UI（吸收时缺口补齐, merge-branch skill A4/T4/T5）:
  A. 项目 → 逻辑设置 → 容器装箱清点:
     槽位完整性门 (空槽标签 + 每盘槽位数) + 物品框/托盘框去重 IoU 阈值
     → 默认值回填 (物品 0.45 / 托盘 0 = 双边客户零差异) → 改值保存
     → GET 双向核对落库 → 重进回显
  B. 系统设置 → 包装箱结算 → 编辑 → 组⑦ 滑块口径设置:
     「只认收尾后放的工单」开关 (tail_paper_only_after_awaiting, 默认关)
     → 可见可开 → 保存落库 → GET 核对

证据: tests/manual_uat/evidence/sy9_ui_2026-08-10/
运行: 前端 http://localhost:6001 + 后端 8001 须在跑。
"""
import json
import pathlib
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:6001"
API = "http://localhost:8001/api/v1"
EV = pathlib.Path(__file__).parent / "evidence" / "sy9_ui_2026-08-10"
EV.mkdir(parents=True, exist_ok=True)

PROJ_NAME = "SY9参数演示"
FLOW_NAME = "演示-SY9只认收尾后"

verdict = {"steps": [], "pass": True}


def check(name, ok, detail=""):
    verdict["steps"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("✅" if ok else "❌"), name, detail if not ok else "")
    if not ok:
        verdict["pass"] = False


def mk_demo_project():
    """混合跟踪 + 托盘容器项目: SY9 四个新字段的宿主。"""
    r = requests.post(f"{API}/projects", json={
        "name": PROJ_NAME, "task_type": "detection", "logic_mode": "custom",
    }, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/projects/{pid}", json={
        "steps_config": [
            {"id": 1, "label": "托盘", "name": "托盘", "enabled": True,
             "threshold": 0.35, "min_frames": 2, "disappear_delay": 0.35},
            {"id": 2, "label": "滑块", "name": "滑块", "enabled": True,
             "threshold": 0.35, "min_frames": 2, "disappear_delay": 0.35,
             "detect_role": "item", "count_mode": "track", "expected_count": 24},
            {"id": 3, "label": "凹槽", "name": "凹槽", "enabled": True,
             "threshold": 0.35, "min_frames": 1, "disappear_delay": 0.35},
            {"id": 4, "label": "封箱", "name": "封箱", "enabled": True,
             "threshold": 0.35, "min_frames": 2, "disappear_delay": 0.35},
        ],
        "pipeline_config": {
            "custom_mixed_with": "tracking",
            "custom_mix_container_label": "托盘",
            "custom_mix_container_count_mode": "items_total",
            "custom_mix_container_item_target": 96,
        },
    }, timeout=5).raise_for_status()
    return pid


def open_logic_tab(page):
    page.goto(f"{BASE_URL}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.locator("input[placeholder*='搜索项目']").fill(PROJ_NAME)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{PROJ_NAME}')").first.click(timeout=5000)
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(0.8)


def container_section(page):
    sec = page.locator(".el-form-item:has-text('容器装箱清点')").first
    sec.scroll_into_view_if_needed()
    time.sleep(0.4)
    return sec


def row_of(sec, label):
    return sec.locator(f"div.flex:has-text('{label}')").last


def set_number_in_row(row, idx, value):
    inp = row.locator(".el-input-number input").nth(idx)
    inp.click()
    inp.press("ControlOrMeta+a")
    inp.type(str(value), delay=15)
    inp.press("Tab")
    time.sleep(0.3)


def part_a_logic_tab(page, pid):
    open_logic_tab(page)
    sec = container_section(page)
    body = sec.inner_text()
    ok = all(k in body for k in ("空槽标签", "每盘槽位数", "物品框去重(IoU)", "托盘框去重(IoU)"))
    check("A1 SY9 四字段渲染", ok, body[:200])

    dedup_row = row_of(sec, "物品框去重(IoU)")
    item_v = dedup_row.locator(".el-input-number input").nth(0).input_value()
    tray_v = dedup_row.locator(".el-input-number input").nth(1).input_value()
    check("A2 去重默认值零差异 (物品0.45/托盘0)",
          item_v == "0.45" and tray_v in ("0", "0.00"),
          f"item={item_v} tray={tray_v}")

    # 槽位门: 空槽标签(allow-create select) + 槽位数
    slot_row = row_of(sec, "空槽标签")
    slot_sel = slot_row.locator(".el-select").first
    slot_sel.click()
    time.sleep(0.4)
    page.locator(".el-select-dropdown__item:visible", has_text="凹槽").first.click()
    time.sleep(0.3)
    set_number_in_row(slot_row, 0, 24)
    # 去重阈值: 物品 0.5 / 托盘 0.5
    set_number_in_row(dedup_row, 0, 0.5)
    set_number_in_row(dedup_row, 1, 0.5)
    sec.screenshot(path=str(EV / "01_sy9_fields_configured.png"))

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)
    pc = requests.get(f"{API}/projects/{pid}", timeout=5).json().get("pipeline_config") or {}
    check("A3 槽位门落库",
          pc.get("custom_mix_container_slot_check_label") == "凹槽"
          and pc.get("custom_mix_container_slot_total") == 24,
          {k: pc.get(k) for k in ("custom_mix_container_slot_check_label",
                                  "custom_mix_container_slot_total")})
    check("A4 去重阈值落库",
          pc.get("custom_mix_container_item_dedup_iou") == 0.5
          and pc.get("custom_mix_container_tray_dedup_iou") == 0.5,
          {k: pc.get(k) for k in ("custom_mix_container_item_dedup_iou",
                                  "custom_mix_container_tray_dedup_iou")})

    # 重进回显
    open_logic_tab(page)
    sec = container_section(page)
    dedup_row = row_of(sec, "物品框去重(IoU)")
    slot_row = row_of(sec, "空槽标签")
    item_v = dedup_row.locator(".el-input-number input").nth(0).input_value()
    tray_v = dedup_row.locator(".el-input-number input").nth(1).input_value()
    slot_n = slot_row.locator(".el-input-number input").nth(0).input_value()
    slot_lbl = slot_row.locator(".el-select").first.inner_text()
    check("A5 重进回显",
          item_v in ("0.5", "0.50") and tray_v in ("0.5", "0.50")
          and slot_n == "24" and "凹槽" in slot_lbl,
          f"item={item_v} tray={tray_v} slot={slot_n} lbl={slot_lbl}")
    sec.screenshot(path=str(EV / "02_sy9_fields_reopen.png"))


def part_b_packaging(page):
    # API 建演示配置 (as_close_action 前置开, 开关才露出)
    r = requests.post(f"{API}/packaging-flows", json={
        "name": FLOW_NAME, "channel_id": 0,
        "count_unit": "sliders",   # 组⑦ 仅滑块口径下渲染
        "tail_paper_order_required": True,
        "tail_paper_step_label": "放工单",
        "tail_paper_as_close_action": True,
    }, timeout=5)
    check("B0 演示包装配置创建", r.status_code in (200, 201), r.text[:200])
    fid = r.json().get("id")

    page.goto(f"{BASE_URL}/#/settings", wait_until="networkidle")
    page.locator(".el-tabs__item:has-text('包装箱结算')").first.click()
    time.sleep(0.8)
    row = page.locator(f"tr:has-text('{FLOW_NAME}')").first
    row.scroll_into_view_if_needed()
    row.locator("button:has-text('编辑')").click()
    page.wait_for_selector(".el-dialog:visible", timeout=8000)
    time.sleep(0.8)
    d = page.locator(".el-dialog:visible").first
    hdr = d.locator(".el-collapse-item__header:has-text('滑块口径设置')").first
    hdr.scroll_into_view_if_needed()
    if "is-active" not in (hdr.get_attribute("class") or ""):
        hdr.click()
    time.sleep(0.6)
    item = d.locator(".el-form-item:has-text('只认收尾后放的工单')").first
    item.scroll_into_view_if_needed()
    time.sleep(0.3)
    check("B1 开关可见", item.count() > 0 and "只认收尾后放的工单" in d.inner_text())
    sw = item.locator(".el-switch").first
    check("B2 默认关", "is-checked" not in (sw.get_attribute("class") or ""))
    sw.click()
    time.sleep(0.3)
    item.screenshot(path=str(EV / "03_tail_paper_switch_on.png"))
    d.locator(".el-dialog__footer button:has-text('保')").last.click()
    time.sleep(1.5)
    flows = requests.get(f"{API}/packaging-flows", timeout=5).json().get("items", [])
    mine = next((f for f in flows if f.get("id") == fid), None)
    check("B3 tail_paper_only_after_awaiting 落库",
          mine is not None and mine.get("tail_paper_only_after_awaiting") is True,
          mine)
    page.screenshot(path=str(EV / "04_packaging_panel.png"), full_page=True)
    # 清理
    if fid:
        requests.delete(f"{API}/packaging-flows/{fid}", timeout=5)


def cleanup():
    items = requests.get(f"{API}/projects", timeout=5).json().get("items", [])
    for p in items:
        if p["name"] == PROJ_NAME:
            requests.delete(f"{API}/projects/{p['id']}", timeout=5)
    flows = requests.get(f"{API}/packaging-flows", timeout=5).json().get("items", [])
    for f in flows:
        if f.get("name") == FLOW_NAME:
            requests.delete(f"{API}/packaging-flows/{f['id']}", timeout=5)


def main():
    cleanup()
    pid = mk_demo_project()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=80)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        try:
            part_a_logic_tab(page, pid)
            part_b_packaging(page)
        finally:
            (EV / "verdict.json").write_text(
                json.dumps(verdict, ensure_ascii=False, indent=2))
            browser.close()
            cleanup()

    print("\n=== 总判定:", "PASS" if verdict["pass"] else "FAIL", "===")
    sys.exit(0 if verdict["pass"] else 1)


if __name__ == "__main__":
    main()
