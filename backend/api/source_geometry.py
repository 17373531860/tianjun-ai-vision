"""几何与字体工具——从 source.py 抽出的纯函数集合。

这些函数原先是 VideoSourceManager 上的 @staticmethod，搬到独立模块后：
  1. 不再借用 VideoSourceManager 命名空间，便于其他模块复用
  2. 字体加载加上 lru_cache，避免每帧绘制时反复尝试 7 个不存在的路径
  3. 可独立单元测试（test_source_geometry.py）

source.py 内通过 `from backend.api.source_geometry import ...` 复用，并保留同名
staticmethod 包装以兼容历史 `self._bbox_iou(a, b)` 这类调用——这些 wrapper 只
是转发，不做新增逻辑。
"""

from __future__ import annotations

from functools import lru_cache
from typing import List, Sequence

from PIL import ImageFont


def bbox_iou(a: dict, b: dict) -> float:
    """两个 {x,y,w,h} 框的 IoU，归一化坐标。"""
    ax1, ay1, ax2, ay2 = a['x'], a['y'], a['x'] + a['w'], a['y'] + a['h']
    bx1, by1, bx2, by2 = b['x'], b['y'], b['x'] + b['w'], b['y'] + b['h']
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = a['w'] * a['h']
    area_b = b['w'] * b['h']
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def bbox_center_dist(a: dict, b: dict) -> float:
    """两个 bbox 中心点的欧氏距离，归一化坐标。"""
    acx, acy = a['x'] + a['w'] / 2, a['y'] + a['h'] / 2
    bcx, bcy = b['x'] + b['w'] / 2, b['y'] + b['h'] / 2
    return ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5


def point_in_polygon(px: float, py: float, polygon: Sequence[Sequence[float]]) -> bool:
    """射线法判断点是否在多边形内。polygon = [[x,y], ...]，至少 3 个顶点。"""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# 可用字体路径（按优先级排）
_FONT_CANDIDATES: List[str] = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "simhei.ttf",
]


@lru_cache(maxsize=16)
def get_chinese_font(size: int = 20):
    """获取中文字体（带缓存）。

    原 VideoSourceManager._get_chinese_font 每次调用都按顺序探 7 个路径，多数环境下前
    6 个都 IOError，每帧绘制几个 box 就要白跑十几次磁盘检查。这里加 lru_cache(16)
    覆盖常见 size，把成本降到一次。
    """
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()
