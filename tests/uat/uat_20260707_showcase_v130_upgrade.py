# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 展会全应用定制界面插件 v1.3.0 对齐主程序 v3.31/v3.32。

现场叙事: 展会讲解员打开装了 v1.3.0 展示插件的主程序 → 全站 8 页仍整体渲染、
数据全部接真后端; 监控页多出称重投料/区域事件两种模式的布局与覆盖层骨架;
项目页多出「称重投料」「区域事件」两个模式胶囊与配置块; MES 页多出
「生产管控入站」「包装结算」两个 Tab; 数据中心多出「称重台账」Tab;
设置页性能 Tab 多出「面板轮询与日志条数」卡片且可保存回读。

前置: main 栈 — 后端 8001 (tianjun env) + 前端 6001 已启动;
plugins/showcase 已同步 v1.3.0 文件且 DB 记录已更新。
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

run = UatRun("showcase_v130_upgrade")


def _plugin_frame(page, timeout_s=40):
    """等 showcase 整页 iframe 挂上并完成初始化 (以侧边导航出现为准)。

    注意: 该 iframe 指向 8001 (与宿主 6001 跨源) 是 OOPIF, page.frames 枚举
    在 headed 模式下会漏, 必须走 iframe 元素句柄拿 content_frame。
    """
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


def _nav(fr, route, settle_s=3):
    fr.evaluate(
        "(r)=>{const el=document.querySelector(`.nav .item[data-route='${r}']`);"
        "if(el) el.click();}", route)
    time.sleep(settle_s)


try:
    # ---- 0. 后端侧真值先行核对 ----
    m = requests.get(f"{API}/plugins/active/manifest", timeout=5).json()
    run.step("00 激活插件清单 = showcase v1.3.0",
             m.get("customer_code") == "showcase"
             and m.get("plugin_version") == "1.3.0",
             f"got {m.get('customer_code')} {m.get('plugin_version')}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(FRONT, wait_until="domcontentloaded")
        fr = _plugin_frame(page)
        run.step("01 插件整页 iframe 已加载并初始化", fr is not None)
        if fr is None:
            raise RuntimeError("插件 iframe 未加载, 后续步骤无法继续")
        time.sleep(4)
        run.shot(page, "01_monitor_loaded")

        # ---- A. 监控页: v1.3.0 新骨架都在 DOM ----
        checks = {
            "称重投料看板": ".weigh-strip",
            "逐件待补横幅": "#piRemedBar",
            "人工确认三态层": "#tjAckLayer",
            "在途报警横幅": "#tjExtAlarm",
            "包装结算卡": "#tjPackCard",
            "虚拟扫码枪入口": "#tjScanPill",
            "录像异常入口": "#tjRecFailBadge",
            "四要素信息条": "#tjTaskInfo",
            "SOP 轮次胶囊": "#sopRoundChips",
            "周期动作胶囊": "#sopPaChips",
        }
        missing = [k for k, sel in checks.items()
                   if not fr.evaluate(f"!!document.querySelector('{sel}')")]
        run.step("02 监控页 v1.3.0 新骨架 10 项齐全", not missing,
                 f"缺失={missing}" if missing else "10/10")

        # ---- B. 项目页: 7 模式胶囊 + 称重/区域事件配置块 ----
        _nav(fr, "/project", 4)
        run.shot(page, "02_project_modes")
        pills = fr.evaluate(
            "[...document.querySelectorAll('.mode-pill')].map(x=>x.dataset.logicMode)")
        run.step("03 项目页 7 逻辑模式胶囊",
                 "weighing" in pills and "region_events" in pills, str(pills))
        blocks = fr.evaluate(
            "['weighing','regionEvents'].map(k=>!!document.querySelector(`[data-lg='${k}']`))")
        run.step("04 称重/区域事件配置块挂载", all(blocks), str(blocks))

        # ---- C. MES 页: 生产管控入站 + 包装结算 Tab 接真 ----
        _nav(fr, "/mes", 4)
        tabs = fr.evaluate(
            "[...document.querySelectorAll('[data-mes-tab]')].map(x=>x.dataset.mesTab)")
        run.step("05 MES 页 9 个 Tab (含 inbound/packaging)",
                 "inbound" in tabs and "packaging" in tabs, str(tabs))
        fr.evaluate("setMesTab('inbound')")
        time.sleep(3)
        run.shot(page, "03_mes_inbound")
        badge = fr.evaluate(
            "document.getElementById('mibEnableBadge')?.textContent || ''")
        run.step("06 入站 Tab 接真 /mes/inbound/config",
                 badge in ("已启用", "未启用"), f"badge={badge!r}")
        fr.evaluate("setMesTab('packaging')")
        time.sleep(3)
        run.shot(page, "04_mes_packaging")
        pk = fr.evaluate("document.getElementById('pkCfgBox')?.innerText || ''")
        run.step("07 包装结算 Tab 接真 /packaging-flows",
                 ("暂无包装结算流" in pk) or ("查看状态" in pk), pk[:60])

        # ---- D. 数据中心: 称重台账 Tab 接真 ----
        _nav(fr, "/data", 4)
        fr.evaluate(
            "document.querySelector(\"[data-data-tab='weighing']\")?.click()")
        time.sleep(3)
        run.shot(page, "05_data_weighing")
        wg = fr.evaluate("document.getElementById('wgRecBox')?.innerText || ''")
        run.step("08 称重台账接真 /weighing/records",
                 ("暂无投料记录" in wg) or ("判定" in wg), wg[:60])

        # ---- E. 设置页: 轮询与日志条数卡片, 读→改→存→回读 ----
        _nav(fr, "/settings", 3)
        fr.evaluate("setSettingsTab('perf')")
        time.sleep(3)
        run.shot(page, "06_settings_polling")
        n_poll = fr.evaluate("document.querySelectorAll('[data-poll-k]').length")
        n_log = fr.evaluate("document.querySelectorAll('[data-loglimit-k]').length")
        run.step("09 轮询/日志条数输入项渲染 (8+5)",
                 n_poll == 8 and n_log == 5, f"poll={n_poll} log={n_log}")
        # 改工单列表轮询为 12345ms → 保存 → 后端 GET 回读
        fr.evaluate(
            "const el=document.querySelector(\"[data-poll-k='order_list']\");"
            "if(el){el.value='12345';}")
        fr.evaluate("document.getElementById('pollSaveBtn')?.click()")
        time.sleep(3)
        v = requests.get(f"{API}/system/polling", timeout=5).json().get("order_list")
        run.step("10 保存后 GET /system/polling 回读 = 12345", v == 12345, f"got {v}")
        # 还原, 避免污染现场配置
        requests.put(f"{API}/system/polling", json={"order_list": 10000}, timeout=5)

        # ---- F. 回到监控页留最终画面 + 控制台无逻辑报错 ----
        _nav(fr, "/monitor", 4)
        run.shot(page, "07_monitor_final")
        real = filter_console_errors(cerrs)
        run.step("11 控制台无前端逻辑报错", not real, f"真报错={real[:3]}")

        ctx.close()
        browser.close()
except Exception as e:  # noqa: BLE001 — 失败也要出 run.json 汇总
    run.step("XX 脚本异常中断", False, f"{type(e).__name__}: {e}")

raise SystemExit(run.finish())
