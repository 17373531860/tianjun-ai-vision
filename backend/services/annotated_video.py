# -*- coding: utf-8 -*-
"""带框录像渲染 (2026-09) — 干净原片 + 检测框 sidecar → 烧框 MP4。

定位: 三个消费场景共用这一条渲染管线 —
  1. 归档规则「投递带框版」(video_archive worker, 渲染进 tmpdir 投完即删)
  2. 数据页「下载带框版」(sessions.py 端点, 渲染进转码缓存目录复用)
  3. (预留) 证据包带框件

为什么渲染而不是录制时烧框: 本地录像库永远只存干净原片一份 (证据纯净 +
磁盘不翻倍 + 不加 FFmpeg 常驻进程), 带框版是低频按需产物。

实现: cv2.VideoCapture 逐帧解码 → 按 sidecar run-length 沿用当前检测框
画到帧上 (cv2 矩形 + PIL 中文标签) → 管道喂 ffmpeg libx264 重编码
(faststart, tmp + 原子落位)。录像已限制 640x360@25fps, 渲染速度远快于
实时, 周期录像秒级完成。
"""
import os
import subprocess
from typing import Any, Dict, List

import cv2
import numpy as np

from backend.api.source_geometry import get_chinese_font
from backend.services.detection_boxes_sidecar import (
    load_sidecar, sidecar_path_for,
)

_BOX_COLOR_BGR = (0, 255, 0)
_BOX_THICKNESS = 2


def _get_ffmpeg_path():
    from backend.api.source import get_cached_ffmpeg_path
    return get_cached_ffmpeg_path()


def _draw_dets(frame: np.ndarray, dets: List[Dict[str, Any]]) -> np.ndarray:
    """把归一化检测框画到帧上 (矩形 cv2, 标签一次性走 PIL 支持中文)。"""
    if not dets:
        return frame
    h, w = frame.shape[:2]
    labels = []  # [(x1, y1, text)]
    for d in dets:
        try:
            x1 = int(float(d["x"]) * w)
            y1 = int(float(d["y"]) * h)
            x2 = x1 + int(float(d["w"]) * w)
            y2 = y1 + int(float(d["h"]) * h)
        except (KeyError, TypeError, ValueError):
            continue
        cv2.rectangle(frame, (x1, y1), (x2, y2), _BOX_COLOR_BGR,
                      _BOX_THICKNESS)
        conf = d.get("conf", d.get("confidence", 0)) or 0
        labels.append((x1, y1, f"{d.get('label', '')} {int(float(conf) * 100)}%"))
    if not labels:
        return frame
    # 一帧只做一次 BGR↔PIL 往返, 所有标签一起画
    try:
        from PIL import Image, ImageDraw
        font = get_chinese_font(14)
        img_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        for x1, y1, text in labels:
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            except Exception:
                tw, th = 8 * len(text), 16
            ty = y1 - th - 6 if y1 - th - 6 > 0 else y1 + 2
            draw.rectangle([x1, ty, x1 + tw + 6, ty + th + 4],
                           fill=(0, 128, 0))
            draw.text((x1 + 3, ty + 2), text, font=font,
                      fill=(255, 255, 255))
        return cv2.cvtColor(np.asarray(img_pil), cv2.COLOR_RGB2BGR)
    except Exception:
        return frame  # 标签画不上不影响矩形框


def render_annotated(video_path: str, sidecar_path: str,
                     out_path: str, timeout: float = 600.0) -> str:
    """渲染烧框 MP4 到 out_path (tmp + 原子落位)。

    失败抛异常并清理半成品 (调用方决定降级策略); 成功返回 out_path。
    """
    sidecar = load_sidecar(sidecar_path)
    if not sidecar:
        raise RuntimeError(f"检测框数据不存在或损坏: {sidecar_path}")
    frames_entries = sidecar.get("frames") or []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开录像: {video_path}")

    tmp_path = out_path + ".render.tmp"
    proc = None
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 360
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0 or fps > 120:
            fps = float(sidecar.get("fps") or 25)

        cmd = [
            _get_ffmpeg_path(), "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", f"{fps:.3f}", "-i", "pipe:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-f", "mp4", tmp_path,
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)

        # run-length 指针: 当前生效的检测框 = 最后一条 f <= 当前帧号的记录
        entry_idx = 0
        current_dets: List[Dict[str, Any]] = []
        frame_idx = 0
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            while (entry_idx < len(frames_entries)
                   and int(frames_entries[entry_idx].get("f", 0)) <= frame_idx):
                current_dets = frames_entries[entry_idx].get("d") or []
                entry_idx += 1
            if current_dets:
                frame = _draw_dets(frame, current_dets)
            proc.stdin.write(frame.tobytes())
            frame_idx += 1

        proc.stdin.close()
        proc.wait(timeout=timeout)
        if proc.returncode != 0 or not os.path.isfile(tmp_path) \
                or os.path.getsize(tmp_path) <= 0:
            err = b""
            try:
                err = proc.stderr.read()[-300:] if proc.stderr else b""
            except Exception:
                pass
            raise RuntimeError(
                f"带框渲染 ffmpeg 失败 rc={proc.returncode}: "
                f"{err.decode('utf-8', 'ignore')}")
        os.replace(tmp_path, out_path)
        print(f"[AnnotatedVideo] 渲染完成: {os.path.basename(out_path)} "
              f"({frame_idx} 帧)")
        return out_path
    finally:
        cap.release()
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def get_or_render_annotated_cached(video_path: str) -> str:
    """手动下载入口: 渲染到转码缓存目录并复用 (与 _h264v2 缓存同生命周期)。

    无 sidecar / 渲染失败抛异常, 由端点转成 HTTP 错误。
    """
    sp = sidecar_path_for(video_path)
    if not os.path.isfile(sp):
        raise FileNotFoundError(
            "该录像没有检测框数据（录制时未开启「记录检测框数据」）")
    from backend.services.recording_storage import get_video_dirs
    cache_dir = get_video_dirs()["cache"]
    os.makedirs(cache_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(video_path))[0]
    out_path = os.path.join(cache_dir, f"{base}_boxed.mp4")
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0 \
            and os.path.getmtime(out_path) >= os.path.getmtime(sp):
        return out_path
    return render_annotated(video_path, sp, out_path)
