"""TP 标准回放·第一步: 模型全片推理 → 归一化检测缓存 (6 类).

用途: v5 / v5.1 换权重后的新旧对比基线。换模型只改 MODEL 路径,
输出缓存路径 OUT 建议带版本号 (如 /tmp/tp_v51_dets_cache.json),
然后跑同目录 replay.py 出逐周期结算流水对账。
"""
import json
import time

import cv2
from ultralytics import YOLO

VIDEO = ('/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/file/'
         '2026-07/现场视频0707/20260707_20260707164731_20260707165907_164759.mp4')
MODEL = '/tmp/tp_v5/train_tp_v5/weights/best.pt'
OUT = '/tmp/tp_v5_dets_cache.json'

model = YOLO(MODEL)
names = model.names
cap = cv2.VideoCapture(VIDEO)
fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
print(f'fps={fps:.2f} total={total}', flush=True)

frames = []
idx = 0
t0 = time.time()
while True:
    ok, frame = cap.read()
    if not ok:
        break
    h, w = frame.shape[:2]
    res = model.predict(frame, imgsz=960, conf=0.2, verbose=False)[0]
    dets = []
    for b in res.boxes:
        x1, y1, x2, y2 = b.xyxy[0].tolist()
        dets.append({
            'label': names[int(b.cls[0])],
            'confidence': round(float(b.conf[0]), 4),
            'x': round(x1 / w, 5), 'y': round(y1 / h, 5),
            'w': round((x2 - x1) / w, 5), 'h': round((y2 - y1) / h, 5),
        })
    frames.append(dets)
    idx += 1
    if idx % 2000 == 0:
        el = time.time() - t0
        print(f'{idx}/{total} ({idx/total*100:.0f}%) {idx/el:.1f}fps', flush=True)
cap.release()
json.dump({'fps': fps, 'frames': frames}, open(OUT, 'w'), ensure_ascii=False)
print(f'done {idx} frames -> {OUT} ({time.time()-t0:.0f}s)', flush=True)
