"""UAT (可见浏览器): 设置页上传导航栏 Logo.

链路: 显示设置上传图片 → 前端压缩为 256×256 data URL → 导航栏立即生效
     → localStorage 持久化 (刷新不丢) → 恢复默认回退内置图。

前置: frontend 6001 已启动 (纯前端功能, 不依赖后端)。
产物: /tmp/uat_navbar_logo/*.png
"""
from __future__ import annotations

import os
import time

from playwright.sync_api import sync_playwright

BASE = "http://localhost:6001"
OUT = "/tmp/uat_navbar_logo"
TEST_LOGO = f"{OUT}/test_logo.png"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail}")


def make_test_logo():
    """生成一张纯紫底白字的测试 logo (与默认图肉眼可区分)。"""
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (400, 400), '#7c3aed')
    d = ImageDraw.Draw(img)
    d.ellipse([100, 100, 300, 300], fill='#ffffff')
    img.save(TEST_LOGO)


def navbar_logo_src(page):
    # 右上角圆形 logo (rounded-full); 左侧可能还有插件主题 logo, 不取
    return page.locator("header img.rounded-full[alt='logo']").first.get_attribute("src")


def main():
    os.makedirs(OUT, exist_ok=True)
    make_test_logo()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})
        page.goto(f"{BASE}/#/settings", wait_until="domcontentloaded")
        page.wait_for_selector("text=基本信息设置", timeout=15000)
        time.sleep(1.0)

        # 0) 初始态: 默认内置图
        check("初始导航栏用内置图", (navbar_logo_src(page) or '').endswith('app-icon.png'),
              f"src={str(navbar_logo_src(page))[:40]}")
        card = page.locator("div.bg-slate-900:has-text('导航栏 Logo')").first
        card.scroll_into_view_if_needed()
        page.screenshot(path=f"{OUT}/01_before.png")

        # 1) 上传 → 导航栏立即变 data URL
        card.locator("input[type='file']").set_input_files(TEST_LOGO)
        time.sleep(1.2)
        src = navbar_logo_src(page) or ''
        check("上传后导航栏立即生效 (data URL)", src.startswith('data:image/'),
              f"src前缀={src[:22]}")
        page.screenshot(path=f"{OUT}/02_uploaded.png")

        # 2) 刷新不丢 (localStorage 持久化)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=基本信息设置", timeout=15000)
        time.sleep(1.0)
        src2 = navbar_logo_src(page) or ''
        check("刷新后 Logo 保持", src2.startswith('data:image/') and src2 == src,
              f"一致={src2 == src}")
        page.screenshot(path=f"{OUT}/03_after_reload.png")

        # 3) 恢复默认
        card = page.locator("div.bg-slate-900:has-text('导航栏 Logo')").first
        card.scroll_into_view_if_needed()
        card.locator("button:has-text('恢复默认')").click()
        time.sleep(0.8)
        check("恢复默认回退内置图", (navbar_logo_src(page) or '').endswith('app-icon.png'),
              f"src={str(navbar_logo_src(page))[:40]}")
        # 恢复后按钮消失 (v-if 守门)
        check("恢复默认按钮随之隐藏", card.locator("button:has-text('恢复默认')").count() == 0)
        page.screenshot(path=f"{OUT}/04_reset.png")

        browser.close()
    fails = [r for r in results if not r[1]]
    print(f"\n结果: {len(results) - len(fails)}/{len(results)} 通过")
    if fails:
        for n, _, d in fails:
            print(f"  FAIL: {n} {d}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
