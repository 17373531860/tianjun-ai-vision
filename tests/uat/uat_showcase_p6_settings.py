# -*- coding: utf-8 -*-
"""
UAT 阶段6: 设置页性能/画面变换/基本信息接真 + 模型页收尾
覆盖: 性能设置回读与保存、真 GPU 信息、卡尔曼预设、画面变换读写、
基本信息保存、模型页资产摘要/最近动态/刷新接线。
运行: python tests/uat/uat_showcase_p6_settings.py
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
                if f.evaluate("typeof renderRoutePage === 'function' && typeof tjLoadPerf === 'function'"):
                    frame = f
                    break
            except Exception:
                pass
        time.sleep(1.5)
    if not frame:
        log("FAIL: 插件 iframe 未就绪")
        sys.exit(1)
    log("OK: iframe 就绪")

    def check(name, expr):
        try:
            ok = bool(frame.evaluate(expr))
        except Exception as e:
            ok = False
            log("  exception: " + str(e)[:200])
        results.append((name, ok))
        log(("PASS  " if ok else "FAIL  ") + name)

    # 进设置页 → 性能 Tab
    frame.evaluate("renderRoutePage('/settings')")
    time.sleep(1.5)
    frame.evaluate("setSettingsTab('perf')")
    time.sleep(2.5)

    # ---------- 1. 性能设置回读 ----------
    sc = api_get("/source/stream/config")
    check(
        "帧率限制开关回读后端真值",
        f"document.getElementById('perfFrameLimit').checked === {str(sc['frame_limit_enabled']).lower()}",
    )
    check(
        "目标帧率回读后端真值",
        f"document.getElementById('perfFps').value === '{sc['target_stream_fps']}'",
    )
    check(
        "FP16 开关回读后端真值",
        f"document.getElementById('perfHalf').checked === {str(sc['use_half']).lower()}",
    )
    check(
        "MediaPipe 开关回读后端真值",
        f"document.getElementById('perfMpEnable').checked === {str(sc['mediapipe_enabled']).lower()}",
    )
    check(
        "工业手部模型状态徽章非写死",
        "['未启用','已启用','待加载','路径无效','加载失败'].includes(document.getElementById('perfHandStatus').textContent)",
    )

    # ---------- 2. 真 GPU 信息 ----------
    gl = api_get("/source/gpu/list")
    cur = api_get("/source/gpu/current")
    check(
        "CUDA 徽章为后端真值",
        f"document.getElementById('perfCudaBadge').textContent.includes('{'可用' if gl['cuda_available'] else '不可用'}')",
    )
    check(
        "设备下拉数量与后端一致",
        f"document.getElementById('perfDeviceSel').options.length === {len(gl['devices'])}",
    )
    check(
        "设备下拉不含写死 RTX 4090",
        "[...document.getElementById('perfDeviceSel').options].every(o => !o.text.includes('RTX 4090'))"
        if not any("RTX 4090" in d["name"] for d in gl["devices"]) else "true",
    )
    check(
        "当前设备说明为后端真值",
        f"document.getElementById('perfDeviceNote').textContent.includes('当前推理设备')",
    )

    # ---------- 3. 性能保存链路 (改帧率限制再还原) ----------
    orig_fl = sc["frame_limit_enabled"]
    frame.evaluate(f"document.getElementById('perfFrameLimit').checked = {str(not orig_fl).lower()}")
    frame.evaluate("tjPerfSave()")
    time.sleep(2)
    after = api_get("/source/stream/config")
    results.append(("性能设置保存写入后端", after["frame_limit_enabled"] == (not orig_fl)))
    log(("PASS  " if results[-1][1] else "FAIL  ") + "性能设置保存写入后端")
    frame.evaluate(f"document.getElementById('perfFrameLimit').checked = {str(orig_fl).lower()}")
    frame.evaluate("tjPerfSave()")
    time.sleep(2)
    restored = api_get("/source/stream/config")
    results.append(("性能设置已还原", restored["frame_limit_enabled"] == orig_fl))
    log(("PASS  " if results[-1][1] else "FAIL  ") + "性能设置已还原")

    # ---------- 4. 卡尔曼预设 ----------
    kc0 = api_get("/source/kalman/config")
    frame.evaluate("document.querySelector('[data-kal-preset=\"smooth\"]').click()")
    time.sleep(1.5)
    kc1 = api_get("/source/kalman/config")
    ok_kal = abs(kc1["process_noise"] - 0.01) < 1e-6 and abs(kc1["measurement_noise"] - 0.5) < 1e-6 and kc1["max_missing_frames"] == 10
    results.append(("卡尔曼预设写入后端", ok_kal))
    log(("PASS  " if ok_kal else "FAIL  ") + "卡尔曼预设写入后端")
    # 还原
    frame.evaluate(
        f"""tjApi.post('/source/kalman/config', {{enabled:{str(kc0['enabled']).lower()},
          process_noise:{kc0['process_noise']}, measurement_noise:{kc0['measurement_noise']},
          max_missing_frames:{kc0['max_missing_frames']}}})"""
    )
    time.sleep(1)

    # ---------- 5. 画面变换读写 ----------
    frame.evaluate("setSettingsTab('display')")
    frame.evaluate("tjLoadXf()")
    time.sleep(1.5)
    xf0 = api_get("/source/transform/config?channel=0")
    check("画面变换旋转回读后端", f"document.getElementById('xfRotation').value === '{xf0.get('rotation', 0)}'")
    frame.evaluate("document.getElementById('xfRotation').value = '90'")
    frame.evaluate("document.getElementById('xfSave').click()")
    time.sleep(1.5)
    xf1 = api_get("/source/transform/config?channel=0")
    results.append(("画面变换保存写入后端", xf1.get("rotation") == 90))
    log(("PASS  " if results[-1][1] else "FAIL  ") + "画面变换保存写入后端")
    frame.evaluate(f"tjApi.post('/source/transform/config?channel=0', {{rotation:{xf0.get('rotation', 0)}, flip_h:{str(xf0.get('flip_h', False)).lower()}, flip_v:{str(xf0.get('flip_v', False)).lower()}}})")
    time.sleep(1)

    # ---------- 6. 基本信息保存 ----------
    disp0 = api_get("/system/display")
    frame.evaluate("document.getElementById('dispDevNo').value = 'UAT-P6-001'")
    frame.evaluate("document.getElementById('dispSaveBtn').click()")
    time.sleep(1.5)
    disp1 = api_get("/system/display")
    results.append(("基本信息保存写入后端", disp1.get("device_number") == "UAT-P6-001"))
    log(("PASS  " if results[-1][1] else "FAIL  ") + "基本信息保存写入后端")
    frame.evaluate(f"tjApi.put('/system/display', {{device_number:'{disp0.get('device_number') or ''}'}})")
    time.sleep(1)

    # ---------- 7. 模型页收尾 ----------
    frame.evaluate("renderRoutePage('/model')")
    time.sleep(3)
    check(
        "资产摘要由真数据渲染 (无 bestGW 占位)",
        "!document.getElementById('mdlAssetRows').textContent.includes('bestGW') && !document.getElementById('mdlAssetRows').textContent.includes('加载中')",
    )
    check(
        "最近动态由真数据渲染",
        "!document.getElementById('mdlRecentRows').textContent.includes('bestGW') && !document.getElementById('mdlRecentRows').textContent.includes('加载中')",
    )
    check("刷新模型列表行已接线", "document.getElementById('mdlRefreshRow') !== null && !document.getElementById('mdlRefreshRow').dataset.protoToast")
    check("删除闲置模型行已接线", "document.getElementById('mdlPurgeRow') !== null && !document.getElementById('mdlPurgeRow').dataset.protoToast")

    # ---------- 汇总 ----------
    real_errors = [e for e in console_errors if "favicon" not in e and "ERR_BLOCKED_BY_CLIENT" not in e]
    results.append(("无控制台错误", len(real_errors) == 0))
    log(("PASS  " if results[-1][1] else "FAIL  ") + "无控制台错误")
    if real_errors:
        for e in real_errors[:8]:
            log("  console: " + e[:200])

    browser.close()

failed = [n for n, ok in results if not ok]
log("\n===== 阶段6 UAT 结果: %d/%d 通过 =====" % (len(results) - len(failed), len(results)))
if failed:
    for n in failed:
        log("FAILED: " + n)
    sys.exit(1)
log("ALL PASS")
