#!/usr/bin/env python3
"""装弹逐压计数 demo（模型版）。

用法：
  python tools/demo_mag_press_count.py detect  # 全帧推理 → dets.csv
  python tools/demo_mag_press_count.py count   # 匣口脉冲计数分析（调参在此）
  python tools/demo_mag_press_count.py render  # 渲染成品叠加视频

计数原理（逐压主账）：
  弹匣由模型逐帧定位（class=magazine，丢帧用上次位置补），匣口区取匣体框
  上方一条窗；一颗弹（class=bullet）出现在匣口区并持续 ≥MIN_RUN 帧，随后
  消失 ≥GAP 帧 → 记一次「压入」，+1。不应期 REFRACTORY 帧防拇指遮挡二次计数。
"""
import csv
import subprocess
import sys
from collections import deque
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import demo_mag_load_count as legacy  # 复用 HUD 字体/视频路径/ffmpeg

VIDEO = legacy.VIDEO_DEFAULT
BEST = Path.home() / "datasets/mag_demo/runs/v1/weights/best.pt"
WORK = Path("/tmp/mag-load-demo/model")
DETS_CSV = WORK / "dets.csv"
CONF = 0.30

# ---- 匣口区几何（相对弹匣框）：压弹位实测 dx∈[-110,-20] dy∈[-300,-150]
#      （相对匣框顶中心）；左拳囤弹在 dx≈-190~-250，务必挡在区外防信号常亮 ----
MOUTH_DX0, MOUTH_DX1 = -70, -5    # 相对 mag.x0 / mag.x1（框宽≈90 → dx -115..+40）
MOUTH_DY0, MOUTH_DY1 = -310, -140  # 相对 mag.y0

# ---- 脉冲判定参数（count 子命令里对着真值调）----
MIN_RUN = 3        # 匣口区连续见弹 ≥3 帧才算一次待压
GAP = 8            # 消失 ≥8 帧确认压入
REFRACTORY = 10    # 计数后不应期
SMOOTH = 3         # 在场信号中值窗


def mouth_rect(mag):
    x0, y0, x1, _ = mag
    return (x0 + MOUTH_DX0, y0 + MOUTH_DY0, x1 + MOUTH_DX1, y0 + MOUTH_DY1)


def cmd_detect():
    from ultralytics import YOLO
    WORK.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(BEST))
    cap = cv2.VideoCapture(VIDEO)
    rows = []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        r = model.predict(frame, conf=CONF, iou=0.5, imgsz=640,
                          device="mps", verbose=False)[0]
        for b in r.boxes:
            x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
            rows.append((i, int(b.cls[0]), float(b.conf[0]),
                         round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)))
        i += 1
        if i % 200 == 0:
            print(f"frame {i}")
    cap.release()
    with open(DETS_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "cls", "conf", "x0", "y0", "x1", "y1"])
        w.writerows(rows)
    print(f"{i} frames, {len(rows)} dets -> {DETS_CSV}")


def load_dets():
    per_frame = {}
    with open(DETS_CSV) as f:
        for row in csv.DictReader(f):
            per_frame.setdefault(int(row["frame"]), []).append(
                (int(row["cls"]), float(row["conf"]),
                 float(row["x0"]), float(row["y0"]),
                 float(row["x1"]), float(row["y1"])))
    return per_frame


def mouth_signal(per_frame, n_frames):
    """逐帧：匣口区内 bullet 检测数（弹匣框缺帧沿用上次）。"""
    sig = np.zeros(n_frames, np.int32)
    mags = [None] * n_frames
    last_mag = None
    for i in range(n_frames):
        dets = per_frame.get(i, [])
        best = None
        for cls, conf, x0, y0, x1, y1 in dets:
            if cls == 1 and (best is None or conf > best[0]):
                best = (conf, x0, y0, x1, y1)
        if best:
            last_mag = best[1:]
        mags[i] = last_mag
        if last_mag is None:
            continue
        mx0, my0, mx1, my1 = mouth_rect(last_mag)
        for cls, conf, x0, y0, x1, y1 in dets:
            if cls != 0:
                continue
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if mx0 <= cx <= mx1 and my0 <= cy <= my1:
                sig[i] += 1
    return sig, mags


MAG_MOVE_VETO = 100  # 待确认窗内弹匣位移超此值(px)判为“弹随匣走”，作废
                     # （本视频实测：24 个真压入 maxdisp≤63，放回托盘的假事件 ≥189）
PENDING_W = 15      # 脉冲结束后再观察 15 帧（0.5s）弹匣是否原地


class PressCounter:
    """匣口在场信号 → 逐压计数 FSM。

    真压入 = 匣不动、弹消失。弹匣被拿走/放回托盘时，露在匣口的满匣顶弹
    会“随匣消失”造成假 +1——所以 +1 不立即入账，先挂「待确认」，再看
    PENDING_W 帧：期间弹匣离开 arm 时刻的锚点超 MAG_MOVE_VETO px 即作废。
    锚点在 arm 时冻结（不逐帧跟随，否则缓慢移动永远量不出位移）。
    """

    def __init__(self):
        self.count = 0
        self.state = "idle"   # idle / armed（匣口有弹）
        self.run = 0          # 连续在场帧数
        self.gap = 0          # 连续缺席帧数
        self.cooldown = 0
        self.buf = deque(maxlen=SMOOTH)
        self.events = []      # (frame, count) —— frame 为脉冲结束帧
        self.anchor = None    # arm 时刻的弹匣中心（冻结）
        self.pending = []     # [(fire_frame, anchor, deadline)]

    def _resolve_pending(self, i, mag_c):
        kept = []
        for fire, anchor, deadline in self.pending:
            moved = 0.0
            if anchor and mag_c:
                moved = ((mag_c[0] - anchor[0]) ** 2 +
                         (mag_c[1] - anchor[1]) ** 2) ** 0.5
            if moved > MAG_MOVE_VETO:
                continue          # 弹随匣走，作废
            if i >= deadline:
                self.count += 1   # 观察期满、弹匣原地 → 确认压入
                self.events.append((fire, self.count))
            else:
                kept.append((fire, anchor, deadline))
        self.pending = kept

    def step(self, i, present_raw, mag=None):
        mag_c = None
        if mag is not None:
            mag_c = ((mag[0] + mag[2]) / 2, (mag[1] + mag[3]) / 2)
        self._resolve_pending(i, mag_c)
        self.buf.append(1 if present_raw else 0)
        present = sorted(self.buf)[len(self.buf) // 2] == 1
        if self.cooldown > 0:
            self.cooldown -= 1
        if self.state == "idle":
            if present:
                self.run += 1
                if self.run >= MIN_RUN and self.cooldown == 0:
                    self.state = "armed"
                    self.gap = 0
                    self.anchor = mag_c
            else:
                self.run = 0
        else:  # armed
            if present:
                self.gap = 0
                self.anchor = self.anchor or mag_c
            else:
                self.gap += 1
                if self.gap >= GAP:
                    self.pending.append((i, self.anchor, i + PENDING_W))
                    self.state = "idle"
                    self.run = 0
                    self.cooldown = REFRACTORY
        return self.count

    def flush(self):
        """视频收尾：把仍在观察期内、未被否决的待确认压入入账。"""
        for fire, _anchor, _deadline in self.pending:
            self.count += 1
            self.events.append((fire, self.count))
        self.pending = []


def run_counter(sig, mags):
    pc = PressCounter()
    for i, v in enumerate(sig):
        pc.step(i, v > 0, mags[i])
    pc.flush()
    return pc


def cmd_count():
    per_frame = load_dets()
    n = max(per_frame) + 1
    sig, mags = mouth_signal(per_frame, n)
    pc = run_counter(sig, mags)
    on = int((sig > 0).sum())
    runs = int(((sig > 0).astype(int) != np.roll((sig > 0).astype(int), 1)).sum() // 2)
    print(f"帧数={n} 匣口在场帧={on} 原始段数={runs}")
    print(f"计数={pc.count}（真值 24）")
    for f, c in pc.events:
        print(f"  press #{c} @frame {f} ({f / 30:.1f}s)")


def cmd_render(out_dir=WORK / "final"):
    from PIL import Image, ImageDraw, ImageFont
    out_dir.mkdir(parents=True, exist_ok=True)
    per_frame = load_dets()
    cap = cv2.VideoCapture(VIDEO)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    sig, mags = mouth_signal(per_frame, n)
    raw = out_dir / "overlay_raw.mp4"
    vw = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    font_big = ImageFont.truetype(legacy.FONT_PATH, 46)
    font_mid = ImageFont.truetype(legacy.FONT_PATH, 26)
    pc = PressCounter()
    colors = {0: (0, 190, 255), 1: (255, 90, 90), 2: (90, 220, 90)}
    names = {0: "弹", 1: "弹匣", 2: "手"}
    flash = 0
    previews = {}
    for i in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        before = pc.count
        cnt = pc.step(i, sig[i] > 0, mags[i])
        if cnt > before:
            flash = 18
        for cls, conf, x0, y0, x1, y1 in per_frame.get(i, []):
            cv2.rectangle(frame, (int(x0), int(y0)), (int(x1), int(y1)),
                          colors[cls], 2)
        if mags[i]:
            mx0, my0, mx1, my1 = (int(v) for v in mouth_rect(mags[i]))
            col = (0, 0, 255) if sig[i] > 0 else (200, 200, 0)
            cv2.rectangle(frame, (mx0, my0), (mx1, my1), col, 2)
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        dr = ImageDraw.Draw(img, "RGBA")
        dr.rectangle([0, 0, W, 92], fill=(15, 15, 25, 190))
        col = (120, 255, 140) if flash > 0 else (255, 255, 255)
        dr.text((24, 16), f"已压 {pc.count} 发", font=font_big, fill=col)
        dr.text((320, 30), f"匣口有弹: {'●' if sig[i] > 0 else '—'}",
                font=font_mid, fill=(255, 210, 90) if sig[i] > 0 else (150, 150, 150))
        dr.text((560, 30), f"帧 {i}  {i / fps:5.1f}s  AI逐压计数(YOLO自训模型)",
                font=font_mid, fill=(170, 170, 190))
        if flash > 0:
            dr.text((24 + 240, 16), "+1", font=font_big, fill=(120, 255, 140))
            flash -= 1
        frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        vw.write(frame)
        for tag, fr in (("start", 30), ("mid", 500), ("press", None), ("end", n - 20)):
            pass
        if i in (30, 260, 520, 780, n - 15) or (cnt > before and len(previews) < 9):
            previews[i] = frame.copy()
        if i % 300 == 0:
            print(f"render {i}/{n}")
    cap.release()
    vw.release()
    for i, fr in list(previews.items())[:9]:
        cv2.imwrite(str(out_dir / f"preview_{i:05d}.jpg"), fr,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    final = out_dir / "overlay.mp4"
    subprocess.run([str(legacy.FFMPEG), "-y", "-i", str(raw), "-c:v", "libx264",
                    "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(final)],
                   check=True, capture_output=True)
    raw.unlink()
    print(f"最终计数={pc.count}（真值 24） -> {final}")


if __name__ == "__main__":
    {"detect": cmd_detect, "count": cmd_count, "render": cmd_render}[sys.argv[1]]()
