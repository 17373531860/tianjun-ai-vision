# -*- coding: utf-8 -*-
"""
UAT 阶段4: 多工位四通道接真 — 模式切换 / 工位卡渲染 / 单卡保存 / GPU 分配
注: 不真启动多路视频源 (开发机无多摄像头), 验证配置链路与持久化。
运行: python tests/uat/uat_showcase_p4_multi.py
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
                if f.evaluate("typeof renderRoutePage === 'function' && typeof tjLoadWorkstations === 'function'"):
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

    orig_count = api_get("/workstations/")["channel_count"]
    log(f"  初始工位数: {orig_count}")

    frame.evaluate("renderRoutePage('/source')")
    time.sleep(3)

    check("工位元数据已加载", "window.__tjWsMeta !== null && Array.isArray(window.__tjWsCfg)")
    check("模式分段回显真值", f"document.querySelector('#wsModeSeg button.active').dataset.wsMode === '{orig_count}'")

    # 切到双工位
    frame.evaluate("document.querySelector('#wsModeSeg [data-ws-mode=\"2\"]').click()")
    time.sleep(3)
    after_mode = api_get("/workstations/")
    ok_mode = after_mode["channel_count"] == 2
    results.append(("切双工位后端生效", ok_mode))
    log(("PASS  " if ok_mode else "FAIL  ") + f"切双工位后端生效 count={after_mode['channel_count']}")
    check("渲染 2 张工位卡", "document.querySelectorAll('#wsGrid [data-ws-ch]').length === 2")
    check("工位卡含真 GPU 选项", "document.querySelector('#wsGrid [data-ws-f=\"gpu_device\"]').options.length >= 2")
    check("工位卡含项目下拉", "document.querySelector('#wsGrid [data-ws-f=\"project_id\"]').options.length >= 1")

    # 工位2 切 RTSP 类型 + 填配置 + 单卡保存
    frame.evaluate("""(() => {
      const card = document.querySelector('#wsGrid [data-ws-ch="1"]');
      card.querySelector('[data-ws-type="rtsp"]').click();
    })()""")
    time.sleep(0.5)
    check("工位2 切 RTSP 渲染 url 输入", "document.querySelector('#wsGrid [data-ws-ch=\"1\"] [data-ws-f=\"url\"]') !== null")
    frame.evaluate("""(() => {
      const el = document.querySelector('#wsGrid [data-ws-ch="1"] [data-ws-f="url"]');
      el.value = 'rtsp://uat-test:554/stream1'; el.dispatchEvent(new Event('change', {bubbles:true}));
      document.querySelector('#wsGrid [data-ws-ch="1"] [data-ws-save]').click();
    })()""")
    time.sleep(2)
    ws = api_get("/workstations/")
    saved = (ws.get("source_configs") or {}).get("1") or {}
    ok_save = saved.get("source_type") == "rtsp" and saved.get("url") == "rtsp://uat-test:554/stream1"
    results.append(("单卡保存持久化", ok_save))
    log(("PASS  " if ok_save else "FAIL  ") + f"单卡保存持久化 saved={json.dumps(saved, ensure_ascii=False)[:120]}")

    # GPU 分配
    gpu_dev = frame.evaluate("""(async () => {
      const g = await tjApi.get('/source/gpu/list');
      const d = (g.devices || []).find(x => x.id === 'cpu');
      if (!d) return null;
      await tjApi.post('/workstations/1/gpu', {device: 'cpu'});
      const alloc = await tjApi.get('/workstations/gpu-allocation');
      return alloc.allocation['1'];
    })()""")
    ok_gpu = gpu_dev == "cpu"
    results.append(("GPU 分配生效", ok_gpu))
    log(("PASS  " if ok_gpu else "FAIL  ") + f"GPU 分配生效 device={gpu_dev}")

    check("启动所有工位按钮已接线", "document.getElementById('wsStartAllBtn') !== null && !document.getElementById('wsStartAllBtn').dataset.protoToast")
    page.screenshot(path="tests/uat/p4_multi.png")

    # 还原工位数
    frame.evaluate(f"tjApi.post('/workstations/mode', {{channel_count: {orig_count}, channels: []}})")
    time.sleep(2)
    final = api_get("/workstations/")["channel_count"]
    ok_rest = final == orig_count
    results.append(("工位数还原", ok_rest))
    log(("PASS  " if ok_rest else "FAIL  ") + f"工位数还原 count={final}")
    browser.close()

fatal = [e for e in console_errors if "favicon" not in e]
log("\nconsole errors: " + str(len(fatal)))
for e in fatal[:8]:
    log("  " + e[:200])
n_pass = sum(1 for _, ok in results if ok)
log(f"\n== {n_pass}/{len(results)} PASS ==")
sys.exit(0 if n_pass == len(results) else 1)
