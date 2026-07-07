# -*- coding: utf-8 -*-
"""
UAT — 展会插件 v1.3.0 第七轮修正: 骨架 HUD 风格预设一键套用

验证点 (用户验收反馈"默认配色太丑、配不上插件风格"):
  性能设置 → 骨架样式 → 「✦ HUD 风格预设」一键把骨架配色切到与插件界面同源的
  青色辉光系 (手部 #2EE0C8 线 + #EAF9F6 点 / 姿态 #38B6FF 线 + #EAF3FF 点, 粗细 2),
  保存走真后端并回读一致。

跑法: conda run -n tianjun python tests/uat/uat_20260707_showcase_mp_hud_preset.py
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

run = UatRun("showcase_mp_hud_preset")

HUD = {
    "mediapipe_custom_style": True,
    "mediapipe_hands_color": "#2EE0C8", "mediapipe_hands_point_color": "#EAF9F6",
    "mediapipe_hands_thickness": 2,
    "mediapipe_pose_color": "#38B6FF", "mediapipe_pose_point_color": "#EAF3FF",
    "mediapipe_pose_thickness": 2,
}


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


def _cfg():
    return requests.get(f"{API}/source/stream/config", timeout=10).json()


try:
    # 先把配色改乱, 验证预设按钮能拉回来
    base = _cfg()
    dirty = {k: base.get(k) for k in (
        "frame_limit_enabled", "target_stream_fps", "use_half",
        "mediapipe_enabled", "mediapipe_pose", "mediapipe_hands",
        "mediapipe_confidence", "mediapipe_interval")}
    dirty.update({"mediapipe_custom_style": True, "mediapipe_hands_color": "#696C69",
                  "mediapipe_hands_point_color": "#E40A0A", "mediapipe_hands_thickness": 2})
    requests.post(f"{API}/source/stream/config", json=dirty, timeout=10)
    run.step("A0 前置: 先写入旧丑配色 (灰线红点)",
             _cfg().get("mediapipe_hands_color") == "#696C69")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(FRONT + "/#/settings", wait_until="domcontentloaded")
        fr = _plugin_frame(page)
        run.step("A1 插件 iframe 已加载", fr is not None)
        if fr is None:
            raise RuntimeError("插件 iframe 未加载")
        fr.evaluate("document.querySelector('.nav .item[data-route=\"/settings\"]').click()")
        time.sleep(1)
        fr.evaluate("""() => {
          const t=[...document.querySelectorAll('[data-set-tab]')].find(x=>x.textContent.includes('性能'));
          if(t) t.click();
        }""")
        time.sleep(3)

        has_btn = fr.evaluate("!!document.getElementById('perfMpHudPreset')")
        run.step("B1 骨架样式区有「HUD 风格预设」按钮", has_btn)
        fr.evaluate("document.getElementById('perfMpHudPreset').click()")
        time.sleep(3)
        after = _cfg()
        ok = all(after.get(k) == v for k, v in HUD.items())
        run.step("B2 一键套用后后端 7 字段 = HUD 配色",
                 ok, {k: after.get(k) for k in HUD})
        ui = fr.evaluate("""() => ({
          hands: document.getElementById('perfMpHandsColor').value,
          handsPt: document.getElementById('perfMpHandsPtColor').value,
          pose: document.getElementById('perfMpPoseColor').value,
          styleOn: document.getElementById('perfMpStyleEnable').checked,
        })""")
        run.step("B3 界面取色器同步显示 HUD 配色",
                 ui["hands"].upper() == "#2EE0C8" and ui["handsPt"].upper() == "#EAF9F6"
                 and ui["pose"].upper() == "#38B6FF" and ui["styleOn"], str(ui))
        run.shot(page, "01_hud_preset_applied")

        real = filter_console_errors(cerrs)
        run.step("C1 控制台无前端逻辑报错", not real, f"真报错={real[:3]}")

        ctx.close()
        browser.close()
finally:
    pass

raise SystemExit(run.finish())
