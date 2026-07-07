"""UAT 2026-07-07: 停用 showcase 插件后，主程序原生 UI 不受影响冒烟。

前置: 后端 8001 / 前端 6001 已起, showcase 插件已 deactivate。
验证点:
  A. 首页加载为主程序原生 Vue UI (无插件 iframe / 无插件覆盖层)
  B. Monitor 页正常: 视频画面出流 + 区域事件 SOP 面板渲染
  C. Settings 页正常打开 (含 MediaPipe 骨架样式设置)
  D. 控制台无红色报错 (插件加载失败类的除外——本来就没插件)
"""
import sys, time
from playwright.sync_api import sync_playwright
from _common import UatRun, launch_browser

BASE = "http://localhost:6001"


def main() -> int:
    run = UatRun("main_without_plugin")
    with sync_playwright() as p:
        browser, context, page, console_errors = launch_browser(p, record_video_dir=run.dir)
        try:
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # A. 无插件 iframe, 原生导航存在
            iframes = page.locator("iframe").count()
            run.step("A1 无插件整页覆盖 iframe", iframes == 0, f"iframe count={iframes}")
            # 主程序导航是收起的抽屉, 常态 DOM 里只有左上角汉堡按钮
            body_home = page.inner_text("body")
            native_ui = ("天军科技" in body_home) and ("检测次数" in body_home or "开始" in body_home)
            run.step("A2 原生主程序 UI 渲染 (导航抽屉收起态)", native_ui)
            run.shot(page, "01_home_native")

            # B. Monitor 页
            page.goto(BASE + "/monitor", wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            img_ok = page.evaluate("""() => {
                const imgs=[...document.querySelectorAll('img')];
                return imgs.some(i=>i.src.includes('video_feed')&&i.naturalWidth>100);
            }""")
            run.step("B1 Monitor 视频画面出流", bool(img_ok), f"video_feed natural ok={img_ok}")
            body_txt = page.inner_text("body")
            sop_ok = ("测硬度" in body_txt) or ("SOP" in body_txt) or ("步骤" in body_txt)
            run.step("B2 区域事件 SOP/步骤面板渲染", sop_ok)
            run.shot(page, "02_monitor_native")

            # C. Settings 页
            page.goto(BASE + "/settings", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            s_txt = page.inner_text("body")
            run.step("C1 Settings 页打开", ("设置" in s_txt) or ("Settings" in s_txt))
            run.shot(page, "03_settings_native")

            # D. 控制台错误
            fatal = [e for e in console_errors if "plugins/active" not in e]
            run.step("D1 控制台无致命报错", len(fatal) == 0, "; ".join(fatal[:3]))
        finally:
            context.close(); browser.close()
    return run.finish()


if __name__ == "__main__":
    sys.exit(main())
