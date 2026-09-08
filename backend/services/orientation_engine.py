"""人体朝向估计服务 (facing_dwell 朝向驻留规则的推理侧, 2026-09 蒸镀点检批次).

职责: 给检测框裁剪 → 姿态关键点 (YOLO11-pose, COCO-17) → 几何计算身体朝向角,
供 VSM 执行层注入到检测框的 'facing' 字段; 区域事件引擎 (纯逻辑层) 只做角度比较。

为什么关键点几何而不是朝向分类模型 (RFC 朝向驻留监控_蒸镀点检场景):
  - 公开朝向模型权重 (6DRepNet/WHENet/MEBOW) 全部被非商用训练数据卡死;
  - 关键点学的是人体几何, 换人天然泛化 (客户"每次操作员不同"的硬约束);
  - YOLO11-pose 走 Ultralytics Enterprise 授权, 商用干净、可离线内置。

朝向角约定 (全链路统一, 引擎侧 bearing 同款):
  归一化图像坐标系 (x/W, y/H, y 向下) 中的方向角, phi = atan2(dx_n, dy_n):
     0° = 朝画面正下方 (通常 = 朝相机);  ±180° = 朝画面正上方 (背对相机);
    +90° = 朝画面右;                      -90° = 朝画面左。
  俯拍/高位机位下画面方向 ≈ 地面方位, "人朝向向量与人→仪表点连线的夹角"
  在此空间内直接可比 (两者同为归一化空间角, 宽高畸变一致抵消)。

几何算法 (facing_from_keypoints, 纯函数可单测):
  - 双肩连线给朝向轴; 左右肩身份消除 180° 歧义 (面向相机时人左肩在画面右侧);
  - 肩宽/躯干高比值的透视缩短给偏转幅度 (正/背面比值最大, 全侧面最小);
  - 头部关键点 (鼻/眼/耳) 相对肩中点的水平偏移给偏转方向 (朝画面左还是右);
  - 近侧面时肩序信号弱, 回退脸部关键点可见性判前/背半球。

模型文件: backend/data/models/yolo11n-pose.pt (随安装包分发, 同 hand_landmarker.task
落位), 环境变量 TIANJUN_POSE_MODEL 可覆盖。缺文件/缺依赖 → is_available()=False,
facing_dwell 规则的朝向条件永不满足 (监控项静默不告警), 不影响其他规则与主流程。

线程模型: annotate_facing 在推理线程内被调用 (与检测同线程, 无并发); 模型加载
一次性持锁, 失败缓存错误不重试 (避免每帧重复报错刷日志)。
"""
from __future__ import annotations

import math
import os
import threading
from typing import List, Optional

# COCO-17 关键点索引
_NOSE, _LEYE, _REYE, _LEAR, _REAR = 0, 1, 2, 3, 4
_LSHO, _RSHO = 5, 6
_LHIP, _RHIP = 11, 12

_KP_CONF = 0.30          # 关键点可信下限
_R0 = 0.75               # 正面肩宽/躯干高标定比 (人体测量学 ~0.78, 留裕量)
_HEAD_DEAD_ZONE = 0.06   # 头部偏移死区 (占躯干高比例): 小于此视为正对/正背
_MAX_BOXES = 4           # 单帧最多跑姿态的主体框数 (按面积取大)
_CROP_PAD = 0.15         # 裁剪外扩比例 (肩/头贴框边时留余量)

_lock = threading.RLock()
_backend = None          # 已加载的 pose backend (None=未加载)
_load_error: Optional[str] = None  # 加载失败原因 (缓存, 不重试)
_test_backend = None     # 测试注入 (set_backend_for_tests)


def _weights_path() -> str:
    env = os.environ.get('TIANJUN_POSE_MODEL')
    if env:
        return env
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    return str(root / 'backend' / 'data' / 'models' / 'yolo11n-pose.pt')


class _YoloPoseBackend:
    """YOLO11-pose 关键点推理 (Ultralytics Enterprise 授权)。"""

    def __init__(self, weights: str):
        from ultralytics import YOLO
        self._model = YOLO(weights)

    def infer_keypoints(self, crop_bgr) -> Optional[list]:
        """裁剪图上跑 pose, 返回最高置信人的 COCO-17 关键点 [(x,y,conf)×17]。

        imgsz=256: 裁剪图里人占主体, 小输入足够肩/髋/头级别的几何, 热路径省时。
        """
        results = self._model.predict(crop_bgr, verbose=False, conf=0.25, imgsz=256)
        if not results:
            return None
        r = results[0]
        kp = getattr(r, 'keypoints', None)
        boxes = getattr(r, 'boxes', None)
        if kp is None or kp.data is None or len(kp.data) == 0:
            return None
        idx = 0
        try:
            if boxes is not None and boxes.conf is not None and len(boxes.conf) > 1:
                idx = int(boxes.conf.argmax())
        except Exception:
            idx = 0
        data = kp.data[idx]
        try:
            data = data.cpu().numpy()
        except Exception:
            pass
        return [(float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 1.0)
                for p in data]


def _get_backend():
    global _backend, _load_error
    if _test_backend is not None:
        return _test_backend
    with _lock:
        if _backend is not None:
            return _backend
        if _load_error is not None:
            return None
        path = _weights_path()
        if not os.path.isfile(path):
            _load_error = f"姿态模型文件不存在: {path}"
            print(f"[Orientation] {_load_error} (facing_dwell 朝向条件将不满足)")
            return None
        try:
            _backend = _YoloPoseBackend(path)
            print(f"[Orientation] YOLO11-pose 已加载: {path}")
            return _backend
        except Exception as e:
            _load_error = f"姿态模型加载失败: {e}"
            print(f"[Orientation] {_load_error}")
            return None


def is_available() -> bool:
    """朝向估计是否可用 (不触发模型加载, 只查文件与缓存错误)。"""
    if _test_backend is not None:
        return True
    if _backend is not None:
        return True
    return _load_error is None and os.path.isfile(_weights_path())


def status() -> dict:
    return {
        'available': is_available(),
        'loaded': _backend is not None or _test_backend is not None,
        'weights': _weights_path(),
        'error': _load_error,
    }


def set_backend_for_tests(backend) -> None:
    """单测注入假 pose backend (None=还原)。"""
    global _test_backend, _load_error
    _test_backend = backend
    if backend is not None:
        _load_error = None


# ==================== 纯几何: 关键点 → 朝向角 ====================

def facing_from_keypoints(kpts: List[tuple], box_h_px: float,
                          frame_w: int, frame_h: int) -> Optional[float]:
    """COCO-17 关键点 (全帧像素坐标) → 归一化空间朝向角 (度)。

    kpts: [(x_px, y_px, conf)×17]; box_h_px: 人框像素高 (髋不可见时的躯干高兜底)。
    返回 None = 关键点不足以判定 (双肩任一不可见)。
    """
    if not kpts or len(kpts) < 13:
        return None
    lsho, rsho = kpts[_LSHO], kpts[_RSHO]
    if lsho[2] < _KP_CONF or rsho[2] < _KP_CONF:
        return None

    sho_mid_x = (lsho[0] + rsho[0]) / 2.0
    sho_mid_y = (lsho[1] + rsho[1]) / 2.0
    shoulder_w = abs(rsho[0] - lsho[0])

    # 躯干高 (尺度基准): 肩中点到髋中点; 髋不可见退人框高的 0.28 (经验比例)
    lhip, rhip = kpts[_LHIP], kpts[_RHIP]
    if lhip[2] >= _KP_CONF and rhip[2] >= _KP_CONF:
        torso_h = abs((lhip[1] + rhip[1]) / 2.0 - sho_mid_y)
    else:
        torso_h = box_h_px * 0.28
    if torso_h <= 1e-6:
        return None

    # 偏转幅度: 肩宽透视缩短。正/背面比值≈_R0, 全侧面≈0 → acos 映射 [0°, 90°]
    ratio = shoulder_w / torso_h
    c = max(0.0, min(1.0, ratio / _R0))
    side_mag = math.degrees(math.acos(c))

    # 前/背半球: 肩序为主 (面向相机时人左肩在画面右侧 → x_lsho > x_rsho);
    # 近侧面 (肩宽 < 0.3×躯干高) 肩序信号弱, 回退脸部关键点可见性
    face_conf = max(kpts[_NOSE][2], kpts[_LEYE][2], kpts[_REYE][2])
    if shoulder_w >= 0.3 * torso_h:
        front = lsho[0] > rsho[0]
    else:
        front = face_conf >= 0.35

    # 偏转方向 (画面左/右): 头部相对肩中点的水平偏移 (人偏转时头先探出去)
    head_pts = [p for p in (kpts[_NOSE], kpts[_LEYE], kpts[_REYE],
                            kpts[_LEAR], kpts[_REAR]) if p[2] >= _KP_CONF]
    if head_pts:
        head_x = sum(p[0] for p in head_pts) / len(head_pts)
        off = head_x - sho_mid_x
        dead = _HEAD_DEAD_ZONE * torso_h
        side_sign = 1 if off > dead else (-1 if off < -dead else 0)
    else:
        side_sign = 0

    # 像素空间朝向角: 0=朝画面下(朝相机), ±180=朝画面上(背对), +90=右, -90=左
    if front:
        phi_px = side_sign * side_mag
    else:
        phi_px = side_sign * (180.0 - side_mag) if side_sign else 180.0

    # 像素空间角 → 归一化空间角 (与引擎侧 bearing 的坐标系对齐, 宽高畸变一致)
    rad = math.radians(phi_px)
    dx_n = math.sin(rad) / max(1, frame_w)
    dy_n = math.cos(rad) / max(1, frame_h)
    return round(math.degrees(math.atan2(dx_n, dy_n)), 1)


# ==================== 注入入口 (VSM 执行层调用) ====================

def annotate_facing(frame_bgr, boxes: List[dict]) -> int:
    """给检测框字典原地注入 'facing' (归一化空间朝向角, 度)。

    boxes: 归一化 {x,y,w,h,...} 字典列表 (原地改, 引擎读同一批对象)。
    返回成功注入的数量。任何失败静默跳过 (朝向缺失 = 该框条件不满足)。
    """
    backend = _get_backend()
    if backend is None or frame_bgr is None or not boxes:
        return 0
    fh, fw = frame_bgr.shape[:2]
    targets = sorted(boxes, key=lambda b: b.get('w', 0) * b.get('h', 0),
                     reverse=True)[:_MAX_BOXES]
    done = 0
    for b in targets:
        try:
            x1 = int(max(0.0, b['x'] - b['w'] * _CROP_PAD) * fw)
            y1 = int(max(0.0, b['y'] - b['h'] * _CROP_PAD) * fh)
            x2 = int(min(1.0, b['x'] + b['w'] * (1 + _CROP_PAD)) * fw)
            y2 = int(min(1.0, b['y'] + b['h'] * (1 + _CROP_PAD)) * fh)
            if x2 - x1 < 16 or y2 - y1 < 16:
                continue
            kpts = backend.infer_keypoints(frame_bgr[y1:y2, x1:x2])
            if not kpts:
                continue
            # 裁剪坐标 → 全帧像素坐标
            kpts = [(x + x1, y + y1, c) for x, y, c in kpts]
            phi = facing_from_keypoints(kpts, b['h'] * fh, fw, fh)
            if phi is not None:
                b['facing'] = phi
                done += 1
        except Exception:
            continue
    return done
