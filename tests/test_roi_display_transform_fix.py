# -*- coding: utf-8 -*-
"""ROI 偏差修复（2026-07 缺陷 B）回归: 模型级 ROI 遮罩的显示坐标 → 原图坐标反变换。

背景: ROI 多边形是在显示帧快照(已套旋转/镜像)上画的, 而遮罩套在未变换的
原图推理帧上。修复前遮罩直接用显示坐标顶点 → 通道配了旋转/镜像时区域整体错位。

覆盖:
  1. map_point_display_to_original 是 map_bbox_to_display 的严格逆(8 种变换组合往返)
  2. ensure_roi_mask 传 transform 后, 遮罩落在原图的正确区域(旋转 90° 场景实测像素)
  3. 变换签名进缓存键: 运行时改旋转 → mask 自动重建; 不变 → 缓存复用
  4. 无变换时行为与修复前完全一致(恒等, 不引入回归)
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.api.source_inference_router import ModelInstance
from backend.api.source_roi import apply_roi_mask, ensure_roi_mask
from backend.api.source_video_transform import VideoTransform


def _vt(rot=0, fh=False, fv=False) -> VideoTransform:
    t = VideoTransform()
    t.video_rotation = rot
    t.video_flip_h = fh
    t.video_flip_v = fv
    return t


ALL_COMBOS = [(rot, fh, fv) for rot in (0, 90, 180, 270)
              for fh in (False, True) for fv in (False, True)]


# ==================== 1. 逆变换正确性 ====================

@pytest.mark.parametrize("rot,fh,fv", ALL_COMBOS)
def test_逆变换是正变换的严格逆(rot, fh, fv):
    t = _vt(rot, fh, fv)
    # 取一组非对称采样点, 避开 0.5 对称轴掩盖错误
    for (x, y) in [(0.1, 0.2), (0.7, 0.3), (0.25, 0.9), (0.0, 0.0), (1.0, 1.0)]:
        # 正变换: 原图点 → 显示点 (bbox 退化为点 w=h=0)
        dx, dy, _, _ = t.map_bbox_to_display(x, y, 0.0, 0.0)
        # 逆变换应还原
        rx, ry = t.map_point_display_to_original(dx, dy)
        assert rx == pytest.approx(x, abs=1e-9), f"rot={rot} fh={fh} fv={fv} x 不还原"
        assert ry == pytest.approx(y, abs=1e-9), f"rot={rot} fh={fh} fv={fv} y 不还原"


# ==================== 2. 遮罩落区实测 ====================

def _mask_for(roi_display, frame_hw, transform):
    """构造 mi + 生成 mask, 返回 (h, w) uint8"""
    mi = ModelInstance(name="t")
    mi.roi = roi_display
    assert ensure_roi_mask(mi, frame_hw, transform=transform)
    return mi._roi_mask_cache


def test_旋转90时遮罩落在原图正确区域():
    """显示帧 = 原图顺时针转 90°。原图左下角转完落到显示左上角
    (正变换 (x,y)→(1-y,x): 原图(0,1)→显示(0,0))。
    用户在显示帧左上角画 ROI → 遮罩应落在原图左下角。"""
    # 原图 100(h) x 200(w)。显示坐标系左上角四分之一块:
    roi_display = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
    mask = _mask_for(roi_display, (100, 200), _vt(rot=90))
    assert mask.shape == (100, 200)
    # 逆推: 显示(0,0)→原图(0,1)=左下角; 显示(0.5,0.5)→原图(0.5,0.5)
    # → 原图上遮罩覆盖 x∈[0,0.5w], y∈[0.5h,h] 的左下角块
    assert mask[75, 50] == 255, "原图左下角应在 ROI 内"
    assert mask[25, 50] == 0, "原图左上角应在 ROI 外"
    assert mask[75, 150] == 0, "原图右下角应在 ROI 外"
    assert mask[25, 150] == 0, "原图右上角应在 ROI 外"


def test_水平镜像时遮罩左右互换():
    roi_display = [[0.0, 0.0], [0.4, 0.0], [0.4, 1.0], [0.0, 1.0]]  # 显示左侧 40%
    mask = _mask_for(roi_display, (100, 100), _vt(fh=True))
    assert mask[50, 80] == 255, "镜像后原图右侧应在 ROI 内"
    assert mask[50, 20] == 0, "镜像后原图左侧应在 ROI 外"


def test_无变换时遮罩与修复前一致():
    roi = [[0.0, 0.0], [0.4, 0.0], [0.4, 1.0], [0.0, 1.0]]
    mask_none = _mask_for(roi, (100, 100), None)
    mask_id = _mask_for(roi, (100, 100), _vt())  # 恒等变换
    assert np.array_equal(mask_none, mask_id)
    assert mask_none[50, 20] == 255 and mask_none[50, 80] == 0


# ==================== 3. 缓存键含变换签名 ====================

def test_运行时改旋转触发mask重建_不变则复用():
    mi = ModelInstance(name="t")
    mi.roi = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
    t = _vt(rot=0)
    assert ensure_roi_mask(mi, (100, 100), transform=t)
    mask_v1 = mi._roi_mask_cache

    # 同签名再调 → 复用同一对象
    assert ensure_roi_mask(mi, (100, 100), transform=t)
    assert mi._roi_mask_cache is mask_v1

    # 改旋转 → 重建且内容不同
    t.video_rotation = 180
    assert ensure_roi_mask(mi, (100, 100), transform=t)
    assert mi._roi_mask_cache is not mask_v1
    assert not np.array_equal(mi._roi_mask_cache, mask_v1)


# ==================== 4. apply_roi_mask 端到端 ====================

def test_apply_roi_mask_带变换端到端():
    frame = np.full((100, 200, 3), 255, dtype=np.uint8)
    mi = ModelInstance(name="t")
    mi.roi = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]  # 显示左上
    out = apply_roi_mask(frame, mi, transform=_vt(rot=90))
    assert out[75, 50].tolist() == [255, 255, 255], "原图左下保留"
    assert out[25, 50].tolist() == [0, 0, 0], "原图左上置黑"
    # 原 frame 不被修改
    assert frame[25, 50].tolist() == [255, 255, 255]


def test_无roi时直接返回原frame():
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    mi = ModelInstance(name="t")
    mi.roi = None
    assert apply_roi_mask(frame, mi, transform=_vt(rot=90)) is frame
