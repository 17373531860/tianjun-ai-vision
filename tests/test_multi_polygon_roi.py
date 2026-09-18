"""2026-09 全系统多块 ROI 改造 — 单元回归.

覆盖「单块(旧)/多块(新)」双格式在各消费点的行为:
  1. source_geometry.normalize_polygons / point_in_any_polygon / normalize_rects
     双格式判别矩阵 (坏块剔除 / 完全非法 → 空)
  2. source_roi: mi.roi 多块 mask 并集像素断言 + 中心点过滤 (任一块命中 /
     夹缝不命中) + is_normalized_bbox_center_in_polygon 双格式
  3. label_split: 多块区域命中改写虚拟步骤 (两块同名区域, 夹缝不改写)
  4. region_events: 多块 region 的 region_enter 规则 (第二块内也确认)
  5. weighing_engine.point_in_polygon 双格式 (无配置=不限制语义保持)
  6. ai_modes _norm_rect / ocr._parse_roi 矩形区双格式

格式约定 (与前端 utils/polygons.js 对齐):
  多边形 单块: [[x,y],...]  /  多块: [[[x,y],...], ...]
  矩形   单块: [x,y,w,h]    /  多块: [[x,y,w,h], ...]
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.api.source_geometry import (
    normalize_polygons,
    normalize_rects,
    point_in_any_polygon,
)

# 两块互不相连的方块: 左上 (0.1~0.3)², 右下 (0.7~0.9)²
BLOCK_A = [[0.1, 0.1], [0.3, 0.1], [0.3, 0.3], [0.1, 0.3]]
BLOCK_B = [[0.7, 0.7], [0.9, 0.7], [0.9, 0.9], [0.7, 0.9]]
MULTI = [BLOCK_A, BLOCK_B]


# ============================================================
# 1. normalize_polygons 双格式判别矩阵
# ============================================================
def test_normalize_polygons_None_空_非法():
    assert normalize_polygons(None) == []
    assert normalize_polygons([]) == []
    assert normalize_polygons("nope") == []
    assert normalize_polygons([[0.1, 0.1], [0.2, 0.2]]) == []  # 单块不足 3 点


def test_normalize_polygons_单块格式():
    out = normalize_polygons(BLOCK_A)
    assert len(out) == 1
    assert out[0] == [[0.1, 0.1], [0.3, 0.1], [0.3, 0.3], [0.1, 0.3]]


def test_normalize_polygons_多块格式():
    out = normalize_polygons(MULTI)
    assert len(out) == 2
    assert out[0][0] == [0.1, 0.1]
    assert out[1][0] == [0.7, 0.7]


def test_normalize_polygons_多块_坏块剔除():
    # 第二块只有 2 点 → 剔除, 保留第一块
    out = normalize_polygons([BLOCK_A, [[0.5, 0.5], [0.6, 0.6]]])
    assert len(out) == 1
    # 全部是坏块 → 空
    assert normalize_polygons([[[0.5, 0.5]], [[0.6, 0.6]]]) == []


def test_point_in_any_polygon_两块与夹缝():
    assert point_in_any_polygon(0.2, 0.2, MULTI) is True    # 块 A 内
    assert point_in_any_polygon(0.8, 0.8, MULTI) is True    # 块 B 内
    assert point_in_any_polygon(0.5, 0.5, MULTI) is False   # 两块夹缝
    assert point_in_any_polygon(0.2, 0.2, BLOCK_A) is True  # 单块格式照常
    assert point_in_any_polygon(0.5, 0.5, None) is False    # 无配置 → False (上层各自决定语义)


# ============================================================
# 2. normalize_rects 双格式
# ============================================================
def test_normalize_rects_双格式():
    assert normalize_rects([0.1, 0.1, 0.2, 0.2]) == [[0.1, 0.1, 0.2, 0.2]]
    out = normalize_rects([[0.1, 0.1, 0.2, 0.2], [0.6, 0.6, 0.3, 0.3]])
    assert len(out) == 2
    assert normalize_rects(None) == []
    assert normalize_rects([0.1, 0.1, 0.0, 0.2]) == []          # w=0 非法
    assert normalize_rects([[0.1, 0.1, 0.2, 0.2], [0, 0, 0, 0]]) == [[0.1, 0.1, 0.2, 0.2]]


# ============================================================
# 3. source_roi: 模型推理 ROI 多块
# ============================================================
def test_roi_mask_多块并集():
    from backend.api.source_inference_router import ModelInstance
    from backend.api.source_roi import apply_roi_mask, ensure_roi_mask

    mi = ModelInstance(name='m', roi=MULTI)
    frame = np.full((100, 100, 3), 200, dtype=np.uint8)
    out = apply_roi_mask(frame, mi)

    assert out[20, 20, 0] == 200   # 块 A 内保留
    assert out[80, 80, 0] == 200   # 块 B 内保留
    assert out[50, 50, 0] == 0     # 夹缝置黑
    assert out[5, 95, 0] == 0      # 两块外置黑

    # 顶点缓存: 两块各一个数组
    assert ensure_roi_mask(mi, (100, 100)) is True
    assert isinstance(mi._roi_polygon_pixels, list)
    assert len(mi._roi_polygon_pixels) == 2


def test_roi_bbox_center_多块任一命中():
    from backend.api.source_inference_router import ModelInstance
    from backend.api.source_roi import ensure_roi_mask, is_bbox_center_in_roi

    mi = ModelInstance(name='m', roi=MULTI)
    ensure_roi_mask(mi, (100, 100))
    # 中心 (0.8, 0.8) 在块 B 内
    assert is_bbox_center_in_roi({'x': 0.75, 'y': 0.75, 'w': 0.1, 'h': 0.1}, mi) is True
    # 中心 (0.5, 0.5) 在夹缝
    assert is_bbox_center_in_roi({'x': 0.45, 'y': 0.45, 'w': 0.1, 'h': 0.1}, mi) is False


def test_norm_bbox_center_多块格式():
    from backend.api.source_roi import is_normalized_bbox_center_in_polygon

    in_b = {'x': 0.75, 'y': 0.75, 'w': 0.1, 'h': 0.1}   # 中心 (0.8, 0.8)
    in_gap = {'x': 0.45, 'y': 0.45, 'w': 0.1, 'h': 0.1}  # 中心 (0.5, 0.5)
    assert is_normalized_bbox_center_in_polygon(in_b, MULTI) is True
    assert is_normalized_bbox_center_in_polygon(in_gap, MULTI) is False
    # 单块格式行为不变
    assert is_normalized_bbox_center_in_polygon(in_gap, BLOCK_A) is False
    # 无有效块 → 不限制
    assert is_normalized_bbox_center_in_polygon(in_gap, None) is True
    assert is_normalized_bbox_center_in_polygon(in_gap, [[0.1, 0.1]]) is True


# ============================================================
# 4. label_split: 多块区域 → 同一虚拟步骤
# ============================================================
def test_label_split_多块区域命中改写():
    from backend.api.source_label_split import LabelSplitEngine, parse_label_splits

    rules = parse_label_splits({'label_splits': [{
        'id': 'ls1', 'enabled': True, 'source_label': '打螺丝',
        'unmatched': 'drop',
        'regions': [{'name': '双区螺丝', 'polygon': MULTI, 'color': '#f97316'}],
    }]})
    assert len(rules) == 1
    engine = LabelSplitEngine(rules)

    def det(cx, cy):
        return {'label': '打螺丝', 'confidence': 0.9,
                'x': cx - 0.02, 'y': cy - 0.02, 'w': 0.04, 'h': 0.04}

    out_a = engine.apply([det(0.2, 0.2)], now=1.0)
    assert len(out_a) == 1 and out_a[0]['label'] == '双区螺丝'   # 块 A 命中
    out_b = engine.apply([det(0.8, 0.8)], now=2.0)
    assert len(out_b) == 1 and out_b[0]['label'] == '双区螺丝'   # 块 B 命中
    out_gap = engine.apply([det(0.5, 0.5)], now=3.0)
    assert out_gap == []                                          # 夹缝 → drop


def test_placement_guide_多块就位():
    from backend.api.source_label_split import PlacementGuideState, parse_placement_guide

    cfg = parse_placement_guide({'placement_guide': {
        'enabled': True, 'anchor_label': '工件', 'polygon': MULTI, 'mode': 'hint',
    }})
    assert cfg is not None
    st = PlacementGuideState(cfg)
    st.update([{'label': '工件', 'confidence': 0.9,
                'x': 0.75, 'y': 0.75, 'w': 0.1, 'h': 0.1}], now=1.0)  # 中心在块 B
    assert st.snapshot(now=1.0)['in_position'] is True
    st.update([{'label': '工件', 'confidence': 0.9,
                'x': 0.45, 'y': 0.45, 'w': 0.1, 'h': 0.1}], now=2.0)  # 中心在夹缝
    assert st.snapshot(now=2.0)['in_position'] is False


# ============================================================
# 5. region_events: 多块 region 的 region_enter
# ============================================================
def test_region_events_多块region_enter():
    from backend.api.source_region_events import RegionEventEngine, parse_region_events

    cfg = parse_region_events({'region_events': {
        'enabled': True, 'gap_tolerance_frames': 3,
        'rules': [{'id': 'r1', 'name': '进区', 'type': 'region_enter',
                   'subject_label': '工件', 'region': MULTI, 'min_frames': 3}],
    }})
    assert cfg is not None

    def det(cx, cy):
        return {'label': '工件', 'confidence': 0.9,
                'x': cx - 0.05, 'y': cy - 0.05, 'w': 0.1, 'h': 0.1}

    def feed(engine, frames):
        out = []
        for i, dets in enumerate(frames):
            out.extend(engine.process_frame(dets, 0.0 + i * 0.04))
        return out

    # 块 B 内连续 5 帧 → 确认
    events_b = feed(RegionEventEngine(cfg), [[det(0.8, 0.8)]] * 5)
    assert any(e.get('action') == 'confirmed' and e.get('rule_name') == '进区'
               for e in events_b)
    # 夹缝连续 5 帧 → 不确认
    events_gap = feed(RegionEventEngine(cfg), [[det(0.5, 0.5)]] * 5)
    assert not any(e.get('action') == 'confirmed' for e in events_gap)


# ============================================================
# 6. weighing_engine.point_in_polygon 双格式 (语义: 无配置=不限制)
# ============================================================
def test_weighing_point_in_polygon_双格式():
    from backend.services.weighing_engine import point_in_polygon

    assert point_in_polygon(0.5, 0.5, None) is True      # 无配置 = 不限制 (老语义保持)
    assert point_in_polygon(0.5, 0.5, []) is True
    assert point_in_polygon(0.2, 0.2, BLOCK_A) is True   # 单块老格式
    assert point_in_polygon(0.8, 0.8, BLOCK_A) is False
    assert point_in_polygon(0.8, 0.8, MULTI) is True     # 多块新格式
    assert point_in_polygon(0.5, 0.5, MULTI) is False


# ============================================================
# 7. OCR / 异常矩形区双格式
# ============================================================
def test_ai_modes_norm_rect_双格式():
    from backend.api.source_ai_modes_mixin import _norm_rect

    assert _norm_rect([0.1, 0.1, 0.2, 0.2]) == [[0.1, 0.1, 0.2, 0.2]]
    out = _norm_rect([[0.1, 0.1, 0.2, 0.2], [0.6, 0.6, 0.3, 0.3]])
    assert out == [[0.1, 0.1, 0.2, 0.2], [0.6, 0.6, 0.3, 0.3]]
    assert _norm_rect(None) is None
    assert _norm_rect([0.1, 0.1, 0, 0.2]) is None        # w=0 非法 → 整帧


def test_ocr_parse_roi_双格式():
    from fastapi import HTTPException

    from backend.api.ocr import _parse_roi

    assert _parse_roi(None) is None
    assert _parse_roi("null") is None
    assert _parse_roi([0.1, 0.1, 0.2, 0.2]) == [[0.1, 0.1, 0.2, 0.2]]
    assert _parse_roi("[[0.1, 0.1, 0.2, 0.2], [0.6, 0.6, 0.3, 0.3]]") == \
        [[0.1, 0.1, 0.2, 0.2], [0.6, 0.6, 0.3, 0.3]]
    with pytest.raises(HTTPException):
        _parse_roi([0.1, 0.1])                            # 缺 w/h
    with pytest.raises(HTTPException):
        _parse_roi([[0.1, 0.1, 1.5, 0.2]])                # 越界


# ============================================================
# 8. 扫码 D zone / tracking_roi 消费面 (纯几何路径, 借 VSM 未绑定方法)
# ============================================================
def test_vsm_is_in_roi_多块():
    from backend.api.source import VideoSourceManager

    class _Stub:
        project_config = {
            'pipeline_config': {'tracking_roi': {'enabled': True, 'polygon': MULTI}},
        }
        _point_in_polygon = staticmethod(VideoSourceManager._point_in_polygon)

    stub = _Stub()
    in_b = {'x': 0.75, 'y': 0.75, 'w': 0.1, 'h': 0.1}
    in_gap = {'x': 0.45, 'y': 0.45, 'w': 0.1, 'h': 0.1}
    assert VideoSourceManager._is_in_roi(stub, in_b) is True
    assert VideoSourceManager._is_in_roi(stub, in_gap) is False

    # 单块老格式行为不变
    stub.project_config['pipeline_config']['tracking_roi']['polygon'] = BLOCK_A
    assert VideoSourceManager._is_in_roi(stub, {'x': 0.15, 'y': 0.15, 'w': 0.1, 'h': 0.1}) is True
    assert VideoSourceManager._is_in_roi(stub, in_b) is False


def test_steps_config_roi_多块解析():
    """_apply_steps_config: step.roi 双格式 → step_roi_polygons 存 canonical 多块."""
    from backend.api.source_project_config_apply import _apply_steps_config

    class _Host:
        pass

    h = _Host()
    for attr in ('step_conf_thresholds', 'step_box_size_limits', 'step_time_config',
                 'step_min_frames', 'step_display_names', 'step_strict_order',
                 'step_accept_once', 'step_detection_type', 'step_static_config',
                 'step_static_triggered', 'step_roi_polygons'):
        setattr(h, attr, {})

    _apply_steps_config(h, [
        {'label': 'a', 'enabled': True, 'roi': BLOCK_A},          # 单块
        {'label': 'b', 'enabled': True, 'roi': MULTI},            # 多块
        {'label': 'c', 'enabled': True, 'roi': [[0.1, 0.1]]},     # 非法
    ])
    assert len(h.step_roi_polygons['a']) == 1
    assert len(h.step_roi_polygons['b']) == 2
    assert 'c' not in h.step_roi_polygons

    # 与消费点闭环: 多块步骤 ROI 任一块命中
    from backend.api.source_roi import is_normalized_bbox_center_in_polygon
    assert is_normalized_bbox_center_in_polygon(
        {'x': 0.75, 'y': 0.75, 'w': 0.1, 'h': 0.1}, h.step_roi_polygons['b']) is True
    assert is_normalized_bbox_center_in_polygon(
        {'x': 0.45, 'y': 0.45, 'w': 0.1, 'h': 0.1}, h.step_roi_polygons['b']) is False
