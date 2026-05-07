"""ROI 裁剪工具 (Step 4 feat/multi-model-roi-link).

提供 mi.roi (归一化多边形) → 像素 mask 的转换 + bbox 后置过滤. 设计目标:

1. ROI mask 缓存: 帧大小不变就复用 mask, 避免每帧 cv2.fillPoly
2. 顶点缓存: 同时缓存像素顶点数组, 给 bbox 中心点过滤直接用
3. 双保险过滤:
     - 前置: 把 frame ROI 外区域置黑, 喂给模型
     - 后置: 检测框中心点不在 ROI 多边形内的也丢掉 (黑色边缘可能伪检测)
4. mi.roi=None / 顶点不足 3 点时, 跳过裁剪 (返回原 frame, 不过滤)

公开 API
========
ensure_roi_mask(mi, frame_shape) -> bool
    确保 mi 上的 ROI mask 缓存有效. 如果 mi.roi 不可用返回 False.

apply_roi_mask(frame, mi) -> np.ndarray
    根据 mi.roi 把 frame ROI 外区域置黑. 无 ROI 时直接返回原 frame.
    inplace 原则: mask 通过 bitwise_and 应用到 frame 副本, 不破坏原 frame.

is_bbox_center_in_roi(bbox_normalized, mi) -> bool
    检测框中心点是否在 ROI 多边形内. 无 ROI 时永远返回 True.
    bbox_normalized: dict{x,y,w,h} 归一化坐标 (0~1).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

import cv2
import numpy as np

if TYPE_CHECKING:
    from backend.api.source_inference_router import ModelInstance


def _validate_roi(roi) -> bool:
    """ROI 多边形是否合法: 非空 list 且至少 3 点"""
    if roi is None:
        return False
    if not isinstance(roi, (list, tuple)):
        return False
    if len(roi) < 3:
        return False
    for p in roi:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            return False
    return True


def ensure_roi_mask(mi: "ModelInstance", frame_shape: Tuple[int, int]) -> bool:
    """确保 mi 上的 ROI mask 缓存对当前 frame_shape 有效. mi.roi 不合法时返回 False.

    frame_shape: (h, w) — 通常 frame.shape[:2]
    """
    if not _validate_roi(mi.roi):
        # 清缓存防泄漏
        mi._roi_mask_cache = None
        mi._roi_mask_shape = None
        mi._roi_polygon_pixels = None
        return False

    h, w = frame_shape
    # 缓存命中: ROI 顶点和 frame_shape 都没变就复用
    if (mi._roi_mask_shape == (h, w)
            and mi._roi_mask_cache is not None
            and mi._roi_polygon_pixels is not None):
        return True

    # 重新生成: 归一化顶点 → 像素顶点 → fillPoly
    pts = np.array([(int(round(x * w)), int(round(y * h))) for x, y in mi.roi],
                   dtype=np.int32)
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)

    mi._roi_mask_cache = mask
    mi._roi_mask_shape = (h, w)
    mi._roi_polygon_pixels = pts
    return True


def apply_roi_mask(frame: np.ndarray, mi: "ModelInstance") -> np.ndarray:
    """把 frame 通过 mi.roi mask 裁剪. 无 ROI 时直接返回原 frame.

    返回的 frame 是新 ndarray (cv2.bitwise_and 创建), 不修改输入.
    """
    if frame is None or frame.size == 0:
        return frame
    if not ensure_roi_mask(mi, frame.shape[:2]):
        return frame
    return cv2.bitwise_and(frame, frame, mask=mi._roi_mask_cache)


def is_bbox_center_in_roi(bbox_normalized: dict, mi: "ModelInstance") -> bool:
    """检测框中心点是否在 ROI 多边形内. mi 上没缓存或无 ROI 时永远 True (不过滤).

    bbox_normalized: 归一化坐标 dict{x, y, w, h}, 来自 _detect_only 等 runner.
    """
    pts = mi._roi_polygon_pixels
    if pts is None or mi._roi_mask_shape is None:
        return True
    h, w = mi._roi_mask_shape
    cx_norm = bbox_normalized.get('x', 0) + bbox_normalized.get('w', 0) / 2
    cy_norm = bbox_normalized.get('y', 0) + bbox_normalized.get('h', 0) / 2
    cx, cy = int(round(cx_norm * w)), int(round(cy_norm * h))
    # 边界容忍: cv2.pointPolygonTest >= 0 表示在内或边上
    return cv2.pointPolygonTest(pts, (cx, cy), False) >= 0


__all__ = ["ensure_roi_mask", "apply_roi_mask", "is_bbox_center_in_roi"]
