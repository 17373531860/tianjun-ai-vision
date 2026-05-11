"""G2 ADR-0002 前端 ESM Loader — 客户视角验收 (Tier 2 UI 插件)

客户叙事:
  ACME IT 部门激活 Tier 2 "Factory Dashboard UI" 插件 + 重启应用, 期待:
    Phase A — 激活生效:
      1. 浏览器 tab 标题 = "Factory Dashboard" (manifest.frontend.theme.app_title)
      2. 侧边菜单**新增**「客户看板」一项
      3. 点击「客户看板」跳到 /factory-dashboard, 渲染 Dashboard 组件
         (h2: "客户看板", 卡片 "当班目标"=1200, "缺陷阈值"=8%)
      4. Pinia store `plugin-internal-demo-dashboard` 真挂上
    Phase B — 停用回退:
      5. 停用 Tier 2 + reload → 菜单项消失, /factory-dashboard 不存在 (跳 404 或主页), store 注销

前置:
  - 后端 8001 端口 active 插件 = Factory Dashboard UI (Tier 2)
  - 前端 dev server 6001 端口
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO / "evidence" / "plugin_g2_2026-05-12"
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

        console_msgs = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))

        page.set_default_timeout(15000)

        print("[1] 打开前端 http://localhost:6001")
        page.goto("http://localhost:6001/")
        time.sleep(3.0)  # 等 plugin loader 把 ESM fetch + dynamic import 全跑完
        shot(page, "01_loaded_tier2")

        title = page.evaluate("() => document.title")
        print(f"    title = {title!r}")

        print("[2] 打开侧边菜单")
        page.locator("header button").first.click()
        time.sleep(0.6)
        shot(page, "02_sidebar_with_plugin_menu")

        menu_items = page.evaluate(
            """() => Array.from(document.querySelectorAll('aside .nav-item')).map(a => ({
                href: a.getAttribute('href'),
                text: a.textContent.trim(),
                isPluginItem: a.classList.contains('plugin-nav-item'),
              }))"""
        )
        print(f"    menu items ({len(menu_items)}):")
        for m in menu_items:
            print(f"      - {m['text']}  href={m['href']}  plugin={m['isPluginItem']}")

        print("[3] 点击「客户看板」")
        page.locator("aside .nav-item.plugin-nav-item").first.click()
        time.sleep(1.5)
        shot(page, "03_dashboard_rendered")

        current_path = page.evaluate("() => location.hash || location.pathname")
        print(f"    current path = {current_path}")

        dashboard_dom = page.evaluate(
            """() => {
              const sec = document.querySelector('section.plugin-dashboard');
              if (!sec) return { rendered: false };
              const h2 = sec.querySelector('h2');
              const cards = Array.from(sec.querySelectorAll('article')).map(a => ({
                label: a.querySelector('strong')?.textContent?.trim() || '',
                value: a.querySelector('span')?.textContent?.trim() || '',
              }));
              return { rendered: true, h2_text: h2?.textContent || '', cards };
            }"""
        )
        print(f"    dashboard_dom = {dashboard_dom}")

        print("[4] 抓 Pinia store")
        pinia_state = page.evaluate(
            """() => {
              const ids = [];
              const node = document.querySelector('#app').__vue_app__;
              if (!node) return { error: 'no app' };
              const pinia = node.config.globalProperties.$pinia;
              if (!pinia) return { error: 'no pinia' };
              for (const [id, store] of pinia.state.value ? Object.entries(pinia.state.value) : []) {
                ids.push(id);
              }
              const pluginStore = pinia.state.value?.['plugin-internal-demo-dashboard'];
              return {
                allStores: ids,
                pluginStore: pluginStore ? { shiftTarget: pluginStore.shiftTarget, defectThreshold: pluginStore.defectThreshold } : null,
              };
            }"""
        )
        print(f"    pinia stores: {pinia_state}")

        phase_a_verdict = {
            "title_actual": title,
            "title_expected": "Factory Dashboard",
            "title_match": title == "Factory Dashboard",
            "plugin_menu_present": any(m["text"] == "客户看板" and m["isPluginItem"] for m in menu_items),
            "menu_items_count": len(menu_items),
            "dashboard_rendered": dashboard_dom.get("rendered", False),
            "dashboard_h2": dashboard_dom.get("h2_text"),
            "dashboard_h2_match": dashboard_dom.get("h2_text", "").strip() == "客户看板",
            "dashboard_cards": dashboard_dom.get("cards", []),
            "card_shifttarget_match": any(
                c["label"] == "当班目标" and c["value"] == "1200"
                for c in dashboard_dom.get("cards", [])
            ),
            "card_defect_match": any(
                c["label"] == "缺陷阈值" and c["value"] == "8%"
                for c in dashboard_dom.get("cards", [])
            ),
            "pinia_plugin_store_present": pinia_state.get("pluginStore") is not None,
            "pinia_plugin_store_state": pinia_state.get("pluginStore"),
        }
        (EVIDENCE_DIR / "verdict_phase_a.json").write_text(
            json.dumps(phase_a_verdict, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print("\n[Phase A verdict]")
        for k in ["title_match", "plugin_menu_present", "dashboard_rendered",
                  "dashboard_h2_match", "card_shifttarget_match", "card_defect_match",
                  "pinia_plugin_store_present"]:
            mark = "✅" if phase_a_verdict[k] else "❌"
            print(f"  {mark} {k}: {phase_a_verdict[k]}")
        phase_a_passed = all([
            phase_a_verdict["title_match"],
            phase_a_verdict["plugin_menu_present"],
            phase_a_verdict["dashboard_rendered"],
            phase_a_verdict["dashboard_h2_match"],
            phase_a_verdict["card_shifttarget_match"],
            phase_a_verdict["card_defect_match"],
            phase_a_verdict["pinia_plugin_store_present"],
        ])

        # ----------------------- Phase B 停用回退 -----------------------
        print("\n[B-1] 通过 API 停用插件")
        deactivate_resp = page.evaluate(
            """async () => {
              const r = await fetch('http://localhost:8001/api/v1/plugins/internal-demo/deactivate', {method:'POST'});
              return r.status;
            }"""
        )
        print(f"    deactivate -> {deactivate_resp}")

        print("[B-2] 刷新页面 (=客户重启应用)")
        page.goto("http://localhost:6001/")
        time.sleep(2.5)
        shot(page, "04_after_deactivate")

        page.locator("header button").first.click()
        time.sleep(0.6)
        shot(page, "05_after_deactivate_sidebar")

        unload_menu = page.evaluate(
            """() => Array.from(document.querySelectorAll('aside .nav-item')).map(a => ({
                text: a.textContent.trim(),
                isPluginItem: a.classList.contains('plugin-nav-item'),
              }))"""
        )

        unload_pinia = page.evaluate(
            """() => {
              const node = document.querySelector('#app').__vue_app__;
              const pinia = node.config.globalProperties.$pinia;
              return pinia.state.value?.['plugin-internal-demo-dashboard'] || null;
            }"""
        )

        phase_b_verdict = {
            "plugin_menu_gone": not any(m["isPluginItem"] for m in unload_menu),
            "menu_text_no_customer_panel": all("客户看板" not in m["text"] for m in unload_menu),
            "pinia_plugin_store_gone": unload_pinia is None,
            "menu_count": len(unload_menu),
            "title_actual": page.evaluate("() => document.title"),
        }
        (EVIDENCE_DIR / "verdict_phase_b.json").write_text(
            json.dumps(phase_b_verdict, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n[Phase B verdict]")
        for k in ["plugin_menu_gone", "menu_text_no_customer_panel", "pinia_plugin_store_gone"]:
            mark = "✅" if phase_b_verdict[k] else "❌"
            print(f"  {mark} {k}: {phase_b_verdict[k]}")

        phase_b_passed = all([
            phase_b_verdict["plugin_menu_gone"],
            phase_b_verdict["menu_text_no_customer_panel"],
            phase_b_verdict["pinia_plugin_store_gone"],
        ])

        (EVIDENCE_DIR / "console_log.txt").write_text("\n".join(console_msgs), encoding="utf-8")
        ctx.close()
        browser.close()

        passed = phase_a_passed and phase_b_passed
        print(f"\n===== 总结: Phase A {'✅' if phase_a_passed else '❌'}  Phase B {'✅' if phase_b_passed else '❌'} =====")
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
