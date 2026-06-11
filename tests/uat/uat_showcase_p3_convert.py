# -*- coding: utf-8 -*-
"""
UAT 阶段3b: 格式切换对话框接真（available 列表 / 诊断 / 应用写编辑模型）
注: 转换任务是后台异步, 只验证发起与状态查询接口, 不等转换完成。
运行: python tests/uat/uat_showcase_p3_convert.py
"""
import sys, time
from playwright.sync_api import sync_playwright

FRONT = "http://localhost:6001"
results = []
console_errors = []


def log(m):
    print(m, flush=True)


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
                if f.evaluate("typeof renderRoutePage === 'function' && typeof tjFillFormatDialog === 'function'"):
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

    # 打开格式对话框
    frame.evaluate("document.querySelector('[data-open-dialog=\"formatDialog\"]').click()")
    time.sleep(2.5)
    check("格式对话框打开", "document.getElementById('formatDialog').open === true")
    check("格式列表真渲染（≥3 项）", "document.querySelectorAll('#fmtList input[name=\"fmtPick\"]').length >= 3")
    # 真数据校验: 对话框 GPU 名必须与诊断接口返回的 gpu_name 一致 (而不是原型写死的 "RTX 4060")
    check("GPU 名来自后端真值", """(async () => {
      const d = await tjApi.get('/models/formats/diagnosis');
      const txt = document.getElementById('fmtGpuName').textContent;
      return d.gpu_name ? txt.includes(d.gpu_name) : txt.includes('当前显卡');
    })()""")
    check("当前格式有勾选", "document.querySelector('#fmtList input[name=\"fmtPick\"]:checked') !== null")
    gpu_txt = frame.evaluate("document.getElementById('fmtGpuName').textContent")
    log(f"  GPU: {gpu_txt}")

    # 应用 FP32（不发转换，仅写编辑模型）
    frame.evaluate("""(() => {
      const r = document.querySelector('#fmtList input[value="pytorch_fp32"]');
      if (r) { r.checked = true; }
      document.getElementById('fmtApplyBtn').click();
    })()""")
    time.sleep(1)
    check("应用 FP32 写入编辑模型", "window.__tjPE.model_format === 'pytorch_fp32'")

    # 环境诊断
    frame.evaluate("document.getElementById('formatDialog').close()")
    frame.evaluate("document.querySelector('[data-open-dialog=\"diagnosisDialog\"]')?.click() || (function(){ tjFillDiagnosisDialog(); document.getElementById('diagnosisDialog').showModal(); })()")
    time.sleep(2.5)
    check("诊断对话框真数据（含 CUDA 行）", "document.getElementById('diagList').textContent.includes('CUDA')")
    check("诊断含 TensorRT 行", "document.getElementById('diagList').textContent.includes('TensorRT')")
    diag_txt = frame.evaluate("document.getElementById('diagList').textContent")
    log("  诊断: " + diag_txt[:160].replace("\n", " "))
    frame.evaluate("document.getElementById('diagnosisDialog').close()")

    # 转换发起 + 状态查询 (用 PyTorch FP16, CPU/GPU 都能转; 仅验证 API 通)
    conv = frame.evaluate("""(async () => {
      const pe = window.__tjPE;
      if (!pe || !pe.default_model_id) return {skip: true};
      try {
        const c = await tjApi.post('/models/' + pe.default_model_id + '/convert', {format: 'pytorch_fp16'});
        const s = await tjApi.get('/models/conversions/' + c.id + '/status');
        return {id: c.id, status: s.status};
      } catch (e) { return {error: (e && e.data && e.data.detail) || e.message || String(e)}; }
    })()""")
    if conv.get("skip"):
        log("SKIP: 项目无主模型，跳过转换发起")
    elif conv.get("error"):
        results.append(("转换发起+状态查询", False))
        log("FAIL  转换发起: " + str(conv["error"]))
    else:
        ok_conv = conv.get("status") in ("queued", "converting", "ready", "failed")
        results.append(("转换发起+状态查询", ok_conv))
        log(("PASS  " if ok_conv else "FAIL  ") + f"转换发起+状态查询 id={conv.get('id')} status={conv.get('status')}")

    page.screenshot(path="tests/uat/p3_convert.png")
    browser.close()

fatal = [e for e in console_errors if "favicon" not in e]
log("\nconsole errors: " + str(len(fatal)))
for e in fatal[:8]:
    log("  " + e[:200])
n_pass = sum(1 for _, ok in results if ok)
log(f"\n== {n_pass}/{len(results)} PASS ==")
sys.exit(0 if n_pass == len(results) else 1)
