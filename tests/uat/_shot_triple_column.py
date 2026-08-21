"""三工位横向三列布局 — 真实 Chromium 截图 + 尺寸测量 (v3.52)。

用法 (后端 8001 + 前端 6001 已起, 工位数=3):
    python tests/uat/_shot_triple_column.py

产物落 tests/uat/artifacts/triple-column-3col/:
    triple-1920x1080.png   全页截图 (1920x1080)
    triple-1920x904.png    全页截图 (1920x904, 工业屏)
    measure.json           三路视频 bbox / 比例 / 页面横向溢出
"""
from __future__ import annotations

import json
import os
from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:6001")
OUT = os.path.join(os.path.dirname(__file__), "artifacts", "triple-column-3col")
os.makedirs(OUT, exist_ok=True)


def _measure(page):
    cards = []
    for ch in range(3):
        box = page.locator(f"[data-testid='channel-card-{ch}']").bounding_box()
        col = page.locator(f"[data-testid='triple-col-{ch}']").bounding_box()
        cards.append({
            "ch": ch,
            "col_x": round(col["x"], 1),
            "video_w": round(box["width"], 1),
            "video_h": round(box["height"], 1),
            "ratio": round(box["width"] / box["height"], 4),
        })
    overflow_x = page.evaluate(
        "() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth"
    )
    tracks = page.locator("[data-testid='triple-grid']").evaluate(
        "el => getComputedStyle(el).gridTemplateColumns"
    )
    return {"cards": cards, "overflow_x": overflow_x, "grid_template_columns": tracks}


def main():
    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for w, h, tag in ((1920, 1080, "1920x1080"), (1920, 904, "1920x904")):
            # —— SOP 流程卡片显示 (默认) ——
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
            page.wait_for_selector("[data-testid='triple-grid']", timeout=15000)
            page.wait_for_timeout(1500)
            page.screenshot(path=os.path.join(OUT, f"triple-{tag}-sop-on.png"), full_page=True)
            m_on = _measure(page)
            m_on["sop_rows"] = page.locator("[data-testid^='triple-sop-']").count()

            # —— 关掉 SOP 流程卡片显示开关 (stepStrip=false) 后刷新 ——
            page.evaluate(
                "window.localStorage.setItem('display_settings',"
                " JSON.stringify({ monitor: { stepStrip: false } }))"
            )
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            page.wait_for_selector("[data-testid='triple-grid']", timeout=15000)
            page.wait_for_timeout(1500)
            page.screenshot(path=os.path.join(OUT, f"triple-{tag}-sop-off.png"), full_page=True)
            m_off = _measure(page)
            m_off["sop_rows"] = page.locator("[data-testid^='triple-sop-']").count()

            result[tag] = {"sop_on": m_on, "sop_off": m_off}
            print(f"[shot] triple-{tag}-sop-on/off.png")
            ctx.close()
        browser.close()
    with open(os.path.join(OUT, "measure.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
