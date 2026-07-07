# -*- coding: utf-8 -*-
"""
UAT — 展会插件 v1.3.0 第五轮修正: 视频播放控制条 + 无源自动恢复/动态占位

验证点 (用户验收反馈):
  1. 视频文件源要有进度条/倍速/逐帧控制 (对齐主程序 Monitor 视频区);
  2. 打开检测中心不再是静态示例照片:
     - 有上次源 → 自动接回 (对齐主程序 autoRestoreSource);
     - 手动停止后 → "画面已停止"动态占位, 不自动顶掉停止;
     - 示例照片被占位层/真实流全程覆盖。

前置: main 栈 — 后端 8001 + 前端 6001 已启动; plugins/showcase 已同步本轮文件。
跑法: conda run -n tianjun python tests/uat/uat_20260707_showcase_video_ctrl.py
"""
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"

run = UatRun("showcase_video_ctrl")


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


def _vinfo():
    return requests.get(f"{API}/source/video/info?channel=0", timeout=10).json()


try:
    # ---- A. 前置: 源停掉, 复现"打开检测中心时没画面"的起点 ----
    requests.post(f"{API}/source/detection/pause?channel=0", timeout=10)
    time.sleep(1)
    run.step("A0 前置: 视频源已停", not _status().get("is_running"))

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(FRONT + "/#/monitor", wait_until="domcontentloaded")
        fr = _plugin_frame(page)
        run.step("A1 插件 iframe 已加载", fr is not None)
        if fr is None:
            raise RuntimeError("插件 iframe 未加载")
        fr.evaluate("document.querySelector('.nav .item[data-route=\"/monitor\"]').click()")

        # ---- B. 自动恢复: 不点任何按钮, 源应自动接回 ----
        st = {}
        deadline = time.time() + 20
        while time.time() < deadline:
            st = _status()
            if st.get("is_running"):
                break
            time.sleep(1)
        run.step("B1 打开检测中心即自动接回上次输入源 (零点击)",
                 bool(st.get("is_running")), f"is_running={st.get('is_running')}")
        time.sleep(3)
        feed_state = fr.evaluate("""() => ({
          img: !!document.getElementById('tjFeed'),
          phShown: document.getElementById('tjNoSrc').classList.contains('show'),
        })""")
        run.step("B2 真实流已接入且占位层收起 (示例照片不可见)",
                 feed_state["img"] and not feed_state["phShown"], str(feed_state))
        run.shot(page, "01_auto_restored")

        # ---- C. 视频控制条: 视频源运行中应显示 ----
        vc = fr.evaluate("""() => {
          const bar=document.getElementById('tjVidCtrl');
          return { shown: bar.classList.contains('show'),
                   prog: !!document.getElementById('vcProg'),
                   speed: !!document.getElementById('vcSpeed'),
                   sync: !!document.getElementById('vcSync') };
        }""")
        run.step("C1 视频源显示播放控制条 (进度/倍速/逐帧)",
                 vc["shown"] and vc["prog"] and vc["speed"] and vc["sync"], str(vc))
        detecting = _status().get("is_detecting")
        prog_disabled = fr.evaluate("document.getElementById('vcProg').disabled")
        run.step("C2 检测中进度条禁用态与主程序一致",
                 bool(prog_disabled) == bool(detecting),
                 f"detecting={detecting} progDisabled={prog_disabled}")

        # ---- D. 待机后拖进度 / 改倍速走真后端 ----
        requests.post(f"{API}/source/detection/standby?channel=0", timeout=10)
        time.sleep(2.5)  # 等下一帧数据把禁用态解开
        fr.evaluate("""() => {
          const p=document.getElementById('vcProg');
          p.value=500; p.dispatchEvent(new Event('change'));
        }""")
        time.sleep(2)
        v = _vinfo()
        run.step("D1 拖进度条到 50% 后端跟随",
                 abs((v.get("progress") or 0) - 0.5) < 0.05,
                 f"progress={v.get('progress'):.3f}")
        fr.evaluate("""() => {
          const s=document.getElementById('vcSpeed');
          s.value='2'; s.dispatchEvent(new Event('change'));
        }""")
        time.sleep(2)
        v2 = _vinfo()
        run.step("D2 倍速改 2x 后端跟随", (v2.get("speed") or 1) == 2,
                 f"speed={v2.get('speed')}")
        fr.evaluate("""() => {
          const s=document.getElementById('vcSpeed');
          s.value='1'; s.dispatchEvent(new Event('change'));
        }""")
        run.shot(page, "02_video_ctrl")

        # ---- E. 手动停止 → "画面已停止"占位, 不被自动恢复顶掉 ----
        fr.evaluate("document.getElementById('detStopBtn').click()")
        time.sleep(4)
        stopped = fr.evaluate("""() => ({
          phShown: document.getElementById('tjNoSrc').classList.contains('show'),
          t1: document.getElementById('tjNoSrcT1').textContent,
          img: !!document.getElementById('tjFeed'),
        })""")
        run.step("E1 停止后显示「画面已停止」动态占位且流已摘除",
                 stopped["phShown"] and "已停止" in stopped["t1"] and not stopped["img"],
                 str(stopped))
        time.sleep(4)
        st5 = _status()
        run.step("E2 手动停止不被自动恢复顶掉 (源保持停止)",
                 not st5.get("is_running"), f"is_running={st5.get('is_running')}")
        run.shot(page, "03_stopped_placeholder")

        # ---- F. 点「开始」画面回来 (第四轮守门路径继续有效) ----
        fr.evaluate("document.getElementById('detStartBtn').click()")
        st6 = {}
        deadline = time.time() + 25
        while time.time() < deadline:
            st6 = _status()
            if st6.get("is_running") and st6.get("is_detecting"):
                break
            time.sleep(1)
        run.step("F1 点「开始」画面与检测恢复", bool(st6.get("is_running") and st6.get("is_detecting")),
                 f"running={st6.get('is_running')} detecting={st6.get('is_detecting')}")
        time.sleep(2)
        run.shot(page, "04_restarted")

        real = filter_console_errors(cerrs)
        run.step("G1 控制台无前端逻辑报错", not real, f"真报错={real[:3]}")

        ctx.close()
        browser.close()
finally:
    pass

raise SystemExit(run.finish())
