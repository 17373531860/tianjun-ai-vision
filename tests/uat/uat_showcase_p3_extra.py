# -*- coding: utf-8 -*-
"""
UAT 阶段3a: 副模型 CRUD + 落库 + 删除清理
运行: python tests/uat/uat_showcase_p3_extra.py
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
                if f.evaluate("typeof renderRoutePage === 'function' && typeof tjAssignExtraModel === 'function'"):
                    frame = f
                    break
            except Exception:
                pass
        time.sleep(1.5)
    if not frame:
        log("FAIL: 插件 iframe 未就绪")
        sys.exit(1)
    log("OK: iframe 就绪")
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

    check("副模型块渲染（空态或列表）", "document.querySelector('[data-lg=\"extras\"] [data-lg-body]').innerHTML.length > 10")
    base_n = frame.evaluate("(window.__tjPE.extra_models||[]).length")
    log(f"  现有副模型数: {base_n}")

    # 添加副模型
    frame.evaluate("document.querySelector('[data-lga=\"exmAdd\"]').click()")
    time.sleep(0.5)
    check("添加副模型后列表+1", f"window.__tjPE.extra_models.length === {base_n + 1}")
    check("新 slot 默认值正确", f"(function(){{var s=window.__tjPE.extra_models[{base_n}]; return s.name.length>0 && s.conf===0.25 && s.schedule_type==='every_n_frames';}})()")
    check("slot UI 渲染出来", f"document.querySelectorAll('[data-lg=\"extras\"] [data-exm]').length === {base_n + 1}")

    # 给 slot 选模型（取模型库第一个）
    mid = frame.evaluate("(async()=>{const r=await tjApi.get('/models');const l=(r&&r.items)||r||[];return l.length?l[0].id:null;})()")
    if mid is not None:
        frame.evaluate(f"tjAssignExtraModel({base_n}, {mid})")
        time.sleep(1.5)
        check("选模型后 slot 元数据回填", f"window.__tjPE.extra_models[{base_n}].model_id === {mid} && window.__tjPE.extra_models[{base_n}].model_name.length > 0")
        check("available_labels 已填充", f"Array.isArray(window.__tjPE.extra_models[{base_n}].available_labels)")
    else:
        log("SKIP: 模型库为空，跳过选模型")

    # 改 conf 验证绑定
    frame.evaluate(f"""(() => {{
      const el = document.querySelector('[data-exm="{base_n}"] [data-b="pe.extra_models.{base_n}.conf"]');
      el.value='0.33'; el.dispatchEvent(new Event('change', {{bubbles:true}}));
    }})()""")
    check("conf 编辑绑定生效", f"window.__tjPE.extra_models[{base_n}].conf === 0.33")

    # 保存 → 后端验证 pipeline_config.models
    pid = frame.evaluate("window.__tjSelectedPid")
    frame.evaluate("document.getElementById('projSaveBtn').click()")
    time.sleep(3)
    after = api_get(f"/projects/{pid}")
    models = (after.get("pipeline_config") or {}).get("models") or []
    has_main = any(m.get("name") == "main" for m in models)
    extras = [m for m in models if m.get("name") != "main"]
    ok_db = has_main and len(extras) == base_n + 1 and (mid is None or extras[-1].get("model_id") == mid) and extras[-1].get("conf") == 0.33
    results.append(("副模型落库后端", ok_db))
    log(("PASS  " if ok_db else "FAIL  ") + f"副模型落库后端 main={has_main} extras={len(extras)}")

    # 删除副模型 → 还原
    frame.evaluate(f"document.querySelector('[data-exm=\"{base_n}\"] [data-lga=\"exmDel\"]').click()")
    time.sleep(0.5)
    check("删除副模型后列表还原", f"window.__tjPE.extra_models.length === {base_n}")
    frame.evaluate("document.getElementById('projSaveBtn').click()")
    time.sleep(3)
    restored = api_get(f"/projects/{pid}")
    rm = (restored.get("pipeline_config") or {}).get("models") or []
    ok_rest = len([m for m in rm if m.get("name") != "main"]) == base_n
    results.append(("删除落库还原", ok_rest))
    log(("PASS  " if ok_rest else "FAIL  ") + "删除落库还原")

    page.screenshot(path="tests/uat/p3_extra.png")
    browser.close()

fatal = [e for e in console_errors if "favicon" not in e]
log("\nconsole errors: " + str(len(fatal)))
for e in fatal[:8]:
    log("  " + e[:200])
n_pass = sum(1 for _, ok in results if ok)
log(f"\n== {n_pass}/{len(results)} PASS ==")
sys.exit(0 if n_pass == len(results) else 1)
