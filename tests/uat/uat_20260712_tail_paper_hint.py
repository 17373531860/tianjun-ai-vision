"""T4: 包装箱结算面板「放工单=收尾动作」子开关 可见浏览器验证.

打开 设置→包装箱结算 → 编辑上银SY配置 → 展开⑦滑块口径设置组 →
确认: ① 主开关"尾箱必须塞工单"文案回到老语义; ② 新子开关"放工单=收尾动作"随主开关出现,
提示写清 开=挂起快照收尾 / 关=老行为. 截图留证.
"""
import sys
import time

from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
OUT = "tests/uat"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=150)
    page = browser.new_page(viewport={"width": 1680, "height": 950})
    page.goto(f"{FE}/#/settings", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    time.sleep(1.5)

    page.get_by_role("tab", name="包装箱结算").click()
    time.sleep(1)
    row = page.locator("tr", has_text="上银SY包装线").first
    row.get_by_role("button", name="编辑").click()
    page.wait_for_selector(".el-dialog", state="visible", timeout=5000)
    time.sleep(0.8)

    page.get_by_text("⑦ 滑块口径设置", exact=False).click()
    time.sleep(0.8)
    sub = page.get_by_text("放工单=收尾动作", exact=True).first
    sub.scroll_into_view_if_needed()
    time.sleep(0.5)
    ok_sub = sub.is_visible()
    ok_hint = page.get_by_text("尾箱装满被拦时先记住这箱成绩", exact=False).first.is_visible()
    ok_old = page.get_by_text("老行为: 只报警等着", exact=False).first.is_visible()
    page.screenshot(path=f"{OUT}/tail_paper_close_action_dialog.png")
    browser.close()
    print("SUB_SWITCH_VISIBLE:", ok_sub, "HINT_NEW:", ok_hint, "HINT_OLD:", ok_old)
    sys.exit(0 if (ok_sub and ok_hint and ok_old) else 1)
