# -*- coding: utf-8 -*-
"""showcase 插件真实环境鼠标模拟 E2E — 复现用户反馈「插件里改配置都不行」。

前置: 后端 8004 (插件 showcase 已激活) + 前端 vite 6001。
流程 (全部真鼠标/真后端, 不打桩):
  1. 打开主程序 → 等插件 iframe 覆盖层出现
  2. 模型仓库: 上传 ~/Downloads/best.pt → 后端 GET /models 验证入库
  3. 项目管理: 新建项目 → 选模型/改配置 → 保存配置 → GET /projects/{id} 验证落库
  4. 系统设置: 改显示设置保存 → GET 验证
每一步失败不中断, 记录现象 + 截图, 最后汇总。

用法:
  ~/miniconda3/envs/tianjun/bin/python tests/uat/showcase_plugin_v150/uat_real_e2e.py
"""
import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
EVID = ROOT / "evidence/showcase_v150/real"
EVID.mkdir(parents=True, exist_ok=True)
FE = "http://localhost:6001"
BE = "http://127.0.0.1:8004/api/v1"
MODEL_FILE = Path.home() / "Downloads/best.pt"
SUF = str(int(time.time()))[-5:]
MODEL_NAME = "e2e-" + SUF
PROJ_NAME = "e2e-项目" + SUF

RESULTS = []


def record(name, ok, note=""):
    RESULTS.append((name, ok, note))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + note) if note else ""))


def api_get(path):
    with urllib.request.urlopen(BE + path, timeout=10) as r:
        return json.loads(r.read().decode())


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.set_default_timeout(15000)
        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type in ("error",) else None)
        api_log = []
        page.on("response", lambda r: api_log.append((r.request.method, r.url, r.status))
                if "/api/v1/" in r.url and r.request.method != "GET" else None)

        print("== 0. 打开主程序, 等插件覆盖层 ==")
        page.goto(FE)
        try:
            page.wait_for_selector('iframe[title*="天军"]', timeout=120000)
            record("插件 iframe 覆盖层出现", True)
        except Exception as e:
            record("插件 iframe 覆盖层出现", False, str(e)[:120])
            page.screenshot(path=str(EVID / "0_no_iframe.png"))
            browser.close()
            return
        page.wait_for_timeout(4000)
        fr = page.frame_locator('iframe[title*="天军"]')

        def jsclick(sel):
            fr.locator(sel).first.evaluate("el => el.click()")

        def shot(name):
            try:
                page.screenshot(path=str(EVID / name), timeout=8000)
            except Exception:
                try:
                    cdp = ctx.new_cdp_session(page)
                    import base64
                    data = cdp.send("Page.captureScreenshot", {"format": "png"})["data"]
                    (EVID / name).write_bytes(base64.b64decode(data))
                except Exception as e2:
                    print("  WARN 截图失败", name, e2)

        # ---------- 1. 模型上传 ----------
        print("== 1. 模型仓库上传 best.pt ==")
        try:
            print("  [step] 点模型页导航")
            jsclick('.nav .item[data-route="/model"]')
            page.wait_for_timeout(1500)
            print("  [step] 点上传对话框按钮")
            jsclick('[data-open-dialog="uploadModelDialog"]')
            page.wait_for_timeout(600)
            print("  [step] set_input_files 开始 (76MB)")
            fr.locator("#modelFileInput").set_input_files(str(MODEL_FILE), timeout=60000)
            print("  [step] set_input_files 完成")
            page.wait_for_timeout(400)
            fr.locator("#modelName").fill(MODEL_NAME)
            fr.locator("#modelVersion").fill("V1.0")
            shot("1_upload_dialog.png")
            print("  [step] 点开始上传")
            jsclick("#modelUploadBtn")
            # 76MB 上传+落库, 等按钮文案恢复/成功提示, 最多 120s
            ok = False
            note = ""
            for _ in range(120):
                page.wait_for_timeout(1000)
                txt = fr.locator("#modelDropText").inner_text()
                if "上传完成" in txt:
                    ok = True
                    break
                toast = fr.locator("#protoToast")
                if toast.count() and toast.first.is_visible():
                    t = toast.first.inner_text()
                    if "失败" in t:
                        note = t
                        break
            shot("1_upload_result.png")
            record("UI 上传流程走通", ok, note)
            models = api_get("/models")
            items = models.get("items", models if isinstance(models, list) else [])
            hit = [m for m in items if MODEL_NAME in (m.get("name") or "")]
            record("后端 /models 入库", bool(hit), f"库内 {len(items)} 个模型")
            model_id = hit[0]["id"] if hit else (items[0]["id"] if items else None)
        except Exception as e:
            record("模型上传流程", False, repr(e)[:200])
            shot("1_upload_exception.png")
            model_id = None

        # ---------- 2. 新建项目 + 改配置保存 ----------
        print("== 2. 项目管理: 新建 + 保存配置 ==")
        proj_id = None
        try:
            jsclick('.nav .item[data-route="/project"]')
            page.wait_for_timeout(1500)
            jsclick('[data-open-dialog="createProjectDialog"]')
            page.wait_for_timeout(500)
            fr.locator("#newProjName").fill(PROJ_NAME)
            fr.locator("#newProjMode").select_option("sequential")
            shot("2_create_dialog.png")
            jsclick("#newProjCreateBtn")
            page.wait_for_timeout(3000)
            projs = api_get("/projects")
            items = projs.get("items", projs if isinstance(projs, list) else [])
            hit = [p for p in items if p.get("name") == PROJ_NAME]
            record("新建项目落库", bool(hit), f"库内 {len(items)} 个项目")
            proj_id = hit[0]["id"] if hit else None
        except Exception as e:
            record("新建项目", False, repr(e)[:200])
            shot("2_create_exception.png")

        if proj_id:
            try:
                page.wait_for_timeout(1500)
                # 选中新项目卡
                jsclick(f'[data-project-card][data-project-id="{proj_id}"]')
                page.wait_for_timeout(800)
                # 基础设置: 改描述性字段 (班次拆分开关)
                jsclick('.project-tab[data-project-tab="basic"]')
                page.wait_for_timeout(500)
                sw = fr.locator('input[data-b="pe.data_config.shift_split_enabled"]')
                shift_before = None
                if sw.count():
                    shift_before = sw.first.evaluate("el => el.checked")
                    sw.first.evaluate("el => el.click()")
                # 逻辑设置: 改结算方式
                jsclick('.project-tab[data-project-tab="logic"]')
                page.wait_for_timeout(600)
                shot("2_logic_panel.png")
                # 保存
                jsclick("#projSaveBtn")
                page.wait_for_timeout(3000)
                shot("2_after_save.png")
                p = api_get(f"/projects/{proj_id}")
                dc = p.get("data_config") or {}
                record("保存配置落库 (shift_split 开关翻转)",
                       shift_before is not None and dc.get("shift_split_enabled") == (not shift_before),
                       f"before={shift_before} after={dc.get('shift_split_enabled')}")
            except Exception as e:
                record("改配置+保存", False, repr(e)[:200])
                shot("2_save_exception.png")

        # ---------- 3. 绑定模型到项目 (若上传成功) ----------
        if proj_id and model_id:
            print("== 3. 项目绑定模型 (选择模型对话框) ==")
            try:
                jsclick("#projPickModelBtn")
                page.wait_for_timeout(1200)
                shot("3_model_dialog.png")
                jsclick(f'[data-model-pick-id="{model_id}"]')
                page.wait_for_timeout(4000)
                p = api_get(f"/projects/{proj_id}")
                record("绑定模型落库", p.get("default_model_id") == model_id,
                       f"default_model_id={p.get('default_model_id')} model_name={p.get('model_name')}")
                record("按模型 labels 重建步骤", bool(p.get("steps_config")),
                       f"steps={len(p.get('steps_config') or [])}")
                shot("3_bind_model.png")
            except Exception as e:
                record("绑定模型", False, repr(e)[:200])
                shot("3_bind_exception.png")

        # ---------- 3b. 步骤设置: 改阈值 + 保存 ----------
        if proj_id:
            print("== 3b. 步骤设置改阈值 + 保存 ==")
            try:
                jsclick('.project-tab[data-project-tab="steps"]')
                page.wait_for_timeout(800)
                th = fr.locator('input[data-k="threshold"]')
                if th.count():
                    th.first.evaluate(
                        "el => { el.value = 66; el.dispatchEvent(new Event('change', {bubbles:true})); }")
                    page.wait_for_timeout(400)
                    jsclick("#projSaveBtn")
                    page.wait_for_timeout(3000)
                    p = api_get(f"/projects/{proj_id}")
                    sc = p.get("steps_config") or []
                    got = sc[0].get("threshold") if sc else None
                    record("步骤阈值落库 (66)", got in (66, 0.66), f"threshold={got}")
                else:
                    record("步骤阈值", False, "步骤表为空 (可能未绑模型)")
                shot("3b_steps.png")
            except Exception as e:
                record("步骤设置", False, repr(e)[:200])

        # ---------- 4. 系统设置: 显示设置保存 ----------
        print("== 4. 系统设置: 显示设置保存 ==")
        try:
            jsclick('.nav .item[data-route="/settings"]')
            page.wait_for_timeout(1500)
            fr.locator("#dispBrand").first.click(force=True)
            fr.locator("#dispBrand").first.fill("E2E品牌" + SUF)
            page.keyboard.press("Tab")
            page.wait_for_timeout(800)
            jsclick("#dispSaveBtn")
            page.wait_for_timeout(2000)
            d = api_get("/system/display")
            record("显示设置落库", d.get("brand_name") == "E2E品牌" + SUF,
                   "brand_name=" + str(d.get("brand_name")))
            shot("4_settings.png")
        except Exception as e:
            record("系统设置", False, repr(e)[:200])
            shot("4_settings_exception.png")

        # ---------- 5. 报警配置保存 ----------
        print("== 5. 报警页: 保存触发配置 ==")
        try:
            jsclick('.nav .item[data-route="/alarm"]')
            page.wait_for_timeout(1500)
            jsclick("#alarmSaveBtn")
            page.wait_for_timeout(2500)
            shot("5_alarm.png")
            # 靠 api_log 判定 POST /alarm/config 是否 200
            hits = [(m, u, s) for m, u, s in api_log if "/alarm/config" in u]
            record("报警配置保存 (POST /alarm/config)", bool(hits) and hits[-1][2] == 200,
                   str(hits[-1][2]) if hits else "无请求发出")
        except Exception as e:
            record("报警配置", False, repr(e)[:200])

        # ---------- 6. 事件设置: 加自定义事件 + 保存 ----------
        if proj_id:
            print("== 6. 事件设置保存 ==")
            try:
                jsclick('.nav .item[data-route="/project"]')
                page.wait_for_timeout(1200)
                jsclick('.project-tab[data-project-tab="events"]')
                page.wait_for_timeout(800)
                shot("6_events.png")
                jsclick("#projSaveBtn")
                page.wait_for_timeout(2500)
                puts = [(m, u, s) for m, u, s in api_log if f"/projects/{proj_id}" in u and m == "PUT"]
                record("事件页保存 (PUT /projects)", bool(puts) and puts[-1][2] == 200,
                       str(puts[-1][2]) if puts else "无请求发出")
            except Exception as e:
                record("事件设置", False, repr(e)[:200])

        shot("9_final.png")
        print()
        print("==== 非 GET API 调用记录 ====")
        for m, u, s in api_log[-40:]:
            print(f"  {m} {u.split('/api/v1')[-1]} -> {s}")
        print("==== console errors (前 10) ====")
        for t in console_errors[:10]:
            print("  ", t[:200])
        print()
        print("==== 汇总 ====")
        for n, ok, note in RESULTS:
            print(("  PASS  " if ok else "  FAIL  ") + n + ((" — " + note) if note else ""))
        browser.close()


if __name__ == "__main__":
    main()
