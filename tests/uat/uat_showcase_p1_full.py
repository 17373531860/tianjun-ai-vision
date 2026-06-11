# -*- coding: utf-8 -*-
"""
UAT 阶段1b/1c/1d: 事件 Tab CRUD + 基础 Tab/侧栏/搜索 + 跟踪步骤表
运行: python tests/uat/uat_showcase_p1_full.py
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


def api_delete(path):
    req = urllib.request.Request(API + path, method="DELETE")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


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

    # ---------- 1c 基础 Tab ----------
    check("基础Tab: 项目名回填", "document.getElementById('projNameInput').value.length > 0")
    check("基础Tab: 任务类型下拉有值", "['detection','segmentation'].includes(document.getElementById('projTaskSelect').value)")
    check("基础Tab: 班次拆分时间回填", "/^\\d{2}:\\d{2}$/.test(document.getElementById('projShiftDay').value)")
    # 搜索过滤
    frame.evaluate("""(() => {
      const s = document.getElementById('projSearchInput');
      s.value = '不存在的项目xyz'; s.dispatchEvent(new Event('input', {bubbles:true}));
    })()""")
    check("搜索: 无匹配时全部隐藏", "Array.from(document.querySelectorAll('[data-project-card]')).every(c => c.style.display === 'none')")
    frame.evaluate("""(() => {
      const s = document.getElementById('projSearchInput');
      s.value = ''; s.dispatchEvent(new Event('input', {bubbles:true}));
    })()""")
    check("搜索: 清空后恢复", "Array.from(document.querySelectorAll('[data-project-card]')).every(c => c.style.display !== 'none')")
    # 选择模型对话框真实填充
    frame.evaluate("document.getElementById('projPickModelBtn').click()")
    time.sleep(2.5)
    check("选择模型对话框: 真模型列表", "document.querySelectorAll('#modelDialogList [data-model-pick-id]').length > 0 || document.getElementById('modelDialogList').textContent.includes('模型库为空')")
    frame.evaluate("document.getElementById('modelDialog').close()")

    # ---------- 1b 事件 Tab ----------
    frame.evaluate("document.querySelector('[data-project-tab=\"events\"]').click()")
    time.sleep(1)
    check("事件Tab: 真事件卡渲染", "document.querySelectorAll('[data-lg=\"events\"] .event-card').length >= 1")
    n_ev = frame.evaluate("window.__tjPE.events_config.length")
    frame.evaluate("document.querySelector('[data-lga=\"evAdd\"]').click()")
    check("事件Tab: 新增事件进编辑模型", f"window.__tjPE.events_config.length === {n_ev + 1}")
    # 给新事件加动作再删
    last = frame.evaluate("window.__tjPE.events_config.length - 1")
    frame.evaluate(f"document.querySelector('[data-lga=\"evActAdd\"][data-i=\"{last}\"]').click()")
    check("事件Tab: 新增计数动作", f"window.__tjPE.events_config[{last}].actions.length === 1")
    frame.evaluate(f"document.querySelector('[data-lga=\"evDel\"][data-i=\"{last}\"]').click()")
    check("事件Tab: 删除事件还原", f"window.__tjPE.events_config.length === {n_ev}")
    # 侧栏计数器
    check("侧栏: 计数器真渲染", "document.querySelectorAll('[data-lg=\"counters\"] .rowline').length >= 3")
    n_ctr = frame.evaluate("window.__tjPE.counters_config.length")
    frame.evaluate("document.querySelector('[data-lga=\"ctrAdd\"]').click()")
    check("侧栏: 新增计数器", f"window.__tjPE.counters_config.length === {n_ctr + 1}")
    frame.evaluate(f"document.querySelector('[data-lga=\"ctrDel\"][data-i=\"{n_ctr}\"]').click()")
    check("侧栏: 删除计数器还原", f"window.__tjPE.counters_config.length === {n_ctr}")

    # ---------- 1d 跟踪步骤表 ----------
    frame.evaluate("document.querySelector('[data-project-tab=\"steps\"]').click()")
    time.sleep(0.5)
    frame.evaluate("setLogicMode('tracking')")
    time.sleep(0.5)
    check("跟踪表: 真步骤行渲染", "document.querySelectorAll('[data-steps-table=\"tracking\"] tbody tr[data-si]').length > 0 || document.querySelector('[data-steps-table=\"tracking\"] .tj-empty') !== null")
    check("跟踪表: 跟踪专属字段存在", "document.querySelectorAll('[data-steps-table=\"tracking\"] [data-k=\"tracking_max_lost_seconds\"]').length > 0 || document.querySelector('[data-steps-table=\"tracking\"] .tj-empty') !== null")
    check("跟踪表切换可见", "document.querySelector('[data-steps-table=\"tracking\"]').style.display !== 'none' && document.querySelector('[data-steps-table=\"normal\"]').style.display === 'none'")
    # 恢复原模式
    orig_mode = frame.evaluate("(window.__tjProjects.find(p=>p.id===window.__tjSelectedPid)||{}).logic_mode || 'sequential'")
    frame.evaluate(f"setLogicMode('{orig_mode}')")

    # ---------- 新建项目 → 后端真建 → 删除 ----------
    frame.evaluate("""(() => {
      document.getElementById('newProjName').value = 'UAT临时项目P1C';
      document.getElementById('newProjTask').value = 'detection';
      document.getElementById('newProjMode').value = 'detection';
      document.getElementById('newProjCreateBtn').click();
    })()""")
    time.sleep(3)
    projs = api_get("/projects")
    created = [x for x in projs.get("items", []) if x.get("name") == "UAT临时项目P1C"]
    results.append(("新建项目落库", len(created) == 1))
    log(("PASS  " if len(created) == 1 else "FAIL  ") + "新建项目落库")
    if created:
        ok_mode = created[0].get("logic_mode") == "detection"
        results.append(("新建项目模式正确", ok_mode))
        log(("PASS  " if ok_mode else "FAIL  ") + "新建项目模式正确")
        api_delete(f"/projects/{created[0]['id']}")
        log("清理: 已删除 UAT 临时项目")

    page.screenshot(path="tests/uat/p1_full.png")
    browser.close()

fatal = [e for e in console_errors if "favicon" not in e]
log("\nconsole errors: " + str(len(fatal)))
for e in fatal[:8]:
    log("  " + e[:200])
n_pass = sum(1 for _, ok in results if ok)
log(f"\n== {n_pass}/{len(results)} PASS ==")
sys.exit(0 if n_pass == len(results) else 1)
