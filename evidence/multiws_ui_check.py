"""v3.47 多工位布局重构 — headless UI 验证脚本 (临时验证用, 不进 CI)

用法: python evidence/multiws_ui_check.py <step>
  step=3ch    : 三工位三行布局截图
  step=grid   : 6 工位总览网格 (自动布局) + 2x2 分页 + 放大详情 + 上一路/下一路
"""
import sys
import time
from playwright.sync_api import sync_playwright

FRONT = "http://localhost:6002"
OUT = "evidence/screenshots"

step = sys.argv[1] if len(sys.argv) > 1 else "3ch"


def snap(page, name):
    page.screenshot(path=f"{OUT}/{name}.png", full_page=False)
    print(f"[snap] {name}.png")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    logs = []
    page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
    page.goto(f"{FRONT}/#/monitor")
    page.wait_for_timeout(4000)

    if step == "3ch":
        snap(page, "01_triple_rows")
        # 断言: 三行, 每行有 视频卡 + 数据面板 (SOP 文案 x3, 控制按钮 x3)
        sop = page.locator("text=SOP").count()
        start_btns = page.get_by_role("button", name="开始").count()
        total_cells = page.locator("text=总产量").count()
        print(f"SOP 区块数={sop} 开始按钮数={start_btns} 总产量计数卡={total_cells}")
        assert start_btns == 3, "三工位应有 3 个开始按钮"
        assert total_cells == 3, "三工位应有 3 个总产量卡"

    elif step == "grid":
        # —— 总览 (自动布局: 6 工位 → 3x3) ——
        snap(page, "02_grid_auto_6ch")
        assert page.locator("text=多工位总览").count() == 1, "应显示总览工具条"
        cards = page.locator("text=/^工位\\d+$/").count()
        print(f"总览卡片角标数={cards}")

        # —— 切 2x2 布局 → 出现分页 ——
        page.get_by_role("button", name="2×2", exact=True).click()
        page.wait_for_timeout(1500)
        snap(page, "03_grid_2x2_page1")
        assert page.locator("text=1 / 2").count() == 1, "6 工位 2x2 应有 2 页"
        page.get_by_role("button", name="下一页 ›").click()
        page.wait_for_timeout(1500)
        snap(page, "04_grid_2x2_page2")
        # 第 2 页应显示 工位5 工位6 + 2 个空位
        assert page.locator("text=— 空 —").count() == 2, "第 2 页应有 2 个空补位"

        # —— 点击工位5 卡片 → 放大详情 ——
        page.locator("text=工位5").first.click()
        page.wait_for_timeout(1500)
        snap(page, "05_zoom_ch5")
        assert page.get_by_role("button", name="‹ 返回总览").count() == 1, "放大视图应有返回按钮"
        assert page.locator("text=SOP 流程").count() == 1, "放大视图应有 SOP 流程面板"

        # —— 下一路 (工位5 → 工位6), 再下一路回卷 (工位6 → 工位1) ——
        page.get_by_role("button", name="下一路 ›").click()
        page.wait_for_timeout(800)
        assert page.locator("text=工位 6").count() >= 1, "下一路应到工位 6"
        page.get_by_role("button", name="下一路 ›").click()
        page.wait_for_timeout(800)
        snap(page, "06_zoom_wrap_ch1")
        assert page.locator("text=工位 1").count() >= 1, "从末路下一路应回卷到工位 1"
        page.get_by_role("button", name="‹ 上一路").click()
        page.wait_for_timeout(800)
        assert page.locator("text=工位 6").count() >= 1, "上一路应回到工位 6"

        # —— 返回总览: 应停在工位6 所在页 (2x2 第 2 页) ——
        page.get_by_role("button", name="‹ 返回总览").click()
        page.wait_for_timeout(1200)
        snap(page, "07_back_to_grid_page2")
        assert page.locator("text=2 / 2").count() == 1, "返回总览应停在工位 6 所在的第 2 页"

        # —— 3x3 布局: 单页无分页控件 ——
        page.get_by_role("button", name="3×3", exact=True).click()
        page.wait_for_timeout(1200)
        snap(page, "08_grid_3x3")
        assert page.locator("text=下一页 ›").count() == 0, "6 工位 3x3 单页不应显示分页"
        assert page.locator("text=— 空 —").count() == 3, "3x3 应有 3 个空补位"

    err = [l for l in logs if l.startswith("error")]
    print("console errors:", err[:10] if err else "无")
    base = [l for l in logs if "Final baseURL" in l]
    print("baseURL:", base)
    browser.close()
    print("PASS", step)
