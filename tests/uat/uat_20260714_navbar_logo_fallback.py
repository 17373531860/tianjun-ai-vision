# -*- coding: utf-8 -*-
"""UAT 2026-07-14: 右上角导航栏 Logo 默认回退验证 (v3.36.2)

背景: v3.36.0 把右上角 Logo 从静态地址改成运行时表达式后, 打包版 (file:// + 相对 base)
下内置回退图标 '/app-icon.png' 会解析到盘根导致图标空白。
本脚本验证修复后:
  1. 未上传自定义 Logo 时, 右上角 img 真实加载出内置图标 (naturalWidth > 0)
  2. 设置页 Logo 预览同样能加载
  3. img 的 src 不再是写死的 '/app-icon.png' 字符串语义 (dev 下 BASE_URL='/', 打包语义另由构建产物 grep 验证)

运行: /home/qianqian/anaconda3/envs/tianjun/bin/python tests/uat/uat_20260714_navbar_logo_fallback.py
前置: 后端 8001 + 前端 6001 已启动
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6001"
OUT_DIR = Path(__file__).parent / "evidence_20260714_navbar_logo"
OUT_DIR.mkdir(exist_ok=True)

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}  {detail}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})
        page = ctx.new_page()

        print("[1] 打开首页 (监控页), 等导航栏渲染")
        page.goto(FRONTEND, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # 右上角 logo: header 最右侧的圆形 img (alt=logo)
        logo = page.locator("header img[alt='logo']").last
        logo.wait_for(state="visible", timeout=10000)
        src = logo.get_attribute("src")
        natural_width = logo.evaluate("el => el.naturalWidth")
        check("导航栏右上角 logo img 可见", True, f"src={src}")
        check("logo 图片真实加载成功 (naturalWidth>0)", natural_width > 0,
              f"naturalWidth={natural_width}")
        check("回退地址由 BASE_URL 拼出 (dev 下应为 /app-icon.png)",
              src is not None and src.endswith("app-icon.png"), f"src={src}")
        page.screenshot(path=str(OUT_DIR / "01_navbar_logo.png"))

        print("[2] 进设置页看 Logo 预览 + 恢复默认按钮不显示 (未上传过)")
        page.goto(f"{FRONTEND}/#/settings", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        preview = page.locator("img[alt='logo预览']")
        preview.wait_for(state="visible", timeout=10000)
        pv_width = preview.evaluate("el => el.naturalWidth")
        check("设置页 Logo 预览真实加载成功", pv_width > 0, f"naturalWidth={pv_width}")
        page.screenshot(path=str(OUT_DIR / "02_settings_logo_preview.png"))

        print("[3] localStorage 有自定义 Logo 时优先生效 (模拟已上传)")
        page.evaluate("""() => {
            const c = document.createElement('canvas');
            c.width = c.height = 32;
            const g = c.getContext('2d');
            g.fillStyle = '#ff0000'; g.fillRect(0, 0, 32, 32);
            const saved = localStorage.getItem('display_settings');
            const d = saved ? JSON.parse(saved) : {};
            d.logoDataUrl = c.toDataURL('image/png');
            localStorage.setItem('display_settings', JSON.stringify(d));
        }""")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        preview2 = page.locator("img[alt='logo预览']")
        preview2.wait_for(state="visible", timeout=10000)
        src2 = preview2.get_attribute("src")
        check("自定义 Logo (data URL) 优先于内置回退",
              src2 is not None and src2.startswith("data:image/png"),
              f"src 前缀={src2[:30] if src2 else None}")
        page.screenshot(path=str(OUT_DIR / "03_custom_logo_override.png"))

        print("[4] 清理注入的自定义 Logo, 恢复默认回退")
        page.evaluate("""() => {
            const saved = localStorage.getItem('display_settings');
            if (saved) {
                const d = JSON.parse(saved);
                d.logoDataUrl = '';
                localStorage.setItem('display_settings', JSON.stringify(d));
            }
        }""")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        logo4 = page.locator("img[alt='logo预览']")
        logo4.wait_for(state="visible", timeout=10000)
        nw4 = logo4.evaluate("el => el.naturalWidth")
        check("清空自定义后回退内置图标仍加载", nw4 > 0, f"naturalWidth={nw4}")
        page.screenshot(path=str(OUT_DIR / "04_reset_fallback.png"))

        browser.close()

    failed = [r for r in results if not r[1]]
    print(f"\n===== UAT 结果: {len(results) - len(failed)}/{len(results)} 通过 =====")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}: {name} {detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
