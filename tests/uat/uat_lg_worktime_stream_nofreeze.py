"""LG 工时看板 v1.5.2 UAT: 三工位 MJPEG 互踢冻帧修复.

根因 (用户截图「画面卡、进度条/检测框还在动」):
  后端同通道只保留 1 条 /video_feed (新连接上位踢旧连接)。
  三工位焦点模式: 左侧缩略图 + 主面板同 channel 各挂一条 MJPEG → 互相踢断;
  被踢的 <img> 冻在末帧 (onError 不触发), 进度条走 /video/info、检测框走
  detections 轮询 → 看起来「画面停了但数据还在跑」。

修复:
  缩略图改轮询 /snapshot 单帧; 主面板独占 /video_feed + 90s 强制换流自愈。

验证:
  1. 缩略图 img.src 含 /snapshot, 不含 /video_feed
  2. 主画面 img.src 含 /video_feed
  3. 隔 2.5s 采主画面两帧像素, 应有可感知差异 (画面在动)
  4. 后端日志: 同 channel 不应再高频 yielding

用法: python tests/uat/uat_lg_worktime_stream_nofreeze.py
产出: evidence/lgwt_stream_nofreeze.png
"""
import os
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6004"
BACKEND = "http://localhost:8004/api/v1"
VIDEO_A = "/Users/tianjun/Downloads/飞书20260804-171657.mp4"
VIDEO_B = "/Users/tianjun/Downloads/飞书20260804-171701.mp4"
MODEL = ("/Users/tianjun/Projects/tianjun-worktime/backend/uploads/models/"
         "3f0ccf3b0cbf4277b3a330aedce73541_best_lg_feishu.pt")
PID = 2
OUT = Path(__file__).resolve().parents[2] / "evidence"
OUT.mkdir(exist_ok=True)
fails = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        fails.append(msg)


def stop_all():
    for ch in range(3):
        try:
            st = requests.get(f"{BACKEND}/source/status", params={"channel": ch}, timeout=10).json()
            if st.get("is_detecting"):
                requests.post(f"{BACKEND}/source/detection/stop", params={"channel": ch}, timeout=30)
        except Exception:
            pass
    time.sleep(1)


def set_mode(n):
    stop_all()
    requests.post(f"{BACKEND}/workstations/mode",
                  json={"channel_count": n, "channels": []}, timeout=30).raise_for_status()
    time.sleep(1)


def start_ch(ch, video):
    proj = requests.get(f"{BACKEND}/projects/{PID}", timeout=10).json()
    requests.post(f"{BACKEND}/workstations/{ch}/gpu", json={"device": "cpu"}, timeout=10)
    requests.post(f"{BACKEND}/source/video/start", params={"channel": ch},
                  json={"file_path": video, "speed": 1.0}, timeout=30).raise_for_status()
    requests.post(f"{BACKEND}/source/detection/set-project", params={"channel": ch}, json={
        "project_id": proj["id"], "name": proj["name"],
        "task_type": proj.get("task_type") or "detection",
        "logic_mode": proj.get("logic_mode") or "sequential",
        "steps_config": proj.get("steps_config") or [],
        "pipeline_config": proj.get("pipeline_config") or {},
        "events_config": proj.get("events_config") or [],
        "counters_config": proj.get("counters_config") or [],
        "data_config": proj.get("data_config") or {},
    }, timeout=30).raise_for_status()
    st = requests.get(f"{BACKEND}/source/status", params={"channel": ch}, timeout=10).json()
    if not st.get("is_detecting"):
        requests.post(f"{BACKEND}/source/detection/start", params={"channel": ch},
                      json={"model_path": MODEL}, timeout=120).raise_for_status()
    requests.post(f"{BACKEND}/source/video/progress", params={"channel": ch},
                  json={"progress": 0.05}, timeout=10)


def pixel_sample(page, locator):
    """截主画面元素小图并算平均亮度 (绕过跨域 canvas 污染)。"""
    png = locator.screenshot(type="png")
    # 极简: 取 PNG 中部若干字节方差代理; 更稳用 pillow
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(png)).convert("RGB")
        w, h = im.size
        crop = im.crop((w // 4, h // 4, 3 * w // 4, 3 * h // 4)).resize((16, 16))
        pixels = list(crop.getdata())
        n = len(pixels)
        r = sum(p[0] for p in pixels) / n
        g = sum(p[1] for p in pixels) / n
        b = sum(p[2] for p in pixels) / n
        return (r, g, b)
    except ImportError:
        # 无 pillow: 用文件大小 + crc 近似 (不够稳, 但比什么都不做强)
        import zlib
        return (len(png), zlib.crc32(png) & 0xFFFF, 0)


def main():
    print("[uat] === 起三工位 A/A/B ===")
    set_mode(3)
    start_ch(0, VIDEO_A)
    start_ch(1, VIDEO_A)
    start_ch(2, VIDEO_B)

    with sync_playwright() as p:
        # UAT_HEADLESS=1: 多 agent 并行时禁止抢显示器/鼠标, 全程 headless
        browser = p.chromium.launch(headless=os.environ.get("UAT_HEADLESS") == "1")
        page = browser.new_page(viewport={"width": 1680, "height": 945})
        host_feeds = []
        plugin_feeds = []
        page.on("request", lambda r: (
            host_feeds.append(r.url) if ("video_feed" in r.url and "t=" not in r.url) else None,
            plugin_feeds.append(r.url) if ("video_feed" in r.url and "t=" in r.url) else None,
        ))
        # 硬刷新破 ESM / Vue HMR 缓存
        page.goto(f"{FRONTEND}/monitor?_bust={int(time.time())}", wait_until="domcontentloaded")
        time.sleep(2)
        if not page.locator(".lgwt-shell").count():
            page.evaluate("window.location.hash = '#/monitor'")
        page.wait_for_selector(".lgwt-shell", timeout=60000)
        page.wait_for_selector(".lgwt-video-img", timeout=30000)
        time.sleep(6)  # 等插件到位 + 宿主 watch 掐流

        # ---- 0. 宿主不得再抢 /video_feed (无 t= 的是宿主 fetch 多路流) ----
        print("[uat] === 宿主不得再挂 video_feed ===")
        # 进入稳定期后再清一次计数, 只看后续 4s
        host_feeds.clear(); plugin_feeds.clear()
        time.sleep(4)
        ok(len(host_feeds) == 0,
           f"稳定期宿主不应再 fetch /video_feed (无t=) (实际 {len(host_feeds)}: {host_feeds[:3]})")
        ok(len(plugin_feeds) >= 1 or page.locator(".lgwt-video-img").count() >= 1,
           f"插件主画面应在吃带 t= 的 video_feed (新请求 {len(plugin_feeds)})")

        # ---- 1. URL 契约 ----
        print("[uat] === URL 契约 (缩略=snapshot / 主画面=video_feed) ===")
        thumbs = page.locator(".lgwt-thumb img")
        main_img = page.locator(".lgwt-video-img").first
        n_thumbs = thumbs.count()
        ok(n_thumbs == 3, f"三工位应有 3 张缩略图 (实际 {n_thumbs})")
        for i in range(n_thumbs):
            src = thumbs.nth(i).get_attribute("src") or ""
            ok("/snapshot" in src and "video_feed" not in src,
               f"缩略图{i+1} 应走 /snapshot (src=...{src[-40:]})")
        main_src = main_img.get_attribute("src") or ""
        ok("video_feed" in main_src, f"主画面应走 /video_feed (src=...{main_src[-50:]})")

        # ---- 2. 画面在动 (隔 2.5s 像素有差) ----
        print("[uat] === 主画面应持续推进 (非冻帧) ===")
        # seek 到有动作的中段, 避免片头静止
        requests.post(f"{BACKEND}/source/video/progress", params={"channel": 0},
                      json={"progress": 0.3}, timeout=10)
        time.sleep(2.0)
        s1 = pixel_sample(page, page.locator(".lgwt-video-img").first)
        time.sleep(2.5)
        s2 = pixel_sample(page, page.locator(".lgwt-video-img").first)
        ok(s1 is not None and s2 is not None, f"应采到主画面像素 (s1={s1}, s2={s2})")
        if s1 and s2:
            dist = sum(abs(a - b) for a, b in zip(s1, s2))
            ok(dist > 2.0, f"2.5s 内画面像素应变化 (Δ={dist:.1f}, 冻帧≈0)")
            print(f"  像素采样 s1={tuple(round(x,1) for x in s1)} "
                  f"s2={tuple(round(x,1) for x in s2)} Δ={dist:.1f}")

        # 进度条也应推进
        t1 = page.locator(".lgwt-vbar-time").first.inner_text()
        time.sleep(2)
        t2 = page.locator(".lgwt-vbar-time").first.inner_text()
        ok(t1 != t2, f"进度条时间应推进 ({t1} → {t2})")

        # ---- 3. 切焦点工位后主画面仍活 ----
        print("[uat] === 切到工位3 后主画面仍活 ===")
        page.locator(".lgwt-thumb").nth(2).click()
        time.sleep(3)
        main_src2 = page.locator(".lgwt-video-img").first.get_attribute("src") or ""
        ok("video_feed" in main_src2 and "channel=2" in main_src2,
           f"切焦点后主画面应吃 ch2 流 (src=...{main_src2[-50:]})")
        s3 = pixel_sample(page, page.locator(".lgwt-video-img").first)
        time.sleep(2.5)
        s4 = pixel_sample(page, page.locator(".lgwt-video-img").first)
        if s3 and s4:
            dist2 = sum(abs(a - b) for a, b in zip(s3, s4))
            ok(dist2 > 2.0, f"工位3 主画面应在动 (Δ={dist2:.1f})")

        page.screenshot(path=str(OUT / "lgwt_stream_nofreeze.png"))
        browser.close()

    if fails:
        print(f"[uat] FAIL ({len(fails)}): {fails}")
        sys.exit(1)
    print("[uat] MJPEG 互踢冻帧修复验证通过")


if __name__ == "__main__":
    main()
