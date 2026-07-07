# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 展会插件 — 导航抽屉对齐主程序 + 虚拟扫码枪显隐门控。

现场叙事: 操作员打开监控页 → 左侧导航默认收起 (和主程序一样), 主区独占整宽;
点顶栏 ☰ 导航滑出, 点菜单项跳页后自动收回, 点遮罩也能收回; 底部虚拟扫码枪
入口只在存在"启用中"的包装结算配置时出现 —— 当前环境唯一的包装结算流是
停用状态, 所以不应看到它 (对齐主程序 Monitor 零差异门控)。

前置: main 栈 — 后端 8001 + 前端 6001 已启动; plugins/showcase 已同步本轮文件。
"""
from __future__ import annotations

import sys
import time

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"

run = UatRun("showcase_nav_drawer")


def _plugin_frame(page, timeout_s=40):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            h = page.query_selector("iframe[src*='showcase-app.html']")
            fr = h.content_frame() if h else None
            if fr and fr.evaluate("!!document.querySelector('.nav .item[data-route]')"):
                return fr
        except Exception:
            pass
        time.sleep(1)
    return None


try:
    flows = requests.get(f"{API}/packaging-flows", timeout=5).json()
    items = flows.get("items") if isinstance(flows, dict) else flows
    any_enabled = any(f.get("enabled") for f in (items or []))
    run.step("00 前置: 当前无启用中的包装结算流 (扫码枪应隐藏)",
             not any_enabled, f"flows={[(f.get('name'), f.get('enabled')) for f in (items or [])]}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(FRONT, wait_until="domcontentloaded")
        fr = _plugin_frame(page)
        run.step("01 插件整页 iframe 已加载", fr is not None)
        if fr is None:
            raise RuntimeError("插件 iframe 未加载")
        time.sleep(5)

        # ---- A. 导航默认收起, 主区整宽 ----
        st = fr.evaluate("""() => {
          const nav=document.querySelector('.nav');
          const r=nav.getBoundingClientRect();
          const main=document.querySelector('.main').getBoundingClientRect();
          return {navOpen:nav.classList.contains('open'), navRight:r.right,
                  mainLeft:Math.round(main.left), hasToggle:!!document.getElementById('navToggle')};
        }""")
        run.step("A1 导航默认收起 (滑出屏外) 且有 ☰ 按钮",
                 not st["navOpen"] and st["navRight"] <= 0 and st["hasToggle"], str(st))
        run.step("A2 主区独占整宽 (左缘无导航占位)", st["mainLeft"] < 40,
                 f"mainLeft={st['mainLeft']}")
        run.shot(page, "01_nav_collapsed")

        # ---- B. ☰ 呼出 → 点菜单项自动收回 ----
        fr.evaluate("document.getElementById('navToggle').click()")
        time.sleep(1)
        opened = fr.evaluate(
            "document.querySelector('.nav').classList.contains('open')"
            " && document.getElementById('navMask').classList.contains('show')")
        run.step("B1 点 ☰ 导航滑出 + 遮罩出现", bool(opened))
        run.shot(page, "02_nav_opened")
        fr.evaluate("document.querySelector(\".nav .item[data-route='/data']\").click()")
        time.sleep(2)
        after = fr.evaluate("""() => ({
          open: document.querySelector('.nav').classList.contains('open'),
          page: document.getElementById('currentPageName').textContent,
        })""")
        run.step("B2 点菜单项跳页后导航自动收回",
                 not after["open"] and after["page"] == "数据中心", str(after))
        # 遮罩关闭路径
        fr.evaluate("document.getElementById('navToggle').click()")
        time.sleep(1)
        fr.evaluate("document.getElementById('navMask').click()")
        time.sleep(1)
        mask_closed = fr.evaluate(
            "!document.querySelector('.nav').classList.contains('open')")
        run.step("B3 点遮罩收回导航", bool(mask_closed))

        # ---- C. 虚拟扫码枪门控: 无启用包装流 → 不可见 ----
        fr.evaluate("document.getElementById('navToggle').click()")
        time.sleep(1)
        fr.evaluate("document.querySelector(\".nav .item[data-route='/monitor']\").click()")
        time.sleep(3)
        pill = fr.evaluate("""() => {
          const el=document.getElementById('tjScanPill');
          return {shown:el.classList.contains('show'),
                  visible:getComputedStyle(el).display!=='none'};
        }""")
        run.step("C1 虚拟扫码枪隐藏 (无启用中的包装结算配置)",
                 not pill["shown"] and not pill["visible"], str(pill))
        run.shot(page, "03_monitor_no_scangun")

        real = filter_console_errors(cerrs)
        run.step("D1 控制台无前端逻辑报错", not real, f"真报错={real[:3]}")

        ctx.close()
        browser.close()
except Exception:  # noqa: BLE001
    import traceback
    _tb = traceback.format_exc()
    print(_tb, flush=True)
    run.step("EXCEPTION 主流程异常中断", False, _tb.strip().splitlines()[-1][:200])

raise SystemExit(run.finish())
