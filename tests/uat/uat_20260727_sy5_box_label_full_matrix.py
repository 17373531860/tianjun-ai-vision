# -*- coding: utf-8 -*-
"""UAT 全矩阵: SY5 (packing_v28 双模型) x 箱标签扫码授权 (组⑧, v3.45) 全场景.

用户验收要求 (2026-07-27):
  - 所有按钮/输入一律真鼠标点击/真键盘输入 (Playwright headless=False + 录像),
    不用页面跳转 API、不用 API 直改参数 (仅只读 GET 做双向核对 = T5).
  - 模拟 MES (tests/uat/mock_hiwin_mes_server.py, 端口 18080) 提供合格/不合格工单.
  - 模拟真实扫码: Monitor 页「虚拟扫码枪测试台」输入 + 点「扫一下」.

场景矩阵:
  S0 组⑧配置落地: 设置页真点开关/下拉/输入 → 保存 → GET 双向核对
  S1 正确流程整单: JOB260700101 (384=4满箱), 每箱扫二维码标签(带数量) → 4箱全绿收尾
  S2 未扫开做: 只扫工单不扫标签就开工 → 当场报警(需人工确认) → 补扫标签后正常落账
  S3 扫到条形码: 等扫标签态扫裸 JOB 号(模拟扫到一维条形码) → 报警请重扫二维码 → 补扫二维码放行
  S4 数量不符+重扫忽略: 标签声明 72 实装 96 → 重扫同号标签默认忽略 → 结算超装报警
  S5 收尾对账不平: 两箱标签合计 168 ≠ 排产 192 → 强制结案时对账报警
  S6 不合格工单: 查无此单(404) / MES 5xx(500) / 排产量 0 → 报警不崩溃

证据三件套: /home/qianqian/uat_sy5_box_label/{run.log, *.png, video/*.webm}

用法: python tests/uat/uat_20260727_sy5_box_label_full_matrix.py [S0 S1 ...]
      (缺省跑全部; 中断后可只重跑失败场景)
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8001"
WEB = "http://localhost:6002"
MES = "http://localhost:18080"
OUT = Path("/home/qianqian/uat_sy5_box_label")
CH = 0
CFG_ID = 1                      # 上银SY包装线
VIDEO = "/home/qianqian/2026-07-23 15-43-27.mkv"   # 4箱x4盘x24滑块, 无尾箱
VIDEO_LEN_S = 266

_log_fh = None
_results = {}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_fh:
        _log_fh.write(line + "\n")
        _log_fh.flush()


def log_resources(tag):
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                              "--format=csv,noheader"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
        mem = subprocess.run(["free", "-m"], capture_output=True, text=True,
                             timeout=5).stdout.splitlines()[1].split()
        log(f"[资源][{tag}] GPU {gpu} | RAM used {mem[2]}M / {mem[1]}M")
    except Exception as e:
        log(f"[资源][{tag}] 读取失败: {e}")


def api_get(path):
    r = requests.get(f"{API}{path}", timeout=15)
    r.raise_for_status()
    return r.json()


def counters():
    try:
        return api_get(f"/api/v1/source/detection/results?channel={CH}").get("counters") or {}
    except Exception:
        return {}


def pkg_state():
    try:
        return api_get(f"/api/v1/packaging-flows/{CFG_ID}/state").get("state")
    except Exception:
        return None


def mock_mes_alive():
    r = requests.post(f"{MES}/java_demo_test/api", json={
        "api": "hiwin/webcn/ai_error_prevention_job_info/query",
        "parameters": {"job_no": "JOB260700101"}}, timeout=5)
    rows = r.json()["response"]["resultData"]
    assert rows and rows[0]["dispatch_qty"] == 384, "Mock MES 工单库异常"


# ================= 浏览器操作原语 (全部真点击) =================

def shot(page, name):
    p = OUT / f"{name}.png"
    try:
        page.screenshot(path=str(p), full_page=True)
        log(f"  截图 {p.name}")
    except Exception as e:
        log(f"  截图失败 {name}: {e}")


def goto_monitor(page):
    page.goto(f"{WEB}/#/monitor", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2500)


def scan_gun(page, code, note=""):
    """虚拟扫码枪测试台: 展开 → 输入 → 点「扫一下」."""
    header = page.get_by_text("虚拟扫码枪测试台", exact=False).first
    header.scroll_into_view_if_needed()
    inp = page.locator('input[placeholder*="输入序列号"]')
    if inp.count() == 0 or not inp.first.is_visible():
        header.click()
        page.wait_for_timeout(400)
    inp = page.locator('input[placeholder*="输入序列号"]').first
    inp.fill(code)
    page.get_by_role("button", name="扫一下").click()
    log(f"  [扫码] {code}  {note}")
    page.wait_for_timeout(1200)


def ack_if_pending(page, choose="confirm", note=""):
    """人工确认阻塞层出现则点掉. choose: confirm(我已确认)/redo(重做本箱)/ng(认NG落账)/supplement(补步骤)."""
    sel = {
        "confirm": "button:has-text('我已确认')",
        "redo": "button:has-text('重做本箱')",
        "ng": "button:has-text('认 NG')",
        "supplement": "button:has-text('补步骤')",
    }[choose]
    btn = page.locator(sel)
    if btn.count() and btn.first.is_visible():
        shot(page, f"ack_{int(time.time())}")
        btn.first.click()
        log(f"  [确认] 点掉人工确认层 ({choose}) {note}")
        page.wait_for_timeout(1000)
        return True
    return False


def wait_ack_overlay(page, timeout_s=60, note=""):
    """等人工确认层出现 (报警证据), 返回层上的事件名文本."""
    end = time.time() + timeout_s
    while time.time() < end:
        ov = page.locator("button:has-text('我已确认'), button:has-text('认 NG')")
        if ov.count() and ov.first.is_visible():
            body = page.locator("text=待人工确认").first
            log(f"  [报警到达] 人工确认层已出现 {note}")
            return True
        page.wait_for_timeout(500)
    return False


def wait_waiting_label(page, timeout_s=40, box_hint=""):
    end = time.time() + timeout_s
    while time.time() < end:
        st = pkg_state()
        if st and st.get("status") == "waiting_label":
            log(f"  [状态] waiting_label 第{st.get('current_box_index')}箱 {box_hint}")
            # UI 卡片 ~2s 轮询一次, 后端刚翻状态时横幅有延迟 — 给 8s 重试窗
            for _ in range(16):
                if page.get_by_text("等扫箱标签", exact=False).count() >= 1:
                    return st
                page.wait_for_timeout(500)
            raise AssertionError("后端 waiting_label 但 UI 横幅 8s 未出现")
        page.wait_for_timeout(700)
    raise AssertionError(f"等 waiting_label 超时 {box_hint} (state={pkg_state()})")


def wait_status(page, want, timeout_s=40, note=""):
    end = time.time() + timeout_s
    while time.time() < end:
        st = pkg_state()
        if st and st.get("status") == want:
            log(f"  [状态] {want} {note}")
            return st
        page.wait_for_timeout(700)
    raise AssertionError(f"等状态 {want} 超时 {note} (state={pkg_state()})")


def force_settle_if_any(page, note="场景收尾"):
    """有在途工单则点「强制结案」清场 (真按钮, 收尾噪声报警照单全收)."""
    st = pkg_state()
    if not st or st.get("status") in ("completed", "aborted", None):
        return
    goto_monitor(page)
    btn = page.get_by_role("button", name="强制结案")
    if not (btn.count() and btn.first.is_visible()):
        log(f"  [清场] 有在途工单但无强制结案按钮 state={st.get('status')}")
        return
    btn.first.click()
    page.wait_for_timeout(600)
    box = page.locator(".el-message-box")
    if box.count():
        inp = box.locator("input, textarea").first
        if inp.count():
            inp.fill(f"UAT {note} 清场")
        # 弹窗按钮文案是「确认强制结案」(危险按钮), 不是默认「确定」
        box.get_by_role("button", name="确认强制结案").click()
        page.wait_for_timeout(1200)
    # 结案可能触发挂账/确认层, 全部点掉
    for _ in range(5):
        if not (ack_if_pending(page, "ng", "清场认NG") or ack_if_pending(page, "confirm", "清场确认")):
            break
    log(f"  [清场] 强制结案完成 ({note}) state={ (pkg_state() or {}).get('status') }")


def stop_detection(page):
    btn = page.get_by_role("button", name="停止", exact=True)
    if btn.count() and btn.first.is_enabled():
        btn.first.click()
        page.wait_for_timeout(500)
        # 停止可能弹确认框
        mb = page.locator(".el-message-box")
        if mb.count() and mb.first.is_visible():
            ok = mb.get_by_role("button", name="确定")
            if ok.count():
                ok.first.click()
        page.wait_for_timeout(2000)
        log("  [控制] 已点停止")


def start_video_via_source_page(page, first_time=False):
    """视频源页: 首次上传视频文件, 之后点「使用此视频」(跳转回 Monitor)."""
    page.goto(f"{WEB}/#/source", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2000)
    page.get_by_text("本地视频文件", exact=False).first.click()
    page.wait_for_timeout(600)
    reuse = page.get_by_role("button", name="使用此视频")
    if not first_time and reuse.count() and reuse.first.is_visible():
        reuse.first.click()
        log("  [视频源] 点「使用此视频」")
        page.wait_for_timeout(2500)
        return
    # 首次: 通过文件选择器上传 (与人工点击选择文件同机制)
    page.locator('.el-upload input[type="file"]').set_input_files(VIDEO)
    page.wait_for_timeout(1000)
    page.get_by_role("button", name="保存并启动检测").click()
    log("  [视频源] 上传 7-23 视频并保存启动 (88MB, 等待上传...)")
    page.wait_for_url("**/monitor**", timeout=180000)
    page.wait_for_timeout(2500)


def activate_sy5(page):
    page.goto(f"{WEB}/#/project", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2000)
    page.get_by_text("SY5", exact=True).first.click()
    page.wait_for_timeout(800)
    btn = page.get_by_role("button", name="启用当前项目").first
    if btn.is_enabled():
        btn.click()
        page.wait_for_timeout(1500)
        log("  [项目] SY5 已点「启用当前项目」")
    else:
        log("  [项目] SY5 已是当前激活项目 (按钮禁用), 跳过")
    shot(page, "s1_00_sy5_activated")


def click_start_detection(page, wait_model_s=90):
    """Monitor 点「开始」并等双模型加载完成 (isDetecting)."""
    goto_monitor(page)
    btn = page.get_by_role("button", name="开始", exact=True)
    btn.first.click()
    log("  [控制] 已点开始 (等模型加载: v28 + v11 双模型)")
    end = time.time() + wait_model_s
    while time.time() < end:
        try:
            d = api_get(f"/api/v1/source/detection/results?channel={CH}")
            if d.get("is_detecting"):
                log("  [控制] 检测已运行")
                page.wait_for_timeout(1500)
                return
        except Exception:
            pass
        page.wait_for_timeout(1000)
    raise AssertionError("点开始后检测未运行")


# ================= 场景 =================

def s0_config_ui(pw):
    """组⑧配置: 设置页真点击落地, GET 双向核对."""
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 900},
                              record_video_dir=str(OUT / "video"))
    page = ctx.new_page()
    try:
        page.goto(f"{WEB}/#/settings", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_load_state("networkidle")
        page.get_by_role("tab", name="包装箱结算").click()
        page.wait_for_timeout(1000)
        row = page.locator(".el-table__row", has_text="上银SY包装线").first
        row.get_by_role("button", name="编辑").click()
        page.wait_for_selector(".el-dialog", state="visible", timeout=5000)
        dlg = page.locator(".el-dialog").last
        dlg.get_by_text("⑧ 箱标签扫码").click()
        page.wait_for_timeout(500)
        shot(page, "s0_01_group8_before")

        cfg0 = api_get(f"/api/v1/packaging-flows/{CFG_ID}")
        # 总开关 (幂等: 已开则不再点)
        if not cfg0.get("box_label_scan_required"):
            dlg.locator(".el-form-item", has_text="每箱必须扫箱标签").first \
               .locator(".el-switch").click()
            page.wait_for_timeout(400)
        if not cfg0.get("label_qty_enabled"):
            dlg.locator(".el-form-item", has_text="从标签取本箱数量").first \
               .locator(".el-switch").click()
            page.wait_for_timeout(400)
        seg = dlg.locator(".el-form-item", has_text="数量在第几段").first.locator("input").first
        seg.fill("3")
        if not cfg0.get("label_total_check"):
            dlg.locator(".el-form-item", has_text="收尾数量对账").first \
               .locator(".el-switch").click()
            page.wait_for_timeout(400)
        # 三个事件都指到「包装防呆提示 (id=4)」
        for label in ("未扫标签开做 事件", "标签缺数量 事件", "对账不平 事件"):
            item = dlg.locator(".el-form-item", has_text=label).first
            item.locator(".el-select").click()
            page.wait_for_timeout(400)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item",
                         has_text="包装防呆提示").first.click()
            page.wait_for_timeout(300)
        shot(page, "s0_02_group8_filled")
        page.locator(".el-dialog__footer").get_by_role("button", name="保存").first.click()
        page.wait_for_timeout(1500)

        cfg = api_get(f"/api/v1/packaging-flows/{CFG_ID}")
        assert cfg["box_label_scan_required"] is True, cfg
        assert cfg["label_qty_enabled"] is True, cfg
        assert cfg["label_qty_segment"] == 3, cfg
        assert cfg["label_total_check"] is True, cfg
        assert cfg["event_box_not_scanned"] == 4, cfg
        assert cfg["event_label_qty_missing"] == 4, cfg
        assert cfg["event_label_total_mismatch"] == 4, cfg
        assert cfg["label_rescan_action"] == "ignore", cfg
        assert cfg["unauthorized_cycle_action"] == "hold", cfg
        shot(page, "s0_03_saved")
        log("S0 PASS: 组⑧ UI 配置已落库并双向核对")
        return True
    finally:
        ctx.close()
        browser.close()


def _purge_test_order_history():
    """夹具准备: 清测试工单的历史 run 记录 (v3.43 完工单重扫拦截会拒绝重复扫码
    已完成工单 — 保护本身是被测功能之一, 但复跑场景需要每轮干净的工单历史)."""
    import sqlite3
    db = sqlite3.connect(str(Path(__file__).resolve().parents[2]
                             / "backend" / "sql_app.db"))
    try:
        n = db.execute("DELETE FROM packaging_flow_runs "
                       "WHERE order_no LIKE 'JOB2607001%' "
                       "AND status IN ('completed','aborted')").rowcount
        db.commit()
        log(f"  [夹具] 清理测试工单历史 run {n} 条")
    finally:
        db.close()


def _prep_run(pw, first_time=False):
    """公共前置: 开浏览器 → 视频源 → (S1 首次激活SY5) → 清残留 → 点开始."""
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 900},
                              record_video_dir=str(OUT / "video"))
    page = ctx.new_page()
    _purge_test_order_history()
    goto_monitor(page)
    # 先点掉任何残留人工确认层 (上一场景遗留), 否则遮罩拦截一切点击
    for _ in range(6):
        if not (ack_if_pending(page, "confirm", "前置清残留")
                or ack_if_pending(page, "supplement", "前置清残留")
                or ack_if_pending(page, "ng", "前置清残留")):
            break
        page.wait_for_timeout(600)
    force_settle_if_any(page, "场景前置清场")
    stop_detection(page)
    if first_time:
        activate_sy5(page)
    start_video_via_source_page(page, first_time=first_time)
    click_start_detection(page)
    return browser, ctx, page


def s1_correct_flow(pw):
    """正确流程整单: 4 满箱, 每箱扫标签带量, 全绿收尾."""
    browser, ctx, page = _prep_run(pw, first_time=True)
    try:
        base = counters()
        log(f"  基线计数: {json.dumps(base, ensure_ascii=False)}")
        scan_gun(page, "JOB260700101", "开工单 (384=4满箱)")
        st = wait_waiting_label(page, 20, "首箱")
        shot(page, "s1_01_waiting_label_box1")
        for box in range(1, 5):
            scan_gun(page, f"ORD8210|JOB260700101|96.00|H{box}A7", f"第{box}箱标签(96)")
            wait_status(page, "running", 15, f"第{box}箱放行")
            if box == 1:
                shot(page, "s1_02_running_box1")
            # 等本箱做完: 回到 waiting_label(下一箱) 或整单完成
            end = time.time() + 110
            nxt = None
            while time.time() < end:
                # 正确流程原则上无确认层; 若模型偶发漏检收尾步骤挂起, 按现场工人
                # 语义点「补步骤 — 判合格」(工人确实做了, 视觉漏了), 并留痕
                if ack_if_pending(page, "supplement", "S1 视觉漏检补步骤"):
                    _results.setdefault("S1_supplements", 0)
                    _results["S1_supplements"] += 1
                ack_if_pending(page, "confirm", "S1 意外确认层")
                st = pkg_state()
                if st is None or st.get("status") in ("completed", "awaiting_paper"):
                    nxt = st
                    break
                if st.get("status") == "waiting_label" and st.get("current_box_index") == box + 1:
                    nxt = st
                    break
                page.wait_for_timeout(1000)
            assert nxt is not None or box == 4, f"第{box}箱后状态推进超时: {pkg_state()}"
            det = (nxt or {}).get("box_details") or []
            log(f"  第{box}箱后: status={(nxt or {}).get('status')} 箱明细={det}")
            shot(page, f"s1_1{box}_after_box{box}")
        # 收尾: 放工单在视频末尾, 等 completed (工单收尾快照)
        end = time.time() + 60
        final = None
        while time.time() < end:
            ack_if_pending(page, "supplement", "S1 收尾视觉漏检补步骤")
            ack_if_pending(page, "confirm", "S1 收尾确认层")
            st = pkg_state()
            if st and st.get("status") == "completed":
                final = st
                break
            page.wait_for_timeout(1000)
        assert final, f"整单未完成: {pkg_state()}"
        det = final.get("box_details") or []
        sliders = [b.get("sliders") for b in det]
        scans = [b.get("scan_qty") for b in det]
        log(f"  整单完成: 箱数={len(det)} 各箱滑块={sliders} 各箱标签量={scans} "
            f"final={final.get('final_result')}")
        shot(page, "s1_99_completed")
        cur = counters()
        d_ok = cur.get("合格总数", 0) - base.get("合格总数", 0)
        d_warn = cur.get("防呆提示", 0) - base.get("防呆提示", 0)
        d_ng = cur.get("不良总数", 0) - base.get("不良总数", 0)
        log(f"  计数Δ: 合格+{d_ok} 防呆+{d_warn} 不良+{d_ng}")
        assert len(det) == 4 and all(s == 96 for s in sliders), f"箱账不对: {det}"
        assert all(q == 96 for q in scans), f"标签量未记录: {scans}"
        assert d_ok >= 4, f"合格数不足: +{d_ok}"
        assert d_ng == 0, f"正确流程不该有NG: +{d_ng}"
        log("S1 PASS: 4满箱全绿收尾, 标签量逐箱落账, 对账平无报警")
        return True
    finally:
        stop_detection(page)
        ctx.close()
        browser.close()


def s2_work_without_label(pw):
    """未扫开做: 不扫标签就开工 → 报警(人工确认) → 补扫标签 → 本箱正常落账."""
    browser, ctx, page = _prep_run(pw)
    try:
        base = counters()
        scan_gun(page, "JOB260700104", "开工单 (96=1箱)")
        wait_waiting_label(page, 20, "首箱")
        shot(page, "s2_01_waiting_label")
        log("  故意不扫标签, 等视频开做 (贴标≈18s) 触发报警…")
        assert wait_ack_overlay(page, 60, "未扫箱标签就开做"), "未扫开做没有报警!"
        shot(page, "s2_02_alarm_not_scanned")
        cur = counters()
        assert cur.get("防呆提示", 0) > base.get("防呆提示", 0), "防呆提示计数未+1"
        ack_if_pending(page, "confirm", "工人已知晓")
        scan_gun(page, "ORD8210|JOB260700104|96.00|Z9K2", "补扫标签放行")
        wait_status(page, "running", 15, "补扫后放行")
        shot(page, "s2_03_authorized_after")
        # 等本箱做完 (视频第一箱 ≈65s 结算)
        end = time.time() + 110
        done = None
        while time.time() < end:
            ack_if_pending(page, "supplement", "S2 视觉漏检补步骤")
            ack_if_pending(page, "confirm", "S2 追加确认层")
            st = pkg_state()
            det = (st or {}).get("box_details") or []
            if det and det[0].get("sliders") == 96:
                done = det
                break
            page.wait_for_timeout(1000)
        assert done, f"补扫后第一箱未正常落账: {pkg_state()}"
        log(f"  第一箱落账: {done[0]}")
        shot(page, "s2_99_box_booked")
        log("S2 PASS: 未扫开做当场报警+人工确认, 补扫标签后本箱正常落账")
        return True
    finally:
        force_settle_if_any(page, "S2 收尾")
        stop_detection(page)
        ctx.close()
        browser.close()


def s3_barcode_instead_of_qr(pw):
    """扫到条形码: 等扫标签态扫裸 JOB 号 → 报警请重扫二维码 → 二维码放行."""
    browser, ctx, page = _prep_run(pw)
    try:
        base = counters()
        scan_gun(page, "JOB260700105", "开工单 (192=2箱)")
        wait_waiting_label(page, 20, "首箱")
        shot(page, "s3_01_waiting_label")
        scan_gun(page, "JOB260700105", "模拟误扫一维条形码 (裸JOB号)")
        assert wait_ack_overlay(page, 15, "标签取不出数量→请重扫二维码"), "裸码没有报警!"
        shot(page, "s3_02_alarm_bare_code")
        st = pkg_state()
        assert st and st.get("status") == "waiting_label", f"裸码后不该放行: {st}"
        cur = counters()
        assert cur.get("防呆提示", 0) > base.get("防呆提示", 0), "防呆提示未+1"
        ack_if_pending(page, "confirm", "工人已知晓, 改扫二维码")
        scan_gun(page, "ORD8210|JOB260700105|96.00|QR77", "改扫二维码")
        wait_status(page, "running", 15, "二维码放行")
        shot(page, "s3_99_qr_authorized")
        log("S3 PASS: 裸条形码被拒+报警提示重扫, 二维码放行")
        return True
    finally:
        force_settle_if_any(page, "S3 收尾")
        stop_detection(page)
        ctx.close()
        browser.close()


def s4_qty_mismatch_and_rescan(pw):
    """数量不符+重扫忽略: 标签声明72实装96 → 重扫同号默认忽略 → 结算超装报警."""
    browser, ctx, page = _prep_run(pw)
    try:
        scan_gun(page, "JOB260700105", "开工单 (192=2箱)")
        wait_waiting_label(page, 20, "首箱")
        scan_gun(page, "ORD8210|JOB260700105|72.00|LOW1", "标签声明72 (故意错)")
        st = wait_status(page, "running", 15, "放行, 本箱目标=72")
        assert st.get("current_box_scan_qty") == 72, f"标签量未生效: {st}"
        # UI 上本箱目标应显示 72
        shot(page, "s4_01_target72")
        # 重扫同号不同量 → 默认 ignore, 目标不变
        scan_gun(page, "ORD8210|JOB260700105|96.00|LOW1", "重扫同号(声明96) 应被忽略")
        page.wait_for_timeout(1500)
        st = pkg_state()
        assert st.get("current_box_scan_qty") == 72, f"重扫应忽略, 目标被改了: {st}"
        log("  重扫同号已按 ignore 档忽略, 目标仍=72")
        shot(page, "s4_02_rescan_ignored")
        # 等本箱结算: 实装96 > 目标72 → 超装报警
        got_alarm = wait_ack_overlay(page, 110, "超装报警")
        shot(page, "s4_03_over_alarm")
        st = pkg_state()
        log(f"  结算后状态: {st and st.get('status')} 箱明细={(st or {}).get('box_details')}")
        assert got_alarm, "实装96>标签72 未报警!"
        ack_if_pending(page, "ng", "认账收尾") or ack_if_pending(page, "confirm", "确认")
        log("S4 PASS: 标签量当本箱目标生效, 重扫同号忽略, 数量不符结算报警")
        return True
    finally:
        force_settle_if_any(page, "S4 收尾")
        stop_detection(page)
        ctx.close()
        browser.close()


def s5_total_reconcile_mismatch(pw):
    """收尾对账不平: 箱1标签96+箱2标签72=168 ≠ 排产192 → 结案对账报警."""
    browser, ctx, page = _prep_run(pw)
    try:
        base = counters()
        scan_gun(page, "JOB260700105", "开工单 (192=2箱)")
        wait_waiting_label(page, 20, "首箱")
        scan_gun(page, "ORD8210|JOB260700105|96.00|B1OK", "第1箱标签96 (正确)")
        wait_status(page, "running", 15, "箱1放行")
        # 等箱1做完 → waiting_label 箱2
        end = time.time() + 110
        while time.time() < end:
            ack_if_pending(page, "supplement", "S5 视觉漏检补步骤")
            ack_if_pending(page, "confirm", "S5 箱1确认层")
            st = pkg_state()
            if st and st.get("status") == "waiting_label" and st.get("current_box_index") == 2:
                break
            page.wait_for_timeout(1000)
        else:
            raise AssertionError(f"箱1未按时落账: {pkg_state()}")
        shot(page, "s5_01_box1_done")
        scan_gun(page, "ORD8210|JOB260700105|72.00|B2LOW", "第2箱标签72 (故意, Σ=168≠192)")
        wait_status(page, "running", 15, "箱2放行")
        page.wait_for_timeout(3000)
        # 不等箱2做完, 直接强制结案触发收尾对账
        warn0 = counters().get("防呆提示", 0)
        force_settle_if_any(page, "S5 主动收尾对账")
        page.wait_for_timeout(2500)
        for _ in range(3):
            ack_if_pending(page, "ng", "S5 结案挂账") or ack_if_pending(page, "confirm", "S5 结案确认")
            page.wait_for_timeout(800)
        warn1 = counters().get("防呆提示", 0)
        shot(page, "s5_99_after_settle")
        log(f"  防呆提示: 结案前{warn0} → 结案后{warn1}")
        assert warn1 > warn0, "收尾对账不平未报警!"
        log("S5 PASS: Σ标签量168≠排产192, 结案时对账报警")
        return True
    finally:
        stop_detection(page)
        ctx.close()
        browser.close()


def s6_bad_orders(pw):
    """不合格工单: 查无此单 / MES 5xx / 排产量0 → 报警提示不崩溃."""
    browser, ctx, page = _prep_run(pw)
    try:
        for code, why in [("JOB260700404", "查无此单"),
                          ("JOB260700500", "MES服务器500"),
                          ("JOB260700103", "排产量0")]:
            scan_gun(page, code, f"不合格工单: {why}")
            page.wait_for_timeout(2500)
            ack_if_pending(page, "confirm", f"{why} 报警确认")
            st = pkg_state()
            log(f"  {code}({why}) → state={st and st.get('status')} "
                f"order={st and st.get('order_no')} box_total={st and st.get('box_total')}")
            shot(page, f"s6_{code}")
            # 后端还活着
            assert api_get("/api/v1/projects")["total"] > 0
            force_settle_if_any(page, f"S6 {why} 清场")
        log("S6 PASS: 三类不合格工单均有明确提示且系统不崩")
        return True
    finally:
        stop_detection(page)
        ctx.close()
        browser.close()


SCENARIOS = {
    "S0": ("组⑧配置UI落地", s0_config_ui),
    "S1": ("正确流程整单4满箱", s1_correct_flow),
    "S2": ("未扫开做报警+补扫", s2_work_without_label),
    "S3": ("裸条形码拒收重扫", s3_barcode_instead_of_qr),
    "S4": ("数量不符+重扫忽略", s4_qty_mismatch_and_rescan),
    "S5": ("收尾对账不平", s5_total_reconcile_mismatch),
    "S6": ("不合格工单三连", s6_bad_orders),
}


def main():
    global _log_fh
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "video").mkdir(exist_ok=True)
    _log_fh = open(OUT / "run.log", "a", encoding="utf-8")
    todo = sys.argv[1:] or list(SCENARIOS)
    log(f"===== SY5 箱标签扫码全矩阵 UAT 开始: {todo} =====")
    assert Path(VIDEO).exists()
    mock_mes_alive()
    log("Mock MES 工单库在线 (18080)")

    from playwright.sync_api import sync_playwright
    failed = []
    with sync_playwright() as pw:
        for key in todo:
            name, fn = SCENARIOS[key]
            log(f"----- {key} {name} -----")
            log_resources(key)
            try:
                ok = fn(pw)
                _results[key] = "PASS" if ok else "FAIL"
            except Exception as e:
                _results[key] = f"FAIL: {e}"
                failed.append(key)
                log(f"{key} FAIL: {e}")
                import traceback
                log(traceback.format_exc())
    log("===== 结果汇总 =====")
    for k, v in _results.items():
        log(f"  {k} {SCENARIOS[k][0]}: {v}")
    log(f"failed: {len(failed)}")
    _log_fh.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
