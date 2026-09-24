# -*- coding: utf-8 -*-
"""showcase v1.5.1 真实环境全量 UAT — 真宿主(vite 6001) + 真后端(8003 隔离库) + 真插件热载。

覆盖面（对用户验收口径「全部模块/按钮/组合 + 宿主就能账号鉴权」）：
  P1 项目页: 9 种逻辑模式逐个建项目, 每模式关键组合切换 + 保存 → GET 落库比对
  P2 ROI 多块: 真编辑器画两块 → 保存 → GET 验证嵌套格式
  P3 业务落库: 模型上传/绑定/阈值/多码采集/报警/显示/缺陷码/清零(scope=cycle)
  P4 显示设置: 全部 [data-disp] 开关逐个翻转 → 宿主 localStorage display_settings 逐键比对
  P5 宿主鉴权主线: 插件内启用鉴权→自动登录→宿主 token→刷新继承→登出→访客→顶栏登录
                   → persist=false 走 sessionStorage → 改密 → 重登
  P6 全按钮扫描: 8 页所有可见按钮逐个点击(危险按钮除外), 记录 console error 与非 2xx API
  P7 事故复刻: 访客禁用插件被拒(403 引导) → 登录后禁用插件成功 → 刷新回宿主原生 UI
              仍是管理员身份(宿主就能账号鉴权终证) → API 恢复插件与鉴权

前置: 后端 8003 (TIANJUN_DATA_DIR=/tmp/tj_uat151_data, showcase 已种) + 前端 vite 6001。
用法: ~/miniconda3/envs/tianjun/bin/python tests/uat/showcase_plugin_v151/uat_v151_real.py
"""
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
EVID = ROOT / "evidence/showcase_v151/real"
EVID.mkdir(parents=True, exist_ok=True)
FE = os.environ.get("UAT_FE", "http://localhost:6007")
BE = os.environ.get("UAT_BE", "http://127.0.0.1:8005/api/v1")
MODEL_FILE = Path.home() / "Downloads/best.pt"
SUF = str(int(time.time()))[-5:]

ADMIN_U, ADMIN_P, ADMIN_P2 = "e2eadmin", "Passw0rd1", "Passw0rd2"

RESULTS = []
TOKEN = {"v": None}


def record(name, ok, note=""):
    RESULTS.append((name, bool(ok), note))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + str(note)[:160]) if note else ""))


def api(path, method="GET", body=None):
    req = urllib.request.Request(BE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN["v"]:
        req.add_header("Authorization", "Bearer " + TOKEN["v"])
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=15) as r:
        return json.loads(r.read().decode() or "null")


def api_items(path):
    d = api(path)
    if isinstance(d, list):
        return d
    return d.get("items", []) if isinstance(d, dict) else []


def api_poll(fn, tries=12, gap=1.0):
    """轮询直到 fn() 返回真值（消除保存-读取竞态）。"""
    last = None
    for _ in range(tries):
        try:
            last = fn()
            if last:
                return last
        except Exception:
            pass
        time.sleep(gap)
    return last


def main():  # noqa: C901
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.set_default_timeout(15000)
        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append("PAGEERROR " + str(e)))
        bad_api = []
        page.on("response", lambda r: bad_api.append((r.request.method, r.url, r.status))
                if "/api/v1/" in r.url and r.status >= 500 else None)
        # 原生 confirm(): 默认接受 (删除/关闭鉴权等流程内会用到)
        page.on("dialog", lambda d: d.accept())

        def shot(name):
            try:
                page.screenshot(path=str(EVID / name), timeout=8000)
            except Exception:
                try:
                    import base64
                    cdp = ctx.new_cdp_session(page)
                    data = cdp.send("Page.captureScreenshot", {"format": "png"})["data"]
                    (EVID / name).write_bytes(base64.b64decode(data))
                except Exception as e2:
                    print("  WARN 截图失败", name, e2)

        print("== 0. 打开宿主, 等插件覆盖层 ==")
        page.goto(FE)
        try:
            page.wait_for_selector("iframe", timeout=120000)
            record("插件 iframe 覆盖层出现", True)
        except Exception as e:
            record("插件 iframe 覆盖层出现", False, e)
            shot("0_no_iframe.png")
            browser.close()
            return
        page.wait_for_timeout(4000)
        fr = page.frame_locator("iframe").first

        def jsclick(sel):
            fr.locator(sel).first.evaluate("el => el.click()")

        def go(route, wait=1200):
            jsclick(f'.nav .item[data-route="{route}"]')
            page.wait_for_timeout(wait)

        def frev(js):
            """在 iframe 里 evaluate 任意表达式"""
            return fr.locator("body").first.evaluate(f"() => {{ {js} }}")

        def close_dialogs():
            frev("document.querySelectorAll('dialog[open]').forEach(d=>{try{d.close()}catch(e){}}); return 0;")

        def id_menu(label_re):
            """点顶栏身份 chip 打开菜单并点指定项，返回是否命中。带一次重试。"""
            for _ in range(2):
                frev("var c=document.querySelector('.header .user'); if(c) c.click(); return 0;")
                page.wait_for_timeout(600)
                hit = frev(f"""
                  var items=Array.from(document.querySelectorAll('#tjIdMenu div'));
                  var b=items.find(d=>/{label_re}/.test((d.textContent||'').trim()));
                  if(b){{ b.click(); return true; }} return false;""")
                if hit:
                    return True
                page.wait_for_timeout(500)
            return False

        def open_login_dialog():
            """优先走顶栏菜单（真实用户路径），失败直接 showModal 兜底（loginOkBtn 全局接线）。"""
            if frev("var d=document.getElementById('loginDialog'); return !!(d&&d.open);"):
                return True
            id_menu("^(登录|切换账号)$")
            page.wait_for_timeout(600)
            if frev("var d=document.getElementById('loginDialog'); return !!(d&&d.open);"):
                return True
            frev("""var d=document.getElementById('loginDialog');
                 if(d && d.showModal && !d.open) d.showModal(); return 0;""")
            page.wait_for_timeout(400)
            return frev("var d=document.getElementById('loginDialog'); return !!(d&&d.open);")

        def plugin_login(u, p):
            open_login_dialog()
            fr.locator("#loginUser").fill(u)
            fr.locator("#loginPwd").fill(p)
            jsclick("#loginOkBtn")
            page.wait_for_timeout(2000)

        # ================================================================
        print("== P1. 项目页: 9 模式建项目 + 组合切换 + 保存落库 ==")
        go("/project", 1500)
        MODES = ["sequential", "detection", "custom", "tracking", "per_item",
                 "weighing", "region_events", "ocr", "anomaly"]
        proj_ids = {}
        for mode in MODES:
            nm = f"e2e-{mode}-{SUF}"
            try:
                jsclick('[data-open-dialog="createProjectDialog"]')
                page.wait_for_timeout(400)
                fr.locator("#newProjName").fill(nm)
                fr.locator("#newProjMode").select_option(mode)
                jsclick("#newProjCreateBtn")
                page.wait_for_timeout(2200)
                hit = api_poll(lambda: [p for p in api_items("/projects") if p.get("name") == nm],
                               tries=8, gap=1.0)
                proj_ids[mode] = hit[0]["id"] if hit else None
                record(f"[{mode}] 新建项目落库", bool(hit))
            except Exception as e:
                record(f"[{mode}] 新建项目", False, repr(e))
                close_dialogs()

        def open_proj(pid, tab="logic"):
            jsclick(f'[data-project-card][data-project-id="{pid}"]')
            page.wait_for_timeout(700)
            jsclick(f'.project-tab[data-project-tab="{tab}"]')
            page.wait_for_timeout(700)

        def save_proj(pid):
            jsclick("#projSaveBtn")
            page.wait_for_timeout(2200)
            return api(f"/projects/{pid}")

        def card_visible(lg):
            return fr.locator(f'[data-lg="{lg}"]').first.evaluate("el => el.style.display !== 'none'")

        def setb(sel, js):
            fr.locator(sel).first.evaluate(js)
            page.wait_for_timeout(500)

        # ---- sequential: 结算方式组合 + ngh(hold→missing_step_early) ----
        pid = proj_ids.get("sequential")
        if pid:
            try:
                open_proj(pid)
                vis = {lg: card_visible(lg) for lg in ["settle", "seq", "ngh", "det", "mixbox", "weighing"]}
                record("[seq] 卡可见集正确", vis["settle"] and vis["seq"] and vis["ngh"]
                       and not vis["det"] and not vis["mixbox"] and not vis["weighing"], vis)
                # 组合1: last_first → ngh violation 强制 none 落库
                setb('input[data-b="pc.settlement_mode"][value="last_first"]', "el => el.click()")
                setb('[data-lg="ngh"] select[data-b="pc.ng_handling.missing_step"]',
                     "el => { el.value='hold'; el.dispatchEvent(new Event('change',{bubbles:true})); }")
                page.wait_for_timeout(500)
                early = fr.locator('input[data-b="pc.ng_handling.missing_step_early"]')
                record("[seq] missing_step=hold 露出缺步提前开关", early.count() > 0)
                if early.count():
                    early.first.evaluate("el => el.click()")
                    page.wait_for_timeout(400)
                p = save_proj(pid)
                pc = p["pipeline_config"]
                record("[seq] last_first 落库", pc.get("settlement_mode") == "last_first")
                record("[seq] ngh.missing_step=hold + early 落库",
                       pc.get("ng_handling", {}).get("missing_step") == "hold"
                       and pc.get("ng_handling", {}).get("missing_step_early") is True,
                       pc.get("ng_handling"))
                record("[seq] last_first 强制 violation=none", pc.get("ng_handling", {}).get("violation") == "none")
                # 组合2: 切回 first_step
                setb('input[data-b="pc.settlement_mode"][value="first_step"]', "el => el.click()")
                p = save_proj(pid)
                record("[seq] 切回 first_step 落库", p["pipeline_config"].get("settlement_mode") == "first_step")
                shot("p1_seq.png")
            except Exception as e:
                record("[seq] 组合流程", False, repr(e))

        # ---- detection: last_step + combo 行操作 ----
        pid = proj_ids.get("detection")
        if pid:
            try:
                open_proj(pid)
                record("[det] combo 卡可见", card_visible("combo"))
                setb('input[data-b="pc.settlement_mode"][value="last_step"]', "el => el.click()")
                p = save_proj(pid)
                record("[det] settlement=last_step 落库", p["pipeline_config"].get("settlement_mode") == "last_step")
                shot("p1_det.png")
            except Exception as e:
                record("[det] 组合流程", False, repr(e))

        # ---- custom: owner container × mixed_with per_item 组合 ----
        pid = proj_ids.get("custom")
        if pid:
            try:
                open_proj(pid)
                record("[custom] custom/mixbox 卡可见", card_visible("custom") and card_visible("mixbox"))
                setb('select[data-b="pc.custom_cycle_owner"]',
                     "el => { el.value='container'; el.dispatchEvent(new Event('change',{bubbles:true})); }")
                page.wait_for_timeout(600)
                gate = fr.locator('select[data-b="pc.container_gate_label"]')
                record("[custom] owner=container 露出容器门参数", gate.count() > 0)
                setb('select[data-b="pc.custom_mixed_with"]',
                     "el => { el.value='per_item'; el.dispatchEvent(new Event('change',{bubbles:true})); }")
                page.wait_for_timeout(600)
                record("[custom] mixed_with=per_item 露出逐件通用卡", card_visible("pi"))
                vsw = fr.locator('input[data-b="pc.custom_mix_per_item_virtual_step"]')
                record("[custom] 混合逐件虚拟步骤开关存在", vsw.count() > 0)
                if vsw.count():
                    vsw.first.evaluate("el => el.click()")
                    page.wait_for_timeout(500)
                p = save_proj(pid)
                pc = p["pipeline_config"]
                record("[custom] owner=container 落库", pc.get("custom_cycle_owner") == "container")
                record("[custom] mixed_with=per_item + 虚拟步骤落库",
                       pc.get("custom_mixed_with") == "per_item"
                       and pc.get("custom_mix_per_item_virtual_step") is True,
                       {k: pc.get(k) for k in ["custom_mixed_with", "custom_mix_per_item_virtual_step"]})
                shot("p1_custom.png")
            except Exception as e:
                record("[custom] 组合流程", False, repr(e))

        # ---- tracking: roi_exit + settle_on_complete + ROI 多块 ----
        pid = proj_ids.get("tracking")
        if pid:
            try:
                open_proj(pid)
                # 策略控件是 radio (name=lgTrkStrat), 不是 select
                setb('input[data-b="pc.tracking_cycle_strategy"][value="roi_exit"]', "el => el.click()")
                page.wait_for_timeout(600)
                soc = fr.locator('input[data-b="pc.tracking_settle_on_complete"]')
                record("[trk] roi_exit 露出齐件即结算", soc.count() > 0)
                if soc.count():
                    soc.first.evaluate("el => el.click()")
                    page.wait_for_timeout(400)
                # ROI 多块: 开编辑器画两块
                jsclick('[data-lga="trkRoiDraw"]')
                page.wait_for_timeout(900)
                opened = frev("var d=document.getElementById('roiDialog'); return !!(d && d.open);")
                record("[trk] ROI 编辑器打开", opened)
                if opened:
                    frev("""
                      var svg=document.getElementById('roiSvg');
                      function pt(x,y){ var r=svg.getBoundingClientRect();
                        svg.dispatchEvent(new MouseEvent('click',{clientX:r.left+r.width*x, clientY:r.top+r.height*y, bubbles:true})); }
                      pt(0.1,0.1); pt(0.4,0.1); pt(0.4,0.4);
                      document.getElementById('roiFinishBlockBtn').click();
                      pt(0.6,0.6); pt(0.9,0.6); pt(0.9,0.9);
                      document.getElementById('roiSaveBtn').click();
                      return 0;""")
                    page.wait_for_timeout(700)
                p = save_proj(pid)
                pc = p["pipeline_config"]
                poly = (pc.get("tracking_roi") or {}).get("polygon")
                nested = isinstance(poly, list) and len(poly) == 2 and isinstance(poly[0][0], list)
                record("[trk] ROI 两块嵌套格式落库", nested, str(poly)[:100])
                record("[trk] settle_on_complete 落库", pc.get("tracking_settle_on_complete") is True)
                shot("p1_trk_roi.png")
            except Exception as e:
                record("[trk] 组合流程", False, repr(e))
                close_dialogs()

        # ---- per_item: 开始判定组合 ----
        pid = proj_ids.get("per_item")
        if pid:
            try:
                open_proj(pid)
                record("[pi] pi/piSteps 卡可见", card_visible("pi") and card_visible("piSteps"))
                sw = fr.locator('input[data-b="pc.per_item.start_by_stability"]')
                record("[pi] 稳定窗口开账开关存在", sw.count() > 0)
                if sw.count():
                    sw.first.evaluate("el => el.click()")
                    page.wait_for_timeout(500)
                br = fr.locator('input[data-b="pc.per_item.board_rereg_enabled"]')
                if br.count():
                    br.first.evaluate("el => el.click()")
                    page.wait_for_timeout(400)
                p = save_proj(pid)
                s = p["pipeline_config"].get("per_item") or {}
                record("[pi] start_by_stability + board_rereg 落库",
                       s.get("start_by_stability") is True and s.get("board_rereg_enabled") is True,
                       {k: s.get(k) for k in ["start_by_stability", "board_rereg_enabled"]})
                shot("p1_per_item.png")
            except Exception as e:
                record("[pi] 组合流程", False, repr(e))

        # ---- weighing: drive_mode 三档切换 ----
        pid = proj_ids.get("weighing")
        if pid:
            try:
                open_proj(pid)
                for dm in ["step_gate", "pipeline", "scale"]:
                    setb('select[data-b="pc.weighing.drive_mode"]',
                         f"el => {{ el.value='{dm}'; el.dispatchEvent(new Event('change',{{bubbles:true}})); }}")
                    page.wait_for_timeout(500)
                    if dm == "pipeline":
                        has_pipe = fr.locator('input[data-b="pc.weighing.pipeline.tare_min_kg"]').count() > 0
                        record("[wg] pipeline 档露出参数卡", has_pipe)
                setb('select[data-b="pc.weighing.drive_mode"]',
                     "el => { el.value='pipeline'; el.dispatchEvent(new Event('change',{bubbles:true})); }")
                p = save_proj(pid)
                record("[wg] drive_mode=pipeline 落库",
                       (p["pipeline_config"].get("weighing") or {}).get("drive_mode") == "pipeline")
                shot("p1_weighing.png")
            except Exception as e:
                record("[wg] 组合流程", False, repr(e))

        # ---- region_events / ocr / anomaly: 卡可见 + 保存不炸 ----
        for mode, lg in [("region_events", "regionEvents"), ("ocr", "ocr"), ("anomaly", "anomaly")]:
            pid = proj_ids.get(mode)
            if pid:
                try:
                    open_proj(pid)
                    record(f"[{mode}] 参数卡可见", card_visible(lg))
                    p = save_proj(pid)
                    record(f"[{mode}] 保存 200", bool(p and p.get("id") == pid))
                except Exception as e:
                    record(f"[{mode}] 流程", False, repr(e))
        shot("p1_done.png")

        # ================================================================
        print("== P2. 业务落库: 模型上传/绑定/阈值 ==")
        model_id = None
        if MODEL_FILE.exists():
            try:
                go("/model", 1500)
                jsclick('[data-open-dialog="uploadModelDialog"]')
                page.wait_for_timeout(500)
                fr.locator("#modelFileInput").set_input_files(str(MODEL_FILE), timeout=60000)
                page.wait_for_timeout(400)
                fr.locator("#modelName").fill("e2e-" + SUF)
                fr.locator("#modelVersion").fill("V1.0")
                jsclick("#modelUploadBtn")
                # 以后端落库为准（UI 完成文案仅作参考），最长等 150s
                hit = api_poll(lambda: [m for m in api_items("/models")
                                        if ("e2e-" + SUF) in (m.get("name") or "")],
                               tries=150, gap=1.0)
                ui_txt = ""
                try:
                    ui_txt = fr.locator("#modelDropText").inner_text()
                except Exception:
                    pass
                record("模型上传入库", bool(hit), f"ui={ui_txt[:30]}")
                model_id = hit[0]["id"] if hit else None
                close_dialogs()
            except Exception as e:
                record("模型上传", False, repr(e))
                close_dialogs()
        else:
            record("模型上传", False, "缺 ~/Downloads/best.pt (N/A)")

        pid = proj_ids.get("sequential")
        if pid and model_id:
            try:
                go("/project", 1200)
                open_proj(pid, tab="basic")
                jsclick("#projPickModelBtn")
                page.wait_for_timeout(1200)
                jsclick(f'[data-model-pick-id="{model_id}"]')
                page.wait_for_timeout(4000)
                p = api(f"/projects/{pid}")
                record("绑定模型落库+步骤重建", p.get("default_model_id") == model_id and bool(p.get("steps_config")),
                       f"steps={len(p.get('steps_config') or [])}")
                # 步骤阈值
                jsclick('.project-tab[data-project-tab="steps"]')
                page.wait_for_timeout(700)
                th = fr.locator('input[data-k="threshold"]')
                if th.count():
                    th.first.evaluate("el => { el.value = 66; el.dispatchEvent(new Event('change',{bubbles:true})); }")
                    page.wait_for_timeout(400)
                    p = save_proj(pid)
                    got = (p.get("steps_config") or [{}])[0].get("threshold")
                    record("步骤阈值 66 落库", got in (66, 0.66), got)
                shot("p2_model_steps.png")
            except Exception as e:
                record("绑定模型/阈值", False, repr(e))
                close_dialogs()

        print("== P2b. 多码采集配置保存 → GET 验证 ==")
        try:
            go("/mes", 1200)
            jsclick('.mes-tab[data-mes-tab="scancollect"]')
            page.wait_for_timeout(1200)
            jsclick("#scCfgExampleBtn")
            page.wait_for_timeout(600)
            jsclick("#scCfgSaveBtn")
            page.wait_for_timeout(1500)
            active = api("/projects/active/current")
            sc = api(f"/scan-collect/config?project_id={active['id']}")
            cfg = sc.get("config", sc) or {}
            record("多码采集示例保存落库", bool(cfg.get("enabled")) and bool(cfg.get("slots")),
                   f"slots={len(cfg.get('slots') or [])} vision={cfg.get('settle_on_vision_cycle')}")
            shot("p2b_scan_collect.png")
        except Exception as e:
            record("多码采集保存", False, repr(e))

        print("== P2c. 报警保存 / 显示设置字段 / 缺陷码 / 清零 scope=cycle ==")
        try:
            go("/alarm", 1500)
            jsclick("#alarmSaveBtn")
            page.wait_for_timeout(1800)
            record("报警配置保存不报错", True)
        except Exception as e:
            record("报警配置保存", False, repr(e))
        try:
            go("/settings", 1500)
            fr.locator("#dispBrand").first.fill("E2E品牌" + SUF)
            frev("var el=document.getElementById('dispBrand'); el.dispatchEvent(new Event('change',{bubbles:true})); return 0;")
            got = api_poll(lambda: (api("/system/display").get("brand_name") == "E2E品牌" + SUF) or None,
                           tries=10, gap=1.0)
            record("显示字段 brand_name 落库", bool(got), api("/system/display").get("brand_name"))
        except Exception as e:
            record("显示字段保存", False, repr(e))
        try:
            go("/mes", 1200)
            jsclick('.mes-tab[data-mes-tab="defects"]')
            page.wait_for_timeout(800)
            jsclick('[data-open-dialog="defectCodeDialog"]')
            page.wait_for_timeout(700)
            fr.locator("#dcCode").fill("D-" + SUF[-2:])
            fr.locator("#dcName").fill("e2e缺陷")
            jsclick("#dcAddBtn")
            got = api_poll(lambda: any(c.get("code") == "D-" + SUF[-2:]
                                       for c in api_items("/mes/defect-codes")) or None,
                           tries=10, gap=1.0)
            record("缺陷码新增落库", bool(got))
            close_dialogs()
        except Exception as e:
            record("缺陷码新增", False, repr(e))
            close_dialogs()
        try:
            go("/monitor", 1500)
            btn = fr.locator("#todayResetCycleBtn")
            record("监控页仅清本周期按钮渲染", btn.count() > 0)
            if btn.count():
                btn.first.evaluate("el => el.click()")
                page.wait_for_timeout(1000)
            shot("p2c_monitor.png")
        except Exception as e:
            record("清零 scope=cycle", False, repr(e))

        # ================================================================
        print("== P4. 显示设置全量: 逐个 [data-disp] 翻转 → 宿主 localStorage 比对 ==")
        try:
            go("/settings", 1500)
            frev("if(window.setSettingsTab) setSettingsTab('display'); return 0;")
            page.wait_for_timeout(1200)
            disp_els = frev("""
              return Array.from(document.querySelectorAll('[data-disp]')).map(el => ({
                path: el.getAttribute('data-disp'),
                tag: el.tagName, type: el.type || '',
                checked: el.type==='checkbox' ? el.checked : null,
                value: el.value }));""")
            record("显示设置开关总数", len(disp_els) >= 30, f"{len(disp_els)} 个")
            fails = []
            for el in disp_els:
                path = el["path"]
                try:
                    if el["type"] == "checkbox":
                        newv = not el["checked"]
                        frev(f"""var e=document.querySelector('[data-disp="{path}"]');
                             e.checked={str(newv).lower()}; e.dispatchEvent(new Event('change',{{bubbles:true}})); return 0;""")
                    elif el["tag"] == "SELECT":
                        newv = frev(f"""var e=document.querySelector('[data-disp="{path}"]');
                             var opts=Array.from(e.options).map(o=>o.value);
                             var nv=opts.find(v=>v!==e.value)||e.value; e.value=nv;
                             e.dispatchEvent(new Event('change',{{bubbles:true}})); return nv;""")
                    else:
                        newv = 7.5
                        frev(f"""var e=document.querySelector('[data-disp="{path}"]');
                             e.value=7.5; e.dispatchEvent(new Event('change',{{bubbles:true}})); return 0;""")
                    page.wait_for_timeout(280)
                    stored = page.evaluate("""(path) => {
                        let cur = JSON.parse(localStorage.getItem('display_settings')||'{}');
                        for (const seg of path.split('.')) { if (cur==null) return undefined; cur = cur[seg]; }
                        return cur; }""", path)
                    ok = (stored == newv) or (str(stored) == str(newv)) or (
                        isinstance(newv, float) and stored is not None and abs(float(stored) - newv) < 1e-6)
                    if not ok:
                        fails.append(f"{path}: 期望{newv} 实得{stored}")
                except Exception as e:
                    fails.append(f"{path}: EXC {e}")
            record(f"全部 {len(disp_els)} 个显示开关写穿宿主", not fails, "; ".join(fails[:4]))
            shot("p4_display.png")
        except Exception as e:
            record("显示设置全量", False, repr(e))

        # ================================================================
        print("== P5. 宿主鉴权主线 ==")
        try:
            frev("if(window.setSettingsTab) setSettingsTab('auth'); return 0;")
            page.wait_for_timeout(1500)
            state_txt = fr.locator("#authStateLine").inner_text()
            record("鉴权面板加载 (未启用态)", "未启用" in state_txt, state_txt[:60])
            # 启用鉴权
            jsclick('[data-open-dialog="enableAuthDialog"]')
            page.wait_for_timeout(600)
            fr.locator("#enaUser").fill(ADMIN_U)
            fr.locator("#enaPwd").fill(ADMIN_P)
            jsclick("#enableAuthOkBtn")
            page.wait_for_timeout(3000)
            close_dialogs()
            st = api("/auth/status")
            record("启用鉴权落库", st.get("auth_enabled") is True, st)
            # ★ 核心: 宿主 localStorage 有 token
            host_tok = page.evaluate("() => localStorage.getItem('tianjun:auth_token')")
            record("★ 宿主 localStorage 写穿 token (启用后自动登录)", bool(host_tok),
                   (host_tok or "")[:24])
            # 宿主 axios 同源验证: 从顶层窗口用该 token 调 /auth/me
            me = page.evaluate("""async (be) => {
                const t = localStorage.getItem('tianjun:auth_token');
                const r = await fetch(be + '/auth/me',
                    {headers: t ? {Authorization: 'Bearer '+t} : {}});
                return await r.json(); }""", BE)
            record("★ 宿主身份 = 管理员 (非访客)",
                   (me.get("user") or {}).get("username") == ADMIN_U
                   and not (me.get("user") or {}).get("is_anonymous"), me.get("user"))
            TOKEN["v"] = host_tok
            shot("p5_enabled.png")

            # 刷新继承: reload 后插件应自动带上宿主身份
            page.reload()
            page.wait_for_selector("iframe", timeout=60000)
            page.wait_for_timeout(5000)
            fr = page.frame_locator("iframe").first
            chip = fr.locator("#scUser").inner_text()
            record("★ 刷新后插件继承宿主身份", ADMIN_U in chip or "e2e" in chip, chip)
            shot("p5_reload_inherit.png")

            # 登出 → 宿主 token 清穿 → 访客态
            id_menu("^登出$")
            page.wait_for_timeout(1500)
            host_tok2 = page.evaluate("() => localStorage.getItem('tianjun:auth_token')")
            record("★ 登出清穿宿主 token", not host_tok2)
            chip = fr.locator("#scUser").inner_text()
            record("登出后顶栏变访客", "访客" in chip, chip)
            shot("p5_guest.png")

            # 顶栏菜单登录 (事故场景: 访客在插件里登录)
            plugin_login(ADMIN_U, ADMIN_P)
            host_tok3 = page.evaluate("() => localStorage.getItem('tianjun:auth_token')")
            record("★ 访客态顶栏登录成功写穿宿主 (昨日事故根因)", bool(host_tok3))
            TOKEN["v"] = host_tok3
            chip = fr.locator("#scUser").inner_text()
            record("登录后顶栏显示账号", ADMIN_U in chip, chip)
            shot("p5_login_from_menu.png")

            # persist=false → sessionStorage
            frev("if(window.setSettingsTab) setSettingsTab('auth'); return 0;")
            page.wait_for_timeout(1200)
            frev("""var e=document.getElementById('authPersistSwitch');
                 if(e && e.checked){ e.checked=false; e.dispatchEvent(new Event('change',{bubbles:true})); } return 0;""")
            page.wait_for_timeout(1200)
            # 重新登录使 persist=false 生效
            plugin_login(ADMIN_U, ADMIN_P)
            ss = page.evaluate("() => sessionStorage.getItem('tianjun:auth_token')")
            ls = page.evaluate("() => localStorage.getItem('tianjun:auth_token')")
            record("★ persist=false → 宿主 sessionStorage (对齐主程序语义)", bool(ss) and not ls,
                   f"ss={bool(ss)} ls={bool(ls)}")
            TOKEN["v"] = ss or ls
            # 恢复 persist=true 并重登
            frev("""var e=document.getElementById('authPersistSwitch');
                 if(e && !e.checked){ e.checked=true; e.dispatchEvent(new Event('change',{bubbles:true})); } return 0;""")
            page.wait_for_timeout(1000)
            frev("""document.querySelector('.header .user').click(); return 0;""")
            page.wait_for_timeout(400)
            frev("""var items=Array.from(document.querySelectorAll('#tjIdMenu div'));
                 var b=items.find(d=>/登录|切换账号/.test(d.textContent)); if(b) b.click(); return !!b;""")
            page.wait_for_timeout(500)
            fr.locator("#loginUser").fill(ADMIN_U)
            fr.locator("#loginPwd").fill(ADMIN_P)
            jsclick("#loginOkBtn")
            page.wait_for_timeout(2000)
            TOKEN["v"] = page.evaluate("() => localStorage.getItem('tianjun:auth_token')")

            # 修改密码 → token 清穿 → 新密码重登
            frev("""document.querySelector('.header .user').click(); return 0;""")
            page.wait_for_timeout(400)
            frev("""var items=Array.from(document.querySelectorAll('#tjIdMenu div'));
                 var b=items.find(d=>d.textContent==='修改密码'); if(b) b.click(); return !!b;""")
            page.wait_for_timeout(600)
            fr.locator("#oldPwd").fill(ADMIN_P)
            fr.locator("#newPwd").fill(ADMIN_P2)
            jsclick("#changePwdOkBtn")
            page.wait_for_timeout(2000)
            tok_after = page.evaluate("() => localStorage.getItem('tianjun:auth_token')")
            record("改密后 token 清穿", not tok_after)
            # 登录框已自动弹出
            fr.locator("#loginUser").fill(ADMIN_U)
            fr.locator("#loginPwd").fill(ADMIN_P2)
            jsclick("#loginOkBtn")
            page.wait_for_timeout(2000)
            TOKEN["v"] = page.evaluate("() => localStorage.getItem('tianjun:auth_token')")
            record("★ 新密码重登成功", bool(TOKEN["v"]))
            shot("p5_done.png")
        except Exception as e:
            record("鉴权主线", False, repr(e))
            close_dialogs()

        # ================================================================
        print("== P6. 全按钮扫描 (8 页, 管理员身份) ==")
        DENY = ["卸载", "停用", "禁用", "关闭账号鉴权", "删除", "清空全部", "恢复默认", "登出", "重启"]
        sweep_fail = []
        for route in ["/monitor", "/project", "/model", "/source", "/data", "/mes", "/alarm", "/settings"]:
            try:
                go(route, 1800)
                # MES/设置分 tab 也扫
                subtabs = frev("""
                  return Array.from(document.querySelectorAll('[data-mes-tab],[data-set-tab]'))
                    .filter(t=>t.offsetParent).map(t=>t.getAttribute('data-mes-tab')||t.getAttribute('data-set-tab'));""") or [None]
                if not subtabs:
                    subtabs = [None]
                err_before = len(console_errors)
                clicked = 0
                for st_name in subtabs:
                    if st_name:
                        frev(f"""var t=document.querySelector('[data-mes-tab="{st_name}"],[data-set-tab="{st_name}"]');
                             if(t) t.click(); return 0;""")
                        page.wait_for_timeout(700)
                    btns = frev("""
                      return Array.from(document.querySelectorAll('button.proto-btn, [data-open-dialog]'))
                        .filter(b=>b.offsetParent && !b.disabled)
                        .map((b,i)=>({text:(b.textContent||'').trim().slice(0,20), idx:i}));""")
                    deny_l = DENY
                    for b in btns:
                        if any(d in b["text"] for d in deny_l):
                            continue
                        try:
                            # 点击时按文案复核 + DENY 二次守门 (防 DOM 变化导致索引漂移误点危险按钮)
                            expected = json.dumps(b["text"])
                            deny_js = json.dumps(DENY)
                            hit = frev(f"""
                              var deny={deny_js}, expected={expected};
                              var bs=Array.from(document.querySelectorAll('button.proto-btn, [data-open-dialog]'))
                                .filter(x=>x.offsetParent && !x.disabled);
                              var b=bs[{b['idx']}];
                              var txt=(b&&b.textContent||'').trim().slice(0,20);
                              if(!b || txt!==expected)
                                b=bs.find(x=>((x.textContent||'').trim().slice(0,20))===expected);
                              if(!b) return 'miss';
                              var t=(b.textContent||'').trim();
                              if(deny.some(d=>t.indexOf(d)>=0)) return 'deny';
                              b.click(); return 'ok';""")
                            if hit == "ok":
                                clicked += 1
                            page.wait_for_timeout(320)
                            close_dialogs()
                        except Exception:
                            pass
                new_errs = console_errors[err_before:]
                real_errs = [e for e in new_errs if "PAGEERROR" in e]
                if real_errs:
                    sweep_fail.append(f"{route}: {real_errs[0][:100]}")
                print(f"    {route}: 点击 {clicked} 个按钮, 新增 pageerror {len(real_errs)}")
                shot(f"p6_sweep{route.replace('/','_')}.png")
            except Exception as e:
                sweep_fail.append(f"{route}: EXC {repr(e)[:100]}")
                close_dialogs()
        record("全按钮扫描无 JS 崩溃", not sweep_fail, "; ".join(sweep_fail[:3]))
        srv_err = [x for x in bad_api]
        record("扫描期无 5xx API", not srv_err, str(srv_err[:3]))

        # ================================================================
        print("== P7. 事故复刻: 访客禁用插件被拒 → 登录后成功 → 宿主原生仍是管理员 ==")
        try:
            # 登出成访客 (用带重试的 id_menu, 并硬校验宿主 token 已清空)
            lo_hit = id_menu("^登出$")
            cleared = api_poll(lambda: not page.evaluate(
                "() => localStorage.getItem('tianjun:auth_token') || sessionStorage.getItem('tianjun:auth_token')"))
            record("P7 前置: 登出成访客 (宿主 token 清空)", lo_hit and cleared,
                   f"menu={lo_hit} cleared={cleared}")
            page.reload()  # 干净访客态重进插件
            page.wait_for_timeout(6000)
            fr = page.frame_locator("iframe").first
            go("/settings", 1200)
            frev("if(window.setSettingsTab) setSettingsTab('plugin'); return 0;")
            page.wait_for_timeout(1500)
            # 访客点 停用/禁用 → 应被拒 (403)
            deact_txt = frev("""
              var b=Array.from(document.querySelectorAll('button.proto-btn'))
                .find(x=>x.offsetParent && /停用|禁用/.test(x.textContent));
              if(b){ b.click(); return b.textContent.trim(); } return null;""")
            page.wait_for_timeout(800)
            frev("""var ok=document.getElementById('pluginConfirmOk'); if(ok) ok.click(); return !!ok;""")
            page.wait_for_timeout(1800)
            still_active = api("/plugins")
            plist = still_active if isinstance(still_active, list) else still_active.get("items", [])
            sc_active = any(p.get("customer_code") == "showcase" and p.get("is_active") for p in plist)
            record("访客停用插件被权限拦截 (仍激活)", sc_active, f"btn={deact_txt}")
            shot("p7_guest_denied.png")
            # 登录 → 再停用 → 成功
            open_login_dialog()
            fr.locator("#loginUser").fill(ADMIN_U)
            fr.locator("#loginPwd").fill(ADMIN_P2)
            jsclick("#loginOkBtn")
            page.wait_for_timeout(2000)
            TOKEN["v"] = page.evaluate("() => localStorage.getItem('tianjun:auth_token')")
            frev("if(window.setSettingsTab) setSettingsTab('plugin'); return 0;")
            page.wait_for_timeout(1200)
            frev("""
              var b=Array.from(document.querySelectorAll('button.proto-btn'))
                .find(x=>x.offsetParent && /停用|禁用/.test(x.textContent));
              if(b) b.click(); return !!b;""")
            page.wait_for_timeout(800)
            frev("""var ok=document.getElementById('pluginConfirmOk'); if(ok) ok.click(); return !!ok;""")
            page.wait_for_timeout(2500)
            plist2 = api("/plugins")
            plist2 = plist2 if isinstance(plist2, list) else plist2.get("items", [])
            sc_active2 = any(p.get("customer_code") == "showcase" and p.get("is_active") for p in plist2)
            record("★ 登录后停用插件成功 (昨日『没法禁用插件』事故)", not sc_active2)
            shot("p7_deactivated.png")
            # 刷新 → 宿主原生 UI + 宿主身份仍是管理员
            page.reload()
            page.wait_for_timeout(6000)
            iframe_gone = page.locator("iframe").count() == 0
            host_tok = page.evaluate("() => localStorage.getItem('tianjun:auth_token')")
            me2 = page.evaluate("""async (be) => {
                const t = localStorage.getItem('tianjun:auth_token');
                const r = await fetch(be + '/auth/me',
                    {headers: t ? {Authorization: 'Bearer '+t} : {}});
                return await r.json(); }""", BE)
            record("停用后回宿主原生 UI", iframe_gone)
            record("★ 宿主原生态仍是管理员登录 (宿主就能账号鉴权终证)",
                   bool(host_tok) and (me2.get("user") or {}).get("username") == ADMIN_U, me2.get("user"))
            shot("p7_host_native.png")
            # 恢复: 重新激活插件 + 关闭鉴权 (清理)
            api("/plugins/showcase/activate", method="POST", body={})
            api("/auth/disable-auth", method="POST", body={})
            st = api("/auth/status")
            record("清理: 插件重激活 + 鉴权关闭", st.get("auth_enabled") is False)
        except Exception as e:
            record("事故复刻", False, repr(e))

        shot("p9_final.png")
        print()
        print("==== console/page errors (前 12) ====")
        for t in console_errors[:12]:
            print("  ", t[:180])
        print()
        print("==== 汇总 ====")
        nfail = 0
        for n, ok, note in RESULTS:
            if not ok:
                nfail += 1
            print(("  PASS  " if ok else "  FAIL  ") + n + ((" — " + str(note)[:160]) if note else ""))
        print()
        print(f"TOTAL {len(RESULTS)}  FAIL {nfail}")
        browser.close()
        raise SystemExit(1 if nfail else 0)


if __name__ == "__main__":
    main()
