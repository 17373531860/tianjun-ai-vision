# -*- coding: utf-8 -*-
"""蒸镀点检现场配置流程离线核对 (2026-09-13 工程师口径 4/5/6)。

现场口径:
  4. 无蒸镀开始/结束信号、无固定开始动作 — 像目标检测一样始终在跑
  5. 四项点检各自独立计时, 确认 A 不重置 B
  6. 只认操作员 (蓝工装); 黄背心外协不计入确认

本脚本按即将写入「蒸镀点检演示」的 facing_dwell 规则, 在 Camera 01
真实视频上抽帧跑 YOLO11n 检人 + person_orientation, 核对:
  - 文档示范段是否出确认 episode
  - 黄背心外协是否被站位区+朝向挡住
  - 四项 last_confirm 时钟是否互相独立

用法: python tests/uat/duamo_field_sop_validation.py
输出: /tmp/duamo3/sop_report.txt + 若干 overlay 帧
"""
from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from ultralytics import YOLO  # noqa: E402

from backend.services.person_orientation import estimate_yaw  # noqa: E402

VDIR = (
    "/Users/tianjun/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
    "xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/video/2026-09"
)
OUT = "/tmp/duamo3"
SAMPLE_FPS = 2.0
TOL = 45.0
DWELL_S = 2.0
MIN_BOX_H = 0.08  # 画面高占比, 滤脚底/残框; 外协近景反而更大, 不能靠这个认操作员

# Camera 01 960x544 固定机位 (ea7aa73 / 1024afc / 26ce86d 同构图)
# 仪表点: 控制屏簇 / 舱口膜面 / 卷绕膜卷留边 / 平台铝舟区
STATIONS = [
    {"id": "param", "name": "工艺参数", "xy": (0.34, 0.40),
     "timeout_s": 300,
     "region": [(0.24, 0.26), (0.42, 0.26), (0.42, 0.64), (0.24, 0.64)],
     "demo": (14, 35)},
    {"id": "film", "name": "膜面", "xy": (0.20, 0.36),
     "timeout_s": 360,
     "region": [(0.14, 0.22), (0.30, 0.22), (0.30, 0.58), (0.14, 0.58)],
     "demo": (35, 45)},
    {"id": "edge", "name": "留边", "xy": (0.70, 0.28),
     "timeout_s": 420,
     "region": [(0.52, 0.08), (0.82, 0.08), (0.82, 0.48), (0.52, 0.48)],
     "demo": (47, 58)},
    {"id": "boat", "name": "铝舟铝线", "xy": (0.58, 0.16),
     "timeout_s": 600,
     "region": [(0.48, 0.00), (0.72, 0.00), (0.72, 0.32), (0.48, 0.32)],
     "demo": (60, 70)},
]
DOOR = {
    "name": "进入卷绕小车门",
    "region": [(0.16, 0.22), (0.30, 0.22), (0.30, 0.52), (0.16, 0.52)],
    "demo": (79, 91),  # 26ce86d 工程师口述 1:19-1:31
}

VIDEOS = [
    ("ea7aa73", os.path.join(VDIR, "ea7aa73b0fbb7c44cd0c609b73a4aeed.mp4"),
     None, "文档(2)配套巡回 3min"),
    ("1024afc", os.path.join(VDIR, "1024afc07dfd0d93f5c4c3a7f75632a9.mp4"),
     (0, 180), "Camera01 长录像前 3min"),
    ("26ce86d", os.path.join(VDIR, "26ce86d62ced5e01432f1997ff032f2c.mp4"),
     None, "擦拭+进小车门"),
]


def _in_poly(nx, ny, poly):
    inside = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if ((yi > ny) != (yj > ny)
                and nx < (xj - xi) * (ny - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def _blue_uniform(crop_bgr) -> bool:
    """蓝工装启发式: HSV 蓝色像素占比. 黄背心外协应返回 False."""
    if crop_bgr is None or crop_bgr.size == 0:
        return False
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (90, 40, 40), (130, 255, 255))
    ratio = float(mask.mean()) / 255.0
    # 黄背心
    ymask = cv2.inRange(hsv, (18, 80, 80), (40, 255, 255))
    yratio = float(ymask.mean()) / 255.0
    return ratio >= 0.08 and yratio < 0.12


def _angle_diff(a, b):
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _bearing(box, xy, wh):
    w, h = wh
    cx = (box[0] + box[2]) / 2.0 / w
    cy = (box[1] + box[3]) / 2.0 / h
    aspect = h / float(w)
    return math.degrees(math.atan2((xy[1] - cy) * aspect, xy[0] - cx)), (cx, cy)


def run_video(tag, path, window, det, pose_model):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, int(round(fps / SAMPLE_FPS)))
    t0, t1 = window if window else (0, 10**9)

    last_confirm = {s["id"]: None for s in STATIONS}
    dwell = {s["id"]: 0.0 for s in STATIONS}
    episodes = {s["id"]: [] for s in STATIONS}
    door_hits = []
    vest_facing_hits = []  # 外协若面向仪表, 记录 (证明必须做操作员过滤)
    stats = {"frames": 0, "persons": 0, "ops": 0, "vests": 0}

    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = idx / fps
        idx += 1
        if t < t0:
            continue
        if t > t1:
            break
        if (idx - 1) % step:
            continue
        stats["frames"] += 1
        res = det.predict(frame, verbose=False, conf=0.30, imgsz=640, classes=[0])[0]
        boxes = []
        if res.boxes is not None:
            for b in res.boxes:
                x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
                if (y2 - y1) < h * MIN_BOX_H:
                    continue
                crop = frame[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)]
                is_op = _blue_uniform(crop)
                boxes.append(((x1, y1, x2, y2), is_op, crop))
        stats["persons"] += len(boxes)
        stats["ops"] += sum(1 for _, op, _ in boxes if op)
        stats["vests"] += sum(1 for _, op, _ in boxes if not op)

        dt = 1.0 / SAMPLE_FPS
        hit_now = {s["id"]: False for s in STATIONS}
        for box, is_op, _crop in boxes:
            est = estimate_yaw(frame, box)
            if not est:
                continue
            yaw = float(est["yaw_deg"])
            for s in STATIONS:
                nx = (box[0] + box[2]) / 2.0 / w
                ny = (box[1] + box[3]) / 2.0 / h
                if not _in_poly(nx, ny, s["region"]):
                    continue
                bearing, _ = _bearing(box, s["xy"], (w, h))
                if _angle_diff(yaw, bearing) > TOL:
                    continue
                if not is_op:
                    vest_facing_hits.append((t, s["name"]))
                    continue
                hit_now[s["id"]] = True
        for s in STATIONS:
            sid = s["id"]
            if hit_now[sid]:
                dwell[sid] += dt
                if dwell[sid] >= DWELL_S and (
                    not episodes[sid] or t - episodes[sid][-1] > 4
                ):
                    episodes[sid].append(round(t, 1))
                    last_confirm[sid] = t
            else:
                dwell[sid] = 0.0

        # 小车门: 操作员中心进区即记
        for box, is_op, _ in boxes:
            if not is_op:
                continue
            nx = (box[0] + box[2]) / 2.0 / w
            ny = (box[1] + box[3]) / 2.0 / h
            if _in_poly(nx, ny, DOOR["region"]):
                if not door_hits or t - door_hits[-1] > 3:
                    door_hits.append(round(t, 1))

    cap.release()
    return {
        "tag": tag, "stats": stats, "episodes": episodes,
        "last_confirm": last_confirm, "door_hits": door_hits,
        "vest_facing_hits": vest_facing_hits[:12],
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    det = YOLO("backend/data/models/yolo11n.pt")
    lines = []
    def log(m):
        print(m, flush=True)
        lines.append(m)

    log("=== 现场 SOP 视频核对 (独立计时 / 只认操作员 / 无开始动作) ===")
    log(f"容差 {TOL}°  确认驻留 {DWELL_S}s  抽帧 {SAMPLE_FPS}Hz")
    for s in STATIONS:
        log(f"  {s['name']}: 仪表{s['xy']} 超时{s['timeout_s']}s 示范{s['demo']}")

    all_ok = True
    for tag, path, window, title in VIDEOS:
        log(f"\n---- {tag} {title} ----")
        r = run_video(tag, path, window, det, None)
        st = r["stats"]
        log(f"  抽帧 {st['frames']}  人框 {st['persons']}  蓝工装 {st['ops']}  非工装 {st['vests']}")
        for s in STATIONS:
            eps = r["episodes"][s["id"]]
            demo = s["demo"]
            in_demo = [t for t in eps if demo[0] - 2 <= t <= demo[1] + 2]
            mark = "✓" if in_demo else "·"
            if tag == "ea7aa73" and s["id"] in ("param", "film", "edge", "boat"):
                if not in_demo:
                    all_ok = False
                    mark = "✗"
            log(f"  {mark} {s['name']} episode@ {eps}  (示范窗 {demo} 命中 {in_demo})")
        log(f"  小车门进入: {r['door_hits']}")
        log(f"  外协面向仪表但被工装过滤: {r['vest_facing_hits']}")
        # Q5: 四时钟互相独立 — 打印 last_confirm, 不应要求它们相同
        lc = r["last_confirm"]
        log(f"  独立时钟 last_confirm: { {k: (None if v is None else round(v,1)) for k,v in lc.items()} }")
        confirmed = [k for k, v in lc.items() if v is not None]
        if len(confirmed) >= 2:
            times = [lc[k] for k in confirmed]
            if max(times) - min(times) > 3:
                log("  ✓ 至少两项确认时刻相差 >3s, 时钟独立成立")
            else:
                log("  · 多项确认时刻接近 (同一次巡回连着看, 仍是独立计数器)")

    log("\n结论标记: ✓命中  ·本段无示范/未要求  ✗示范段应命中却没有")
    log("all_ok=" + str(all_ok))
    with open(os.path.join(OUT, "sop_report.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
