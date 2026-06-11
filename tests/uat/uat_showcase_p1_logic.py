# -*- coding: utf-8 -*-
"""
UAT 阶段1a: showcase 插件逻辑 Tab 接真 — 渲染 + 落库回读
运行: python tests/uat/uat_showcase_p1_logic.py
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
        log("FAIL: 插件 iframe 未就绪")
        sys.exit(1)
    log("OK: 插件 iframe 就绪")
    time.sleep(3)

    def check(name, expr):
        try:
            ok = bool(frame.evaluate(expr))
        except Exception as e:
            ok = False
            log("  exception: " + str(e)[:160])
        results.append((name, ok))
        log(("PASS  " if ok else "FAIL  ") + name)

    frame.evaluate("renderRoutePage('/project')")
    time.sleep(3)

    check("编辑模型已建立 (__tjPE)", "window.__tjPE && window.__tjPE.id != null")
    frame.evaluate("document.querySelector('[data-project-tab=\"logic\"]').click()")
    time.sleep(1)

    # 阶段3a 增加了 extras (附加模型) 区块, 渲染体数量从 12 起只增不减
    check("12+ 个逻辑区块均有渲染体", "document.querySelectorAll('[data-lg] [data-lg-body]').length >= 12")
    check("结算方式区块有 4 个单选", "document.querySelectorAll('[data-lg=\"settle\"] input[type=radio]').length === 4")
    check("防重复结算区块有开关+数值", "!!document.querySelector('[data-lg=\"dedup\"] [data-b=\"pc.settle_dedup\"]') && !!document.querySelector('[data-lg=\"dedup\"] [data-b=\"pc.settle_dedup_window_seconds\"]')")
    check("误判过滤区块有真标签下拉", "document.querySelectorAll('[data-lg=\"filter\"] select option').length > 2")
    check("周期性强制动作区块渲染", "!!document.querySelector('[data-lg=\"periodic\"] [data-lga=\"paAdd\"]')")
    check("同时出现组区块渲染", "!!document.querySelector('[data-lg=\"sim\"] [data-lga=\"simGAdd\"]')")

    # 改一个值: 空闲超时 → 7, NG 保护 → 3.5, 开防重复结算
    frame.evaluate("""(() => {
      const idle = document.querySelector('[data-lg="settle"] [data-b="pc.idle_timeout_seconds"]');
      idle.value = '7'; idle.dispatchEvent(new Event('change', {bubbles:true}));
      const ng = document.querySelector('[data-lg="ng"] [data-b="pc.ng_cycle_protect_seconds"]');
      if (ng) { ng.value = '3.5'; ng.dispatchEvent(new Event('change', {bubbles:true})); }
      const dd = document.querySelector('[data-lg="dedup"] [data-b="pc.settle_dedup"]');
      dd.checked = true; dd.dispatchEvent(new Event('change', {bubbles:true}));
    })()""")
    check("变更已写入编辑模型", "window.__tjPE.pipeline_config.idle_timeout_seconds === 7 && window.__tjPE.pipeline_config.settle_dedup === true")

    pid = frame.evaluate("window.__tjSelectedPid")
    before = api_get(f"/projects/{pid}")
    frame.evaluate("document.getElementById('projSaveBtn').click()")
    time.sleep(3)

    after = api_get(f"/projects/{pid}")
    pc = after.get("pipeline_config") or {}
    ok_save = pc.get("idle_timeout_seconds") == 7 and pc.get("settle_dedup") is True
    ng_ok = abs(float(pc.get("ng_cycle_protect_seconds") or 0) - 3.5) < 1e-6
    results.append(("保存后后端 pipeline_config 三键落库", ok_save and ng_ok))
    log(("PASS  " if (ok_save and ng_ok) else "FAIL  ") + f"保存后后端落库 idle={pc.get('idle_timeout_seconds')} dedup={pc.get('settle_dedup')} ng={pc.get('ng_cycle_protect_seconds')}")
    # 不丢未知键: models / per_item 仍在
    keep_ok = ("models" not in (before.get("pipeline_config") or {})) or ("models" in pc)
    results.append(("未知键不丢失(models)", keep_ok))
    log(("PASS  " if keep_ok else "FAIL  ") + "未知键不丢失(models)")

    # 还原
    frame.evaluate("""(() => {
      const idle = document.querySelector('[data-lg="settle"] [data-b="pc.idle_timeout_seconds"]');
      idle.value = '0'; idle.dispatchEvent(new Event('change', {bubbles:true}));
      const ng = document.querySelector('[data-lg="ng"] [data-b="pc.ng_cycle_protect_seconds"]');
      if (ng) { ng.value = '0'; ng.dispatchEvent(new Event('change', {bubbles:true})); }
      const dd = document.querySelector('[data-lg="dedup"] [data-b="pc.settle_dedup"]');
      dd.checked = false; dd.dispatchEvent(new Event('change', {bubbles:true}));
      document.getElementById('projSaveBtn').click();
    })()""")
    time.sleep(3)
    restored = api_get(f"/projects/{pid}")
    rpc = restored.get("pipeline_config") or {}
    r_ok = rpc.get("idle_timeout_seconds") == 0 and rpc.get("settle_dedup") is False
    results.append(("配置还原成功", r_ok))
    log(("PASS  " if r_ok else "FAIL  ") + "配置还原成功")

    page.screenshot(path="tests/uat/p1a_logic_tab.png", full_page=False)
    browser.close()

fatal = [e for e in console_errors if "favicon" not in e and "ERR_CONNECTION" not in e]
log("\nconsole errors: " + str(len(fatal)))
for e in fatal[:8]:
    log("  " + e[:200])
n_pass = sum(1 for _, ok in results if ok)
log(f"\n== {n_pass}/{len(results)} PASS ==")
sys.exit(0 if n_pass == len(results) else 1)
