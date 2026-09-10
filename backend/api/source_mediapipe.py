"""MediaPipeOverlay 组件 (v2.7.16 P7 第二刀, 组合优于继承).

把 MediaPipe 姿态/手部识别相关功能从 VideoSourceManager 搬出, 形成独立组件.

v3.8.0 二段 pipeline 升级 (feat/hand-skeleton):
    - 新增 hand-detector 槽位: 当 host.mediapipe_hand_detector_path 非空时, 走二段
      pipeline (YOLO 检框 -> 扩 ROI -> HandLandmarker 跑在 ROI 上), 解决工业场景
      MediaPipe PalmDetector 不识别戴手套/握工具姿态的问题.
    - 第二阶段 landmarker 优先级:
        1. backend/data/models/hand_landmarker.task 存在 -> MediaPipe Tasks API
           (新版, 更稳, 推荐)
        2. 文件不存在 -> 回退 mp.solutions.hands (legacy 单帧推理)
    - 配 hand_detector 但加载失败 -> 自动回退原 baseline, 不阻塞推理
    - hand-detector kind: 'v8'(ultralytics) / 'v5'(legacy, monkeypatch torch.load)

v3.32.0 异步推理升级:
    - 推理不再内联在采集循环: apply_overlay 只投递帧副本(单槽位) + 画上一次缓存结果,
      后台 worker 线程负责 lazy init / 热重载 / 推理, 采集帧率不再被手部模型拖垮.
    - 代价: 骨架相对画面滞后一次推理周期 (视觉基本无感).

多屏手部副屏支持:
    - 仅在副屏请求后短时发布“干净推理帧 + 同帧手框/关节”观测,
      不改采集循环、默认 snapshot 或 MJPEG 通路.
    - 裁切、平滑、绘制与 JPEG 缓存均属于本组件.

字段所有权:
  老 baseline:
    _mp_pose / _mp_hands / _mp_landmarker_tasks  : lazy-loaded 模型句柄
    _mp_draw / _mp_draw_styles                   : drawing utils
    _mp_last_pose_results / _mp_last_hands_results : 帧间复用
    _mp_frame_counter / _mp_process_interval
  v3.8.0 新增 (二段 pipeline):
    _hand_detector                       : YOLO 检测器实例 (None=未启用)
    _hand_detector_path_loaded           : 已加载的路径 (热更新检测)
    _hand_detector_kind_loaded           : 已加载的 kind
    _last_two_stage_results              : 帧间复用的 ROI 关键点 [(roi_offset, roi_size, landmarks), ...]
  v3.32.0 新增 (异步推理):
    _worker_thread / _worker_running     : 后台推理线程
    _pending_lock / _pending_cond / _pending_frame : 单槽位帧投递
  多屏手部副屏:
    _hand_observation_*                  : 干净帧与同帧手部几何快照
    _hand_crop_*                         : 裁切坐标平滑与 JPEG 缓存

用户配置 (公共字段保留在 VSM, 通过 __setattr__ 转发):
  老 4 个: mediapipe_enabled / mediapipe_pose / mediapipe_hands / mediapipe_confidence
  新 6 个: mediapipe_hand_detector_path / _conf / _iou / _imgsz / _class / _kind
           mediapipe_hand_roi_pad

公共 API: init() / release() / apply_overlay(frame) / get_hands_crop_snapshot().
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw

from backend.api.source_geometry import get_chinese_font

# HandLandmarker .task 默认路径 (跟项目同级分发, 客户可换)
DEFAULT_TASK_MODEL_REL = "backend/data/models/hand_landmarker.task"


def _hex_to_bgr(hex_color: str, fallback: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """'#RRGGBB' -> BGR tuple (cv2/mediapipe DrawingSpec 都吃 BGR). 解析失败回退."""
    try:
        s = (hex_color or "").strip().lstrip("#")
        if len(s) != 6:
            return fallback
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        return (b, g, r)
    except Exception:
        return fallback


# ==================== 二段 pipeline: hand-detector 适配器 ====================

class _YOLOv8HandDetector:
    """ultralytics YOLOv8/v11 hand-detector. 内部使用, 失败抛 RuntimeError."""

    def __init__(self, model_path: str, conf: float, iou: float, imgsz: int,
                 device: str, class_filter: int):
        from ultralytics import YOLO
        self.model = YOLO(model_path)
        self.conf = max(0.05, min(0.95, conf))
        self.iou = max(0.1, min(0.9, iou))
        self.imgsz = max(160, min(1280, imgsz))
        # device: 'auto' -> ultralytics 默认; 否则原样
        self.device = device if device and device != "auto" else None
        # class_filter: -1 表示所有类, 否则只保留该类
        self.class_filter = int(class_filter) if class_filter is not None else -1
        self.names = getattr(self.model, "names", {}) or {}

    def predict(self, frame) -> List[Tuple[int, int, int, int]]:
        """返回 [(x1, y1, x2, y2), ...] (只输出 bbox, 不带 conf/cls)."""
        try:
            kwargs = dict(conf=self.conf, iou=self.iou, imgsz=self.imgsz, verbose=False)
            if self.device:
                kwargs["device"] = self.device
            results = self.model.predict(frame, **kwargs)
        except Exception as e:
            print(f"[MediaPipe two-stage] hand-detector 推理失败: {e}", file=sys.stderr)
            return []
        if not results:
            return []
        res = results[0]
        if res.boxes is None or len(res.boxes) == 0:
            return []
        xyxy = res.boxes.xyxy.cpu().numpy()
        clses = res.boxes.cls.cpu().numpy().astype(int)
        out = []
        for (x1, y1, x2, y2), cl in zip(xyxy, clses):
            if self.class_filter >= 0 and int(cl) != self.class_filter:
                continue
            out.append((int(x1), int(y1), int(x2), int(y2)))
        return out


class _YOLOv5HandDetector:
    """yolov5 legacy hand-detector (用于加载 hf 上的 .pt 旧权重).

    monkeypatch torch.load(weights_only=False) 绕开 torch 2.6+ 安全检查.
    """

    def __init__(self, model_path: str, conf: float, iou: float, imgsz: int,
                 device: str, class_filter: int):
        import torch
        _orig = torch.load
        def _patched(*args, **kwargs):
            kwargs['weights_only'] = False
            return _orig(*args, **kwargs)
        torch.load = _patched
        try:
            import yolov5  # noqa: F401
        except ImportError as exc:
            torch.load = _orig
            raise RuntimeError("yolov5 包未安装, 无法加载 yolov5 格式 .pt") from exc
        import yolov5
        from backend.core.torch_device import resolve_auto_device
        dev = device if (device and device != "auto") else resolve_auto_device()
        self.model = yolov5.load(model_path, device=dev)
        self.model.conf = max(0.05, min(0.95, conf))
        self.model.iou = max(0.1, min(0.9, iou))
        self.imgsz = max(160, min(1280, imgsz))
        self.class_filter = int(class_filter) if class_filter is not None else -1
        self.names = getattr(self.model, "names", {}) or {}
        if isinstance(self.names, list):
            self.names = {i: n for i, n in enumerate(self.names)}
        torch.load = _orig

    def predict(self, frame) -> List[Tuple[int, int, int, int]]:
        try:
            res = self.model(frame, size=self.imgsz)
        except Exception as e:
            print(f"[MediaPipe two-stage] yolov5 推理失败: {e}", file=sys.stderr)
            return []
        if len(res.xyxy) == 0:
            return []
        det = res.xyxy[0].cpu().numpy()
        out = []
        for row in det:
            x1, y1, x2, y2, cf, cl = row[:6]
            if self.class_filter >= 0 and int(cl) != self.class_filter:
                continue
            out.append((int(x1), int(y1), int(x2), int(y2)))
        return out


# ==================== 二段 pipeline: HandLandmarker Tasks API ====================

class _HandLandmarkerTasksAdapter:
    """MediaPipe Tasks API HandLandmarker, 跑在 RGB 图像上, 输出 21 关键点."""

    def __init__(self, task_path: str, num_hands: int, min_det_conf: float,
                 min_track_conf: float):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_py
        from mediapipe.tasks.python import vision as mp_vis

        self._mp = mp
        base_options = mp_py.BaseOptions(model_asset_path=task_path)
        opts = mp_vis.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vis.RunningMode.IMAGE,
            num_hands=max(1, min(4, int(num_hands))),
            min_hand_detection_confidence=max(0.05, min(0.9, min_det_conf)),
            min_hand_presence_confidence=max(0.05, min(0.9, min_det_conf)),
            min_tracking_confidence=max(0.05, min(0.9, min_track_conf)),
        )
        self._landmarker = mp_vis.HandLandmarker.create_from_options(opts)

    def detect(self, bgr_image):
        """返回 list[landmarks_per_hand], 空 list 表示未命中.

        每个 landmarks_per_hand 是 list[NormalizedLandmark], 长度 21.
        """
        rgb = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(mp_image)
        return list(result.hand_landmarks) if result.hand_landmarks else []

    def close(self):
        try:
            self._landmarker.close()
        except Exception:
            pass


# ==================== ROI 计算工具 ====================

def _expand_bbox(x1: int, y1: int, x2: int, y2: int, frame_w: int, frame_h: int,
                 pad_ratio: float, min_size: int = 96) -> Tuple[int, int, int, int]:
    """把 bbox 外扩 pad_ratio * max(w,h), 不超出帧, 不小于 min_size."""
    w = x2 - x1
    h = y2 - y1
    pad = int(max(w, h) * max(0.0, pad_ratio))
    nx1 = max(0, x1 - pad)
    ny1 = max(0, y1 - pad)
    nx2 = min(frame_w, x2 + pad)
    ny2 = min(frame_h, y2 + pad)
    if (nx2 - nx1) < min_size:
        cx = (nx1 + nx2) // 2
        nx1 = max(0, cx - min_size // 2)
        nx2 = min(frame_w, nx1 + min_size)
    if (ny2 - ny1) < min_size:
        cy = (ny1 + ny2) // 2
        ny1 = max(0, cy - min_size // 2)
        ny2 = min(frame_h, ny1 + min_size)
    return nx1, ny1, nx2, ny2


_HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)

_HAND_CROP_ASPECT = 16.0 / 9.0
_HAND_CROP_MIN_WIDTH_RATIO = 0.3
# 副屏固定取景：源帧中心、宽度 50%，单双手切换时均不改变。
_HAND_CROP_VIEW_WIDTH_RATIO = 0.5


def _clip_crop_rect(rect, frame_w: int, frame_h: int) -> Optional[Tuple[int, int, int, int]]:
    """把像素裁切框夹到帧内；空框返回 None。"""
    if rect is None or frame_w <= 0 or frame_h <= 0:
        return None
    try:
        x1, y1, x2, y2 = (int(round(float(v))) for v in rect)
    except (TypeError, ValueError):
        return None
    x1 = max(0, min(frame_w, x1))
    y1 = max(0, min(frame_h, y1))
    x2 = max(0, min(frame_w, x2))
    y2 = max(0, min(frame_h, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _compute_center_crop_rect(
    frame_shape: Sequence[int],
    *,
    width_ratio: float,
) -> Optional[Tuple[int, int, int, int]]:
    """按源帧尺寸计算固定居中的 16:9 ROI，不读取任何检测结果。"""
    if len(frame_shape) < 2:
        return None
    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    if frame_w <= 0 or frame_h <= 0:
        return None

    ratio = float(width_ratio)
    if not math.isfinite(ratio):
        return None
    ratio = max(0.1, min(1.0, ratio))
    viewport_w = min(frame_w, max(96, int(round(frame_w * ratio))))
    viewport_h = max(1, int(round(viewport_w / _HAND_CROP_ASPECT)))
    if viewport_h > frame_h:
        viewport_h = frame_h
        viewport_w = min(
            frame_w,
            max(1, int(round(viewport_h * _HAND_CROP_ASPECT))),
        )

    crop_x1 = (frame_w - viewport_w) // 2
    crop_y1 = (frame_h - viewport_h) // 2
    return (
        crop_x1,
        crop_y1,
        crop_x1 + viewport_w,
        crop_y1 + viewport_h,
    )


def _compute_hands_crop_rect(
    frame_shape: Sequence[int],
    hand_boxes: Sequence[Sequence[float]],
    landmark_groups: Sequence[Sequence[Sequence[float]]],
    *,
    pad_ratio: float,
    previous_rect: Optional[Sequence[float]] = None,
    smoothing_alpha: float = 0.35,
    fixed_width_ratio: Optional[float] = None,
    follow_center: bool = False,
) -> Optional[Tuple[int, int, int, int]]:
    """计算手部裁切框（纯函数）。

    坐标均为当前干净帧上的像素坐标。先取所有 hand-detector 框与双手
    landmarks 外接框的并集，再复用 ``_expand_bbox`` 按现有
    ``mediapipe_hand_roi_pad`` 口径外扩。传入 ``fixed_width_ratio`` 时，
    视窗始终保持固定 16:9 尺寸；手部接近边缘时只平移不缩放，手部并集超过
    固定视窗时以并集中心为焦点。``follow_center=True`` 会让同尺寸视窗中心
    按 EMA 平滑跟随手部并集；默认仍保留触边后最小平移的 dead zone 行为。
    未传 ``fixed_width_ratio`` 时保留“完整容纳并集、必要时扩容”的通用计算。
    没有新几何时保留上一裁切框。
    """
    if len(frame_shape) < 2:
        return None
    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    if frame_w <= 0 or frame_h <= 0:
        return None

    xs: List[float] = []
    ys: List[float] = []

    for box in hand_boxes or ():
        if box is None or len(box) < 4:
            continue
        try:
            bx1, by1, bx2, by2 = (float(v) for v in box[:4])
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (bx1, by1, bx2, by2)):
            continue
        bx1, bx2 = sorted((bx1, bx2))
        by1, by2 = sorted((by1, by2))
        bx1, bx2 = max(0.0, bx1), min(float(frame_w), bx2)
        by1, by2 = max(0.0, by1), min(float(frame_h), by2)
        if bx2 > bx1 and by2 > by1:
            xs.extend((bx1, bx2))
            ys.extend((by1, by2))

    for landmarks in landmark_groups or ():
        for point in landmarks or ():
            if point is None or len(point) < 2:
                continue
            try:
                px, py = float(point[0]), float(point[1])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(px) or not math.isfinite(py):
                continue
            xs.append(max(0.0, min(float(frame_w), px)))
            ys.append(max(0.0, min(float(frame_h), py)))

    if not xs or not ys:
        return _clip_crop_rect(previous_rect, frame_w, frame_h)

    x1 = int(math.floor(min(xs)))
    y1 = int(math.floor(min(ys)))
    x2 = int(math.ceil(max(xs)))
    y2 = int(math.ceil(max(ys)))
    if x2 <= x1:
        x2 = min(frame_w, x1 + 1)
    if y2 <= y1:
        y2 = min(frame_h, y1 + 1)
    required = _expand_bbox(
        x1, y1, x2, y2, frame_w, frame_h,
        max(0.0, float(pad_ratio)),
    )

    previous = _clip_crop_rect(previous_rect, frame_w, frame_h)
    required_w = required[2] - required[0]
    required_h = required[3] - required[1]
    previous_w = previous[2] - previous[0] if previous is not None else 0
    previous_h = previous[3] - previous[1] if previous is not None else 0

    if fixed_width_ratio is not None:
        # 与 fixed 中心模式共用同一尺寸算法，保证低分辨率输入也不会因
        # 模式切换改变缩放比例（follow 只移动视窗中心）。
        center_rect = _compute_center_crop_rect(
            frame_shape,
            width_ratio=float(fixed_width_ratio),
        )
        if center_rect is None:
            return previous
        viewport_w = center_rect[2] - center_rect[0]
        viewport_h = center_rect[3] - center_rect[1]

        required_cx = (required[0] + required[2]) / 2.0
        required_cy = (required[1] + required[3]) / 2.0
        required_w = required[2] - required[0]
        required_h = required[3] - required[1]

        if previous is None:
            crop_x1 = int(round(required_cx - viewport_w / 2.0))
            crop_y1 = int(round(required_cy - viewport_h / 2.0))
        elif follow_center:
            alpha = max(0.0, min(1.0, float(smoothing_alpha)))
            previous_cx = (previous[0] + previous[2]) / 2.0
            previous_cy = (previous[1] + previous[3]) / 2.0
            center_x = previous_cx * (1.0 - alpha) + required_cx * alpha
            center_y = previous_cy * (1.0 - alpha) + required_cy * alpha
            crop_x1 = int(round(center_x - viewport_w / 2.0))
            crop_y1 = int(round(center_y - viewport_h / 2.0))

            # 连续移动时以 EMA 为主；若手部突然跳位且 padded union 能放入
            # 固定窗口，则只做刚好容纳它的最小修正，避免副屏短暂完全丢手。
            if required_w <= viewport_w:
                if required[0] < crop_x1:
                    crop_x1 = required[0]
                elif required[2] > crop_x1 + viewport_w:
                    crop_x1 = required[2] - viewport_w
            if required_h <= viewport_h:
                if required[1] < crop_y1:
                    crop_y1 = required[1]
                elif required[3] > crop_y1 + viewport_h:
                    crop_y1 = required[3] - viewport_h
        else:
            # 固定摄像头下保留上一取景位置作为 dead zone：手还在窗口内就
            # 完全不移动，只有触边才做最小平移，避免画面跟着 bbox 抖动。
            previous_cx = (previous[0] + previous[2]) / 2.0
            previous_cy = (previous[1] + previous[3]) / 2.0
            crop_x1 = int(round(previous_cx - viewport_w / 2.0))
            crop_y1 = int(round(previous_cy - viewport_h / 2.0))

            if required_w <= viewport_w:
                if required[0] < crop_x1:
                    crop_x1 = required[0]
                elif required[2] > crop_x1 + viewport_w:
                    crop_x1 = required[2] - viewport_w
            else:
                alpha = max(0.0, min(1.0, float(smoothing_alpha)))
                center_x = previous_cx * (1.0 - alpha) + required_cx * alpha
                crop_x1 = int(round(center_x - viewport_w / 2.0))

            if required_h <= viewport_h:
                if required[1] < crop_y1:
                    crop_y1 = required[1]
                elif required[3] > crop_y1 + viewport_h:
                    crop_y1 = required[3] - viewport_h
            else:
                alpha = max(0.0, min(1.0, float(smoothing_alpha)))
                center_y = previous_cy * (1.0 - alpha) + required_cy * alpha
                crop_y1 = int(round(center_y - viewport_h / 2.0))

        crop_x1 = max(0, min(frame_w - viewport_w, crop_x1))
        crop_y1 = max(0, min(frame_h - viewport_h, crop_y1))
        return (
            crop_x1,
            crop_y1,
            crop_x1 + viewport_w,
            crop_y1 + viewport_h,
        )

    min_w = min(
        frame_w,
        max(96, int(round(frame_w * _HAND_CROP_MIN_WIDTH_RATIO))),
    )
    min_h = min(frame_h, max(96, int(math.ceil(min_w / _HAND_CROP_ASPECT))))

    # 正常帧严格复用既有宽高，只让中心移动。只有 padded union 或首次
    # 最小视窗放不下时才扩容；扩容后不会因下一帧手框变小而回缩。
    if (
        previous is not None
        and previous_w >= required_w
        and previous_h >= required_h
        and previous_w >= min_w
        and previous_h >= min_h
    ):
        viewport_w, viewport_h = previous_w, previous_h
    else:
        base_w = min(frame_w, max(required_w, previous_w, min_w))
        base_h = min(frame_h, max(required_h, previous_h, min_h))
        viewport_w = int(math.ceil(max(base_w, base_h * _HAND_CROP_ASPECT)))
        viewport_h = int(math.ceil(viewport_w / _HAND_CROP_ASPECT))

        if viewport_w > frame_w or viewport_h > frame_h:
            # 优先保住 16:9；当源画幅本身不足以容纳 required 时，退到
            # 能完整包含它的最小窗口。输出端仍会 letterbox 到 640x360。
            fit_w = frame_w
            fit_h = int(math.ceil(fit_w / _HAND_CROP_ASPECT))
            if fit_h >= base_h and fit_h <= frame_h:
                viewport_w, viewport_h = fit_w, fit_h
            else:
                fit_h = frame_h
                fit_w = int(math.ceil(fit_h * _HAND_CROP_ASPECT))
                if fit_w >= base_w and fit_w <= frame_w:
                    viewport_w, viewport_h = fit_w, fit_h
                else:
                    viewport_w = min(
                        frame_w,
                        max(base_w, int(math.ceil(base_h * _HAND_CROP_ASPECT))),
                    )
                    viewport_h = min(
                        frame_h,
                        max(base_h, int(math.ceil(base_w / _HAND_CROP_ASPECT))),
                    )

    required_cx = (required[0] + required[2]) / 2.0
    required_cy = (required[1] + required[3]) / 2.0
    if previous is None:
        center_x, center_y = required_cx, required_cy
    else:
        alpha = max(0.0, min(1.0, float(smoothing_alpha)))
        previous_cx = (previous[0] + previous[2]) / 2.0
        previous_cy = (previous[1] + previous[3]) / 2.0
        center_x = previous_cx * (1.0 - alpha) + required_cx * alpha
        center_y = previous_cy * (1.0 - alpha) + required_cy * alpha

    ideal_x1 = int(round(center_x - viewport_w / 2.0))
    ideal_y1 = int(round(center_y - viewport_h / 2.0))
    # 可行区间同时保证：窗口不出帧、required 始终完整落在窗口内。
    min_x1 = max(0, required[2] - viewport_w)
    max_x1 = min(required[0], frame_w - viewport_w)
    min_y1 = max(0, required[3] - viewport_h)
    max_y1 = min(required[1], frame_h - viewport_h)
    crop_x1 = max(min_x1, min(max_x1, ideal_x1))
    crop_y1 = max(min_y1, min(max_y1, ideal_y1))
    return (
        crop_x1,
        crop_y1,
        crop_x1 + viewport_w,
        crop_y1 + viewport_h,
    )


def _render_hands_crop(
    frame,
    crop_rect: Sequence[int],
    hand_boxes: Sequence[Sequence[float]],
    landmark_groups: Sequence[Sequence[Sequence[float]]],
    *,
    max_edge: int = 640,
    box_color: Tuple[int, int, int] = (0, 255, 0),
    line_color: Tuple[int, int, int] = (0, 255, 0),
    point_color: Tuple[int, int, int] = (0, 255, 0),
    thickness: int = 2,
    draw_landmarks=None,
    connections=_HAND_CONNECTIONS,
    landmark_drawing_spec=None,
    connection_drawing_spec=None,
):
    """先裁切干净帧，再输出固定 640x360 并只画手框与指关节。"""
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    frame_h, frame_w = frame.shape[:2]
    clipped = _clip_crop_rect(crop_rect, frame_w, frame_h)
    if clipped is None:
        return None
    x1, y1, x2, y2 = clipped

    # 必须先裁切，禁止把全图编码后交给前端 CSS/canvas 再裁。
    crop = frame[y1:y2, x1:x2].copy()
    crop_h, crop_w = crop.shape[:2]
    if crop_w <= 0 or crop_h <= 0:
        return None
    target_edge = max(1, int(max_edge))
    out_w = target_edge
    out_h = max(1, int(round(target_edge / _HAND_CROP_ASPECT)))
    scale = min(out_w / float(crop_w), out_h / float(crop_h))
    resized_w = max(1, min(out_w, int(round(crop_w * scale))))
    resized_h = max(1, min(out_h, int(round(crop_h * scale))))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(crop, (resized_w, resized_h), interpolation=interpolation)
    offset_x = (out_w - resized_w) // 2
    offset_y = (out_h - resized_h) // 2
    crop = np.zeros((out_h, out_w, 3), dtype=frame.dtype)
    crop[offset_y:offset_y + resized_h, offset_x:offset_x + resized_w] = resized

    scale_x = resized_w / float(crop_w)
    scale_y = resized_h / float(crop_h)
    draw_thickness = max(1, min(10, int(thickness)))
    point_radius = max(2, draw_thickness + 1)

    def _project(px: float, py: float) -> Tuple[int, int]:
        return (
            offset_x + int(round((float(px) - x1) * scale_x)),
            offset_y + int(round((float(py) - y1) * scale_y)),
        )

    for box in hand_boxes or ():
        if box is None or len(box) < 4:
            continue
        try:
            bx1, by1, bx2, by2 = (float(v) for v in box[:4])
        except (TypeError, ValueError):
            continue
        bx1, bx2 = sorted((bx1, bx2))
        by1, by2 = sorted((by1, by2))
        ix1, iy1 = max(float(x1), bx1), max(float(y1), by1)
        ix2, iy2 = min(float(x2), bx2), min(float(y2), by2)
        if ix2 <= ix1 or iy2 <= iy1:
            continue
        p1 = _project(ix1, iy1)
        p2 = _project(ix2, iy2)
        cv2.rectangle(crop, p1, p2, box_color, draw_thickness)

    for landmarks in landmark_groups or ():
        points = []
        for point in landmarks or ():
            try:
                points.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError, IndexError):
                points.append((float("nan"), float("nan")))

        # 正常运行时复用主屏同一个 MediaPipe draw_landmarks 与 DrawingSpec，
        # 包括默认的逐手指花色；无 MediaPipe 绘图句柄的纯函数测试才走 cv2 回退。
        if draw_landmarks is not None and \
           landmark_drawing_spec is not None and \
           connection_drawing_spec is not None:
            from mediapipe.framework.formats import landmark_pb2

            normalized_landmarks = []
            for px, py in points:
                if not math.isfinite(px) or not math.isfinite(py):
                    normalized_landmarks.append(
                        landmark_pb2.NormalizedLandmark(x=-1.0, y=-1.0)
                    )
                    continue
                projected_x, projected_y = _project(px, py)
                normalized_landmarks.append(landmark_pb2.NormalizedLandmark(
                    x=projected_x / float(out_w),
                    y=projected_y / float(out_h),
                ))
            draw_landmarks(
                crop,
                landmark_pb2.NormalizedLandmarkList(
                    landmark=normalized_landmarks,
                ),
                connections,
                landmark_drawing_spec,
                connection_drawing_spec,
            )
            continue

        for start_idx, end_idx in _HAND_CONNECTIONS:
            if start_idx >= len(points) or end_idx >= len(points):
                continue
            start, end = points[start_idx], points[end_idx]
            if not all(math.isfinite(v) for v in (*start, *end)):
                continue
            cv2.line(crop, _project(*start), _project(*end), line_color, draw_thickness)
        for px, py in points:
            if not math.isfinite(px) or not math.isfinite(py):
                continue
            if x1 <= px <= x2 and y1 <= py <= y2:
                cv2.circle(crop, _project(px, py), point_radius, point_color, -1)
    return crop


def _make_no_hands_frame():
    """生成 640x360 黑底“未检测到手”占位图。"""
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    text = "未检测到手"
    try:
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(image)
        font = get_chinese_font(30)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((640 - text_w) // 2, (360 - text_h) // 2),
            text,
            font=font,
            fill=(220, 220, 220),
        )
        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR).copy()
    except Exception:
        cv2.putText(
            frame, "No hands detected", (180, 185),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220, 220, 220), 2,
        )
        return frame


# ==================== MediaPipeOverlay 主类 ====================

class MediaPipeOverlay:
    """MediaPipe 叠加层, 支持两种模式:

    Mode A (baseline): host.mediapipe_hand_detector_path 为空
        - 跟 v2.7.16 行为完全一致: mp.solutions.hands 跑整帧
    Mode B (二段 pipeline): host.mediapipe_hand_detector_path 配了有效路径
        - 加载 YOLO hand-detector + HandLandmarker(Tasks API)
        - 每帧: YOLO 检框 -> 扩 ROI -> HandLandmarker on ROI -> 关键点变回全帧
        - 加载任意一步失败 -> 自动回退到 Mode A
    """

    def __init__(self, host=None):
        self._host = host
        # ---------- 老 baseline 字段 (向后兼容) ----------
        self._mp_pose = None
        self._mp_hands = None
        self._mp_draw = None
        self._mp_draw_styles = None
        self._mp_last_pose_results = None
        self._mp_last_hands_results = None
        self._mp_frame_counter = 0
        self._mp_process_interval = 2

        # ---------- v3.8.0 二段 pipeline 字段 ----------
        self._hand_detector = None
        self._hand_detector_path_loaded: Optional[str] = None
        self._hand_detector_kind_loaded: Optional[str] = None
        self._hand_landmarker_tasks: Optional[_HandLandmarkerTasksAdapter] = None
        # 帧间缓存的 ROI 关键点: [(offset_xy, roi_size_wh, landmarks_list), ...]
        self._last_two_stage_results: List[tuple] = []
        # 二段 pipeline 启用状态 (由 init() 决定)
        self._two_stage_active = False
        self._init_lock = threading.Lock()

        # ---------- v3.32.0 异步推理线程 ----------
        # 历史问题: 推理原先内联在采集循环里, 手部模型 (尤其 complexity=1 +
        # interval=1) 单帧 30-40ms, 直接把采集帧率从 40+ 拖到十几帧、视频源慢放.
        # 现在: apply_overlay 只画上一次算好的结果 (1-2ms), 帧提交到单槽位,
        # 后台线程按自己的节奏跑推理; 模型加载/热重载也在后台线程做, 不再卡采集.
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_running = False
        self._pending_lock = threading.Lock()
        self._pending_cond = threading.Condition(self._pending_lock)
        self._pending_frame = None

        # ---------- 手部裁切快照 ----------
        # worker 发布的 observation 把“干净帧 + 同帧手框/关键点”绑成一个原子快照；
        # HTTP 短轮询只读该快照，不碰采集热循环，也不会读带 pose 的 current_frame。
        self._hand_observation_lock = threading.Lock()
        self._hand_crop_epoch = 0
        self._hand_observation_seq = 0
        self._hand_crop_observation = None
        # 没有副屏短轮询时不做任何裁切几何整理，保持功能关闭零热路径差异。
        self._hand_crop_requested_until = 0.0
        # 多个短轮询请求可能并发，裁切框 EMA 与 JPEG cache 单独串行化。
        self._hand_crop_lock = threading.Lock()
        self._hand_crop_last_seq = 0
        self._hand_crop_rect: Optional[Tuple[int, int, int, int]] = None
        self._hand_crop_cache_key = None
        self._hand_crop_jpeg: Optional[bytes] = None
        self._no_hands_jpeg: Optional[bytes] = None

    # ---------------- 公共 API ----------------

    def init(self):
        """Lazy-load: 根据 host 配置决定走 baseline 还是二段 pipeline."""
        host = self._host
        with self._init_lock:
            try:
                import mediapipe as mp
                self._mp_draw = mp.solutions.drawing_utils
                self._mp_draw_styles = mp.solutions.drawing_styles
                conf = max(0.05, min(1.0, getattr(host, "mediapipe_confidence", 0.7)))

                # ---------- pose 部分: 维持老逻辑 (不受二段影响) ----------
                if host.mediapipe_pose and self._mp_pose is None:
                    self._mp_pose = mp.solutions.pose.Pose(
                        static_image_mode=False,
                        model_complexity=0,
                        min_detection_confidence=conf,
                        min_tracking_confidence=0.5,
                    )
                    print(f"[MediaPipe] Pose 模型已加载 (confidence={conf})")

                # ---------- hands 部分: 二选一 ----------
                if host.mediapipe_hands:
                    self._init_hands_pipeline(conf)
                else:
                    self._two_stage_active = False

            except ImportError:
                print("[MediaPipe] 警告: mediapipe 未安装，pip install mediapipe")
                host.mediapipe_enabled = False
            except Exception as e:
                print(f"[MediaPipe] 初始化失败: {e}")
                host.mediapipe_enabled = False

    def release(self):
        """释放所有资源, 重置缓存. 先停后台推理线程再关模型, 避免关到一半还在用."""
        self._worker_running = False
        with self._pending_lock:
            self._pending_frame = None
            self._pending_cond.notify_all()
        if self._worker_thread is not None:
            try:
                self._worker_thread.join(timeout=3.0)
            except Exception:
                pass
            self._worker_thread = None
        if self._mp_pose is not None:
            try:
                self._mp_pose.close()
            except Exception:
                pass
            self._mp_pose = None
        if self._mp_hands is not None:
            try:
                self._mp_hands.close()
            except Exception:
                pass
            self._mp_hands = None
        if self._hand_landmarker_tasks is not None:
            try:
                self._hand_landmarker_tasks.close()
            except Exception:
                pass
            self._hand_landmarker_tasks = None
        # hand-detector (YOLO) 没有显式 close, 让 GC 收
        self._hand_detector = None
        self._hand_detector_path_loaded = None
        self._hand_detector_kind_loaded = None
        self._two_stage_active = False
        self._mp_last_pose_results = None
        self._mp_last_hands_results = None
        self._last_two_stage_results = []
        self._mp_frame_counter = 0
        self._clear_hand_crop_state()
        print("[MediaPipe] 资源已释放")

    def _clear_hand_crop_state(self):
        """清掉跨会话的手部裁切缓存，避免停用/重载后展示陈旧画面。"""
        with self._hand_observation_lock:
            self._hand_crop_epoch += 1
            self._hand_observation_seq += 1
            barrier_seq = self._hand_observation_seq
            self._hand_crop_observation = None
            self._hand_crop_requested_until = 0.0
        with self._hand_crop_lock:
            # 已抓到旧 observation、但尚未进入 crop 锁的 HTTP 请求，必须被
            # 这条 barrier 拒绝，不能在 clear 返回后把跨会话旧 JPEG 写回来。
            self._hand_crop_last_seq = max(self._hand_crop_last_seq, barrier_seq)
            self._hand_crop_rect = None
            self._hand_crop_cache_key = None
            self._hand_crop_jpeg = None

    def _publish_hand_crop_observation(
        self,
        frame,
        hand_boxes,
        landmark_groups,
        *,
        epoch: Optional[int] = None,
    ):
        """由 MediaPipe worker 发布同帧干净画面与像素几何。"""
        frozen_boxes = tuple(
            tuple(float(value) for value in box[:4])
            for box in (hand_boxes or ())
            if box is not None and len(box) >= 4
        )
        frozen_landmarks = tuple(
            tuple((float(point[0]), float(point[1])) for point in (landmarks or ()))
            for landmarks in (landmark_groups or ())
        )
        # frame 是 pending_frame 的独占副本；worker 后续不再修改，直接转移引用，
        # 避免每次手部推理再复制一张全分辨率图。
        with self._hand_observation_lock:
            # release/clear 可能在一次慢推理期间发生。旧代 worker 即使稍后返回，
            # 也不能重新发布已经失效的帧。
            if epoch is not None and epoch != self._hand_crop_epoch:
                return False
            self._hand_observation_seq += 1
            self._hand_crop_observation = (
                self._hand_observation_seq,
                frame,
                frozen_boxes,
                frozen_landmarks,
            )
            return True

    def _hand_crop_is_requested(self) -> bool:
        # CPython 下 float 引用读写原子；默认 0 只做一次比较，不给原热路径加锁。
        deadline = self._hand_crop_requested_until
        return deadline > 0.0 and deadline >= time.monotonic()

    @staticmethod
    def _baseline_hand_geometry(results, frame_w: int, frame_h: int):
        """把 baseline 全帧归一化 landmarks 转成像素点；不伪造模型手框。"""
        groups = []
        for hand_lm in getattr(results, "multi_hand_landmarks", None) or ():
            points = tuple(
                (
                    max(0.0, min(float(frame_w), float(lm.x) * frame_w)),
                    max(0.0, min(float(frame_h), float(lm.y) * frame_h)),
                )
                for lm in getattr(hand_lm, "landmark", ())
            )
            if not points:
                continue
            groups.append(points)
        return (), groups

    def _no_hands_snapshot_locked(self) -> Optional[bytes]:
        if self._no_hands_jpeg is None:
            ok, buffer = cv2.imencode(
                ".jpg", _make_no_hands_frame(),
                [cv2.IMWRITE_JPEG_QUALITY, 60],
            )
            if ok:
                self._no_hands_jpeg = buffer.tobytes()
        return self._no_hands_jpeg

    def _get_hands_crop_snapshot(self, view_mode: str = "follow") -> Optional[bytes]:
        host = self._host
        normalized_view_mode = (
            "fixed"
            if str(view_mode).strip().lower() == "fixed"
            else "follow"
        )
        if not getattr(host, "mediapipe_enabled", False) or not getattr(host, "mediapipe_hands", False):
            self._clear_hand_crop_state()
            with self._hand_crop_lock:
                return self._no_hands_snapshot_locked()

        with self._hand_observation_lock:
            # 前端 10-15fps 轮询会持续续期；窗口关闭后 3 秒自动退出附加整理路径。
            self._hand_crop_requested_until = time.monotonic() + 3.0
            observation = self._hand_crop_observation

        with self._hand_crop_lock:
            if observation is None:
                return self._hand_crop_jpeg or self._no_hands_snapshot_locked()

            seq, frame, hand_boxes, landmark_groups = observation
            if seq < self._hand_crop_last_seq:
                # 较新的并发请求已经提交，旧请求只复用最新缓存，禁止倒退 EMA/JPEG。
                return self._hand_crop_jpeg or self._no_hands_snapshot_locked()
            self._hand_crop_last_seq = seq
            if not hand_boxes and not any(landmark_groups):
                # 暂时丢手时冻结最后一张裁切，绝不退回整幅工位图。
                return self._hand_crop_jpeg or self._no_hands_snapshot_locked()

            pad_ratio = max(0.0, min(
                2.0,
                float(getattr(host, "mediapipe_hand_roi_pad", 0.3)),
            ))
            line_color = _hex_to_bgr(
                getattr(host, "mediapipe_hands_color", "#00FF00"),
                (0, 255, 0),
            )
            point_hex = getattr(host, "mediapipe_hands_point_color", "") or \
                getattr(host, "mediapipe_hands_color", "#00FF00")
            point_color = _hex_to_bgr(point_hex, line_color)
            thickness = max(1, min(
                10,
                int(getattr(host, "mediapipe_hands_thickness", 2)),
            ))
            custom_style = bool(getattr(host, "mediapipe_custom_style", False))
            cache_key = (
                seq,
                normalized_view_mode,
                pad_ratio,
                custom_style,
                line_color,
                point_color,
                thickness,
            )
            if cache_key == self._hand_crop_cache_key and self._hand_crop_jpeg is not None:
                return self._hand_crop_jpeg

            if normalized_view_mode == "follow":
                crop_rect = _compute_hands_crop_rect(
                    frame.shape,
                    hand_boxes,
                    landmark_groups,
                    pad_ratio=pad_ratio,
                    previous_rect=self._hand_crop_rect,
                    fixed_width_ratio=_HAND_CROP_VIEW_WIDTH_RATIO,
                    follow_center=True,
                )
            else:
                crop_rect = _compute_center_crop_rect(
                    frame.shape,
                    width_ratio=_HAND_CROP_VIEW_WIDTH_RATIO,
                )
            hand_specs = self._hand_draw_specs()
            crop = _render_hands_crop(
                frame,
                crop_rect,
                hand_boxes,
                landmark_groups,
                max_edge=640,
                box_color=line_color,
                line_color=line_color,
                point_color=point_color,
                thickness=thickness,
                draw_landmarks=(
                    self._mp_draw.draw_landmarks
                    if hand_specs is not None and self._mp_draw is not None
                    else None
                ),
                landmark_drawing_spec=hand_specs[0] if hand_specs is not None else None,
                connection_drawing_spec=hand_specs[1] if hand_specs is not None else None,
            ) if crop_rect is not None else None
            if crop is None:
                return self._hand_crop_jpeg or self._no_hands_snapshot_locked()

            ok, buffer = cv2.imencode(
                ".jpg", crop,
                [cv2.IMWRITE_JPEG_QUALITY, 60],
            )
            if not ok:
                return self._hand_crop_jpeg or self._no_hands_snapshot_locked()

            self._hand_crop_rect = crop_rect
            self._hand_crop_cache_key = cache_key
            self._hand_crop_jpeg = buffer.tobytes()
            return self._hand_crop_jpeg

    def get_hands_crop_snapshot(self, view_mode: str = "follow") -> Optional[bytes]:
        """返回只含手框/指关节的裁切 JPEG；异常隔离在副屏路径内。"""
        try:
            return self._get_hands_crop_snapshot(view_mode=view_mode)
        except Exception as exc:
            print(f"[MediaPipe hands crop] 生成快照失败: {exc}", file=sys.stderr)
            with self._hand_crop_lock:
                return self._hand_crop_jpeg or self._no_hands_snapshot_locked()

    def apply_overlay(self, frame):
        """在帧上画 pose + hands 骨架 (v3.32.0 异步化).

        本方法跑在采集线程, 只做两件轻活:
          1. 按处理间隔把当前帧副本投递给后台推理线程 (单槽位, 忙时跳过不排队)
          2. 把后台线程上一次算好的骨架画到本帧 (1-2ms)
        模型 lazy 加载 / 热重载 / 推理全在后台线程, 不阻塞采集.
        """
        host = self._host
        if not host.mediapipe_enabled:
            return frame

        self._ensure_worker()

        # ---------- 投递帧 (按间隔; latest-wins: 覆盖旧帧, 后台永远算最新画面) ----------
        # 不做"忙时跳过": 跳过会让后台消费到一帧 20-40ms 前的旧画面,
        # 快速动作下骨架滞后被放大一个推理周期. 覆盖的代价只是一次帧拷贝 (<1ms).
        self._mp_frame_counter += 1
        should_process = (self._mp_frame_counter %
                          max(self._mp_process_interval, 1)) == 0
        if should_process:
            with self._pending_lock:
                self._pending_frame = frame.copy()
                self._pending_cond.notify()

        # ---------- 渲染缓存结果 (init 未完成时先原样返回) ----------
        if self._mp_draw is None:
            return frame

        # 取本地引用, 后台线程整体替换结果对象, 不原地修改 → 无需加锁
        pose_results = self._mp_last_pose_results
        if pose_results and pose_results.pose_landmarks:
            import mediapipe as mp
            pose_specs = self._custom_draw_specs("pose")
            if pose_specs is not None:
                self._mp_draw.draw_landmarks(
                    frame,
                    pose_results.pose_landmarks,
                    mp.solutions.pose.POSE_CONNECTIONS,
                    landmark_drawing_spec=pose_specs[0],
                    connection_drawing_spec=pose_specs[1],
                )
            else:
                self._mp_draw.draw_landmarks(
                    frame,
                    pose_results.pose_landmarks,
                    mp.solutions.pose.POSE_CONNECTIONS,
                    landmark_drawing_spec=self._mp_draw_styles.get_default_pose_landmarks_style(),
                )

        # hands: 二段或 baseline 不同渲染路径
        if self._two_stage_active:
            self._draw_two_stage_hands(frame)
        else:
            self._draw_baseline_hands(frame)

        return frame

    # ---------------- 后台推理线程 ----------------

    def _ensure_worker(self):
        """确保后台推理线程在跑 (幂等, 双检)."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        with self._init_lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._worker_running = True
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name=f"mp-overlay-worker-ch{getattr(self._host, 'channel_id', '?')}",
                daemon=True,
            )
            self._worker_thread.start()

    def _worker_loop(self):
        """后台推理循环: 等帧 → (首帧 lazy init / 热重载) → 推理 → 写结果缓存."""
        while self._worker_running:
            with self._pending_lock:
                while self._pending_frame is None and self._worker_running:
                    self._pending_cond.wait(timeout=0.5)
                frame = self._pending_frame
                self._pending_frame = None
            if frame is None or not self._worker_running:
                continue
            try:
                if self._mp_draw is None:
                    self.init()
                    if self._mp_draw is None or not self._host.mediapipe_enabled:
                        continue  # init 失败 (未安装等), enabled 已被置 False
                self._check_hot_reload()
                self._run_inference(frame)
            except Exception as e:
                print(f"[MediaPipe] 后台推理异常: {e}", file=sys.stderr)

    def _check_hot_reload(self):
        """hand-detector 路径/类型变了 → 后台线程内重载 (模型加载不卡采集)."""
        host = self._host
        cur_path = (getattr(host, "mediapipe_hand_detector_path", "") or "").strip()
        cur_kind = (getattr(host, "mediapipe_hand_detector_kind", "v8") or "v8").strip()
        if cur_path != (self._hand_detector_path_loaded or "") or \
           cur_kind != (self._hand_detector_kind_loaded or ""):
            self._reload_hands_pipeline()

    # ---------------- 内部方法 ----------------

    def _custom_draw_specs(self, kind: str):
        """自定义纯色骨架样式 (v3.32.0; 关键点/连线颜色可分开配).

        开关关闭时返回 None (走 MediaPipe 默认花色样式, 与老版本行为一致);
        开启时返回 (landmark_spec, connection_spec), 姿态/手部各用各的颜色+粗细.
        关键点颜色字段缺省/为空时跟随线条颜色 (老配置升级视觉不变).
        """
        host = self._host
        if not getattr(host, "mediapipe_custom_style", False):
            return None
        if kind == "pose":
            line_hex = getattr(host, "mediapipe_pose_color", "#00FF00")
            point_hex = getattr(host, "mediapipe_pose_point_color", "") or line_hex
            thickness = int(getattr(host, "mediapipe_pose_thickness", 2))
        else:
            line_hex = getattr(host, "mediapipe_hands_color", "#00FF00")
            point_hex = getattr(host, "mediapipe_hands_point_color", "") or line_hex
            thickness = int(getattr(host, "mediapipe_hands_thickness", 2))
        line_color = _hex_to_bgr(line_hex, (0, 255, 0))
        point_color = _hex_to_bgr(point_hex, line_color)
        thickness = max(1, min(10, thickness))
        radius = max(2, thickness + 1)
        landmark_spec = self._mp_draw.DrawingSpec(
            color=point_color, thickness=thickness, circle_radius=radius)
        connection_spec = self._mp_draw.DrawingSpec(
            color=line_color, thickness=thickness, circle_radius=radius)
        return landmark_spec, connection_spec

    def _hand_draw_specs(self):
        """返回主、副屏共同使用的手部 DrawingSpec。"""
        if self._mp_draw is None:
            return None
        custom_specs = self._custom_draw_specs("hands")
        if custom_specs is not None:
            return custom_specs
        if self._mp_draw_styles is None:
            return None
        return (
            self._mp_draw_styles.get_default_hand_landmarks_style(),
            self._mp_draw_styles.get_default_hand_connections_style(),
        )

    def _init_hands_pipeline(self, conf: float):
        """根据 host.mediapipe_hand_detector_path 决定走 baseline 还是二段."""
        host = self._host
        cur_path = (getattr(host, "mediapipe_hand_detector_path", "") or "").strip()
        cur_kind = (getattr(host, "mediapipe_hand_detector_kind", "v8") or "v8").strip()

        if cur_path and os.path.exists(cur_path):
            ok = self._try_init_two_stage(cur_path, cur_kind, conf)
            if ok:
                self._two_stage_active = True
                self._hand_detector_path_loaded = cur_path
                self._hand_detector_kind_loaded = cur_kind
                print(f"[MediaPipe] 二段 pipeline 已启用 (detector={cur_path}, kind={cur_kind})")
                return
            else:
                print("[MediaPipe] 二段 pipeline 初始化失败, 回退 baseline")

        # baseline: mp.solutions.hands
        self._two_stage_active = False
        if self._mp_hands is None:
            import mediapipe as mp
            # v3.8.0: 暴露 model_complexity 与 track_confidence 给 host 控制
            # 朋友程序的"完美骨架"密码 = complexity=1 + det_conf=0.5 + track_conf=0.5
            mc = int(getattr(host, "mediapipe_model_complexity", 0))
            mc = max(0, min(1, mc))  # 老版 mp.solutions.hands 只支持 0/1
            track_conf = float(getattr(host, "mediapipe_track_confidence", 0.5))
            track_conf = max(0.05, min(0.95, track_conf))
            self._mp_hands = mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=2,
                model_complexity=mc,
                min_detection_confidence=conf,
                min_tracking_confidence=track_conf,
            )
            print(f"[MediaPipe] Hands 模型已加载 (baseline mp.solutions.hands, "
                  f"complexity={mc}, det_conf={conf}, track_conf={track_conf})")
        # baseline 也要把 path/kind 记下, 避免热更新比较时误以为"配置变了"
        self._hand_detector_path_loaded = cur_path
        self._hand_detector_kind_loaded = cur_kind

    def _try_init_two_stage(self, detector_path: str, kind: str, conf: float) -> bool:
        """尝试初始化二段 pipeline. 成功返回 True, 失败返回 False (调用方回退 baseline)."""
        try:
            det_conf = float(getattr(self._host, "mediapipe_hand_detector_conf", 0.25))
            det_iou = float(getattr(self._host, "mediapipe_hand_detector_iou", 0.45))
            det_imgsz = int(getattr(self._host, "mediapipe_hand_detector_imgsz", 640))
            det_class = int(getattr(self._host, "mediapipe_hand_detector_class", -1))
            det_device = (getattr(self._host, "device", "auto") or "auto")

            if kind == "v5":
                detector = _YOLOv5HandDetector(detector_path, det_conf, det_iou,
                                               det_imgsz, det_device, det_class)
            else:
                detector = _YOLOv8HandDetector(detector_path, det_conf, det_iou,
                                               det_imgsz, det_device, det_class)
            # 加载 HandLandmarker .task
            task_path = self._resolve_task_model_path()
            if not task_path or not os.path.exists(task_path):
                print(f"[MediaPipe] .task 模型不存在 ({task_path}), 二段 pipeline 不可用")
                return False
            self._hand_detector = detector
            self._hand_landmarker_tasks = _HandLandmarkerTasksAdapter(
                task_path,
                num_hands=2,
                min_det_conf=conf,
                min_track_conf=conf,
            )
            print(f"[MediaPipe] hand-detector ({kind}) 已加载: {detector_path}  "
                  f"类别={getattr(detector, 'names', {})}")
            print(f"[MediaPipe] HandLandmarker(Tasks) 已加载: {task_path}")
            return True
        except Exception as e:
            print(f"[MediaPipe] 二段 pipeline 初始化异常: {e}", file=sys.stderr)
            self._hand_detector = None
            if self._hand_landmarker_tasks is not None:
                try:
                    self._hand_landmarker_tasks.close()
                except Exception:
                    pass
                self._hand_landmarker_tasks = None
            return False

    def _resolve_task_model_path(self) -> Optional[str]:
        """找 hand_landmarker.task 模型路径.

        优先级:
            1. host.mediapipe_landmarker_task_path (显式配)
            2. backend/data/models/hand_landmarker.task (项目内置)
        """
        explicit = (getattr(self._host, "mediapipe_landmarker_task_path", "") or "").strip()
        if explicit and os.path.exists(explicit):
            return explicit
        # 项目内置位置 (相对 backend/api/source_mediapipe.py)
        here = Path(__file__).resolve()
        for ancestor in [here.parents[2], here.parents[1]]:  # tianjun 根 / backend
            candidate = ancestor / DEFAULT_TASK_MODEL_REL.split("backend/", 1)[-1] \
                if "backend/" in str(ancestor).lower() else ancestor / DEFAULT_TASK_MODEL_REL
            if candidate.exists():
                return str(candidate)
        # 兜底: 直接拼 backend/data/models/hand_landmarker.task
        backend_dir = here.parents[1]  # .../backend
        candidate = backend_dir / "data" / "models" / "hand_landmarker.task"
        return str(candidate) if candidate.exists() else None

    def _reload_hands_pipeline(self):
        """配置变更后重新加载 hands 部分 (pose 保留)."""
        if self._mp_hands is not None:
            try:
                self._mp_hands.close()
            except Exception:
                pass
            self._mp_hands = None
        if self._hand_landmarker_tasks is not None:
            try:
                self._hand_landmarker_tasks.close()
            except Exception:
                pass
            self._hand_landmarker_tasks = None
        self._hand_detector = None
        self._two_stage_active = False
        conf = max(0.05, min(1.0, getattr(self._host, "mediapipe_confidence", 0.7)))
        self._init_hands_pipeline(conf)

    def _run_inference(self, frame):
        """跑 pose + hands 推理 (二段或 baseline 自动选择)."""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        crop_requested = self._hand_crop_is_requested()
        crop_epoch = self._hand_crop_epoch

        # pose 部分
        if self._mp_pose is not None and self._host.mediapipe_pose:
            try:
                self._mp_last_pose_results = self._mp_pose.process(rgb)
            except Exception:
                self._mp_last_pose_results = None
        else:
            self._mp_last_pose_results = None

        # hands 部分: 分支
        if not self._host.mediapipe_hands:
            self._mp_last_hands_results = None
            self._last_two_stage_results = []
            if crop_requested:
                self._publish_hand_crop_observation(frame, (), (), epoch=crop_epoch)
            return

        if self._two_stage_active and self._hand_detector and self._hand_landmarker_tasks:
            hand_boxes, landmark_groups = self._run_two_stage_hands(
                frame,
                collect_crop_geometry=crop_requested,
            )
            if crop_requested:
                self._publish_hand_crop_observation(
                    frame,
                    hand_boxes,
                    landmark_groups,
                    epoch=crop_epoch,
                )
        elif self._mp_hands is not None:
            try:
                self._mp_last_hands_results = self._mp_hands.process(rgb)
            except Exception:
                self._mp_last_hands_results = None
            if crop_requested:
                frame_h, frame_w = frame.shape[:2]
                hand_boxes, landmark_groups = self._baseline_hand_geometry(
                    self._mp_last_hands_results,
                    frame_w,
                    frame_h,
                )
                self._publish_hand_crop_observation(
                    frame,
                    hand_boxes,
                    landmark_groups,
                    epoch=crop_epoch,
                )
        elif crop_requested:
            self._publish_hand_crop_observation(frame, (), (), epoch=crop_epoch)

    def _run_two_stage_hands(self, frame, collect_crop_geometry: bool = False):
        """二段 pipeline 推理，并返回同帧原始手框与全帧像素关键点。"""
        h, w = frame.shape[:2]
        pad_ratio = float(getattr(self._host, "mediapipe_hand_roi_pad", 0.3))
        bboxes = self._hand_detector.predict(frame)
        results = []
        for (x1, y1, x2, y2) in bboxes:
            ex1, ey1, ex2, ey2 = _expand_bbox(x1, y1, x2, y2, w, h, pad_ratio)
            roi = frame[ey1:ey2, ex1:ex2]
            if roi.size == 0:
                continue
            try:
                hands_lm = self._hand_landmarker_tasks.detect(roi)
            except Exception as e:
                print(f"[MediaPipe two-stage] landmarker 跑 ROI 失败: {e}", file=sys.stderr)
                continue
            for lm in hands_lm:
                results.append(((ex1, ey1), (ex2 - ex1, ey2 - ey1), lm))
        self._last_two_stage_results = results
        if not collect_crop_geometry:
            return (), ()
        landmark_groups = []
        for (offset, roi_size, landmarks) in results:
            ox, oy = offset
            rw, rh = roi_size
            landmark_groups.append(tuple(
                (
                    max(0.0, min(float(w), ox + float(lm.x) * rw)),
                    max(0.0, min(float(h), oy + float(lm.y) * rh)),
                )
                for lm in landmarks
            ))
        # bboxes 是未外扩的 YOLO 手框。裁切层会把它与 landmarks 并集后只按
        # mediapipe_hand_roi_pad 外扩一次；不能拿 ex* ROI 再外扩造成双 padding。
        return bboxes, landmark_groups

    def _draw_baseline_hands(self, frame):
        """老 baseline 渲染: mp.solutions.hands 结果."""
        if not self._mp_last_hands_results:
            return
        if not getattr(self._mp_last_hands_results, "multi_hand_landmarks", None):
            return
        import mediapipe as mp
        hand_specs = self._hand_draw_specs()
        if hand_specs is None:
            return
        for hand_lm in self._mp_last_hands_results.multi_hand_landmarks:
            self._mp_draw.draw_landmarks(
                frame,
                hand_lm,
                mp.solutions.hands.HAND_CONNECTIONS,
                hand_specs[0],
                hand_specs[1],
            )

    def _draw_two_stage_hands(self, frame):
        """二段 pipeline 渲染: ROI 归一化关键点 → 全帧归一化坐标 → 复用 baseline 同款绘制.

        把 Tasks API 输出转成 NormalizedLandmarkList 后走 mp draw_landmarks,
        默认多彩配色 / 自定义纯色两条路都与 baseline 完全一致 (老版蓝线黄点手工渲染已废弃).
        """
        if not self._last_two_stage_results:
            return
        import mediapipe as mp
        from mediapipe.framework.formats import landmark_pb2
        # 直调本方法的工具/测试可能未走 init(), 就地补齐绘图句柄
        if self._mp_draw is None:
            self._mp_draw = mp.solutions.drawing_utils
            self._mp_draw_styles = mp.solutions.drawing_styles
        fh, fw = frame.shape[:2]
        hand_specs = self._hand_draw_specs()
        if hand_specs is None:
            return
        for (offset, roi_size, landmarks) in self._last_two_stage_results:
            ox, oy = offset
            rw, rh = roi_size
            lm_list = landmark_pb2.NormalizedLandmarkList(landmark=[
                landmark_pb2.NormalizedLandmark(
                    x=(ox + lm.x * rw) / fw,
                    y=(oy + lm.y * rh) / fh,
                    z=getattr(lm, "z", 0.0),
                )
                for lm in landmarks
            ])
            self._mp_draw.draw_landmarks(
                frame,
                lm_list,
                mp.solutions.hands.HAND_CONNECTIONS,
                hand_specs[0],
                hand_specs[1],
            )
