# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 展会插件打开真实 TP 项目 (id=22) — 全参数回显对账。

现场叙事: 展会工程师在插件项目页点开 TP 项目 → 三条动作规则的每个微调参数
(确认时长秒基/位移门槛/目标框扩边/消失确认/漏检容忍/每类置信度/结算判定)
与后端落库值逐项一致 — 这是发给现场照着配的同一张画面, 回显错一个格就会
把现场带偏。只读不保存, 不动现场配置。
"""
from __future__ import annotations

import sys
import time

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"
TP_PID = 22

run = UatRun("showcase_tp_echo")


def _plugin_frame(page, timeout_s=60):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            h = page.query_selector("iframe[src*='showcase-app.html']")
            fr = h.content_frame() if h else None
            if fr and fr.evaluate("typeof renderRoutePage==='function' && typeof tjSelectProject==='function'"):
                return fr
        except Exception:
            pass
        time.sleep(1)
    return None


def _val(fr, selector):
    return fr.evaluate(
        "(sel)=>{const el=document.querySelector(sel); return el?String(el.value):null;}",
        selector)


try:
    # 后端真值
    p = requests.get(f"{API}/projects/{TP_PID}", timeout=5).json()
    re_cfg = (p.get("pipeline_config") or {}).get("region_events") or {}
    rules = re_cfg.get("rules") or []
    by_name = {r["name"]: (i, r) for i, r in enumerate(rules)}
    run.step("后端真值就绪 (3 规则)", set(by_name) == {"测硬度", "扫码", "下工件"},
             f"rules={list(by_name)}")

    with sync_playwright() as pw:
        browser, ctx, page, cerrs = launch_browser(pw, record_video_dir=run.video_dir)
        page.goto(FRONT, wait_until="domcontentloaded")
        fr = _plugin_frame(page)
        run.step("插件整页 iframe 已加载", fr is not None)
        if fr is None:
            raise RuntimeError("插件 iframe 未加载")
        time.sleep(2)

        fr.evaluate("renderRoutePage('/project')")
        time.sleep(2)
        fr.evaluate("tjLoadProjects()")
        time.sleep(2)
        fr.evaluate(f"tjSelectProject({TP_PID})")
        time.sleep(2)
        ok_sel = fr.evaluate(f"window.__tjSelectedPid==={TP_PID}")
        run.step("TP 项目已在插件中打开", ok_sel)

        # 逐规则逐参数对账: 后端值 vs 插件输入框回显
        checks = []  # (说明, 后端值, data-b 后缀)
        for name, keys in (
            ("测硬度", ["min_frames", "min_seconds", "gone_seconds"]),
            ("扫码", ["min_frames", "min_seconds", "min_move",
                      "object_margin", "gone_seconds"]),
        ):
            idx, r = by_name[name]
            for k in keys:
                sel = f'[data-b="pc.region_events.rules.{idx}.{k}"]'
                ui = _val(fr, sel)
                backend = r.get(k)
                same = (ui is not None and backend is not None
                        and abs(float(ui) - float(backend)) < 1e-6)
                checks.append((f"{name}.{k}", backend, ui, same))
        bad = [c for c in checks if not c[3]]
        detail = "; ".join(f"{n} 库={b} UI={u}" for n, b, u, _ in (bad or checks))
        run.step(f"规则微调参数回显 {len(checks) - len(bad)}/{len(checks)}",
                 not bad, detail)

        # 全局: 漏检容忍 + 每类置信度 (6 类)
        gap_ui = _val(fr, '[data-b="pc.region_events.gap_tolerance_frames"]')
        run.step("漏检容忍回显", gap_ui is not None
                 and int(float(gap_ui)) == re_cfg.get("gap_tolerance_frames"),
                 f"库={re_cfg.get('gap_tolerance_frames')} UI={gap_ui}")
        cc = re_cfg.get("class_conf") or {}
        cc_bad = []
        for label, conf in cc.items():
            ui = _val(fr, f'[data-b="pc.region_events.class_conf.{label}"]')
            if ui is None or abs(float(ui) - float(conf)) > 1e-6:
                cc_bad.append(f"{label} 库={conf} UI={ui}")
        run.step(f"每类置信度回显 {len(cc) - len(cc_bad)}/{len(cc)} (含停放两类)",
                 not cc_bad, "; ".join(cc_bad))

        # 结算判定行数
        n_sr_ui = fr.evaluate(
            "document.querySelectorAll('[data-b^=\"pc.region_events.settlement_rules.\"][data-b$=\".match\"]').length")
        n_sr = len(re_cfg.get("settlement_rules") or [])
        run.step("结算判定行数回显", n_sr_ui == n_sr, f"库={n_sr} UI={n_sr_ui}")

        # 滚到区域事件卡截图留证
        fr.evaluate("document.querySelector('[data-lg=\"regionEvents\"]')?.scrollIntoView()")
        time.sleep(1)
        run.shot(page, "01_tp_region_rules")

        browser.close()
finally:
    run.finish()
