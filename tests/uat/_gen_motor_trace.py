# -*- coding: utf-8 -*-
"""离线生成电机装配视频的逐帧检测轨迹(全标签, 低阈值 0.25)。

产物: tests/uat/_motor_trace_95_255.json
  {"fps": <推理采样fps>, "t0": 95.0, "frames": [{"t": 秒, "detections":
   [{"label", "confidence", "bbox": {x,y,w,h 归一化}}]}]}

供 _replay_motor_trace.py 确定性回放, 以及全流程(螺丝/力矩/标记/横放)
时间窗分析。/tmp 会被系统清理, 所以落在项目目录里。

跑法: ~/anaconda3/envs/tianjun/bin/python tests/uat/_gen_motor_trace.py
"""
import json
import os

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
import cv2  # noqa: E402
from ultralytics import YOLO  # noqa: E402

VIDEO = ("/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/video/"
         "2026-07/1593386d5368d7afda8e0b4f47b15587.mp4")
MODEL = "/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/file/2026-07/best(3).pt"
OUT = os.path.join(os.path.dirname(__file__), "_motor_trace_95_255.json")
T0, T1 = 95.0, 255.0
STRIDE = 1          # 每帧都推理(视频 25fps)

m = YOLO(MODEL)
cap = cv2.VideoCapture(VIDEO)
fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
W = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
H = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
cap.set(cv2.CAP_PROP_POS_MSEC, T0 * 1000)

frames = []
n = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
    if t > T1:
        break
    n += 1
    if n % STRIDE:
        continue
    r = m.predict(frame, conf=0.25, iou=0.45, verbose=False, device=0)[0]
    dets = []
    for b in r.boxes:
        x1, y1, x2, y2 = b.xyxy[0].tolist()
        # bbox 必须是归一化列表 [x, y, w, h] —— 虚拟剧本源按此格式解析
        dets.append({
            "label": r.names[int(b.cls[0])],
            "confidence": round(float(b.conf[0]), 3),
            "bbox": [round(x1 / W, 4), round(y1 / H, 4),
                     round((x2 - x1) / W, 4), round((y2 - y1) / H, 4)],
        })
    frames.append({"t": round(t, 3), "detections": dets})
    if len(frames) % 500 == 0:
        print(f"  已推理 {len(frames)} 帧, t={t:.1f}s", flush=True)

cap.release()
json.dump({"fps": fps / STRIDE, "t0": T0, "frames": frames},
          open(OUT, "w"), ensure_ascii=False)
print(f"完成: {len(frames)} 帧 → {OUT} ({os.path.getsize(OUT)//1024}KB)")
