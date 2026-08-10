"""LG 工时看板 v1.2 布局 UAT: 单/双/三工位三种布局真浏览器截图.

三种模式断言 (与 index.esm.js layoutMode 对齐):
  1 工位 mode-single: 1 面板 + 流程带 + 右栏含「当前周期」卡 (数据全量) + KPI 副行可见
  2 工位 mode-dual:   2 面板并排, 各自流程带
  3 工位 mode-focus:  左缩略列 3 张(可点切换) + 1 焦点面板 + 流程带

用法 (dev 环境, 后端 8004 + 前端 6004 + lg-worktime 插件已激活 + 演示数据已 seed):
    python tests/uat/uat_lg_worktime_layouts.py

产出: evidence/lgwt_layout_{1,2,3}ws.png + lgwt_layout_3ws_switch.png
"""
import os
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6004"
BACKEND = "http://localhost:8004/api/v1"
OUT_DIR = Path(__file__).resolve().parents[2] / "evidence"
OUT_DIR.mkdir(exist_ok=True)


def set_channel_count(n: int):
    r = requests.post(f"{BACKEND}/workstations/mode", json={"channel_count": n, "channels": []}, timeout=10)
    r.raise_for_status()
    print(f"[uat] channel_count -> {n}: {r.json().get('status')}")


def goto_dashboard(page, n):
    set_channel_count(n)
    page.goto(f"{FRONTEND}/monitor", wait_until="domcontentloaded")
    page.wait_for_selector(".lgwt-dashboard", timeout=20000)
    time.sleep(3.5)  # 等轮询数据 + 流程带渲染


def main():
    fails = []
    with sync_playwright() as p:
        # UAT_HEADLESS=1: 多 agent 并行时禁止抢显示器/鼠标, 全程 headless
        browser = p.chromium.launch(headless=os.environ.get("UAT_HEADLESS") == "1")
        page = browser.new_page(viewport={"width": 1680, "height": 945})
        try:
            # ---- 1 工位 single: 数据全量 ----
            goto_dashboard(page, 1)
            assert page.locator(".lgwt-main.mode-single").count() == 1, "应为 mode-single"
            assert page.locator(".lgwt-station").count() == 1
            assert page.locator(".lgwt-card-live").count() == 1, "single 模式右栏应有当前周期卡"
            assert page.locator(".lgwt-flow-card").count() >= 3, "流程带应有步骤卡"
            # v1.5.x 起 KPI 副行按需求隐藏 (theme.css: 副行不占高度, 画面优先),
            # 断言从"可见"翻转为"必须隐藏", 防止旧样式复活又把顶部撑高
            if page.locator(".lgwt-kpi-sub").count():
                assert not page.locator(".lgwt-kpi-sub").first.is_visible(), \
                    "KPI 副行应保持隐藏 (画面优先设计)"
            vw = page.locator(".lgwt-video-wrap").first.bounding_box()
            page.screenshot(path=str(OUT_DIR / "lgwt_layout_1ws.png"))
            print(f"[uat] 1工位 single OK 视频={round(vw['width'])}x{round(vw['height'])}")

            # ---- 2 工位 dual ----
            goto_dashboard(page, 2)
            assert page.locator(".lgwt-main.mode-dual").count() == 1, "应为 mode-dual"
            assert page.locator(".lgwt-station").count() == 2
            assert page.locator(".lgwt-rail .lgwt-card-live").count() == 1, "dual 右栏应有焦点工位当前周期卡"
            page.screenshot(path=str(OUT_DIR / "lgwt_layout_2ws.png"))
            print("[uat] 2工位 dual OK")

            # ---- 3 工位 focus: 缩略列 + 主画面 ----
            goto_dashboard(page, 3)
            assert page.locator(".lgwt-main.mode-focus").count() == 1, "应为 mode-focus"
            assert page.locator(".lgwt-thumb").count() == 3, "缩略列应 3 张"
            assert page.locator(".lgwt-station").count() == 1, "focus 模式只 1 个焦点面板"
            assert "工位 1" in page.locator(".lgwt-video-tag").first.inner_text()
            page.screenshot(path=str(OUT_DIR / "lgwt_layout_3ws.png"))
            # 点缩略图切到工位 3
            page.locator(".lgwt-thumb").nth(2).click()
            time.sleep(1.2)
            assert "工位 3" in page.locator(".lgwt-video-tag").first.inner_text(), "点缩略图应切焦点"
            assert page.locator(".lgwt-thumb.is-focused").count() == 1
            page.screenshot(path=str(OUT_DIR / "lgwt_layout_3ws_switch.png"))
            print("[uat] 3工位 focus + 缩略切换 OK")
        except AssertionError as e:
            fails.append(str(e))
            page.screenshot(path=str(OUT_DIR / "lgwt_layout_FAIL.png"))
        finally:
            set_channel_count(1)  # 恢复
            browser.close()

    if fails:
        print(f"[uat] FAIL: {fails}")
        sys.exit(1)
    print("[uat] 三种布局全部通过")


if __name__ == "__main__":
    main()
