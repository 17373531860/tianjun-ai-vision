# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 检测中心视频流健康度 — 卡顿 / 黑屏 / 只剩检测框 三症状专项。

现场叙事: 操作员在监控页对 TP 工位(区域事件模式)开检测, 画面应持续流畅播放
现场视频, 检测框叠在真实画面上; 不允许出现 (a) 画面冻结不动 (b) 整块黑屏
(c) 黑底上只剩检测框在跳 (历史"频闪"事故形态)。本脚本用真实 0707 现场视频 +
TP-v5 真模型开检, 连续 90 秒对视频区做像素级采样:
  - 亮度中位数 > 25   → 不是黑屏/裸框
  - 相邻采样帧差异率 > 60% → 画面在动, 不是冻结
  - 无连续 5 次(≈10s)完全相同的采样 → 无长冻结
  - 检测结果在场标志无高频空/满交替 → 无双推理线程频闪回归
  - 周期结算计数在窗口内前进 → 判定链路活着
证据: 视频 webm + 关键截图 + run.json (三件套)。
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

SAMPLE_SECONDS = 90
SAMPLE_INTERVAL = 2.0

run = UatRun("monitor_stream_health")


def _results():
    try:
        return requests.get(f"{API}/source/detection/results?channel={CH}",
                            timeout=5).json()
    except Exception:
        return {}


def _img_stats(png_bytes):
    """返回 (平均亮度, 灰度缩略图 bytes) 供帧间对比。"""
    im = Image.open(io.BytesIO(png_bytes)).convert("L").resize((64, 36))
    px = list(im.getdata())
    return sum(px) / len(px), bytes(px)


def _diff(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


try:
    # ---- 0. 起 TP 项目 + 真实视频 + 真模型检测 ----
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=15)
    requests.post(f"{API}/source/video/stop?channel={CH}", timeout=15)
    r = requests.post(f"{API}/projects/{TP_PID}/activate", timeout=30)
    run.step("00 激活 TP 项目(区域事件)", r.status_code == 200, f"status={r.status_code}")
    r = requests.post(f"{API}/source/video/start?channel={CH}",
                      json={"file_path": TP_VIDEO}, timeout=30)
    run.step("01 真实现场视频源已起", r.status_code == 200, f"status={r.status_code}")
    r = requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"model_path": TP_MODEL, "conf": 0.25}, timeout=120)
    run.step("02 检测已启动 (TP-v5 真模型)", r.status_code == 200,
             f"status={r.status_code} body={r.text[:120]}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(8)
        run.shot(page, "01_monitor_loaded")

        video_box = page.locator("img[src*='video_feed']").first
        run.step("03 视频流 img 元素在页面上", video_box.count() >= 1)
        container = page.locator("div.bg-black.border-2").first

        # ---- 采样循环: 像素健康度 + 检测在场标志 ----
        brightness, thumbs, presence, ok_counts = [], [], [], []
        det_poll_next = 0.0
        t_end = time.time() + SAMPLE_SECONDS
        base_ok = (_results().get("counters") or {}).get("合格总数", 0)
        while time.time() < t_end:
            try:
                png = container.screenshot(timeout=8000)
                bri, thumb = _img_stats(png)
                brightness.append(bri)
                thumbs.append(thumb)
            except Exception as e:  # noqa: BLE001
                print(f">>> sample skip: {e}", flush=True)
            now = time.time()
            if now >= det_poll_next:
                d = _results()
                dets = d.get("detections") or []
                presence.append(1 if dets else 0)
                ok_counts.append((d.get("counters") or {}).get("合格总数", 0))
                det_poll_next = now + 0.3
            time.sleep(SAMPLE_INTERVAL)
        run.shot(page, "02_monitor_after_sampling")

        # ---- 判定 ----
        n = len(brightness)
        med_bri = sorted(brightness)[n // 2] if n else 0
        run.step("04 无黑屏/裸框: 亮度中位数>25",
                 med_bri > 25, f"median={med_bri:.1f} n={n} min={min(brightness):.1f}")

        diffs = [_diff(thumbs[i], thumbs[i + 1]) for i in range(len(thumbs) - 1)]
        moving = sum(1 for d in diffs if d > 1.0)
        run.step("05 画面在动: 相邻采样差异率>60%",
                 diffs and moving / len(diffs) > 0.6,
                 f"moving={moving}/{len(diffs)}")

        frozen_run = cur = 0
        for d in diffs:
            cur = cur + 1 if d < 0.3 else 0
            frozen_run = max(frozen_run, cur)
        run.step("06 无长冻结: 最长静止连击<5 (≈10s)",
                 frozen_run < 5, f"max_frozen_run={frozen_run}")

        # 双推理线程频闪形态 = 在场标志逐次 0/1 交替; 统计最长交替连击
        alt_run = cur = 0
        for i in range(1, len(presence)):
            cur = cur + 1 if presence[i] != presence[i - 1] else 0
            alt_run = max(alt_run, cur)
        run.step("07 无检测频闪: 在场标志最长0/1交替连击<6",
                 alt_run < 6, f"max_alt_run={alt_run} samples={len(presence)}")

        final_ok = ok_counts[-1] if ok_counts else 0
        run.step("08 判定链路活着: 周期结算计数前进",
                 final_ok > base_ok, f"合格 {base_ok} -> {final_ok}")

        real = filter_console_errors(cerrs)
        run.step("09 控制台无前端逻辑报错", not real, f"真报错={real[:3]}")
        ctx.close()
        browser.close()
finally:
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=15)
    requests.post(f"{API}/source/video/stop?channel={CH}", timeout=15)

raise SystemExit(run.finish())
