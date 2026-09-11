# -*- coding: utf-8 -*-
"""蒸镀点检固定机位离线验证 (2026-09-11): 真实 CCTV 视频跑两段式朝向管线。

素材: 客户发的 Camera 01 固定机位视频 (ULVAC ULFARAD-600 蒸镀机, 960x544),
《镀膜过程监控要求》引用其时间戳作为示范段:
  0:50-0:59  确认各工艺参数 (操作员面向控制屏)
  1:00-1:06  确认膜面
  1:09-1:15  确认铝舟/铝线

验证目标 (facing_dwell 上线前的金标准检查):
  1. 固定广角机位下两段式管线 (YOLO 检人 → 裁剪 → 朝向) 的人/朝向检出率;
  2. 示范段内 "操作员朝向与 人→仪表点连线 的夹角 ≤ 容差" 是否持续成立
     (即 facing_dwell 规则在真实素材上能否出 episode);
  3. 输出带朝向箭头+对准线的可视化 overlay 视频供人眼复核。

用法: python tests/uat/duamo_cctv_facing_validation.py [视频路径]
输出: /tmp/duamo/out/ (逐秒时间线 + episode 汇总 + overlay 视频)
"""
import math
import os
import sys

os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'threads;1')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from backend.services.person_orientation import estimate_yaw, is_available  # noqa: E402

VIDEO = sys.argv[1] if len(sys.argv) > 1 else "/tmp/duamo/v2.mp4"
OUT_DIR = "/tmp/duamo/out"

# 仪表点 (归一化, 在 v2 固定机位画面上人工量取): 蒸镀机控制屏
# (工艺参数确认位, 文档 0:50-0:59 示范段操作员所面向的屏)
INSTRUMENT = {"name": "工艺参数控制屏", "xy": (0.465, 0.36)}
TOLERANCE_DEG = 45.0     # 朝向容差 (RFC 默认 35, 广角俯视先放 45 观察分布)
DWELL_MIN_S = 3.0        # 驻留门槛
SAMPLE_FPS = 2.0         # 抽帧频率
WINDOW = (40, 100)       # 分析窗口 (秒): 覆盖三个示范段 + 前后余量
PERSON_CONF = 0.35
MIN_BOX_H_FRAC = 0.10    # 人框高至少占画面 10% (滤远处/误检)


def _person_boxes(model, frame):
    """全帧 YOLO 检人 (生产路径是预置行人模型+ByteTrack, 离线验证等价)。"""
    res = model.predict(frame, verbose=False, conf=PERSON_CONF,
                        imgsz=640, classes=[0])[0]
    boxes = []
    if res.boxes is not None:
        for b in res.boxes:
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
            if (y2 - y1) >= frame.shape[0] * MIN_BOX_H_FRAC:
                boxes.append((x1, y1, x2, y2))
    return boxes


def _alignment(box, yaw_deg, frame_shape):
    """人中心→仪表点 方位角 与 朝向角 的最小夹角 (像素平面口径)。"""
    h, w = frame_shape[:2]
    cx = (box[0] + box[2]) / 2.0
    cy = (box[1] + box[3]) / 2.0
    tx, ty = INSTRUMENT["xy"][0] * w, INSTRUMENT["xy"][1] * h
    bearing = math.degrees(math.atan2(ty - cy, tx - cx))
    d = abs(yaw_deg - bearing) % 360.0
    return d if d <= 180.0 else 360.0 - d


def main():
    if not is_available():
        print("!! 朝向后端不可用")
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    from ultralytics import YOLO
    det = YOLO(os.path.join(os.path.dirname(__file__), '..', '..',
                            'backend', 'data', 'models', 'yolo11n-pose.pt'))

    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 20
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    vw = cv2.VideoWriter(os.path.join(OUT_DIR, 'overlay.mp4'),
                         cv2.VideoWriter_fourcc(*'mp4v'), SAMPLE_FPS, (w, h))
    tx, ty = int(INSTRUMENT["xy"][0] * w), int(INSTRUMENT["xy"][1] * h)

    stats = {"frames": 0, "person_frames": 0, "orient_frames": 0}
    episodes = []          # (start_s, end_s)
    dwell_start = None
    last_hit_ts = None
    print(f"{'秒':>5} {'人数':>4} {'朝向':>8} {'头部':>8} {'对准夹角':>8} 命中")

    ts = WINDOW[0]
    while ts <= WINDOW[1]:
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        stats["frames"] += 1
        boxes = _person_boxes(det, frame)
        if boxes:
            stats["person_frames"] += 1
        vis = frame.copy()
        cv2.drawMarker(vis, (tx, ty), (0, 0, 255), cv2.MARKER_CROSS, 26, 3)
        cv2.putText(vis, INSTRUMENT["name"], (tx + 14, ty - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)

        hit = False
        best = None
        for box in boxes:
            r = estimate_yaw(frame, box)
            cx = int((box[0] + box[2]) / 2)
            cy = int((box[1] + box[3]) / 2)
            cv2.rectangle(vis, (int(box[0]), int(box[1])),
                          (int(box[2]), int(box[3])), (200, 200, 0), 2)
            if r is None:
                continue
            stats["orient_frames"] += 1
            yaw_used = (r["head_yaw_deg"] if r.get("head_yaw_deg") is not None
                        else r["yaw_deg"])
            align = _alignment(box, yaw_used, frame.shape)
            if best is None or align < best[0]:
                best = (align, r)
            ok_hit = align <= TOLERANCE_DEG
            hit = hit or ok_hit
            # 画朝向箭头 (身体绿 / 头部橙) + 人→仪表连线
            L = 60
            for ang, color, thick in ((r["yaw_deg"], (0, 220, 0), 3),
                                      (r.get("head_yaw_deg"), (0, 160, 255), 2)):
                if ang is None:
                    continue
                rad = math.radians(ang)
                cv2.arrowedLine(vis, (cx, cy),
                                (int(cx + L * math.cos(rad)),
                                 int(cy + L * math.sin(rad))),
                                color, thick, tipLength=0.3)
            cv2.line(vis, (cx, cy), (tx, ty),
                     (0, 255, 0) if ok_hit else (90, 90, 90), 1)
            cv2.putText(vis, f"{align:.0f}deg", (cx + 8, cy - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (0, 255, 0) if ok_hit else (160, 160, 160), 2)

        # episode 状态机 (dwell ≥ DWELL_MIN_S 记一次)
        if hit:
            dwell_start = dwell_start if dwell_start is not None else ts
            last_hit_ts = ts
        elif dwell_start is not None and ts - (last_hit_ts or ts) > 1.5:
            if last_hit_ts - dwell_start >= DWELL_MIN_S:
                episodes.append((dwell_start, last_hit_ts))
            dwell_start = None

        state = "●" if hit else ""
        if best:
            r = best[1]
            head = (f"{r['head_yaw_deg']:>7.1f}"
                    if r.get('head_yaw_deg') is not None else "      -")
            print(f"{ts:>5.1f} {len(boxes):>4} {r['yaw_deg']:>8.1f} {head} "
                  f"{best[0]:>8.1f} {state}")
        else:
            print(f"{ts:>5.1f} {len(boxes):>4} {'-':>8} {'-':>8} {'-':>8}")
        cv2.putText(vis, f"t={ts:.0f}s dwell={'YES' if hit else 'no'}",
                    (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (0, 255, 0) if hit else (180, 180, 180), 2)
        vw.write(vis)
        ts += 1.0 / SAMPLE_FPS

    if dwell_start is not None and last_hit_ts and \
            last_hit_ts - dwell_start >= DWELL_MIN_S:
        episodes.append((dwell_start, last_hit_ts))
    cap.release()
    vw.release()

    print()
    print(f"抽帧 {stats['frames']} | 有人帧 {stats['person_frames']} | "
          f"出朝向帧 {stats['orient_frames']}")
    print(f"episode (≥{DWELL_MIN_S}s 对准驻留): "
          f"{[(round(a,1), round(b,1)) for a, b in episodes] or '无'}")
    print(f"文档示范段: 50-59s 应出 episode; overlay: {OUT_DIR}/overlay.mp4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
