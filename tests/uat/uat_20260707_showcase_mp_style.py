# -*- coding: utf-8 -*-
"""
UAT — 展会插件 v1.3.0 第六轮修正: MediaPipe 骨架样式可配置 (对齐主程序 v3.32.0)

验证点 (用户验收反馈):
  1. 插件性能设置里的 MediaPipe 开关反映后端真实启用状态;
  2. 骨架样式 (自定义纯色/连线颜色/关键点颜色/线条粗细) 插件可读可改,
     与主程序走同一 /source/stream/config, 保存后主程序侧同样生效。

前置: main 栈 — 后端 8001 + 前端 6001 已启动; plugins/showcase 已同步本轮文件。
跑法: conda run -n tianjun python tests/uat/uat_20260707_showcase_mp_style.py
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

run = UatRun("showcase_mp_style")


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
    before = _cfg()
    run.step("A0 前置: 后端 MediaPipe 当前状态可读",
             "mediapipe_enabled" in before,
             f"enabled={before.get('mediapipe_enabled')} custom_style={before.get('mediapipe_custom_style')}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(FRONT + "/#/settings", wait_until="domcontentloaded")
        fr = _plugin_frame(page)
        run.step("A1 插件 iframe 已加载", fr is not None)
        if fr is None:
            raise RuntimeError("插件 iframe 未加载")
        # 进设置页 → 性能设置 Tab
        fr.evaluate("document.querySelector('.nav .item[data-route=\"/settings\"]').click()")
        time.sleep(1)
        fr.evaluate("""() => {
          const tabs=[...document.querySelectorAll('[data-set-tab]')];
          const t=tabs.find(x=>x.textContent.includes('性能'));
          if(t) t.click();
        }""")
        time.sleep(3)

        # ---- B. 启用状态回读: 插件开关 = 后端真值 ----
        ui = fr.evaluate("""() => ({
          enable: document.getElementById('perfMpEnable').checked,
          pose: document.getElementById('perfMpPose').checked,
          hands: document.getElementById('perfMpHands').checked,
          styleOn: document.getElementById('perfMpStyleEnable').checked,
          gridShown: document.getElementById('perfMpStyleGrid').style.display !== 'none',
          handsColor: document.getElementById('perfMpHandsColor').value,
          handsPt: document.getElementById('perfMpHandsPtColor').value,
          handsPtFollow: document.getElementById('perfMpHandsPtFollow').checked,
          poseThick: document.getElementById('perfMpPoseThick').value,
        })""")
        run.step("B1 启用开关回读后端真值",
                 ui["enable"] == bool(before.get("mediapipe_enabled"))
                 and ui["pose"] == bool(before.get("mediapipe_pose"))
                 and ui["hands"] == bool(before.get("mediapipe_hands")), str(ui))
        run.step("B2 骨架样式区随自定义开关联动显示",
                 ui["styleOn"] == bool(before.get("mediapipe_custom_style"))
                 and ui["gridShown"] == bool(before.get("mediapipe_custom_style")),
                 f"styleOn={ui['styleOn']} gridShown={ui['gridShown']}")
        exp_hands = (before.get("mediapipe_hands_color") or "#00FF00").upper()
        run.step("B3 手部连线颜色回读一致",
                 ui["handsColor"].upper() == exp_hands,
                 f"ui={ui['handsColor']} backend={exp_hands}")
        exp_pt = before.get("mediapipe_hands_point_color") or ""
        run.step("B4 手部关键点颜色/跟随语义回读一致",
                 (ui["handsPtFollow"] and exp_pt == "") or
                 (not ui["handsPtFollow"] and ui["handsPt"].upper() == exp_pt.upper()),
                 f"follow={ui['handsPtFollow']} ui={ui['handsPt']} backend='{exp_pt}'")
        run.shot(page, "01_style_loaded")

        # ---- C. 插件里改样式 → 保存 → 后端跟随 ----
        fr.evaluate("""() => {
          const se=document.getElementById('perfMpStyleEnable');
          if(!se.checked){ se.checked=true; se.dispatchEvent(new Event('change',{bubbles:true})); }
          document.getElementById('perfMpHandsColor').value='#12ABCD';
          document.getElementById('perfMpHandsThick').value='4';
          const f=document.getElementById('perfMpHandsPtFollow');
          if(!f.checked){ f.checked=true; f.dispatchEvent(new Event('change',{bubbles:true})); }
          document.querySelector('[data-perf-save]').click();
        }""")
        time.sleep(3)
        after = _cfg()
        run.step("C1 保存后后端: 自定义样式开启 + 手部连线 #12ABCD + 粗细 4 + 关键点跟随",
                 after.get("mediapipe_custom_style") is True
                 and after.get("mediapipe_hands_color") == "#12ABCD"
                 and after.get("mediapipe_hands_thickness") == 4
                 and (after.get("mediapipe_hands_point_color") or "") == "",
                 f"color={after.get('mediapipe_hands_color')} thick={after.get('mediapipe_hands_thickness')} pt='{after.get('mediapipe_hands_point_color')}'")
        run.shot(page, "02_style_saved")

        # ---- D. 恢复原值 (不污染用户配置) ----
        restore = {k: before.get(k) for k in (
            "frame_limit_enabled", "target_stream_fps", "use_half",
            "mediapipe_enabled", "mediapipe_pose", "mediapipe_hands",
            "mediapipe_confidence", "mediapipe_interval",
            "mediapipe_custom_style", "mediapipe_pose_color", "mediapipe_pose_point_color",
            "mediapipe_pose_thickness", "mediapipe_hands_color", "mediapipe_hands_point_color",
            "mediapipe_hands_thickness")}
        requests.post(f"{API}/source/stream/config", json=restore, timeout=10)
        fin = _cfg()
        run.step("D1 原配置已恢复",
                 fin.get("mediapipe_hands_color") == before.get("mediapipe_hands_color")
                 and fin.get("mediapipe_custom_style") == before.get("mediapipe_custom_style"),
                 f"color={fin.get('mediapipe_hands_color')}")

        real = filter_console_errors(cerrs)
        run.step("E1 控制台无前端逻辑报错", not real, f"真报错={real[:3]}")

        ctx.close()
        browser.close()
finally:
    pass

raise SystemExit(run.finish())
