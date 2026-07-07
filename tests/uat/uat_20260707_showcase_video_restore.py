# -*- coding: utf-8 -*-
"""
UAT — 展会插件 v1.3.0 第四轮修正: "画面跑哪去了" (No Source 黑屏) 修复验证

复现路径: 视频源被停掉 (暂停/清理) → 用户在插件监控页点「开始」→
旧行为: 只启动检测线程, 画面是后端 "Ch0 - No Source" 占位黑屏。
新行为 (对齐主程序 startDetection 暂停/待机恢复路径):
  源没跑 → 先 /detection/resume 由后端重启记忆中的视频源 (画面+检测一起回来);
  源在跑+模型已载 → resume-inference 待机快速恢复;
  从没配过源 → 明确提示去「输入源设置」, 不再空跑黑屏。

前置: main 栈 — 后端 8001 + 前端 6001 已启动; plugins/showcase 已同步本轮文件。
跑法: conda run -n tianjun python tests/uat/uat_20260707_showcase_video_restore.py
"""
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import UatRun, launch_browser  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"

run = UatRun("showcase_video_restore")


def _plugin_frame(page, timeout_s=40):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            h = page.query_selector("iframe[src*='showcase-app']")
            fr = h.content_frame() if h else None
            if fr and fr.evaluate("!!document.querySelector('.nav .item[data-route]')"):
                return fr
        except Exception:
            pass
        time.sleep(1)
    return None


def _status():
    return requests.get(f"{API}/source/status?channel=0", timeout=10).json()


try:
    # ---- A. 前置: 把系统弄进用户遇到的状态 — 源和检测全停 ----
    requests.post(f"{API}/source/detection/pause?channel=0", timeout=10)
    time.sleep(1)
    st = _status()
    run.step("A0 前置: 视频源已停 (复现用户场景)", not st.get("is_running"),
             f"is_running={st.get('is_running')}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(FRONT + "/#/monitor", wait_until="domcontentloaded")
        fr = _plugin_frame(page)
        run.step("A1 插件整页 iframe 已加载", fr is not None)
        if fr is None:
            raise RuntimeError("插件 iframe 未加载")
        fr.evaluate("document.querySelector('.nav .item[data-route=\"/monitor\"]').click()")
        time.sleep(2)
        run.shot(page, "01_before_start_no_source")

        # ---- B. 点插件「开始」→ 源守门应先恢复视频源 ----
        fr.evaluate("document.getElementById('detStartBtn').click()")
        st2 = {}
        deadline = time.time() + 25
        while time.time() < deadline:
            st2 = _status()
            if st2.get("is_running") and st2.get("is_detecting"):
                break
            time.sleep(1)
        run.step("B1 点「开始」后视频源恢复运行", bool(st2.get("is_running")),
                 f"is_running={st2.get('is_running')}")
        run.step("B2 点「开始」后检测同步恢复", bool(st2.get("is_detecting")),
                 f"is_detecting={st2.get('is_detecting')}")

        # ---- C. 画面是真视频帧, 不是 No Source 静态占位 (抓两张快照比较) ----
        time.sleep(2)
        f1 = requests.get("http://127.0.0.1:8001/snapshot?channel=0", timeout=10).content
        time.sleep(1.5)
        f2 = requests.get("http://127.0.0.1:8001/snapshot?channel=0", timeout=10).content
        run.step("C1 画面为真实视频帧 (非 No Source 静态占位)",
                 len(f1) > 5000 and f1 != f2,
                 f"frame1={len(f1)}B frame2={len(f2)}B same={f1 == f2}")
        time.sleep(2)
        run.shot(page, "02_after_start_picture_back")

        # ---- D. 待机 → 再点「开始」= 快速恢复推理 (幂等, 不报错) ----
        requests.post(f"{API}/source/detection/standby?channel=0", timeout=10)
        time.sleep(1)
        st3 = _status()
        run.step("D1 待机后画面仍在跑", bool(st3.get("is_running")) and not st3.get("is_detecting"),
                 f"is_running={st3.get('is_running')} is_detecting={st3.get('is_detecting')}")
        fr.evaluate("document.getElementById('detStartBtn').click()")
        st4 = {}
        deadline = time.time() + 15
        while time.time() < deadline:
            st4 = _status()
            if st4.get("is_detecting"):
                break
            time.sleep(1)
        run.step("D2 待机态点「开始」快速恢复推理", bool(st4.get("is_detecting")),
                 f"is_detecting={st4.get('is_detecting')}")
        run.shot(page, "03_standby_resume")

        ctx.close()
        browser.close()
finally:
    pass

raise SystemExit(run.finish())
