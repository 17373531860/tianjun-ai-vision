# -*- coding: utf-8 -*-
"""
UAT 阶段2: ROI 画布编辑器 — 打开/打点/保存/落库/清除
运行: python tests/uat/uat_showcase_p2_roi.py
"""
import sys, time, json
import urllib.request
from playwright.sync_api import sync_playwright

FRONT = "http://localhost:6001"
API = "http://localhost:8001/api/v1"
results = []
console_errors = []


def log(m):
    print(m, flush=True)


def api_get(path):
    with urllib.request.urlopen(API + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1680, "height": 950})
    page = ctx.new_page()
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append("pageerror: " + str(e)))
    page.goto(FRONT, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("iframe[src*='showcase-app']", timeout=120000, state="attached")
    frame = None
    deadline = time.time() + 90
    while time.time() < deadline:
        el = page.query_selector("iframe[src*='showcase-app']")
        f = el.content_frame() if el else None
        if f:
            try:
                if f.evaluate("typeof renderRoutePage === 'function' && typeof tjOpenRoiEditor === 'function'"):
                    frame = f
                    break
            except Exception:
                pass
        time.sleep(1.5)
    if not frame:
        log("FAIL: 插件 iframe 未就绪或 ROI 编辑器缺失")
        sys.exit(1)
    log("OK: iframe + ROI 编辑器就绪")
    time.sleep(2)

    def check(name, expr):
        try:
            ok = bool(frame.evaluate(expr))
        except Exception as e:
            ok = False
            log("  exception: " + str(e)[:200])
        results.append((name, ok))
        log(("PASS  " if ok else "FAIL  ") + name)

    frame.evaluate("renderRoutePage('/project')")
    time.sleep(3)
    frame.evaluate("document.querySelector('[data-project-tab=\"steps\"]').click()")
    time.sleep(0.5)

    check("步骤表有 ROI 设置按钮", "document.querySelectorAll('[data-steps-table=\"normal\"] [data-roi-step]').length > 0")
    # 打开第一个步骤的 ROI 编辑器
    frame.evaluate("document.querySelector('[data-steps-table=\"normal\"] [data-roi-step]').click()")
    time.sleep(1.5)
    check("ROI 对话框打开", "document.getElementById('roiDialog').open === true")
    # 程序化打 4 个点 (模拟点击 SVG 不同位置)
    frame.evaluate("""(() => {
      const svg = document.getElementById('roiSvg');
      const r = svg.getBoundingClientRect();
      [[0.2,0.2],[0.8,0.25],[0.72,0.8],[0.28,0.75]].forEach(([nx,ny]) => {
        svg.dispatchEvent(new MouseEvent('click', {bubbles:true, clientX:r.left + r.width*nx, clientY:r.top + r.height*ny}));
      });
    })()""")
    check("打点 4 个顶点", "window.__tjRoiPts.length === 4")
    frame.evaluate("document.getElementById('roiUndoBtn').click()")
    check("撤销后 3 个顶点", "window.__tjRoiPts.length === 3")
    frame.evaluate("document.getElementById('roiSaveBtn').click()")
    time.sleep(0.5)
    check("保存后写入步骤编辑模型", "Array.isArray(window.__tjPE.steps_config[0].roi) && window.__tjPE.steps_config[0].roi.length === 3")
    check("ROI 顶点为归一化坐标", "window.__tjPE.steps_config[0].roi.every(p => p[0]>=0 && p[0]<=1 && p[1]>=0 && p[1]<=1)")
    check("步骤表 ROI 列显示顶点数", "document.querySelector('[data-steps-table=\"normal\"] [data-roi-step-clear]') !== null")

    # 保存配置 → 后端验证
    pid = frame.evaluate("window.__tjSelectedPid")
    frame.evaluate("document.getElementById('projSaveBtn').click()")
    time.sleep(3)
    after = api_get(f"/projects/{pid}")
    roi = (after.get("steps_config") or [{}])[0].get("roi")
    ok_db = isinstance(roi, list) and len(roi) == 3
    results.append(("ROI 落库后端", ok_db))
    log(("PASS  " if ok_db else "FAIL  ") + f"ROI 落库后端 roi={str(roi)[:80]}")

    # 清除还原
    frame.evaluate("document.querySelector('[data-steps-table=\"normal\"] [data-roi-step-clear]').click()")
    time.sleep(0.5)
    frame.evaluate("document.getElementById('projSaveBtn').click()")
    time.sleep(3)
    restored = api_get(f"/projects/{pid}")
    roi2 = (restored.get("steps_config") or [{}])[0].get("roi")
    ok_clr = roi2 is None
    results.append(("ROI 清除还原落库", ok_clr))
    log(("PASS  " if ok_clr else "FAIL  ") + f"ROI 清除还原落库 roi={roi2}")

    # 跟踪清点 ROI 入口存在
    frame.evaluate("document.querySelector('[data-project-tab=\"logic\"]').click(); setLogicMode('tracking')")
    time.sleep(0.5)
    check("跟踪清点区有设置区域按钮", "document.querySelector('[data-lga=\"trkRoiDraw\"]') !== null")
    orig_mode = frame.evaluate("(window.__tjProjects.find(p=>p.id===window.__tjSelectedPid)||{}).logic_mode || 'sequential'")
    frame.evaluate(f"setLogicMode('{orig_mode}')")

    page.screenshot(path="tests/uat/p2_roi.png")
    browser.close()

fatal = [e for e in console_errors if "favicon" not in e]
log("\nconsole errors: " + str(len(fatal)))
for e in fatal[:8]:
    log("  " + e[:200])
n_pass = sum(1 for _, ok in results if ok)
log(f"\n== {n_pass}/{len(results)} PASS ==")
sys.exit(0 if n_pass == len(results) else 1)
