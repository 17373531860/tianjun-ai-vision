# -*- coding: utf-8 -*-
"""人体朝向估计服务 (2026-09 朝向驻留监控 RFC 二期) — 关键点几何, 零训练。

定位: 蒸镀点检类巡检合规场景, 判断"操作员面向哪台仪表并驻留多久"。
仪表在空间上可能挨在一起 (用户否决纯 ROI 方案的核心理由), 必须有连续
角度的朝向; 且操作员每次不同, 方案必须与身份无关 —— 因此走 MediaPipe
Pose (Apache 2.0, 已内置) 关键点几何计算, 换人天然泛化, 无许可依赖。

管线 (两段式, 对抗远距离小人):
  人体检测框 (region_events 引擎已有) → 裁剪放大 → MediaPipe Pose (静态模式)
  → 双肩连线法向量 = 躯干朝向轴 → 鼻/耳可见性消除 180° 歧义
  → 图像平面朝向角 (deg) + 置信度

角度约定 (图像平面, 与仪表点判定同一坐标系):
  yaw_deg = atan2(fy, fx), fx 朝图像右为正, fy 朝图像下为正。
  即 0°=朝图像右, 90°=朝图像下 (朝向相机/画面近处), ±180°=朝图像左,
  -90°=朝图像上 (背对相机/画面深处)。
  斜俯视机位下图像纵轴 ≈ 地面纵深, 该近似配 ±30~40° 容差足够巡检判定;
  真实视频验证脚本见 tests/uat/orientation/。

线程模型: 与 source_mediapipe 同思路 —— 推理在调用方线程串行 (facing 规则
按节流间隔调用, 非每帧), 单例 Pose 实例加锁保护。
"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Optional

import numpy as np

_lock = threading.Lock()
_pose = None            # Pose 推理适配器单例 (legacy solutions 或 Tasks API)
_pose_failed = False    # 初始化失败后不再重试 (mediapipe 未装等)

# Tasks API 模型文件 (mediapipe >=1.0 移除 legacy solutions 后的唯一路径)。
# 与 hand_landmarker.task 同目录随安装包内置 (Apache 2.0, 离线可用)。
_TASK_MODEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "models", "pose_landmarker_full.task")

# MediaPipe Pose 关键点索引
_NOSE, _L_EAR, _R_EAR = 0, 7, 8
_L_SHOULDER, _R_SHOULDER = 11, 12
_L_HIP, _R_HIP = 23, 24

_CROP_MARGIN = 0.15     # 裁剪外扩比例 (给 pose 留上下文)
_MIN_CROP_PX = 48       # 裁出的人太小就不推 (关键点不可信)


def is_available() -> bool:
    try:
        import mediapipe  # noqa: F401
        return True
    except Exception:
        return False


class _LegacyPoseAdapter:
    """mediapipe <1.0 的 mp.solutions.pose 通路。"""

    def __init__(self):
        import mediapipe as mp
        self._pose = mp.solutions.pose.Pose(
            static_image_mode=True,
            model_complexity=1,
            min_detection_confidence=0.4,
        )

    def landmarks(self, rgb: np.ndarray):
        res = self._pose.process(rgb)
        lm = getattr(res, "pose_landmarks", None)
        return lm.landmark if lm is not None else None

    def close(self):
        self._pose.close()


class _TasksPoseAdapter:
    """mediapipe >=1.0 Tasks API PoseLandmarker (IMAGE 模式)。

    输出的 NormalizedLandmark 同样带 x/y/z/visibility, 索引口径与
    legacy 33 关键点一致, estimate_yaw 的几何计算两条通路共用。
    """

    def __init__(self, task_path: str):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_py
        from mediapipe.tasks.python import vision as mp_vis
        self._mp = mp
        opts = mp_vis.PoseLandmarkerOptions(
            base_options=mp_py.BaseOptions(model_asset_path=task_path),
            running_mode=mp_vis.RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=0.4,
        )
        self._landmarker = mp_vis.PoseLandmarker.create_from_options(opts)

    def landmarks(self, rgb: np.ndarray):
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        res = self._landmarker.detect(img)
        if not res.pose_landmarks:
            return None
        return res.pose_landmarks[0]

    def close(self):
        self._landmarker.close()


def _get_pose():
    """静态/IMAGE 模式 Pose 单例: 输入是不同人的裁剪图, 不能用跟踪模式。"""
    global _pose, _pose_failed
    if _pose is not None or _pose_failed:
        return _pose
    with _lock:
        if _pose is not None or _pose_failed:
            return _pose
        try:
            import mediapipe as mp
            if hasattr(mp, "solutions") and hasattr(mp.solutions, "pose"):
                _pose = _LegacyPoseAdapter()
                print("[Orientation] MediaPipe Pose 已加载 (legacy solutions, static)")
            elif os.path.exists(_TASK_MODEL):
                _pose = _TasksPoseAdapter(_TASK_MODEL)
                print(f"[Orientation] MediaPipe PoseLandmarker(Tasks) 已加载: {_TASK_MODEL}")
            else:
                raise RuntimeError(
                    f"mediapipe 无 legacy solutions 且缺 Tasks 模型文件: {_TASK_MODEL}")
        except Exception as e:
            print(f"[Orientation] MediaPipe Pose 不可用: {e}")
            _pose_failed = True
    return _pose


def release():
    """释放 Pose 实例 (通道停止/测试清理用)。"""
    global _pose, _pose_failed
    with _lock:
        if _pose is not None:
            try:
                _pose.close()
            except Exception:
                pass
        _pose = None
        _pose_failed = False


def estimate_yaw(frame_bgr: np.ndarray, box_xyxy) -> Optional[dict]:
    """对单个人框估计图像平面朝向角。

    box_xyxy: 像素坐标 (x1, y1, x2, y2)。
    返回 None (估计失败/人太小/关键点不可见) 或:
      {"yaw_deg": float, "conf": float, "head_yaw_deg": float|None,
       "facing_camera": bool}
    """
    pose = _get_pose()
    if pose is None or frame_bgr is None:
        return None
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in box_xyxy]
    bw, bh = x2 - x1, y2 - y1
    if bw < _MIN_CROP_PX or bh < _MIN_CROP_PX:
        return None
    mx, my = bw * _CROP_MARGIN, bh * _CROP_MARGIN
    cx1 = max(0, int(x1 - mx)); cy1 = max(0, int(y1 - my))
    cx2 = min(w, int(x2 + mx)); cy2 = min(h, int(y2 + my))
    crop = frame_bgr[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return None

    import cv2
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    with _lock:
        pts = pose.landmarks(rgb)
    if pts is None:
        return None

    ls, rs = pts[_L_SHOULDER], pts[_R_SHOULDER]
    if min(ls.visibility, rs.visibility) < 0.35:
        return None

    # ---- 躯干朝向: 双肩连线 (归一化裁剪坐标, z 为 mediapipe 相对深度,
    #      z 越小越靠近相机) 的法向量。歧义消除靠肩向量本身:
    #      面向相机时人的左肩在图像右 (镜像, sx>0), 背对时在图像左 (sx<0)。
    sx = ls.x - rs.x            # 肩向量 (右肩→左肩) 图像 x 分量
    sz = ls.z - rs.z            # 深度分量
    # 水平面 (x 朝图像右, z 朝画面深处) 内把肩向量顺时针转 90° 得躯干法向:
    #   面向相机 (sx>0, sz≈0) → (fx,fz)=(0,-1) 指向相机 ✓
    #   面向相机身体左转 (人左肩后退, sz>0) → fx>0 朝图像右 ✓
    fx = sz
    fz = -sx
    norm = math.hypot(fx, fz)
    if norm < 1e-6:
        return None
    fx, fz = fx / norm, fz / norm
    # 图像平面映射: 朝相机 (fz<0) => 朝图像下 (fy>0); 背对 => 朝图像上
    fy = -fz
    yaw_deg = math.degrees(math.atan2(fy, fx))

    # ---- 头部 yaw (仪表挨得近时比躯干更准): 鼻相对双耳中点的偏移
    head_yaw = None
    nose, le, re_ = pts[_NOSE], pts[_L_EAR], pts[_R_EAR]
    ear_vis = min(le.visibility, re_.visibility)
    if nose.visibility > 0.35 and ear_vis > 0.2:
        ear_mid_x = (le.x + re_.x) / 2.0
        ear_span = abs(le.x - re_.x) or 1e-6
        # 偏移比: 0=正对/正背, ±1≈侧向 90°
        ratio = max(-1.5, min(1.5, (nose.x - ear_mid_x) / ear_span))
        facing_cam = nose.z < min(le.z, re_.z)  # 鼻更靠近相机 => 面向相机
        base = 90.0 if facing_cam else -90.0    # 面向相机=朝图像下
        # 面向相机时鼻偏图像右 => 人朝向偏其自身左 => 图像角向 0° 方向偏
        head_yaw = base - ratio * 60.0 * (1.0 if facing_cam else -1.0)

    facing_camera = fy > 0
    conf = float(min(ls.visibility, rs.visibility))
    return {
        "yaw_deg": round(yaw_deg, 1),
        "conf": round(conf, 3),
        "head_yaw_deg": round(head_yaw, 1) if head_yaw is not None else None,
        "facing_camera": bool(facing_camera),
    }


def angle_diff_deg(a: float, b: float) -> float:
    """两角最小差 (0~180)。"""
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


class YawSmoother:
    """每轨迹朝向平滑器: 单位向量 EMA + 轨迹行进方向先验。

    - 关键点朝向到达时按置信度加权融合;
    - 位移速度超过阈值时行进方向是身体朝向的强先验 (人往哪走就朝哪);
    - 站定后保持最近航向, 慢速跟随新观测。
    """

    def __init__(self, alpha: float = 0.35, move_prior_px: float = 6.0):
        self._vx = 0.0
        self._vy = 0.0
        self._alpha = alpha
        self._move_prior_px = move_prior_px
        self._last_center = None
        self._last_ts = 0.0
        self.yaw_deg: Optional[float] = None

    def update(self, ts: float, center_xy=None,
               obs_yaw_deg: Optional[float] = None,
               obs_conf: float = 1.0) -> Optional[float]:
        # 轨迹先验: 帧间位移足够大时融合行进方向 (低权重, 只做托底)
        if center_xy is not None and self._last_center is not None:
            dx = center_xy[0] - self._last_center[0]
            dy = center_xy[1] - self._last_center[1]
            if math.hypot(dx, dy) >= self._move_prior_px:
                self._blend(math.degrees(math.atan2(dy, dx)), 0.15)
        if center_xy is not None:
            self._last_center = tuple(center_xy)
        self._last_ts = ts

        if obs_yaw_deg is not None:
            self._blend(obs_yaw_deg, self._alpha * max(0.2, min(1.0, obs_conf)))
        return self.yaw_deg

    def _blend(self, yaw_deg: float, weight: float):
        r = math.radians(yaw_deg)
        self._vx = (1 - weight) * self._vx + weight * math.cos(r)
        self._vy = (1 - weight) * self._vy + weight * math.sin(r)
        if math.hypot(self._vx, self._vy) > 1e-6:
            self.yaw_deg = round(math.degrees(math.atan2(self._vy, self._vx)), 1)


def facing_alignment_deg(person_center, person_yaw_deg: float,
                         instrument_point_px) -> float:
    """人的朝向 与 人→仪表点连线 的夹角 (0~180, 越小越对准)。"""
    bearing = math.degrees(math.atan2(
        instrument_point_px[1] - person_center[1],
        instrument_point_px[0] - person_center[0]))
    return angle_diff_deg(person_yaw_deg, bearing)
