#!/usr/bin/env python3
"""装弹 demo 数据集构建：抽帧 + 半自动引导标注（YOLO 格式）。

类别：0=bullet 弹  1=magazine 弹匣  2=fist 拳/手套
引导标注来源（仅 demo 用，正式项目请工程师用 X-AnyLabeling 人工标注）：
  bullet   — 分区颜色阈值 + 分水岭拆粘连（托盘区从严防木纹，上部工作区放宽）
  magazine — 首帧模板 + matchTemplate 逐帧跟踪（黑色匣体）
  fist     — 手套白色掩码连通块
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_mag_load_count as d  # noqa: E402

OUT = Path.home() / "datasets" / "mag_demo"
STEP = 6  # 每 6 帧抽 1 张 ≈ 5fps


def bullet_boxes(frame, hsv, tray_mask):
    """两套门槛：托盘区严（防木纹），上部区松（背景是手套/黑匣，无木纹干扰）。"""
    h, s, v = cv2.split(hsv)
    boxes = []
    # 托盘区（严）
    m1 = ((h >= 12) & (h <= 32) & (s >= 88) & (v >= 110)).astype(np.uint8) * 255
    m1 = cv2.bitwise_and(m1, tray_mask)
    # 上部工作区（松）：y < 300 且非托盘
    upper = np.zeros(frame.shape[:2], np.uint8)
    upper[:300, 280:900] = 255
    upper[tray_mask > 0] = 0
    m2 = ((h >= 10) & (h <= 34) & (s >= 65) & (v >= 80)).astype(np.uint8) * 255
    m2 = cv2.bitwise_and(m2, upper)
    for m, strict in ((m1, True), (m2, False)):
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        blobs = d._split_blobs(m, min_area=110, max_area=1600, min_peak=4.0, peak_gap=10)
        for cx, cy, bw, bh, area in blobs:
            x0, y0 = max(0, int(cx - bw / 2)), max(0, int(cy - bh / 2))
            x1, y1 = min(m.shape[1], int(cx + bw / 2)), min(m.shape[0], int(cy + bh / 2))
            hroi = hsv[y0:y1, x0:x1]
            sel = m[y0:y1, x0:x1] > 0
            if hroi.size == 0 or not sel.any():
                continue
            hm = float(hroi[:, :, 0][sel].mean())
            sm = float(hroi[:, :, 1][sel].mean())
            vm = float(hroi[:, :, 2][sel].mean())
            if strict:
                if sm < 95 or vm < 128:
                    continue
            else:
                # 上部区逐 blob 均值门：h>=14 排皮肤（皮肤 h≈9-10），
                # s>=93 且 v>=126 排托盘上沿木纹（木纹 s≈82-89 / v≈118-123）
                if hm < 14 or sm < 93 or vm < 126:
                    continue
            if max(bw, bh) > 80 or min(bw, bh) < 6 or area > 1500:
                continue
            boxes.append((cx, cy, bw, bh))
    return boxes


def fist_boxes(glove_core):
    n, _, st, _ = cv2.connectedComponentsWithStats(glove_core, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = (int(st[i, j]) for j in range(5))
        if a >= 6000:
            out.append((x + w / 2, y + h / 2, w, h))
    return out


def to_yolo(cls, cx, cy, w, h, W, H):
    return f"{cls} {cx / W:.6f} {cy / H:.6f} {w / W:.6f} {h / H:.6f}"


def main():
    for sub in ("images/all", "labels/all", "review"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(d.VIDEO_DEFAULT)
    ok, first = cap.read()
    assert ok
    trk = d.MagTracker(first)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    H, W = first.shape[:2]
    i = 0
    n_saved = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        mag = trk.update(frame)
        if i % STEP == 0:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            tray = d._poly_mask(frame.shape, d.TRAY_INNER)
            glove_core, _ = d._glove_mask(hsv, frame.shape)
            lines = []
            for cx, cy, bw, bh in bullet_boxes(frame, hsv, tray):
                lines.append(to_yolo(0, cx, cy, bw, bh, W, H))
            mx, my, mw, mh = mag
            lines.append(to_yolo(1, mx + mw / 2, my + mh / 2, mw, mh, W, H))
            for cx, cy, bw, bh in fist_boxes(glove_core):
                lines.append(to_yolo(2, cx, cy, bw, bh, W, H))
            stem = f"f{i:05d}"
            cv2.imwrite(str(OUT / "images/all" / f"{stem}.jpg"), frame,
                        [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            (OUT / "labels/all" / f"{stem}.txt").write_text("\n".join(lines) + "\n")
            n_saved += 1
        i += 1
    cap.release()
    print(f"saved {n_saved} frames -> {OUT}")


if __name__ == "__main__":
    main()
