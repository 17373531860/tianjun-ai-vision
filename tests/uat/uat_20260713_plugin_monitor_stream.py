# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 展会插件监控页视频流健康度 (卡顿/黑屏专项, 与主程序版对照)。

现场叙事: 展会环境激活 showcase 插件后, 监控页由插件 iframe 整页接管,
视频流走简化版 <img :src=video_feed>。操作员开检测后, 插件页面上视频
必须持续流畅播放、检测数据面板在走。采样判定与主程序版同口径:
亮度中位数(非黑屏) / 帧间差异率(在动) / 无长冻结。
前置: 后端 8001 + 前端 6001 在跑; TP 项目(id=22) + 真实 0707 视频 + TP-v5 模型。
"""
from __future__ import annotations

import io
import sys
import time

import requests
from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"
CH = 0
TP_PID = 22
TP_VIDEO = ("/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/file/"
            "2026-07/现场视频0707/20260707_20260707164731_20260707165907_164759.mp4")
TP_MODEL = ("/home/qianqian/桌面/word/tianjun-main/backend/uploads/models/"
            "725324eec64c4f179d304aac87a34d6b_tp_v5_best.pt")

SAMPLE_SECONDS = 60
SAMPLE_INTERVAL = 2.0

run = UatRun("plugin_monitor_stream")


def _img_stats(png_bytes):
    im = Image.open(io.BytesIO(png_bytes)).convert("L").resize((64, 36))
    px = list(im.getdata())
    return sum(px) / len(px), bytes(px)


def _diff(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


try:
    r = requests.post(f"{API}/plugins/showcase/activate", timeout=15)
    run.step("00 showcase 插件已激活", r.status_code == 200, f"status={r.status_code}")
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=15)
    requests.post(f"{API}/source/video/stop?channel={CH}", timeout=15)
    r = requests.post(f"{API}/projects/{TP_PID}/activate", timeout=30)
    run.step("01 激活 TP 项目", r.status_code == 200, f"status={r.status_code}")
    r = requests.post(f"{API}/source/video/start?channel={CH}",
                      json={"file_path": TP_VIDEO}, timeout=30)
    run.step("02 真实现场视频源已起", r.status_code == 200, f"status={r.status_code}")
    r = requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"model_path": TP_MODEL, "conf": 0.25}, timeout=120)
    run.step("03 检测已启动 (TP-v5 真模型)", r.status_code == 200, f"status={r.status_code}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(FRONT, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("iframe[src*='showcase-app']", timeout=120000,
                               state="attached")
        time.sleep(6)
        fl = page.frame_locator("iframe[src*='showcase-app']")
        # 插件首页即监控风格布局; 若有导航则点监控入口
        try:
            nav = fl.locator("text=监控").first
            if nav.count():
                nav.click(timeout=3000)
                time.sleep(4)
        except Exception:
            pass
        run.shot(page, "01_plugin_loaded")

        stream_img = fl.locator("img[src*='video_feed']").first
        ok_img = False
        try:
            ok_img = stream_img.count() >= 1
        except Exception:
            pass
        run.step("04 插件页视频流 img 存在", ok_img)

        brightness, thumbs = [], []
        t_end = time.time() + SAMPLE_SECONDS
        while time.time() < t_end:
            try:
                png = stream_img.screenshot(timeout=8000)
                bri, thumb = _img_stats(png)
                brightness.append(bri)
                thumbs.append(thumb)
            except Exception as e:  # noqa: BLE001
                print(f">>> sample skip: {e}", flush=True)
            time.sleep(SAMPLE_INTERVAL)
        run.shot(page, "02_after_sampling")

        n = len(brightness)
        med_bri = sorted(brightness)[n // 2] if n else 0
        run.step("05 无黑屏: 亮度中位数>25", med_bri > 25,
                 f"median={med_bri:.1f} n={n}")
        diffs = [_diff(thumbs[i], thumbs[i + 1]) for i in range(len(thumbs) - 1)]
        moving = sum(1 for d in diffs if d > 1.0)
        run.step("06 画面在动: 相邻采样差异率>60%",
                 diffs and moving / len(diffs) > 0.6, f"moving={moving}/{len(diffs)}")
        frozen_run = cur = 0
        for d in diffs:
            cur = cur + 1 if d < 0.3 else 0
            frozen_run = max(frozen_run, cur)
        run.step("07 无长冻结: 最长静止连击<5", frozen_run < 5,
                 f"max_frozen_run={frozen_run}")

        real = filter_console_errors(cerrs)
        run.step("08 控制台无前端逻辑报错", not real, f"真报错={real[:3]}")
        ctx.close()
        browser.close()
finally:
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=15)
    requests.post(f"{API}/source/video/stop?channel={CH}", timeout=15)

raise SystemExit(run.finish())
