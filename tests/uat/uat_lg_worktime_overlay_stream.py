"""LG 工时看板: 检测框叠加 + 单工位 MJPEG 不抢流 (客户反馈复验).

现场叙事:
  1. 操作员在单工位 Monitor 看板看飞书录像检测
  2. 画面应持续滚动, 目标上应有检测框
  3. 后端推理/步骤进度继续推进
  4. UI: canvas.fjjl-det-overlay 存在且有非透明像素; 宿主不再另连 /video_feed
"""
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6004"
BACKEND = "http://localhost:8004/api/v1"
ROOT = "http://localhost:8004"
OUT = Path(__file__).resolve().parents[2] / "evidence"
OUT.mkdir(exist_ok=True)
fails = []


def ok(cond, msg):
    if cond:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")
        fails.append(msg)


def main():
    # 确保单工位 + 有源在跑
    requests.post(f"{BACKEND}/workstations/mode", json={"channel_count": 1, "channels": []}, timeout=10)
    st = requests.get(f"{BACKEND}/source/status", params={"channel": 0}, timeout=10).json()
    print("[status]", {k: st.get(k) for k in ("is_running", "is_detecting", "source_type", "model_loaded", "fps_actual")})
    if not st.get("is_running"):
        # 尝试用已有 video 源启动
        try:
            requests.post(f"{BACKEND}/source/start", params={"channel": 0}, timeout=15)
        except Exception as e:
            print("start warn", e)
    if not st.get("is_detecting"):
        try:
            requests.post(f"{BACKEND}/source/detection/start", params={"channel": 0}, timeout=30)
        except Exception as e:
            print("detect warn", e)
        time.sleep(2)
    st2 = requests.get(f"{BACKEND}/source/status", params={"channel": 0}, timeout=10).json()
    ndet = len(st2.get("detections") or [])
    print("[status2] detecting=", st2.get("is_detecting"), "ndet=", ndet, "fps=", st2.get("fps_actual"))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1680, "height": 945},
            record_video_dir=str(OUT / "uat_video"),
        )
        page = context.new_page()
        feeds = []
        page.on("request", lambda r: feeds.append(r.url) if "/video_feed" in r.url else None)

        page.goto(f"{FRONTEND}/monitor", wait_until="domcontentloaded")
        page.wait_for_selector(".lgwt-dashboard", timeout=25000)
        page.wait_for_timeout(4000)

        overlay_n = page.locator("canvas.fjjl-det-overlay").count()
        ok(overlay_n >= 1, f"存在检测框 canvas (n={overlay_n})")

        img_n = page.locator("img.lgwt-video-img").count()
        ok(img_n >= 1, f"存在看板视频 img (n={img_n})")

        # 宿主双缓冲 img 不应再挂 video_feed (layout.body 独占)
        host_feed_imgs = page.evaluate("""() => {
          const imgs = [...document.querySelectorAll('img')];
          return imgs.filter(i => (i.src||'').includes('/video_feed')
            && !i.classList.contains('lgwt-video-img')).length;
        }""")
        ok(host_feed_imgs == 0, f"宿主不应另挂 video_feed img (n={host_feed_imgs})")

        # 等检测几秒后看 canvas 是否有非透明像素
        page.wait_for_timeout(5000)
        painted = page.evaluate("""() => {
          const c = document.querySelector('canvas.fjjl-det-overlay');
          if (!c) return {ok:false, reason:'no-canvas'};
          const ctx = c.getContext('2d');
          const w = c.width || 0, h = c.height || 0;
          if (w < 2 || h < 2) return {ok:false, reason:'canvas-size', w, h};
          const data = ctx.getImageData(0, 0, w, h).data;
          let nonzero = 0;
          for (let i = 3; i < data.length; i += 16) { if (data[i] > 0) nonzero++; }
          return {ok: nonzero > 0, nonzero, w, h};
        }""")
        print("[canvas]", painted)
        ok(painted.get("ok"), f"检测框 canvas 有绘制像素 ({painted})")

        # 视频 src 稳定 (不应每秒换 t=)
        src1 = page.locator("img.lgwt-video-img").first.get_attribute("src") or ""
        page.wait_for_timeout(3500)
        src2 = page.locator("img.lgwt-video-img").first.get_attribute("src") or ""
        ok(src1 == src2, "3.5s 内视频 URL 未无故刷新 (防自踢 MJPEG)")

        # 请求侧: 页面生命周期内 video_feed 连接数不应爆炸
        feed_reqs = [u for u in feeds if "/video_feed" in u]
        print(f"[network] video_feed reqs={len(feed_reqs)}")
        ok(len(feed_reqs) <= 3, f"video_feed 请求次数可控 ({len(feed_reqs)})")

        page.screenshot(path=str(OUT / "lgwt_overlay_stream_fix.png"), full_page=False)
        context.close()
        browser.close()

    print("\n==== RESULT ====")
    if fails:
        print(f"failed:{len(fails)}")
        for f in fails:
            print(" -", f)
        return 1
    print("failed:0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
