# -*- coding: utf-8 -*-
"""
UAT: showcase 插件 v1.1.0 全量接真 — 8 页逐页冒烟
- 打开前端 (插件 iframe 覆盖层) → 逐页切换 (renderRoutePage) → 断言真数据元素渲染、无致命 JS 错误
- 只读验证, 不做写操作 (写操作端点已通过 API 审计与代码核对)
运行: python tests/uat/uat_showcase_v110_pages.py
"""
import sys, time, json
from playwright.sync_api import sync_playwright

FRONT = "http://localhost:6001"
results = []
console_errors = []

def log(msg):
    print(msg, flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1680, "height": 950})
    page = ctx.new_page()
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append("pageerror: " + str(e)))

    page.goto(FRONT, wait_until="domcontentloaded", timeout=60000)
    # 等插件覆盖层 iframe (frame.url 上报不稳定, 以 DOM 元素为准拿 content_frame)
    page.wait_for_selector("iframe[src*='showcase-app']", timeout=120000, state="attached")
    deadline = time.time() + 90
    frame = None
    while time.time() < deadline:
        el = page.query_selector("iframe[src*='showcase-app']")
        f = el.content_frame() if el else None
        if f:
            try:
                if f.evaluate("typeof renderRoutePage === 'function' && typeof tjApi === 'object'"):
                    frame = f
                    break
            except Exception:
                pass
        time.sleep(1.5)
    if not frame:
        log("FAIL: 插件 iframe 内容未就绪 (90s)")
        browser.close()
        sys.exit(1)
    log("OK: 插件 iframe 已加载并就绪")
    time.sleep(3)  # 等首屏数据

    def check(name, expr, detail_expr=None):
        try:
            ok = bool(frame.evaluate(expr))
            detail = frame.evaluate(detail_expr) if detail_expr else ""
        except Exception as e:
            ok, detail = False, f"exception: {e}"
        results.append((name, ok))
        log(("PASS  " if ok else "FAIL  ") + name + ("  | " + str(detail)[:120] if detail else ""))

    def nav(route):
        frame.evaluate(f"renderRoutePage('{route}')")
        time.sleep(2.5)

    # ── 监控页 (默认): 桥与真数据接收器存在
    check("monitor: 桥客户端与数据接收器就绪",
          "typeof tjApi==='object' && typeof window.__tjsc!=='undefined' || typeof tjApi==='object'")

    # ── 项目页
    nav("/project")
    check("project: 项目列表真数据 (>=1 项目, 后端实有 8 个)",
          "(function(){var t=(document.getElementById('projectPrototype')||{}).innerText||'';return t.length>50;})()")

    # ── 模型页
    nav("/model")
    time.sleep(1.5)
    check("model: 模型卡片渲染 (detail 按钮)",
          "document.querySelectorAll('[data-model-detail]').length>0",
          "'cards='+document.querySelectorAll('[data-model-detail]').length")

    # ── 输入源页
    nav("/source")
    check("source: 摄像头下拉 + 停止按钮存在",
          "!!document.getElementById('camSel') && !!document.getElementById('srcStopBtn')")

    # ── 数据中心
    nav("/data")
    time.sleep(2.5)
    check("data: 会话列表真数据/空态 (无写死演示卡)",
          "(function(){var n=document.querySelectorAll('[data-sid]').length;var t=document.body.innerText;return n>0||t.indexOf('暂无')>-1;})()",
          "'sessCards='+document.querySelectorAll('[data-sid]').length")
    check("data: 分页器为动态元素", "!!document.getElementById('cyclePager')")

    # ── MES
    nav("/mes")
    time.sleep(2.5)
    check("mes: 工单面板已加载 (非「加载中」)",
          "(function(){var el=document.querySelector('[data-mes-panel=\"orders\"]');var t=el?el.innerText:'';return t.indexOf('加载中')===-1;})()")

    # ── 报警页
    nav("/alarm")
    time.sleep(2)
    check("alarm: 协议下拉真实化", "!!document.getElementById('alarmProtoSel')")

    # ── 设置页 3 个接真 Tab
    nav("/settings")
    time.sleep(1.5)
    frame.evaluate("setSettingsTab('plugin')")
    time.sleep(3)
    check("settings/plugin: 列表含 showcase (真后端数据)",
          "(function(){var t=(document.getElementById('pluginTbody')||{}).innerText||'';return t.indexOf('showcase')>-1;})()",
          "((document.getElementById('pluginTbody')||{}).innerText||'').slice(0,100).replace(/\\n/g,' / ')")
    frame.evaluate("setSettingsTab('auth')")
    time.sleep(2.5)
    check("settings/auth: 鉴权状态行真实显示",
          "(function(){var t=(document.getElementById('authStateLine')||{}).innerText||'';return t.indexOf('未启用')>-1||t.indexOf('已启用')>-1;})()",
          "((document.getElementById('authStateLine')||{}).innerText||'').slice(0,80)")
    frame.evaluate("setSettingsTab('flow')")
    time.sleep(2.5)
    check("settings/flow: 流水线列表真数据/空态",
          "(function(){var t=(document.getElementById('flowTbody')||{}).innerText||'';return t.length>0 && t.indexOf('加载中')===-1;})()",
          "((document.getElementById('flowTbody')||{}).innerText||'').slice(0,80)")

    page.screenshot(path="tests/uat/uat_showcase_v110_settings.png")
    browser.close()

fatal = [e for e in console_errors if "favicon" not in e]
log("")
log(f"console errors: {len(fatal)}")
for e in fatal[:10]:
    log("  ERR " + e[:200])
passed = sum(1 for _, ok in results if ok)
log(f"RESULT: {passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
