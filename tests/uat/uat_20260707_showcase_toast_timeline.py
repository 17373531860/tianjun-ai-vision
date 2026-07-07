# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 展会插件 — 时间轴回归修复 + 提示框/检测框全配置化对齐主程序。

现场叙事: 展会讲解员用 TP 区域事件项目开检测 → 监控页「区域动作时间轴」
仍是插件自己的时间轴卡片 (序号/名称/状态 + 连接段), 不再被主程序式截图卡
撑成图片墙; 工件完成一个周期 → 按项目事件配置弹出「合格」提示框 (颜色/文字/
位置/时长全部来自项目检测配置, 含自定义提示框); 设置页·检测框设置 Tab 里
检测框外观 + 系统预设提示框 + 自定义提示框可读可改可存, 保存落项目配置;
项目页事件配置的提示框下拉能选到本项目的自定义提示框。

前置: main 栈 — 后端 8001 + 前端 6001 已启动; plugins/showcase 已同步
本轮修复文件且 DB 记录已更新; TP 项目 (id=22, region_events) 已配好。
"""
from __future__ import annotations

import copy
import sys
import time

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"
CH = 0
TP_PID = 22
TP_VIDEO = ("/home/qianqian/桌面/word/tianjun-main/backend/"
            "uploads/videos/4b0995aa8f96075a8f51533e0d0fd324.mp4")

run = UatRun("showcase_toast_timeline")


def _plugin_frame(page, timeout_s=40):
    """跨源 OOPIF: page.frames 在 headed 模式会漏, 走 iframe 句柄拿 content_frame。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            h = page.query_selector("iframe[src*='showcase-app.html']")
            fr = h.content_frame() if h else None
            if fr and fr.evaluate("!!document.querySelector('.nav .item[data-route]')"):
                return fr
        except Exception:
            pass
        time.sleep(1)
    return None


def _nav(fr, route, settle_s=3):
    fr.evaluate(
        "(r)=>{const el=document.querySelector(`.nav .item[data-route='${r}']`);"
        "if(el) el.click();}", route)
    time.sleep(settle_s)


def _results():
    return requests.get(f"{API}/source/detection/results?channel={CH}",
                        timeout=5).json()


orig_dc = None
try:
    # ---- 0. 备份 TP 项目检测配置 (脚本改动最后还原) + 起真实检测链路 ----
    orig_dc = copy.deepcopy(
        requests.get(f"{API}/projects/{TP_PID}", timeout=5).json()
        .get("detection_config") or {})
    run.step("00 TP 检测配置已备份 (含 3 个自定义提示框)",
             len(orig_dc.get("customToasts") or []) >= 1,
             f"customToasts={len(orig_dc.get('customToasts') or [])}")

    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
    requests.post(f"{API}/source/video/stop?channel={CH}", timeout=10)
    r = requests.post(f"{API}/projects/{TP_PID}/activate", timeout=30)
    run.step("01 激活 TP 区域事件项目", r.status_code == 200, f"status={r.status_code}")
    r = requests.post(f"{API}/source/video/start?channel={CH}",
                      json={"file_path": TP_VIDEO}, timeout=20)
    ok_video = r.status_code == 200
    r = requests.post(f"{API}/source/detection/start?channel={CH}", json={}, timeout=60)
    run.step("02 现场视频 + 真模型检测已启动",
             ok_video and r.status_code == 200, f"det status={r.status_code}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(FRONT, wait_until="domcontentloaded")
        fr = _plugin_frame(page)
        run.step("03 插件整页 iframe 已加载", fr is not None)
        if fr is None:
            raise RuntimeError("插件 iframe 未加载")
        time.sleep(5)

        # ---- A. 时间轴回归修复: 卡片式时间轴, 无截图 img ----
        ph = fr.evaluate("document.querySelector('.sop .ph')?.textContent || ''")
        n_cards = fr.evaluate("document.querySelectorAll('.sop .flow .step-card').length")
        n_caps = fr.evaluate("document.querySelectorAll('.sop .flow .endcap').length")
        n_imgs = fr.evaluate("document.querySelectorAll('.sop .flow img').length")
        run.step("A1 区域模式时间轴标题正确", ph == "区域动作时间轴", f"ph={ph!r}")
        run.step("A2 时间轴 = 开始/结束端帽 + 步骤卡 (3 张)",
                 n_cards == 3 and n_caps == 2, f"cards={n_cards} caps={n_caps}")
        run.step("A3 时间轴卡片内无截图 <img> (回归修复点)",
                 n_imgs == 0, f"imgs={n_imgs}")
        run.shot(page, "01_region_timeline")

        # ---- B. 提示框配置缓存: 从激活项目 detection_config 拉取 ----
        t0 = time.time()
        cfg_loaded = False
        while time.time() - t0 < 20:
            n = fr.evaluate(
                "window.__tjToastCfg ? (window.__tjToastCfg.customToasts||[]).length : -1")
            if n >= 0:
                cfg_loaded = True
                break
            time.sleep(1)
        run.step("B1 监控页已拉取项目提示框配置 (含自定义)",
                 cfg_loaded and n == len(orig_dc.get("customToasts") or []),
                 f"customToasts={n}")

        # ---- C. 真实事件 → 配置化提示框弹出 (合格总数+1 时应弹「合格」toast) ----
        base_ok = (_results().get("counters") or {}).get("合格总数", 0)
        toast_seen = None
        settled = False
        deadline = time.time() + 150
        while time.time() < deadline:
            t = fr.evaluate("""() => {
              const lays=[...document.querySelectorAll("[id^='tjToastLayer_']")];
              for(const l of lays){
                if(l.children.length) return {pos:l.id, text:l.lastChild.innerText};
              }
              return null;
            }""")
            if t and toast_seen is None:
                toast_seen = t
                run.shot(page, "02_event_toast")
            c = (_results().get("counters") or {}).get("合格总数", 0)
            if c >= base_ok + 1:
                settled = True
            if toast_seen and settled:
                break
            time.sleep(0.2)
        run.step("C1 周期结算发生 (合格总数 +1)", settled, f"base={base_ok}")
        cfg_pos = str((orig_dc.get("toasts") or {}).get("ok", {})
                      .get("position", "top-right"))
        cfg_text = str((orig_dc.get("toasts") or {}).get("ok", {}).get("text", "合格"))
        run.step("C2 事件提示框弹出且按配置渲染 (位置/文字来自项目配置)",
                 toast_seen is not None
                 and cfg_pos.replace("-", "") in toast_seen.get("pos", "")
                 and cfg_text in toast_seen.get("text", ""),
                 f"toast={toast_seen} cfg_pos={cfg_pos} cfg_text={cfg_text}")

        # ---- D. 设置页: 检测框外观 + 提示框读→改→存→后端回读 ----
        _nav(fr, "/settings", 3)
        fr.evaluate("setSettingsTab('box')")
        time.sleep(3)
        run.shot(page, "03_settings_box_tab")
        n_tt = fr.evaluate("document.querySelectorAll('#toastPresetGrid [data-tt]').length")
        n_dcf = fr.evaluate("document.querySelectorAll('[data-dcf]').length")
        n_custom = fr.evaluate("document.querySelectorAll('#toastCustomList .ev-block').length")
        run.step("D1 预设提示框 4 块 + 外观字段 8 项 + 自定义提示框列表渲染",
                 n_tt >= 24 and n_dcf == 8
                 and n_custom == len(orig_dc.get("customToasts") or []),
                 f"tt={n_tt} dcf={n_dcf} custom={n_custom}")
        # 改 NG 提示框颜色 + 检测框线宽 → 保存
        fr.evaluate("""() => {
          const c=document.querySelector("[data-tt='ng'][data-tf='color']");
          c.value='#123456'; c.dispatchEvent(new Event('input',{bubbles:true}));
          const w=document.querySelector("[data-dcf='boxLineWidth']");
          w.value='5'; w.dispatchEvent(new Event('input',{bubbles:true}));
          document.getElementById('toastSaveBtn').click();
        }""")
        time.sleep(4)
        dc2 = requests.get(f"{API}/projects/{TP_PID}", timeout=5).json() \
            .get("detection_config") or {}
        run.step("D2 保存后后端回读: NG色=#123456 线宽=5, 其余键不丢",
                 (dc2.get("toasts") or {}).get("ng", {}).get("color") == "#123456"
                 and dc2.get("boxLineWidth") == 5
                 and dc2.get("boxColor") == orig_dc.get("boxColor")
                 and len(dc2.get("customToasts") or [])
                 == len(orig_dc.get("customToasts") or []),
                 f"ng.color={(dc2.get('toasts') or {}).get('ng', {}).get('color')} "
                 f"lw={dc2.get('boxLineWidth')}")
        # 新建自定义提示框 (仅 DOM 层验证, 不落库)
        fr.evaluate("document.getElementById('toastCustomAddBtn').click()")
        time.sleep(1)
        n_custom2 = fr.evaluate(
            "document.querySelectorAll('#toastCustomList .ev-block').length")
        run.step("D3 新建自定义提示框块即时出现", n_custom2 == n_custom + 1,
                 f"{n_custom} -> {n_custom2}")

        # ---- E. 项目页: 事件配置提示框下拉含本项目自定义提示框 ----
        _nav(fr, "/project", 4)
        fr.evaluate(f"tjSelectProject({TP_PID}, true)")
        time.sleep(2)
        opts = fr.evaluate("""() => {
          const sel=[...document.querySelectorAll("[data-lg='events'] select")]
            .find(s => (s.dataset.b||'').endsWith('toast_id'));
          return sel ? [...sel.options].map(o=>o.value) : [];
        }""")
        has_custom = any(str(v).startswith("custom_") for v in opts)
        run.step("E1 事件提示框下拉 = 系统预设 + 自定义提示框 (对齐主程序)",
                 "ok" in opts and "ng" in opts and has_custom, f"opts={opts}")
        run.shot(page, "04_project_event_toast_select")

        # ---- F. 控制台无逻辑报错 ----
        real = filter_console_errors(cerrs)
        run.step("F1 控制台无前端逻辑报错", not real, f"真报错={real[:3]}")

        ctx.close()
        browser.close()
except Exception:  # noqa: BLE001 — 主流程异常必须显式记录
    import traceback
    _tb = traceback.format_exc()
    print(_tb, flush=True)
    run.step("EXCEPTION 主流程异常中断", False, _tb.strip().splitlines()[-1][:200])
finally:
    try:
        requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/source/video/stop?channel={CH}", timeout=10)
        if orig_dc is not None:  # 还原检测配置, 不污染现场
            requests.put(f"{API}/projects/{TP_PID}",
                         json={"detection_config": orig_dc}, timeout=10)
    except Exception:
        pass
    raise SystemExit(run.finish())
