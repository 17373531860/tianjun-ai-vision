# -*- coding: utf-8 -*-
"""一拖多第二笔：局域网工位屏配置卡 + 吉田手部裁切副屏 可见浏览器 UAT。

现场叙事：
    工程师在工作站打开「工位与输入源 → 局域网工位屏」，照着表格把每个工位的地址
    抄进对应一体机的浏览器。吉田那一路还要再挂一块只读副屏，于是他在该工位把
    「手部裁切副屏」开关打开，复制副屏地址；别的工位开关保持关着，一帧手部识别
    都不该算。副屏只显示手部框 + 指关节的裁切画面，不显示整幅工位图。

前置：后端 8001（RUNTIME_MODE=test）+ 已 npm run build（后端托管 dist）
    python tests/uat/uat_20260911_lan_station_hands_aux.py
"""
from __future__ import annotations

import os
import sys

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import UatRun, filter_console_errors, launch_browser  # noqa: E402

STATION = os.environ.get("UAT_STATION_ORIGIN", "http://127.0.0.1:8001")
API = STATION

SCENARIO = {
    "name": "hands-aux-uat",
    "fps": 25,
    "timeline": [
        {"from": 0, "to": 100000, "detections": [
            {"label": "工件", "confidence": 0.9, "bbox": [0.3, 0.3, 0.4, 0.4]},
        ]},
    ],
}


def api(method: str, path: str, payload: dict | None = None):
    return requests.request(method, f"{API}{path}", json=payload, timeout=20)


def hands_aux() -> dict:
    return api("GET", "/api/v1/workstations/hands-aux").json().get("channels", {})


def main() -> int:
    run = UatRun("lan_station_hands_aux")
    original = hands_aux()
    browser = ctx = None

    try:
        api("POST", "/api/v1/test/synthetic/start",
            {"scenario_json": SCENARIO, "channel": 0, "with_project": True})

        run.step("初始状态：所有工位手部副屏默认关（其它客户零差异）",
                 all(v is False for v in original.values()),
                 f"hands-aux={original}")

        with sync_playwright() as playwright:
            browser, ctx, page, console_errors = launch_browser(
                playwright, headless=False, slow_mo=120,
                viewport=(1600, 1000), record_video_dir=run.video_dir)

            page.goto(f"{STATION}/#/source", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            page.locator('.el-tabs__item:has-text("局域网工位屏")').click()
            page.wait_for_selector("[data-testid=lan-station-panel]", timeout=15000)
            page.wait_for_timeout(1500)
            run.shot(page, "01_lan_station_panel")

            # --- 工位地址表：直接可抄 ---
            url0 = page.locator("[data-testid=lan-station-url-0]").inner_text()
            run.step("工位 0 操作屏地址按契约生成且是可抄的绝对地址",
                     url0.startswith(STATION)
                     and "channel=0" in url0 and "kiosk=1" in url0 and "readonly=0" in url0,
                     url0)
            url1 = page.locator("[data-testid=lan-station-url-1]").inner_text()
            run.step("工位 1 地址指向 channel=1（逐工位各一条）",
                     "channel=1" in url1, url1)

            # --- 副屏开关默认关、地址不给 ---
            aux_switch = page.locator("[data-testid=lan-station-hands-aux-0] input")
            run.step("手部裁切副屏开关默认关",
                     aux_switch.is_checked() is False)
            run.step("未启用时不给副屏地址（避免误配）",
                     page.locator("[data-testid=lan-station-hands-url-0]").count() == 0)

            # --- 打开开关 → 落库 + 运行态 ---
            page.locator("[data-testid=lan-station-hands-aux-0]").click()
            page.wait_for_timeout(2000)
            run.shot(page, "02_hands_aux_enabled")
            after = hands_aux()
            run.step("UI 打开开关 → 后端工位 0 已启用（UI→后端落库）",
                     after.get("0") is True, f"hands-aux={after}")
            run.step("别的工位不受牵连（MediaPipe 只开需要的工位）",
                     after.get("1") is False, f"hands-aux={after}")

            hands_url = page.locator("[data-testid=lan-station-hands-url-0]").inner_text()
            run.step("启用后给出副屏地址（只读 + 只看手部裁切）",
                     "video_only=1" in hands_url and "hands_crop=1" in hands_url
                     and "readonly=1" in hands_url and "channel=0" in hands_url,
                     hands_url)

            # --- 副屏页面本身：极瘦、只拉 snapshot ---
            aux_ctx = browser.new_context(viewport={"width": 1024, "height": 600})
            aux_page = aux_ctx.new_page()
            aux_reqs: list[str] = []
            aux_page.on("request", lambda r: aux_reqs.append(r.url)
                        if ("/video_feed" in r.url or "/snapshot" in r.url) else None)
            aux_page.goto(hands_url, wait_until="domcontentloaded")
            aux_page.wait_for_selector("[data-testid=hands-crop-view]", timeout=15000)
            aux_page.wait_for_timeout(3000)
            run.shot(aux_page, "03_hands_aux_secondary_screen")

            run.step("副屏渲染手部裁切视图（不是整幅工位图）",
                     aux_page.locator("[data-testid=hands-crop-image]").count() == 1)
            snaps = [u for u in aux_reqs if "/snapshot" in u]
            feeds = [u for u in aux_reqs if "/video_feed" in u]
            run.step("副屏只拉 /snapshot?view=hands，绝不拉 /video_feed",
                     snaps and not feeds
                     and all("view=hands" in u for u in snaps),
                     f"snapshot={len(snaps)} video_feed={len(feeds)} 样例={snaps[:1]}")
            run.step("副屏轮询在 10-15fps 量级（不是全帧率直播）",
                     3 <= len(snaps) <= 60, f"3 秒内 {len(snaps)} 帧")

            img = api("GET", "/snapshot?channel=0&view=hands")
            run.step("后端 hands 快照返回 JPEG（先裁切再编码）",
                     img.status_code == 200
                     and img.headers.get("content-type") == "image/jpeg"
                     and img.content.startswith(b"\xff\xd8"),
                     f"status={img.status_code} bytes={len(img.content)}")

            # --- 未启用的工位取 hands 快照 → 占位图, 不顺手拉起 MediaPipe ---
            img1 = api("GET", "/snapshot?channel=1&view=hands")
            run.step("未启用工位取 hands 快照回占位图（一个 GET 不该打开每帧算力）",
                     img1.status_code == 200 and img1.content.startswith(b"\xff\xd8"),
                     f"status={img1.status_code} bytes={len(img1.content)}")

            # --- 关回去 ---
            aux_ctx.close()
            page.bring_to_front()
            page.locator("[data-testid=lan-station-hands-aux-0]").click()
            page.wait_for_timeout(2000)
            run.step("关回去后落库同步为关",
                     hands_aux().get("0") is False, f"hands-aux={hands_aux()}")
            run.shot(page, "04_hands_aux_disabled_again")

            real = filter_console_errors(console_errors)
            run.step("配置页无前端逻辑报错", not real, f"真报错={real[:3]}")

    finally:
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        for ch, enabled in original.items():
            api("PUT", "/api/v1/workstations/hands-aux",
                {"channel_id": int(ch), "enabled": bool(enabled)})
        api("POST", "/api/v1/source/detection/stop?channel=0")
        api("POST", "/api/v1/test/synthetic/stop?channel=0")

    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
