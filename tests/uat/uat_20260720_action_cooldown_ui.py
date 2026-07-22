# -*- coding: utf-8 -*-
"""UAT: 进箱确认「动作门槛直配三参数」(v3.43.1) — 可见浏览器真操作验证。

场景: 治客户机"放托盘一次动作结算两次" — 动作门槛此前借用步骤字段且该模式无 UI,
本批次在逻辑设置 → 进箱确认方式里露出: 动作出现确认帧 / 动作消失确认帧 / 进箱最小间隔(秒)。

验证路径 (操作员视角, 写侧全走真实鼠标键盘, API 只做读侧核对):
  1. 项目管理页搜索 → 点项目卡 → 点「逻辑设置」Tab → 三个新字段肉眼可见 (入口断言)
  2. 真实键入 4 / 20 / 3.5 → 点「保存配置」
  3. 读侧: GET 项目详情, pipeline 配置三键落库核对
  4. 刷新页面重进 Tab → 输入框回显 4 / 20 / 3.5 (水合断言)

前置: backend 8001 + frontend 6001 已启动。临时项目用 API 造 (前置条件, 非被测功能),
结束删除。不激活任何项目, 不碰现场在用的 SY3。
"""
import sys
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

API = "http://localhost:8001"
WEB = "http://localhost:6001"
SHOT_DIR = "/tmp/uat_action_cooldown"

NAME = f"__uat_cd_{uuid.uuid4().hex[:6]}"


def seed_project():
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": NAME, "task_type": "detection", "logic_mode": "custom",
        "pipeline_config": {
            "custom_based_on": "sequential",
            "custom_mixed_with": "tracking",
            "custom_mix_container_label": "托盘",
            "custom_mix_container_count_mode": "items_total",
            "custom_mix_container_item_target": 96,
            "custom_mix_container_confirm_by_frames": False,
            "custom_mix_container_confirm_by_action": True,
            "custom_mix_container_action_label": "放托盘",
        },
        "steps_config": [
            {"id": 1, "label": "贴标", "enabled": True},
            {"id": 2, "label": "封箱", "enabled": True},
            {"id": 3, "label": "放托盘", "enabled": True, "min_frames": 2},
            {"id": 4, "label": "滑块", "enabled": True, "detect_role": "item",
             "count_mode": "track", "expected_count": 24},
        ],
        "events_config": [], "counters_config": [], "data_config": {},
    }, timeout=10)
    r.raise_for_status()
    return r.json()["id"]


def fill_number(page, row_text, nth, value):
    """定位含 row_text 的行里第 nth 个数字输入框, 真实键入 value。"""
    row = page.locator(f"div.flex:has-text('{row_text}')").last
    inp = row.locator(".el-input-number input").nth(nth)
    inp.click()
    inp.press("ControlOrMeta+a")
    inp.type(str(value), delay=30)
    inp.press("Tab")
    time.sleep(0.3)


def open_logic_tab(page):
    page.goto(f"{WEB}/#/project", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("text=项目管理", timeout=10000)
    time.sleep(1.0)
    page.locator("input[placeholder*='搜索项目']").fill(NAME)
    time.sleep(0.8)
    page.locator(f"div.p-4:has-text('{NAME}')").first.click(timeout=5000)
    time.sleep(1.0)
    page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
    time.sleep(1.0)


def main():
    import os
    os.makedirs(SHOT_DIR, exist_ok=True)
    pid = seed_project()
    print(f"[UAT] 临时项目 {NAME} id={pid}")
    ok = True
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, slow_mo=120)
            page = browser.new_page(viewport={"width": 1600, "height": 950})
            open_logic_tab(page)

            # ---- 入口断言: 操作员能看到三个新字段 ----
            body = page.evaluate("document.body.innerText")
            for label in ("动作出现确认帧", "动作消失确认帧", "进箱最小间隔"):
                assert label in body, f"入口断言失败: 页面上找不到「{label}」"
            page.locator("text=进箱确认方式").first.scroll_into_view_if_needed()
            time.sleep(0.5)
            page.screenshot(path=f"{SHOT_DIR}/01_fields_visible.png", full_page=True)
            print("[UAT] 入口断言通过: 三个字段可见")

            # ---- 真实键入 + 保存 ----
            fill_number(page, "动作出现确认帧", 0, 4)     # 出现帧
            fill_number(page, "动作出现确认帧", 1, 20)    # 消失帧 (同一行第 2 个)
            fill_number(page, "进箱最小间隔", 0, 3.5)     # 不应期
            page.screenshot(path=f"{SHOT_DIR}/02_values_typed.png", full_page=True)
            page.locator("button:has-text('保存配置')").first.click()
            time.sleep(2.5)

            # ---- 读侧核对: 落库 ----
            detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
            pc = detail.get("pipeline_config") or {}
            got = (pc.get("custom_mix_container_action_min_frames"),
                   pc.get("custom_mix_container_action_gone_frames"),
                   pc.get("custom_mix_container_action_cooldown_s"))
            assert got == (4, 20, 3.5), f"落库核对失败: {got}"
            print(f"[UAT] 落库核对通过: {got}")

            # ---- 刷新回显 (水合) ----
            open_logic_tab(page)
            page.locator("text=进箱确认方式").first.scroll_into_view_if_needed()
            time.sleep(0.5)
            row = page.locator("div.flex:has-text('动作出现确认帧')").last
            v1 = row.locator(".el-input-number input").nth(0).input_value()
            v2 = row.locator(".el-input-number input").nth(1).input_value()
            row2 = page.locator("div.flex:has-text('进箱最小间隔')").last
            v3 = row2.locator(".el-input-number input").nth(0).input_value()
            assert (v1, v2, v3) == ("4", "20", "3.5"), f"回显失败: {(v1, v2, v3)}"
            page.screenshot(path=f"{SHOT_DIR}/03_reload_hydrated.png", full_page=True)
            print(f"[UAT] 刷新回显通过: {(v1, v2, v3)}")
            browser.close()
    except Exception as e:
        ok = False
        print(f"[UAT] 失败: {e}")
    finally:
        requests.delete(f"{API}/api/v1/projects/{pid}", timeout=5)
        print(f"[UAT] 临时项目已删除 id={pid}")
    print("[UAT] 结果:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
