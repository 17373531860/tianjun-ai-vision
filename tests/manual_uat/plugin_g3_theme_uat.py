"""G3 Tier 1 主题钩子 — 客户视角验收

客户叙事 (ISSUES.md §G3):
  ACME 运维激活 Tier 1 白标主题包 + 重启应用, 期待:
  1. 浏览器 tab 标题变 "ACME AI Vision"
  2. CSS 变量 --tj-primary 等 4 个真生效 (computed style)
  3. document.querySelector("link[rel='icon']") 的 href 指向插件 favicon
  4. Logo <img> 出现, src 指向插件 logo
  5. 侧边菜单点开后, "报警设置" 项不出现
  6. 抓截图 + DOM 状态作为不可抵赖证据

前置:
  - 后端已经在 8001 端口, active 插件是 internal-demo (Tier 1 ACME 主题)
  - 前端 dev server 在 6001 端口

收尾:
  - 测试完不还原插件状态 (留给 cleanup 脚本)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO / "evidence" / "plugin_g3_2026-05-12"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def shot(page, name: str) -> Path:
    path = EVIDENCE_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"  📸 {path.name}")
    return path


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()

        page.set_default_timeout(15000)

        print("[1] 打开前端 http://localhost:6001")
        page.goto("http://localhost:6001/")
        # 等 main.js 的 dynamic import 把 themeStore 跑起来
        time.sleep(2.5)
        shot(page, "01_loaded_with_theme")

        print("[2] 抓 document.title")
        title = page.evaluate("() => document.title")
        print(f"    title = {title!r}")

        print("[3] 抓 CSS 变量 (computed)")
        css_vars = page.evaluate(
            """() => {
              const cs = getComputedStyle(document.documentElement);
              return {
                '--tj-primary': cs.getPropertyValue('--tj-primary').trim(),
                '--tj-primary-hover': cs.getPropertyValue('--tj-primary-hover').trim(),
                '--tj-bg-page': cs.getPropertyValue('--tj-bg-page').trim(),
                '--tj-text-main': cs.getPropertyValue('--tj-text-main').trim(),
              };
            }"""
        )
        print(f"    css_vars = {css_vars}")

        print("[4] 抓 favicon")
        favicon = page.evaluate(
            """() => {
              const link = document.querySelector("link[rel='icon']");
              return link ? link.href : null;
            }"""
        )
        print(f"    favicon = {favicon}")

        print("[5] 抓 Logo <img>")
        logo_info = page.evaluate(
            """() => {
              const imgs = Array.from(document.querySelectorAll('header img'));
              return imgs.map(i => ({ src: i.src, alt: i.alt, w: i.naturalWidth }));
            }"""
        )
        print(f"    logo imgs = {logo_info}")

        print("[6] 打开侧边菜单 + 截图")
        # Navbar 左侧第一个 button 是侧边菜单触发
        menu_btn = page.locator("header button").first
        menu_btn.click()
        time.sleep(0.6)
        shot(page, "02_sidebar_open")

        menu_items = page.evaluate(
            """() => Array.from(document.querySelectorAll('aside .nav-item')).map(a => ({
                href: a.getAttribute('href'),
                text: a.textContent.trim(),
              }))"""
        )
        print(f"    menu items = {menu_items}")

        print("[7] 抓 active manifest 作为参照")
        manifest_state = page.evaluate(
            """async () => {
              const resp = await fetch('http://localhost:8001/api/v1/plugins/active/manifest');
              return await resp.json();
            }"""
        )

        verdict = {
            "expected_title": "ACME AI Vision",
            "actual_title": title,
            "title_match": title == "ACME AI Vision",
            "css_vars_expected": {
                "--tj-primary": "#38bdf8",
                "--tj-primary-hover": "#0ea5e9",
                "--tj-bg-page": "#020617",
                "--tj-text-main": "#e2e8f0",
            },
            "css_vars_actual": css_vars,
            "css_vars_all_applied": all(
                css_vars[k].lower() == v.lower() for k, v in {
                    "--tj-primary": "#38bdf8",
                    "--tj-primary-hover": "#0ea5e9",
                    "--tj-bg-page": "#020617",
                    "--tj-text-main": "#e2e8f0",
                }.items()
            ),
            "favicon": favicon,
            "favicon_is_plugin": "plugins/active/assets" in (favicon or ""),
            "logo_imgs": logo_info,
            "logo_present": any("plugins/active/assets" in i["src"] for i in logo_info),
            "menu_items": menu_items,
            # 双重断言: hash 路由的 href 是 '#/alarm', 也确认 text 里没"报警"
            "alarm_hidden": all(
                not i["href"].endswith("/alarm") and "报警" not in i["text"]
                for i in menu_items
            ),
            "menu_count": len(menu_items),
            "manifest_customer": manifest_state.get("customer_code"),
        }
        (EVIDENCE_DIR / "verdict.json").write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[verdict] {EVIDENCE_DIR / 'verdict.json'}")
        for k in ["title_match", "css_vars_all_applied", "favicon_is_plugin", "logo_present", "alarm_hidden"]:
            mark = "✅" if verdict[k] else "❌"
            print(f"  {mark} {k}: {verdict[k]}")

        phase_a_passed = all([
            verdict["title_match"],
            verdict["css_vars_all_applied"],
            verdict["favicon_is_plugin"],
            verdict["logo_present"],
            verdict["alarm_hidden"],
        ])

        # ----------------------- Phase B: 停用回退 -----------------------
        print("\n[B-1] 通过 API 停用插件 (模拟客户从 Settings 关掉白标)")
        deactivate_resp = page.evaluate(
            """async () => {
              const r = await fetch('http://localhost:8001/api/v1/plugins/internal-demo/deactivate',
                {method: 'POST'});
              return {status: r.status, body: await r.text()};
            }"""
        )
        print(f"    deactivate -> {deactivate_resp['status']}")

        print("[B-2] 刷新页面 (客户场景 = 重启应用)")
        page.reload()
        time.sleep(2.5)
        shot(page, "03_after_deactivate_reload")

        unload_state = page.evaluate(
            """() => {
              const cs = getComputedStyle(document.documentElement);
              return {
                title: document.title,
                css_primary: cs.getPropertyValue('--tj-primary').trim(),
                css_bg: cs.getPropertyValue('--tj-bg-page').trim(),
                favicon: (document.querySelector("link[rel='icon']")||{}).href || null,
                logo_imgs: Array.from(document.querySelectorAll('header img')).map(i => i.src),
              };
            }"""
        )
        page.locator("header button").first.click()
        time.sleep(0.5)
        shot(page, "04_after_deactivate_sidebar")
        unload_menu = page.evaluate(
            """() => Array.from(document.querySelectorAll('aside .nav-item')).map(a => a.textContent.trim())"""
        )

        unload_verdict = {
            "title_reverted": unload_state["title"] != "ACME AI Vision",
            "title_actual": unload_state["title"],
            "css_primary_not_plugin": unload_state["css_primary"].lower() != "#38bdf8",
            "css_primary_actual": unload_state["css_primary"],
            "favicon_not_plugin": "plugins/active/assets" not in (unload_state["favicon"] or ""),
            "favicon_actual": unload_state["favicon"],
            "no_plugin_logo": not any("plugins/active/assets" in s for s in unload_state["logo_imgs"]),
            "logo_imgs": unload_state["logo_imgs"],
            "alarm_visible_again": any("报警" in m for m in unload_menu),
            "menu_items": unload_menu,
        }
        (EVIDENCE_DIR / "verdict_unload.json").write_text(
            json.dumps(unload_verdict, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n[Phase B verdict]")
        for k in ["title_reverted", "css_primary_not_plugin", "favicon_not_plugin", "no_plugin_logo", "alarm_visible_again"]:
            mark = "✅" if unload_verdict[k] else "❌"
            print(f"  {mark} {k}: {unload_verdict[k]}")

        phase_b_passed = all([
            unload_verdict["title_reverted"],
            unload_verdict["css_primary_not_plugin"],
            unload_verdict["favicon_not_plugin"],
            unload_verdict["no_plugin_logo"],
            unload_verdict["alarm_visible_again"],
        ])

        ctx.close()
        browser.close()
        passed = phase_a_passed and phase_b_passed
        print(f"\n===== 总结: Phase A {'✅' if phase_a_passed else '❌'}  Phase B {'✅' if phase_b_passed else '❌'} =====")
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
