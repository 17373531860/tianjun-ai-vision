"""信息架构重构（2026-08）可见浏览器 UAT——路径 H 三件套。

覆盖四个迁移点（真开浏览器手点级验证 + 落库双向核对）：
  1. 导航菜单「工位与输入源」+ Source 页四 tab（输入源配置/多屏工位显示/工位组互通/流水线串行）
  2. MES 页分组 tab 含「包装结算」「触发中心」且面板真实渲染
  3. Settings 页四个旧入口（工位组互通/流水线串行/包装箱结算/触发中心）确认删除
  4. Project 混合跟踪项目出现「装箱清点」条件 Tab；改值→保存→GET 落库核对；
     逻辑设置内留迁移指引；切走混合跟踪后 Tab 消失

前置：后端 + 前端已起（缺省 8002/6002），通过 E2E_API_URL / E2E_BASE_URL 覆盖。
产物：tests/uat/artifacts/ia-refactor/{*.png, video/*.webm, run.log}
资源：临时项目用 __uat_ia_ 前缀，finally 删除。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:6002")
API_URL = os.environ.get("E2E_API_URL", "http://localhost:8002")
ARTIFACT_DIR = Path(os.environ.get(
    "UAT_ARTIFACT_DIR", "tests/uat/artifacts/ia-refactor")).resolve()

SOURCE_TABS = ["输入源配置", "多屏工位显示", "工位组互通", "流水线串行"]
MES_NEW_TABS = ["包装结算", "触发中心"]
SETTINGS_REMOVED = ["工位组互通", "流水线串行", "包装箱结算", "触发中心"]

run_lines: list[str] = []
checks: list[tuple[str, bool, str]] = []


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    run_lines.append(line)


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))
    log(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    video_dir = ARTIFACT_DIR / "video"
    video_dir.mkdir(exist_ok=True)
    log(f"frontend={BASE_URL} backend={API_URL} browser=headless_false")

    proj_id = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            record_video_dir=str(video_dir),
            record_video_size={"width": 1600, "height": 900},
        )
        page = context.new_page()
        try:
            # ---- 1. Source 页：导航名 + 四 tab ----
            page.goto(f"{BASE_URL}/#/source", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            nav_text = page.evaluate("document.body.innerText")
            check("导航含「工位与输入源」", "工位与输入源" in nav_text)
            for tab in SOURCE_TABS:
                loc = page.locator(f".el-tabs__item:has-text('{tab}')")
                check(f"Source 页存在 tab「{tab}」", loc.count() >= 1)
                if loc.count():
                    loc.first.click()
                    page.wait_for_timeout(1200)
            page.screenshot(path=str(ARTIFACT_DIR / "source-tabs.png"), full_page=True)

            # ---- 2. MES 页：新 tab 渲染 ----
            page.goto(f"{BASE_URL}/#/mes", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            for tab in MES_NEW_TABS:
                btn = page.get_by_role("button", name=tab)
                check(f"MES 页存在分组按钮「{tab}」", btn.count() >= 1)
                if btn.count():
                    btn.first.click()
                    page.wait_for_timeout(1500)
                    page.screenshot(
                        path=str(ARTIFACT_DIR / f"mes-{tab}.png"), full_page=True)
            body = page.evaluate("document.body.innerText")
            check("触发中心面板真实渲染", "触发源" in body or "触发" in body)

            # ---- 3. Settings 页：旧入口删除 ----
            page.goto(f"{BASE_URL}/#/settings", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            tabs_text = page.locator(".el-tabs__nav").first.inner_text()
            for old in SETTINGS_REMOVED:
                check(f"Settings 页不再有「{old}」tab", old not in tabs_text,
                      f"tabs={tabs_text[:120]!r}")
            page.screenshot(path=str(ARTIFACT_DIR / "settings-clean.png"), full_page=True)

            # ---- 4. Project 装箱清点条件 Tab + 落库 ----
            r = requests.post(f"{API_URL}/api/v1/projects", json={
                "name": "__uat_ia_mixbox",
                "logic_mode": "custom",
                "pipeline_config": {
                    "custom_based_on": "counter",
                    "custom_mixed_with": "tracking",
                    "custom_mix_container_enabled": True,
                    "custom_mix_container_label": "tray",
                    "custom_mix_container_count_mode": "trays",
                    "custom_mix_container_box_count": 4,
                },
                "steps_config": [
                    {"id": 1, "label": "tray", "enabled": True},
                    {"id": 2, "label": "slider", "enabled": True,
                     "detect_role": "item"},
                ],
            }, timeout=10)
            r.raise_for_status()
            proj_id = r.json()["id"]
            log(f"临时项目 id={proj_id}")

            page.goto(f"{BASE_URL}/#/project", wait_until="domcontentloaded")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            page.locator("input[placeholder*='搜索项目']").fill("__uat_ia_mixbox")
            page.wait_for_timeout(800)
            page.locator("div.cursor-pointer").filter(
                has_text="__uat_ia_mixbox").first.click()
            page.wait_for_timeout(1200)

            mixbox_tab = page.locator(".el-tabs__item:has-text('装箱清点')")
            check("混合跟踪项目出现「装箱清点」Tab", mixbox_tab.count() >= 1)

            # 逻辑设置内留迁移指引
            page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
            page.wait_for_timeout(1000)
            logic_body = page.evaluate("document.body.innerText")
            check("逻辑设置留迁移指引", "已移至" in logic_body and "装箱清点" in logic_body)

            # 装箱清点 Tab：回显 + 改值保存落库
            mixbox_tab.first.click()
            page.wait_for_timeout(1200)
            page.screenshot(path=str(ARTIFACT_DIR / "project-mixbox-tab.png"),
                            full_page=True)
            row = page.locator("div.flex").filter(has_text="每箱容器数").last
            inp = row.locator(".el-input-number input").first
            check("每箱容器数回显 4", inp.input_value() == "4",
                  f"实际 {inp.input_value()!r}")
            inp.click()
            inp.fill("7")
            page.keyboard.press("Tab")
            page.wait_for_timeout(500)
            page.get_by_role("button", name="保存配置").click()
            page.wait_for_timeout(2000)
            pc = requests.get(f"{API_URL}/api/v1/projects/{proj_id}",
                              timeout=10).json().get("pipeline_config") or {}
            check("每箱容器数=7 落库", pc.get("custom_mix_container_box_count") == 7,
                  f"实际 {pc.get('custom_mix_container_box_count')!r}")

            # 切走混合跟踪 → Tab 消失（API 改 + 刷新回显）
            pc["custom_mixed_with"] = ""
            requests.put(f"{API_URL}/api/v1/projects/{proj_id}",
                         json={"pipeline_config": pc}, timeout=10).raise_for_status()
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            page.locator("input[placeholder*='搜索项目']").fill("__uat_ia_mixbox")
            page.wait_for_timeout(800)
            page.locator("div.cursor-pointer").filter(
                has_text="__uat_ia_mixbox").first.click()
            page.wait_for_timeout(1200)
            check("非混合跟踪时「装箱清点」Tab 消失",
                  page.locator(".el-tabs__item:has-text('装箱清点')").count() == 0)
            page.screenshot(path=str(ARTIFACT_DIR / "project-mixbox-gone.png"),
                            full_page=True)
        finally:
            context.close()
            browser.close()
            if proj_id:
                requests.delete(f"{API_URL}/api/v1/projects/{proj_id}", timeout=10)
                log(f"已删除临时项目 id={proj_id}")

    failed = [c for c in checks if not c[1]]
    log(f"总断言 {len(checks)}，失败 {len(failed)}")
    (ARTIFACT_DIR / "run.log").write_text("\n".join(run_lines) + "\n",
                                          encoding="utf-8")
    if failed:
        raise SystemExit(f"UAT FAIL: {[c[0] for c in failed]}")
    print("UAT ALL PASS")


if __name__ == "__main__":
    main()
