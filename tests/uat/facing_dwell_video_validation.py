# -*- coding: utf-8 -*-
"""朝向模块离线验证: 用工程师现场真实视频跑 person_orientation。

对每段视频抽帧 → 全帧跑 MediaPipe Pose 估计 yaw → 统计检出率/yaw 分布,
并把带朝向箭头的可视化帧存到 tests/uat/facing_dwell_out/。

用法: python tests/uat/facing_dwell_video_validation.py
"""
import os
import sys
import math

os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'threads;1')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from backend.services.person_orientation import estimate_yaw, is_available  # noqa: E402

BASE = ("/Users/tianjun/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
        "xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/attach/"
        "77aafe9c529463ae0166060bdc7a8efb/2026-09/Rec/7d9c724e2b700632/V")
VIDEOS = [
    (f"{BASE}/2.mp4", "确认膜面是否平整"),
    (f"{BASE}/4.mp4", "确认留边是否正常"),
    (f"{BASE}/6.mp4", "确认铝丝是否正常"),
    (f"{BASE}/8.mp4", "每天点检作业"),
    (f"{BASE}/10.mp4", "把这个门打开里面"),
    (f"{BASE}/12.mp4", "开仓后视频"),
    ("/Users/tianjun/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
     "xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/video/2026-09/"
     "3d186d08bfd844e25735136d154847d4.mp4", "车间全景"),
]
OUT_DIR = os.path.join(os.path.dirname(__file__), "facing_dwell_out")
SAMPLE_EVERY_S = 1.0     # 每秒抽 1 帧
MAX_SAVED = 4            # 每段最多存 4 张可视化


def draw_arrow(frame, yaw_deg, head_yaw_deg, conf):
    h, w = frame.shape[:2]
    cx, cy = w // 2, h // 2
    L = min(w, h) // 4
    rad = math.radians(yaw_deg)
    # yaw 是图像坐标系角 (y 向下为正), 绘制直接用 +sin
    ex, ey = int(cx + L * math.cos(rad)), int(cy + L * math.sin(rad))
    cv2.arrowedLine(frame, (cx, cy), (ex, ey), (0, 220, 0), 4, tipLength=0.25)
    txt = f"yaw={yaw_deg:.0f} conf={conf:.2f}"
    if head_yaw_deg is not None:
        txt += f" head={head_yaw_deg:.0f}"
        rad2 = math.radians(head_yaw_deg)
        ex2 = int(cx + L * 0.7 * math.cos(rad2))
        ey2 = int(cy + L * 0.7 * math.sin(rad2))
        cv2.arrowedLine(frame, (cx, cy), (ex2, ey2), (0, 160, 255), 2,
                        tipLength=0.3)
    cv2.putText(frame, txt, (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (0, 220, 0), 2)
    return frame


def run_video(path, label):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"label": label, "error": "无法打开"}
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, int(fps * SAMPLE_EVERY_S))
    total = detected = saved = 0
    yaws = []
    idx = 0
    stem = os.path.splitext(os.path.basename(path))[0]
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step:
            idx += 1
            continue
        idx += 1
        total += 1
        h, w = frame.shape[:2]
        r = estimate_yaw(frame, (0, 0, w, h))   # 全帧当人框 (手机竖拍近景)
        if r is not None:
            detected += 1
            yaws.append(r["yaw_deg"])
            if saved < MAX_SAVED:
                vis = draw_arrow(frame.copy(), r["yaw_deg"],
                                 r.get("head_yaw_deg"), r["conf"])
                out = os.path.join(OUT_DIR, f"{stem}_{label}_{total:03d}.jpg")
                cv2.imwrite(out, vis)
                saved += 1
    cap.release()
    return {"label": label, "file": os.path.basename(path),
            "frames": total, "detected": detected,
            "rate": round(detected / total, 2) if total else 0,
            "yaw_min": round(min(yaws), 1) if yaws else None,
            "yaw_max": round(max(yaws), 1) if yaws else None,
            "yaw_span": round(max(yaws) - min(yaws), 1) if yaws else None}


def main():
    if not is_available():
        print("!! MediaPipe 不可用, 无法验证")
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"{'视频':<28}{'抽帧':>5}{'检出':>5}{'检出率':>7}{'yaw范围':>18}")
    for path, label in VIDEOS:
        if not os.path.exists(path):
            print(f"{label:<28}  文件不存在, 跳过")
            continue
        r = run_video(path, label)
        if "error" in r:
            print(f"{label:<28}  {r['error']}")
            continue
        span = (f"[{r['yaw_min']}, {r['yaw_max']}]"
                if r['yaw_min'] is not None else "-")
        print(f"{r['label']:<26}{r['frames']:>5}{r['detected']:>5}"
              f"{r['rate']:>7.0%}{span:>20}")
    print(f"\n可视化帧已存: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
