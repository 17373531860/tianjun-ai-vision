#!/usr/bin/env python3
"""离线 demo：弹匣装弹「手持减少 + 弹匣吸收」计数（不进主程序）。

三区：
  T 盘余 — 托盘内底、不碰手套/弹匣
  H 手持 — 与手套重叠、不碰弹匣
  M 弹匣 — 只当周期门和 +1 确认，不当主计数器

已压：稳定后 H 下降且 T 不增，且匣口附近有动作 → +1（单调）。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

VIDEO_DEFAULT = (
    "/Users/tianjun/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
    "xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/video/2026-09/"
    "76c8057a2469b7f223ae3393f5d4ac82_raw.mp4"
)
FFMPEG = Path(__file__).resolve().parents[1] / "ffmpeg" / "ffmpeg"
FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"

# 本视频固定机位，内底 / 工作区多边形（1280x720）
# 内底多边形向内收，避开木框边沿高光（结尾空盘时那里有常驻假黄铜）
TRAY_INNER = np.array([[272, 292], [742, 252], [800, 488], [262, 532]], np.int32)
WORK_AREA = np.array([[200, 70], [900, 40], [940, 560], [170, 640]], np.int32)
HAND_BAND = (380, 40, 920, 430)  # x0,y0,x1,y1 手套搜索带


def _poly_mask(shape, pts):
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillPoly(m, [pts], 255)
    return m


def _local_maxima(dist, min_dist, min_val):
    k = int(max(3, min_dist * 2 + 1))
    if k % 2 == 0:
        k += 1
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    dil = cv2.dilate(dist, kern)
    peaks = (np.abs(dist - dil) < 1e-3) & (dist >= min_val)
    return peaks.astype(np.uint8)


def _split_blobs(bin_mask, min_area=80, max_area=3500, min_peak=3.2, peak_gap=9):
    """距离变换分水岭，把相碰的弹拆开。返回 (cx,cy,w,h,area) 列表。"""
    if bin_mask.max() == 0:
        return []
    mask = cv2.medianBlur(bin_mask, 5)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    peaks = _local_maxima(dist, peak_gap, min_peak)
    sure_fg = (peaks * 255).astype(np.uint8)
    n_fg, markers = cv2.connectedComponents(sure_fg)
    if n_fg <= 1:
        n, _, st, cent = cv2.connectedComponentsWithStats(mask, 8)
        out = []
        for i in range(1, n):
            a = int(st[i, cv2.CC_STAT_AREA])
            if a < min_area or a > max_area:
                continue
            x, y, w, h = (int(st[i, j]) for j in range(4))
            out.append((float(cent[i, 0]), float(cent[i, 1]), w, h, a))
        return out
    sure_bg = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    unknown = cv2.subtract(sure_bg, sure_fg)
    markers = markers.astype(np.int32) + 1  # 1=背景, 2+=种子
    markers[unknown == 255] = 0
    vis = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    ws = cv2.watershed(vis, markers)
    boxes = []
    for lab in range(2, int(ws.max()) + 1):
        ys, xs = np.where(ws == lab)
        if xs.size < min_area or xs.size > max_area:
            continue
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        boxes.append((float(xs.mean()), float(ys.mean()), x1 - x0 + 1, y1 - y0 + 1, int(xs.size)))
    return boxes


def _brass_mask(hsv, work):
    h, s, v = cv2.split(hsv)
    m = ((h >= 12) & (h <= 32) & (s >= 88) & (v >= 110)).astype(np.uint8) * 255
    m = cv2.bitwise_and(m, work)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return m


def _glove_mask(hsv, shape):
    h, s, v = cv2.split(hsv)
    x0, y0, x1, y1 = HAND_BAND
    band = np.zeros(shape[:2], np.uint8)
    band[y0:y1, x0:x1] = 255
    g = ((s <= 22) & (v >= 158)).astype(np.uint8) * 255
    g = cv2.bitwise_and(g, band)
    g = cv2.morphologyEx(g, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    g = cv2.morphologyEx(g, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(g, 8)
    keep = np.zeros_like(g)
    cands = []
    for i in range(1, n):
        a = int(st[i, cv2.CC_STAT_AREA])
        if 4000 <= a <= 90000:
            cands.append((a, i))
    cands.sort(reverse=True)
    for _, i in cands[:2]:
        keep[lab == i] = 255
    core = keep.copy()
    dilated = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    return core, dilated


def _glove_blobs(glove_core):
    n, _, st, _ = cv2.connectedComponentsWithStats(glove_core, 8)
    blobs = []
    for i in range(1, n):
        x, y, w, h, a = (int(st[i, j]) for j in range(5))
        if a >= 4000:
            blobs.append((x + w / 2.0, x, y, w, h, a))
    blobs.sort()  # left to right
    return blobs


def _mag_bbox(hsv, tray, glove_core, brass=None):
    """弹匣跟右手：从右侧手套掌心向下挂一截。横放时再在托盘里找扁框。"""
    blobs = _glove_blobs(glove_core)
    if not blobs:
        return None
    cx, x, y, w, h, _ = blobs[-1]
    # 弹匣在右手尺侧，用手套框右沿对齐，避免双手粘连时中心偏左
    mx = int(np.clip(x + w - 92, 0, hsv.shape[1] - 80))
    my = int(y + h * 0.42)
    mw, mh = 78, 215
    my = int(np.clip(my, 0, hsv.shape[0] - mh - 1))
    return mx, my, mw, mh


def _pt_in_mask(x, y, mask):
    h, w = mask.shape
    xi, yi = int(round(x)), int(round(y))
    if xi < 0 or yi < 0 or xi >= w or yi >= h:
        return False
    return mask[yi, xi] > 0


def _overlap_frac(cx, cy, bw, bh, mask):
    x0 = max(0, int(cx - bw / 2))
    y0 = max(0, int(cy - bh / 2))
    x1 = min(mask.shape[1], int(cx + bw / 2))
    y1 = min(mask.shape[0], int(cy + bh / 2))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    roi = mask[y0:y1, x0:x1]
    return float(np.count_nonzero(roi)) / float(roi.size)


def _inflate_rect(rect, px, py, shape):
    x, y, w, h = rect
    x0 = max(0, x - px)
    y0 = max(0, y - py)
    x1 = min(shape[1], x + w + px)
    y1 = min(shape[0], y + h + py)
    return x0, y0, x1 - x0, y1 - y0


@dataclass
class LoadCounter:
    """守恒记账：已压 = 基线(开局 盘+手) − 当前盘余 − 当前手持，单调递增。

    抓弹瞬间 T 减 H 增互相抵消；每压入一发 H 净减 1 → 已压 +1。
    手攥紧时 H 会瞬时少数 → raw 虚高，靠去抖（连续 N 帧同值）扛住；
    每把弹压完手空的时刻 raw = 基线 − T 恰好归真，误差不累积。
    """

    capacity: int = 15
    loaded: int = 0
    alarm: str = ""
    cycle_active: bool = False
    t_hist: deque = field(default_factory=lambda: deque(maxlen=11))
    h_hist: deque = field(default_factory=lambda: deque(maxlen=9))
    t_slow: deque = field(default_factory=lambda: deque(maxlen=45))
    stable_t: int = 0
    stable_h: int = 0
    baseline: int | None = None
    warmup: list = field(default_factory=list)
    frame_no: int = 0
    raw_cand: int | None = None
    raw_streak: int = 0
    empty_streak: int = 0
    settled: str = ""
    last_event: str = ""
    events: list = field(default_factory=list)

    WARMUP_FRAMES = 40
    CONFIRM_FRAMES = 5
    FP_FLOOR = 2  # 允许的常驻误检底噪（本视频托盘沿高光）

    def update(self, t_cnt: int, h_cnt: int, mag_ok: bool, mouth_hit: bool, mag_laid: bool):
        self.frame_no += 1
        self.t_hist.append(t_cnt)
        self.h_hist.append(h_cnt)
        t_med = int(round(float(np.median(self.t_hist))))
        # 遮挡只会让 T 偏小：取滚动最大值当"可信盘余"
        self.t_slow.append(t_med)
        st = int(max(self.t_slow))
        sh = int(round(float(np.median(self.h_hist))))
        self.stable_t, self.stable_h = st, sh
        self.last_event = ""

        if self.baseline is None:
            if self.frame_no >= 10:
                self.warmup.append(st + sh)
            if self.frame_no >= self.WARMUP_FRAMES:
                self.baseline = int(round(float(np.median(self.warmup))))
                self.cycle_active = True
                self.last_event = f"周期开始 基线={self.baseline}"
            return

        if self.settled:
            return

        raw = self.baseline - st - sh
        raw = max(0, min(raw, self.baseline))
        if raw > self.loaded:
            if raw == self.raw_cand:
                self.raw_streak += 1
            else:
                self.raw_cand = raw
                self.raw_streak = 1
            if self.raw_streak >= self.CONFIRM_FRAMES:
                inc = raw - self.loaded
                self.loaded = raw
                self.events.append(("insert", inc))
                self.last_event = f"压入 +{inc}"
                self.raw_cand = None
                self.raw_streak = 0
        else:
            self.raw_cand = None
            self.raw_streak = 0

        # 收尾：盘空手空持续 → 周期结算（容忍 FP 底噪）
        if st <= self.FP_FLOOR and sh == 0 and self.loaded > 0:
            self.empty_streak += 1
        else:
            self.empty_streak = 0
        if self.empty_streak >= 45:
            self.settled = "ok" if self.loaded >= self.capacity else "ng"
            self.last_event = "周期结算 " + ("OK 满匣" if self.settled == "ok" else "NG 少装")

        self.alarm = ""
        if self.settled == "ok":
            self.alarm = "周期OK"
        elif self.settled == "ng":
            self.alarm = "少装NG"
        elif self.loaded > self.capacity:
            self.alarm = "多装"
        elif self.loaded >= self.capacity:
            self.alarm = "满匣"


class MagTracker:
    """本视频弹匣模板跟踪（首帧裁一块，后面 matchTemplate）。"""

    TPL_BOX = (557, 377, 90, 132)

    def __init__(self, first_frame):
        x, y, w, h = self.TPL_BOX
        self.tpl = first_frame[y:y + h, x:x + w].copy()
        self.tpl_g = cv2.cvtColor(self.tpl, cv2.COLOR_BGR2GRAY)
        self.rect = (x, y, w, h)
        self.score = 1.0

    def update(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        th, tw = self.tpl_g.shape
        x, y, w, h = self.rect
        pad = 90
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(gray.shape[1], x + w + pad)
        y1 = min(gray.shape[0], y + h + pad)
        roi = gray[y0:y1, x0:x1]
        if roi.shape[0] < th or roi.shape[1] < tw:
            return self.rect
        res = cv2.matchTemplate(roi, self.tpl_g, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxl = cv2.minMaxLoc(res)
        self.score = float(maxv)
        if maxv < 0.35:
            return self.rect
        nx = x0 + int(maxl[0])
        ny = y0 + int(maxl[1])
        self.rect = (nx, ny, tw, th)
        return self.rect


def detect(frame, mag=None):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    tray = _poly_mask(frame.shape, TRAY_INNER)
    work = _poly_mask(frame.shape, WORK_AREA)
    glove_core, glove = _glove_mask(hsv, frame.shape)
    brass = _brass_mask(hsv, work)
    if mag is None:
        mag = _mag_bbox(hsv, tray, glove_core, brass)

    mag_mask = np.zeros(frame.shape[:2], np.uint8)
    mouth_mask = np.zeros(frame.shape[:2], np.uint8)
    mag_laid = False
    if mag:
        x, y, w, h = mag
        cv2.rectangle(mag_mask, (x, y), (x + w, y + h), 255, -1)
        mag_mask = cv2.dilate(mag_mask, np.ones((13, 13), np.uint8))
        # 匣口：上沿 35%（竖握）或整匣（横放）
        mag_laid = w > h * 1.1
        mh = h if mag_laid else max(24, int(h * 0.38))
        cv2.rectangle(mouth_mask, (x - 12, y - 18), (x + w + 12, y + mh), 255, -1)

    boxes = _split_blobs(brass, min_area=110, max_area=1600, min_peak=4.0, peak_gap=10)
    items = []
    for cx, cy, bw, bh, area in boxes:
        x0, y0 = max(0, int(cx - bw / 2)), max(0, int(cy - bh / 2))
        x1, y1 = min(brass.shape[1], int(cx + bw / 2)), min(brass.shape[0], int(cy + bh / 2))
        hroi = hsv[y0:y1, x0:x1]
        sel = brass[y0:y1, x0:x1] > 0
        if hroi.size == 0 or not sel.any():
            continue
        m_s = float(hroi[:, :, 1][sel].mean())
        m_v = float(hroi[:, :, 2][sel].mean())
        if m_s < 95 or m_v < 128:
            continue
        if max(bw, bh) > 70 or min(bw, bh) < 6 or area > 1400:
            continue
        if _pt_in_mask(cx, cy, mag_mask) or _overlap_frac(cx, cy, bw, bh, mag_mask) > 0.25:
            zone = "M"
        elif _overlap_frac(cx, cy, bw, bh, glove) > 0.12 or _pt_in_mask(cx, cy, glove):
            zone = "H"
        elif _pt_in_mask(cx, cy, tray):
            zone = "T"
        else:
            continue
        items.append((zone, cx, cy, bw, bh))

    t_cnt = sum(1 for z, *_ in items if z == "T")
    h_cnt = sum(1 for z, *_ in items if z == "H")
    mouth_hit = False
    if mag:
        mouth_hit = any(z == "H" and _pt_in_mask(cx, cy, mouth_mask) for z, cx, cy, *_ in items)
        if not mouth_hit:
            # 手套压在匣口上也算动作
            mouth_hit = cv2.countNonZero(cv2.bitwise_and(glove, mouth_mask)) > 80
    return {
        "items": items,
        "t": t_cnt,
        "h": h_cnt,
        "mag": mag,
        "mag_laid": mag_laid,
        "mouth_hit": mouth_hit,
        "glove": glove,
        "tray": tray,
    }


def _font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size, index=0)
    except Exception:
        return ImageFont.load_default()


def draw_overlay(frame, det, counter: LoadCounter, fps, idx, nframes):
    vis = frame.copy()
    cv2.polylines(vis, [TRAY_INNER], True, (80, 220, 80), 2)
    if det["mag"]:
        x, y, w, h = det["mag"]
        cv2.rectangle(vis, (x, y), (x + w, y + h), (40, 40, 40), 2)
        cv2.putText(vis, "MAG", (x, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 40, 40), 1)
    color = {"T": (60, 200, 60), "H": (0, 140, 255), "M": (0, 0, 220)}
    for zone, cx, cy, bw, bh in det["items"]:
        x0, y0 = int(cx - bw / 2), int(cy - bh / 2)
        cv2.rectangle(vis, (x0, y0), (x0 + int(bw), y0 + int(bh)), color[zone], 2)
        cv2.circle(vis, (int(cx), int(cy)), 3, color[zone], -1)

    alarm = counter.alarm
    if alarm in ("满匣", "周期OK"):
        banner = (40, 160, 80)
    elif alarm in ("少装NG", "多装"):
        banner = (40, 40, 200)
    else:
        banner = (30, 30, 30)
    hud = vis.copy()
    cv2.rectangle(hud, (16, 16), (430, 210), banner, -1)
    vis = cv2.addWeighted(hud, 0.62, vis, 0.38, 0)

    img = Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img)
    f_big = _font(36)
    f = _font(22)
    f_s = _font(16)
    loaded = counter.loaded
    cap = counter.capacity
    draw.text((28, 24), f"已压  {loaded}  /  {cap}", font=f_big, fill=(255, 255, 255))
    draw.text((28, 74), f"盘余 T={counter.stable_t}    手持 H={counter.stable_h}", font=f, fill=(230, 230, 230))
    st = "装弹中" if counter.cycle_active else "基线学习中"
    if alarm:
        st = alarm
    draw.text((28, 108), f"状态  {st}", font=f, fill=(255, 220, 120) if alarm else (200, 255, 200))
    ev = counter.last_event or "—"
    draw.text((28, 140), f"事件  {ev}", font=f, fill=(255, 255, 255))
    tsec = idx / max(fps, 1)
    draw.text((28, 172), f"{tsec:5.1f}s   frame {idx}/{nframes}", font=f_s, fill=(200, 200, 200))
    legend = "绿=盘余T   橙=手持H   黑框=弹匣"
    draw.text((16, frame.shape[0] - 32), legend, font=f_s, fill=(255, 255, 255))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def run(video, out_dir, capacity, preview_every, max_frames):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"打不开视频: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    counter = LoadCounter(capacity=capacity)
    ok0, first = cap.read()
    if not ok0:
        raise SystemExit("视频是空的")
    mag_tracker = MagTracker(first)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    raw_path = str(out_dir / "overlay_raw.mp4")
    writer = cv2.VideoWriter(raw_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    previews = []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if max_frames and i >= max_frames:
            break
        mag = mag_tracker.update(frame)
        det = detect(frame, mag=mag)
        mag_ok = det["mag"] is not None
        counter.update(det["t"], det["h"], mag_ok, det["mouth_hit"], det["mag_laid"])
        vis = draw_overlay(frame, det, counter, fps, i, nframes)
        writer.write(vis)
        if preview_every and i % preview_every == 0:
            p = out_dir / f"preview_{i:05d}.jpg"
            cv2.imwrite(str(p), vis, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            previews.append(p)
        i += 1
        if i % 60 == 0:
            print(f"  {i}/{nframes}  已压={counter.loaded}  T={counter.stable_t} H={counter.stable_h} {counter.alarm}")
    cap.release()
    writer.release()

    final = out_dir / "overlay.mp4"
    if FFMPEG.exists():
        cmd = (
            f'"{FFMPEG}" -y -i "{raw_path}" -c:v libx264 -pix_fmt yuv420p '
            f'-movflags +faststart "{final}" >/dev/null 2>&1'
        )
        os.system(cmd)
        if final.exists():
            os.remove(raw_path)
    else:
        final = Path(raw_path)

    summary = out_dir / "summary.txt"
    summary.write_text(
        "\n".join(
            [
                f"frames={i}",
                f"loaded={counter.loaded}",
                f"capacity={counter.capacity}",
                f"alarm={counter.alarm}",
                f"insert_events={sum(n for k,n in counter.events if k=='insert')}",
                f"event_count={len(counter.events)}",
                f"video={final}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"done loaded={counter.loaded}/{capacity} alarm={counter.alarm} -> {final}")
    return final, previews, counter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=VIDEO_DEFAULT)
    ap.add_argument("--out", default="/tmp/mag-load-demo")
    ap.add_argument("--capacity", type=int, default=15)
    ap.add_argument("--preview-every", type=int, default=30)
    ap.add_argument("--max-frames", type=int, default=0)
    args = ap.parse_args()
    run(args.video, args.out, args.capacity, args.preview_every, args.max_frames)


if __name__ == "__main__":
    sys.exit(main())
