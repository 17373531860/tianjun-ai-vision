# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 展会插件 v1.4.1 区域规则「目标框扩边 object_margin」对齐。

现场叙事: 讲解员在插件项目页给 overlap 动作规则填目标框扩边 0.02 → 保存 →
后端项目配置真实落库 (与主程序 2026-07-13 引擎新参数同键名, 激活即生效)。

前置: main 栈 8001/6001 已启动, plugins/showcase 已同步 v1.4.1 并 dev-activate。
"""
from __future__ import annotations

import sys
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"

run = UatRun("showcase_object_margin")
pid = None


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


def _set(fr, selector, value):
    fr.evaluate(
        """([sel, v]) => { const el=document.querySelector(sel); if(!el) return false;
             if(el.type==='checkbox'){ el.checked=!!v; } else { el.value=String(v); }
             el.dispatchEvent(new Event('change',{bubbles:true})); return true; }""",
        [selector, value])


try:
    m = requests.get(f"{API}/plugins/active/manifest", timeout=5).json()
    run.step("激活插件清单 = showcase v1.4.1",
             m.get("customer_code") == "showcase" and m.get("plugin_version") == "1.4.1",
             f"got {m.get('customer_code')} {m.get('plugin_version')}")

    name = f"uat_margin_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/projects", json={
        "name": name, "task_type": "detection", "logic_mode": "region_events"}, timeout=5)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/projects/{pid}", json={"steps_config": [
        {"id": 1, "label": "工件", "displayLabel": "工件", "enabled": True, "threshold": 50},
        {"id": 2, "label": "扫码枪", "displayLabel": "扫码枪", "enabled": True, "threshold": 50},
    ]}, timeout=5).raise_for_status()
    run.step("测试项目已建", True, f"pid={pid} {name}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
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
        fr.evaluate(f"tjSelectProject({pid})")
        time.sleep(2)

        # 新增 overlap 规则 → 扩边输入渲染
        fr.evaluate("document.querySelector('[data-lga=\"reAdd\"]').click()")
        time.sleep(1)
        has_field = fr.evaluate(
            "!!document.querySelector('[data-lg=\"regionEvents\"] [data-b$=\".object_margin\"]')")
        run.step("目标框扩边输入渲染 (overlap 规则)", has_field)
        run.shot(page, "01_margin_field")

        # 填值保存 → 落库回读
        _set(fr, '[data-b$=".subject_label"]', "扫码枪")
        _set(fr, '[data-b$=".object_label"]', "工件")
        _set(fr, '[data-b$=".object_margin"]', "0.02")
        time.sleep(0.5)
        fr.evaluate("document.getElementById('projSaveBtn').click()")
        time.sleep(3)
        after = requests.get(f"{API}/projects/{pid}", timeout=5).json()
        rule0 = (((after.get("pipeline_config") or {}).get("region_events") or {})
                 .get("rules") or [{}])[0]
        run.step("object_margin 落库 0.02",
                 abs((rule0.get("object_margin") or 0) - 0.02) < 1e-6,
                 f"object_margin={rule0.get('object_margin')}")

        # 切出区消失类型 → 保存清洗应删掉该键
        _set(fr, '[data-b$=".type"]', "region_exit")
        time.sleep(1)
        fr.evaluate("document.getElementById('projSaveBtn').click()")
        time.sleep(3)
        after2 = requests.get(f"{API}/projects/{pid}", timeout=5).json()
        rule0b = (((after2.get("pipeline_config") or {}).get("region_events") or {})
                  .get("rules") or [{}])[0]
        run.step("region_exit 清洗剔除扩边键",
                 "object_margin" not in rule0b,
                 f"keys={sorted(rule0b.keys())}")
        run.shot(page, "02_after_save")

        browser.close()
finally:
    if pid:
        requests.delete(f"{API}/projects/{pid}", timeout=5)
    run.finish()
