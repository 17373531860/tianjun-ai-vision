"""Step 4 单测: source_roi.py — ROI mask 生成 / 缓存 / bbox 中心点过滤。

不依赖 GPU 或 ultralytics, 只测纯几何/cv2 逻辑.
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.api.source_inference_router import ModelInstance
from backend.api.source_roi import (
    ensure_roi_mask,
    apply_roi_mask,
    is_bbox_center_in_roi,
    _validate_roi,
)


# ============================================================
# _validate_roi
# ============================================================
def test_validate_roi_None_返回_False():
    assert _validate_roi(None) is False


def test_validate_roi_空列表_返回_False():
    assert _validate_roi([]) is False


def test_validate_roi_少于3点_返回_False():
    assert _validate_roi([[0.0, 0.0]]) is False
    assert _validate_roi([[0.0, 0.0], [1.0, 0.0]]) is False


def test_validate_roi_正确格式():
    assert _validate_roi([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]) is True
    assert _validate_roi([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]) is True


def test_validate_roi_非list_类型_返回_False():
    assert _validate_roi("not a list") is False
    assert _validate_roi({"key": "val"}) is False
    assert _validate_roi([[0.1], [0.2], [0.3]]) is False  # 单坐标点


# ============================================================
# ensure_roi_mask
# ============================================================
def test_ensure_roi_mask_无_ROI_返回_False_并清缓存():
    mi = ModelInstance(name='m', roi=None)
    mi._roi_mask_cache = "stale"
    mi._roi_mask_shape = (100, 200)
    mi._roi_polygon_pixels = "stale"

    ok = ensure_roi_mask(mi, (480, 640))
    assert ok is False
    assert mi._roi_mask_cache is None
    assert mi._roi_mask_shape is None
    assert mi._roi_polygon_pixels is None


def test_ensure_roi_mask_首次调用_生成_mask_和顶点():
    mi = ModelInstance(name='m', roi=[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    ok = ensure_roi_mask(mi, (100, 200))

    assert ok is True
    assert mi._roi_mask_cache is not None
    assert mi._roi_mask_cache.shape == (100, 200)
    assert mi._roi_mask_cache.dtype == np.uint8
    assert mi._roi_mask_shape == (100, 200)
    assert mi._roi_polygon_pixels is not None
    assert mi._roi_polygon_pixels.shape == (4, 2)
    # 像素坐标对应归一化 (200*0.1, 100*0.1) ~ (20, 10), 等等
    expected_first = [int(round(0.1 * 200)), int(round(0.1 * 100))]
    assert list(mi._roi_polygon_pixels[0]) == expected_first


def test_ensure_roi_mask_同样shape_命中缓存():
    mi = ModelInstance(name='m', roi=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
    ensure_roi_mask(mi, (100, 200))
    cached_mask = mi._roi_mask_cache
    cached_pts = mi._roi_polygon_pixels

    ensure_roi_mask(mi, (100, 200))
    assert mi._roi_mask_cache is cached_mask  # 同一对象, 没重建
    assert mi._roi_polygon_pixels is cached_pts


def test_ensure_roi_mask_shape变化_重建():
    mi = ModelInstance(name='m', roi=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
    ensure_roi_mask(mi, (100, 200))
    cached_mask = mi._roi_mask_cache

    ensure_roi_mask(mi, (200, 400))
    assert mi._roi_mask_cache is not cached_mask  # 重建了
    assert mi._roi_mask_cache.shape == (200, 400)


# ============================================================
# apply_roi_mask
# ============================================================
def test_apply_roi_mask_无_ROI_返回原frame():
    mi = ModelInstance(name='m', roi=None)
    frame = np.full((100, 200, 3), 128, dtype=np.uint8)
    out = apply_roi_mask(frame, mi)
    assert out is frame  # 同一对象, 没 copy


def test_apply_roi_mask_有_ROI_外置黑_内保留():
    mi = ModelInstance(name='m', roi=[[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]])
    frame = np.full((100, 200, 3), 200, dtype=np.uint8)
    out = apply_roi_mask(frame, mi)

    assert out is not frame  # cv2.bitwise_and 返回新 ndarray
    assert out.shape == frame.shape

    # 中心 (50, 100) 在 ROI 内: 保持原值 200
    assert out[50, 100, 0] == 200

    # 角落 (5, 5) 在 ROI 外: 应是 0
    assert out[5, 5, 0] == 0

    # 原 frame 不被修改
    assert frame[5, 5, 0] == 200


def test_apply_roi_mask_None_frame():
    mi = ModelInstance(name='m', roi=[[0, 0], [1, 0], [1, 1]])
    assert apply_roi_mask(None, mi) is None


def test_apply_roi_mask_空frame():
    mi = ModelInstance(name='m', roi=[[0, 0], [1, 0], [1, 1]])
    empty = np.array([], dtype=np.uint8)
    out = apply_roi_mask(empty, mi)
    assert out is empty


# ============================================================
# is_bbox_center_in_roi
# ============================================================
def test_bbox_center_无_ROI_永远_True():
    mi = ModelInstance(name='m', roi=None)
    bbox = {'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1}
    assert is_bbox_center_in_roi(bbox, mi) is True


def test_bbox_center_未_ensure_mask_永远_True():
    """没调 ensure_roi_mask 之前, _roi_polygon_pixels 还是 None"""
    mi = ModelInstance(name='m', roi=[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    # 没调 ensure_roi_mask
    bbox = {'x': 0.5, 'y': 0.5, 'w': 0.1, 'h': 0.1}
    assert is_bbox_center_in_roi(bbox, mi) is True


def test_bbox_center_在ROI内_True():
    mi = ModelInstance(name='m', roi=[[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]])
    ensure_roi_mask(mi, (100, 200))
    bbox = {'x': 0.45, 'y': 0.45, 'w': 0.1, 'h': 0.1}  # 中心 (0.5, 0.5)
    assert is_bbox_center_in_roi(bbox, mi) is True


def test_bbox_center_在ROI外_False():
    mi = ModelInstance(name='m', roi=[[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]])
    ensure_roi_mask(mi, (100, 200))
    bbox = {'x': 0.0, 'y': 0.0, 'w': 0.1, 'h': 0.1}  # 中心 (0.05, 0.05)
    assert is_bbox_center_in_roi(bbox, mi) is False


def test_bbox_center_在ROI边界_True():
    """cv2.pointPolygonTest >= 0 包含边界"""
    mi = ModelInstance(name='m', roi=[[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]])
    ensure_roi_mask(mi, (100, 200))
    # 中心精确落到 ROI 顶点 (0.25, 0.25)
    bbox = {'x': 0.20, 'y': 0.20, 'w': 0.10, 'h': 0.10}
    assert is_bbox_center_in_roi(bbox, mi) is True


# ============================================================
# 综合: 三角形 ROI + 多个 bbox 测试
# ============================================================
def test_综合_三角形ROI_测多个bbox():
    """三角形 ROI: (0.1, 0.1), (0.9, 0.1), (0.5, 0.9)
    顶点是上窄下宽的形状, 中心点 (0.5, 0.5) 在内, (0.2, 0.8) 在外."""
    mi = ModelInstance(name='m', roi=[[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]])
    ensure_roi_mask(mi, (100, 100))

    cases = [
        ({'x': 0.45, 'y': 0.45, 'w': 0.1, 'h': 0.1}, True, "中心 (0.5, 0.5) 在三角形内"),
        ({'x': 0.15, 'y': 0.75, 'w': 0.1, 'h': 0.1}, False, "中心 (0.2, 0.8) 在三角形外"),
        ({'x': 0.45, 'y': 0.05, 'w': 0.1, 'h': 0.1}, True, "中心 (0.5, 0.1) 在三角形上边"),
    ]
    for bbox, expected, msg in cases:
        actual = is_bbox_center_in_roi(bbox, mi)
        assert actual is expected, f"{msg}: 期望 {expected}, 实际 {actual}"
