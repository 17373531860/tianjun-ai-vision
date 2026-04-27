"""backend/api/source_geometry.py 的单元测试。

覆盖 bbox_iou、bbox_center_dist、point_in_polygon、get_chinese_font 四个纯函数，
重点验证：
  - 数值正确性（含完全重叠/不重叠/部分重叠等边界）
  - 退化输入安全（零面积、负坐标、空多边形）
  - get_chinese_font 的 lru_cache 命中行为（同 size 多次调用返回同一对象）
"""

import os
import sys
import unittest

os.environ.setdefault("BACKEND_SKIP_INIT", "1")
sys.path.insert(0, os.path.dirname(__file__))

from backend.api.source_geometry import (
    bbox_iou,
    bbox_center_dist,
    point_in_polygon,
    get_chinese_font,
)


def _box(x: float, y: float, w: float, h: float) -> dict:
    return {'x': x, 'y': y, 'w': w, 'h': h}


class TestBBoxIoU(unittest.TestCase):
    def test_identical(self):
        a = _box(0.1, 0.1, 0.2, 0.2)
        self.assertAlmostEqual(bbox_iou(a, a), 1.0)

    def test_disjoint(self):
        self.assertEqual(bbox_iou(_box(0, 0, 0.1, 0.1), _box(0.5, 0.5, 0.1, 0.1)), 0.0)

    def test_partial(self):
        # A=[0,0,1,1] B=[0.5,0.5,1,1]，交=[0.5,0.5]^2=0.25, 并=2*1-0.25=1.75
        iou = bbox_iou(_box(0, 0, 1, 1), _box(0.5, 0.5, 1, 1))
        self.assertAlmostEqual(iou, 0.25 / 1.75)

    def test_zero_area(self):
        # 空 box 兜底为 0，不应抛除零异常
        self.assertEqual(bbox_iou(_box(0, 0, 0, 0), _box(0, 0, 0, 0)), 0.0)

    def test_contained(self):
        # B 完全在 A 内，IoU = area_b / area_a
        a, b = _box(0, 0, 1, 1), _box(0.25, 0.25, 0.5, 0.5)
        self.assertAlmostEqual(bbox_iou(a, b), 0.25)


class TestBBoxCenterDist(unittest.TestCase):
    def test_same_center(self):
        a = _box(0, 0, 0.4, 0.4)
        b = _box(0.1, 0.1, 0.2, 0.2)
        # 两者中心都是 (0.2, 0.2)
        self.assertAlmostEqual(bbox_center_dist(a, b), 0.0)

    def test_known_distance(self):
        # 中心 (0,0) vs (3,4) -> 5
        a = {'x': -0.5, 'y': -0.5, 'w': 1.0, 'h': 1.0}
        b = {'x': 2.5, 'y': 3.5, 'w': 1.0, 'h': 1.0}
        self.assertAlmostEqual(bbox_center_dist(a, b), 5.0)


class TestPointInPolygon(unittest.TestCase):
    SQUARE = [[0, 0], [1, 0], [1, 1], [0, 1]]

    def test_inside(self):
        self.assertTrue(point_in_polygon(0.5, 0.5, self.SQUARE))

    def test_outside(self):
        self.assertFalse(point_in_polygon(1.5, 0.5, self.SQUARE))
        self.assertFalse(point_in_polygon(-0.1, 0.5, self.SQUARE))

    def test_concave(self):
        # L 形多边形：右下凹缺口
        L = [[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]]
        self.assertTrue(point_in_polygon(0.5, 1.5, L))   # 凹口左上臂
        self.assertFalse(point_in_polygon(1.5, 1.5, L))  # 凹口右上方（缺角）

    def test_degenerate(self):
        # 顶点不足 3 个直接判 false，不抛异常
        self.assertFalse(point_in_polygon(0.5, 0.5, []))
        self.assertFalse(point_in_polygon(0.5, 0.5, [[0, 0]]))
        self.assertFalse(point_in_polygon(0.5, 0.5, [[0, 0], [1, 1]]))


class TestChineseFontCache(unittest.TestCase):
    def test_returns_font_object(self):
        f = get_chinese_font(20)
        self.assertIsNotNone(f)
        # PIL Font 都有 getbbox / getmask 接口，不强校验类型
        self.assertTrue(hasattr(f, 'getbbox') or hasattr(f, 'getsize'))

    def test_lru_cache_same_object(self):
        a = get_chinese_font(18)
        b = get_chinese_font(18)
        self.assertIs(a, b, "lru_cache 应让同 size 复用同一对象")

    def test_lru_cache_different_size(self):
        a = get_chinese_font(18)
        b = get_chinese_font(24)
        # 不同 size 应得到不同实例（即便最终 fallback 也是不同对象）
        self.assertIsNot(a, b)


if __name__ == '__main__':
    unittest.main(verbosity=2)
