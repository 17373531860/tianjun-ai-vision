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


def clip_bbox_normalized(x1: float, y1: float, x2: float, y2: float,
                          w: float, h: float) -> tuple:
    """像素坐标 (x1,y1,x2,y2) → 归一化 (x,y,w,h)，并 clip 到 [0, 1]。

    防御性边界处理，避免以下情况让框跑到画面外：
      - 模型输出 letterbox 边界外的像素值 (理论上 ultralytics 会 clip, 但兜底)
      - 浮点除法误差导致 x+w > 1.0 (例: 1.000000001)
      - 后续 Kalman 滤波/坐标变换累积误差

    Args:
        x1,y1,x2,y2: 像素坐标 (左上 / 右下)
        w,h: 帧的 宽度 / 高度 (像素)
    Returns:
        (nx, ny, nw, nh): 归一化坐标, 保证 0 <= nx, nx+nw <= 1, 0 <= ny, ny+nh <= 1
    """
    if w <= 0 or h <= 0:
        return 0.0, 0.0, 0.0, 0.0
    nx = max(0.0, min(1.0, float(x1) / w))
    ny = max(0.0, min(1.0, float(y1) / h))
    nx2 = max(0.0, min(1.0, float(x2) / w))
    ny2 = max(0.0, min(1.0, float(y2) / h))
    return nx, ny, max(0.0, nx2 - nx), max(0.0, ny2 - ny)


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


# ==================== 多块 ROI 归一化 (2026-09 全系统多块 ROI 改造) ====================
# 存储双格式约定（全部区域配置字段通用, 老配置零迁移）:
#   单块(旧): [[x,y], ...]            —— 元素是点
#   多块(新): [[[x,y],...], [[x,y],...]] —— 元素是多边形
# 判别依据: 首元素的首元素是数字 = 单块; 是 list/tuple = 多块。
# 判定语义: 中心点落在【任一块】内即命中; 推理 mask = 所有块并集。

def _is_valid_polygon(poly) -> bool:
    """单个多边形是否合法: ≥3 点、每点至少 2 个数值。"""
    if not isinstance(poly, (list, tuple)) or len(poly) < 3:
        return False
    for p in poly:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            return False
        try:
            float(p[0]); float(p[1])
        except (TypeError, ValueError):
            return False
    return True


def normalize_polygons(raw) -> List[list]:
    """把「单块/多块」双格式统一成多边形列表 [[[x,y],...], ...]。

    - None / 空 / 完全不合法 → []
    - 单块格式 [[x,y],...] → [该多边形]
    - 多块格式 [[[x,y],...], ...] → 逐块校验, 坏块剔除
    每个点统一转成 [float, float]。
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        return []
    first = raw[0]
    if isinstance(first, (list, tuple)) and first and isinstance(first[0], (list, tuple)):
        candidates = raw          # 多块格式
    else:
        candidates = [raw]        # 单块格式
    out = []
    for poly in candidates:
        if _is_valid_polygon(poly):
            out.append([[float(p[0]), float(p[1])] for p in poly])
    return out


def point_in_any_polygon(px: float, py: float, raw) -> bool:
    """点是否落在（单块/多块格式）任一多边形内。无有效多边形返回 False。"""
    for poly in normalize_polygons(raw):
        if point_in_polygon(px, py, poly):
            return True
    return False


def normalize_rects(raw) -> List[list]:
    """矩形区双格式归一: [x,y,w,h] 或 [[x,y,w,h],...] → [[x,y,w,h], ...]。

    仅保留 w>0 且 h>0 的合法块; None/非法 → []。
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        return []
    if isinstance(raw[0], (list, tuple)):
        candidates = raw
    else:
        candidates = [raw]
    out = []
    for r in candidates:
        if not isinstance(r, (list, tuple)) or len(r) < 4:
            continue
        try:
            x, y, w, h = (float(r[0]), float(r[1]), float(r[2]), float(r[3]))
        except (TypeError, ValueError):
            continue
        if w > 0 and h > 0:
            out.append([x, y, w, h])
    return out


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
