# -*- coding: utf-8 -*-
"""人体朝向估计服务 (2026-09 朝向驻留监控 RFC 二期; 2026-09-10 多后端统一).

定位: 蒸镀点检类巡检合规场景, 判断"操作员面向哪台仪表并驻留多久"。
仪表在空间上可能挨在一起 (用户否决纯 ROI 方案的核心理由), 必须有连续
角度的朝向; 且操作员每次不同, 方案必须与身份无关 —— 识别几何而非识别人。

三层后端 ("全部加进去", 按授权/文件在场情况自动降级, 缺任何一层不拖垮主程序):

  [关键点层] 身体朝向主信号, 二选一:
    - yolo11_pose : YOLO11-pose COCO-17 关键点 (Ultralytics Enterprise 授权,
                    RFC 首选)。权重 backend/data/models/yolo11n-pose.pt 随安装
                    包分发 (与 hand_landmarker.task 同落位), TIANJUN_POSE_MODEL
                    可覆盖。两段式裁剪下对远距离小人更稳。
    - mediapipe   : MediaPipe Pose 33 关键点 (Apache 2.0, legacy solutions 或
                    Tasks API PoseLandmarker), 自带 z 相对深度, 双肩深度差直接
                    给朝向。模型 pose_landmarker_full.task 已随包。
    选择顺序: TIANJUN_ORIENTATION_BACKEND 显式指定 ('yolo11'|'mediapipe')
              > yolo11 权重文件在场 > mediapipe。

  [头姿精化层] 可选, 商用授权权重到位后放文件即热生效:
    - headpose_onnx : 6DRepNet / 6DRepNet360 / WHENet / HopeNet 系头部姿态
                      ONNX 模型 (TIANJUN_HEADPOSE_MODEL 或
                      backend/data/models/headpose.onnx)。从关键点定位头部
                      裁剪 224×224 回归头部 yaw, 精化 head_yaw_deg ——
                      仪表挨得近时"头转身不转"更常见, 头部朝向比躯干更准。
                      缺文件/缺 onnxruntime → 静默跳过, 关键点几何 head_yaw
                      兜底。输出解码兼容四种导出形态 (见 _decode_headpose)。

  [平滑层] YawSmoother: 单位向量 EMA + 轨迹行进方向先验 (与后端无关)。

角度约定 (图像平面, 全链路统一, 引擎侧 bearing 同口径):
  yaw_deg = atan2(fy, fx), fx 朝图像右为正, fy 朝图像下为正。
  即 0°=朝图像右, 90°=朝图像下 (朝向相机/画面近处), ±180°=朝图像左,
  -90°=朝图像上 (背对相机/画面深处)。
  斜俯视机位下图像纵轴 ≈ 地面纵深, 该近似配 ±30~40° 容差足够巡检判定;
  真实视频验证脚本见 tests/uat/facing_dwell_video_validation.py。

线程模型: 推理在调用方线程串行 (facing 规则按节流间隔调用, 非每帧),
后端单例加锁保护; 加载失败缓存错误不重试 (避免每帧刷日志)。
"""
from __future__ import annotations

import math
import os
import threading
from typing import List, Optional

import numpy as np

_lock = threading.Lock()
_kp_backend = None          # 关键点后端单例 (None=未加载)
_kp_error: Optional[str] = None   # 关键点后端加载失败原因 (缓存不重试)
_test_backend = None        # 测试注入 (set_backend_for_tests)

_headpose = None            # 头姿 ONNX 会话单例
_headpose_error: Optional[str] = None

# MediaPipe Pose 关键点索引 (33 点)
_MP_NOSE, _MP_LEAR, _MP_REAR = 0, 7, 8
_MP_LSHO, _MP_RSHO = 11, 12

# COCO-17 关键点索引 (YOLO pose)
_CO_NOSE, _CO_LEYE, _CO_REYE, _CO_LEAR, _CO_REAR = 0, 1, 2, 3, 4
_CO_LSHO, _CO_RSHO = 5, 6
_CO_LHIP, _CO_RHIP = 11, 12

_CROP_MARGIN = 0.15     # 裁剪外扩比例 (给 pose 留上下文)
_MIN_CROP_PX = 48       # 裁出的人太小就不推 (关键点不可信)
_KP_CONF = 0.30         # COCO 关键点可信下限
_R0 = 0.75              # 正面肩宽/躯干高标定比 (人体测量学 ~0.78, 留裕量)
_HEAD_DEAD_ZONE = 0.06  # 头部偏移死区 (占躯干高比例)

_MODELS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "models")
_TASK_MODEL = os.path.join(_MODELS_DIR, "pose_landmarker_full.task")


def _repo_binding(capability: str):
    """模型仓库能力绑定路径 (2026-09 内置能力模型入仓); 任何异常回 None。"""
    try:
        from backend.services.builtin_models import resolve_capability_weight
        return resolve_capability_weight(capability)
    except Exception:
        return None


def _yolo_weights_path() -> str:
    # 解析顺序: env 显式 (开发调试) > 模型仓库绑定 (用户可换) > 出厂默认
    return (os.environ.get("TIANJUN_POSE_MODEL")
            or _repo_binding("pose")
            or os.path.join(_MODELS_DIR, "yolo11n-pose.pt"))


def _headpose_path() -> str:
    return (os.environ.get("TIANJUN_HEADPOSE_MODEL")
            or _repo_binding("headpose")
            or os.path.join(_MODELS_DIR, "headpose.onnx"))


# ---- 头姿全角度开关 (SystemConfig KV, 现场按绑定权重的种类配置) ----
# 正脸模型 (6DRepNet/HopeNet 常规导出, 出厂默认假定): 对背对相机的人只有
# 后脑勺, 输出无意义 → 背面跳过精化 (六和蒸镀 2026-09-16 现场修复)。
# 全角度模型 (6DRepNet360/WHENet full-range): 背面输出有效 → 现场把本开关
# 置 true 恢复背面精化。粒度=全局: 头姿权重经模型仓库能力绑定全局一颗。
HEADPOSE_FULL_RANGE_KEY = "orientation.headpose_full_range"
_FULL_RANGE_TTL = 10.0
_full_range_cache: Optional[bool] = None
_full_range_at: float = 0.0


def headpose_full_range() -> bool:
    """当前是否信任头姿模型的背面输出 (KV 带 TTL 缓存, 异常回 False)。"""
    global _full_range_cache, _full_range_at
    import time
    now = time.time()
    if _full_range_cache is not None and now - _full_range_at < _FULL_RANGE_TTL:
        return _full_range_cache
    val = False
    try:
        from backend.db.database import SessionLocal
        from backend.models.models import SystemConfig
        db = SessionLocal()
        try:
            row = db.query(SystemConfig).filter(
                SystemConfig.key == HEADPOSE_FULL_RANGE_KEY).first()
            val = (row is not None
                   and (row.value or "").strip().lower() in ("1", "true", "yes"))
        finally:
            db.close()
    except Exception:
        val = bool(_full_range_cache)  # DB 不可用时沿用上次值 (默认 False)
    _full_range_cache = val
    _full_range_at = now
    return val


def set_headpose_full_range_cache(value: Optional[bool]) -> None:
    """PUT 配置后即时刷新缓存 (None=失效待重读; 推理线程下一帧生效)。"""
    global _full_range_cache, _full_range_at
    import time
    _full_range_cache = value
    _full_range_at = time.time() if value is not None else 0.0


def _mediapipe_importable() -> bool:
    try:
        import mediapipe  # noqa: F401
        return True
    except Exception:
        return False


def _preferred_backend() -> str:
    """当前环境应选的关键点后端名 (不触发加载)。"""
    explicit = (os.environ.get("TIANJUN_ORIENTATION_BACKEND") or "").strip().lower()
    if explicit in ("yolo11", "yolo11_pose", "yolo"):
        return "yolo11_pose"
    if explicit in ("mediapipe", "mediapipe_pose", "mp"):
        return "mediapipe_pose"
    if os.path.isfile(_yolo_weights_path()):
        return "yolo11_pose"
    return "mediapipe_pose"


def is_available() -> bool:
    """朝向估计是否可能可用 (不触发模型加载)。"""
    if _test_backend is not None or _kp_backend is not None:
        return True
    if _kp_error is not None:
        return False
    return os.path.isfile(_yolo_weights_path()) or _mediapipe_importable()


# ==================== 关键点后端适配器 ====================

class _LegacyPoseAdapter:
    """mediapipe <1.0 的 mp.solutions.pose 通路。"""

    kind = "mediapipe_pose"

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
    legacy 33 关键点一致, 几何计算两条通路共用。
    """

    kind = "mediapipe_pose"

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


class _YoloPoseAdapter:
    """YOLO11-pose COCO-17 关键点 (Ultralytics Enterprise 授权)。"""

    kind = "yolo11_pose"

    def __init__(self, weights: str):
        from ultralytics import YOLO
        self._model = YOLO(weights)

    def infer_keypoints(self, crop_bgr) -> Optional[list]:
        """裁剪图上跑 pose, 返回最高置信人的 COCO-17 关键点 [(x,y,conf)×17]。

        imgsz=256: 裁剪图里人占主体, 小输入足够肩/髋/头级别的几何, 热路径省时。
        """
        results = self._model.predict(crop_bgr, verbose=False, conf=0.25,
                                      imgsz=256)
        if not results:
            return None
        r = results[0]
        kp = getattr(r, "keypoints", None)
        boxes = getattr(r, "boxes", None)
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

    def close(self):
        self._model = None


def _get_backend():
    """关键点后端单例 (静态/IMAGE 模式: 输入是不同人的裁剪图, 不能用跟踪模式)。"""
    global _kp_backend, _kp_error
    if _test_backend is not None:
        return _test_backend
    if _kp_backend is not None or _kp_error is not None:
        return _kp_backend
    with _lock:
        if _kp_backend is not None or _kp_error is not None:
            return _kp_backend
        pref = _preferred_backend()
        try:
            if pref == "yolo11_pose":
                path = _yolo_weights_path()
                if not os.path.isfile(path):
                    raise RuntimeError(f"YOLO pose 权重不存在: {path}")
                _kp_backend = _YoloPoseAdapter(path)
                print(f"[Orientation] YOLO11-pose 已加载: {path}")
                return _kp_backend
        except Exception as e:
            # 显式指定 yolo 失败 → 不静默换后端 (角度口径差异需用户知情),
            # 但自动选择路径下回落 mediapipe
            if (os.environ.get("TIANJUN_ORIENTATION_BACKEND") or "").strip():
                _kp_error = f"YOLO11-pose 加载失败: {e}"
                print(f"[Orientation] {_kp_error}")
                return None
            print(f"[Orientation] YOLO11-pose 加载失败, 回落 MediaPipe: {e}")
        try:
            import mediapipe as mp
            if hasattr(mp, "solutions") and hasattr(mp.solutions, "pose"):
                _kp_backend = _LegacyPoseAdapter()
                print("[Orientation] MediaPipe Pose 已加载 (legacy solutions, static)")
            elif os.path.exists(_TASK_MODEL):
                _kp_backend = _TasksPoseAdapter(_TASK_MODEL)
                print(f"[Orientation] MediaPipe PoseLandmarker(Tasks) 已加载: {_TASK_MODEL}")
            else:
                raise RuntimeError(
                    f"mediapipe 无 legacy solutions 且缺 Tasks 模型文件: {_TASK_MODEL}")
        except Exception as e:
            _kp_error = f"朝向关键点后端不可用: {e}"
            print(f"[Orientation] {_kp_error}")
    return _kp_backend


def set_backend_for_tests(backend) -> None:
    """单测注入假关键点后端 (None=还原)。

    注入对象需带 kind 属性 ('yolo11_pose' 走 infer_keypoints(crop_bgr),
    'mediapipe_pose' 走 landmarks(rgb))。
    """
    global _test_backend, _kp_error
    _test_backend = backend
    if backend is not None:
        _kp_error = None


def release():
    """释放全部后端实例 (通道停止/测试清理用)。"""
    global _kp_backend, _kp_error, _headpose, _headpose_error
    with _lock:
        if _kp_backend is not None:
            try:
                _kp_backend.close()
            except Exception:
                pass
        _kp_backend = None
        _kp_error = None
        _headpose = None
        _headpose_error = None
    set_headpose_full_range_cache(None)


def engine_status() -> dict:
    """状态探针 (模型仓库能力目录/试一试抽屉/运维消费)。"""
    yolo_path = _yolo_weights_path()
    hp_path = _headpose_path()
    active = None
    if _test_backend is not None:
        active = getattr(_test_backend, "kind", "test")
    elif _kp_backend is not None:
        active = _kp_backend.kind
    return {
        "available": is_available(),
        "backend": active or _preferred_backend(),
        "loaded": _kp_backend is not None or _test_backend is not None,
        "error": _kp_error,
        "backends": {
            "yolo11_pose": {"weights": yolo_path,
                            "present": os.path.isfile(yolo_path)},
            "mediapipe_pose": {"importable": _mediapipe_importable(),
                               "task_model": os.path.isfile(_TASK_MODEL)},
            "headpose_onnx": {"model": hp_path,
                              "present": os.path.isfile(hp_path),
                              "loaded": _headpose is not None,
                              "error": _headpose_error,
                              "full_range": headpose_full_range()},
        },
    }


# ==================== 头姿精化层 (ONNX, 可选) ====================

def _get_headpose():
    global _headpose, _headpose_error
    if _headpose is not None or _headpose_error is not None:
        return _headpose
    path = _headpose_path()
    if not os.path.isfile(path):
        return None  # 未配置即未启用, 不算错误
    with _lock:
        if _headpose is not None or _headpose_error is not None:
            return _headpose
        try:
            import onnxruntime as ort
            _headpose = ort.InferenceSession(
                path, providers=["CPUExecutionProvider"])
            print(f"[Orientation] 头姿 ONNX 已加载: {path}")
        except Exception as e:
            _headpose_error = f"头姿 ONNX 加载失败: {e}"
            print(f"[Orientation] {_headpose_error}")
    return _headpose


def _decode_headpose(outputs: list) -> Optional[float]:
    """头姿模型输出 → 模型口径 yaw (度, 0=正对相机, 正=转向本人左侧)。

    兼容四种常见导出形态:
      1. 三输出 binned 分类 (HopeNet/WHENet 系): 每输出 (1,N) logits,
         softmax 期望 × 3° − 1.5N 得角度; 第一个输出按惯例是 yaw。
      2. 单输出 (1,3,3)/(3,3) 旋转矩阵 (6DRepNet 官方导出形态):
         yaw = atan2(-R[2,0], sqrt(R[0,0]²+R[1,0]²))。
      3. 单输出 (1,6) 6D 旋转表示 (Zhou et al.): Gram-Schmidt → R → 同上。
      4. 单输出 (1,3) 欧拉角: 假定 [yaw,pitch,roll]。单位默认按度
         (PINTO zoo 6DRepNet360 导出实测输出度), 权重方输出弧度时设
         TIANJUN_HEADPOSE_UNITS=radians。
    """
    def _euler_yaw_from_R(R: np.ndarray) -> float:
        sy = math.sqrt(float(R[0, 0]) ** 2 + float(R[1, 0]) ** 2)
        return math.degrees(math.atan2(-float(R[2, 0]), sy))

    try:
        if len(outputs) >= 3:  # binned 分类
            logits = np.asarray(outputs[0], dtype=np.float64).reshape(-1)
            e = np.exp(logits - logits.max())
            p = e / e.sum()
            n = p.shape[0]
            return float(3.0 * np.dot(p, np.arange(n)) - 1.5 * n)
        out = np.asarray(outputs[0], dtype=np.float64)
        flat = out.reshape(-1)
        if flat.size == 9:
            return _euler_yaw_from_R(out.reshape(3, 3))
        if flat.size == 6:
            a1, a2 = flat[:3], flat[3:]
            b1 = a1 / (np.linalg.norm(a1) or 1e-9)
            a2p = a2 - np.dot(b1, a2) * b1
            b2 = a2p / (np.linalg.norm(a2p) or 1e-9)
            b3 = np.cross(b1, b2)
            R = np.stack([b1, b2, b3], axis=1)
            return _euler_yaw_from_R(R)
        if flat.size == 3:
            yaw = float(flat[0])
            if (os.environ.get("TIANJUN_HEADPOSE_UNITS") or "").lower().startswith("rad"):
                return math.degrees(yaw)
            return yaw
    except Exception:
        pass
    return None


def _norm_deg(a: float) -> float:
    """归一到 (-180, 180]。"""
    a = a % 360.0
    if a > 180.0:
        a -= 360.0
    return a


def _headpose_refine(crop_bgr: np.ndarray, head_box_px) -> Optional[float]:
    """头部裁剪跑头姿 ONNX, 返回图像平面 head_yaw (度) 或 None。

    模型口径 (0=正对相机, 正=转向本人左侧≈鼻朝图像右) → 图像平面:
    head_yaw_img = 90° − yaw_model (全角度模型 ±180 同样成立:
    ±180 背对 → −90 朝图像上)。
    """
    sess = _get_headpose()
    if sess is None or head_box_px is None:
        return None
    try:
        import cv2
        x1, y1, x2, y2 = [int(v) for v in head_box_px]
        h, w = crop_bgr.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 12 or y2 - y1 < 12:
            return None
        head = cv2.cvtColor(crop_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2RGB)
        head = cv2.resize(head, (224, 224)).astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        head = ((head - mean) / std).transpose(2, 0, 1)[None]
        inp = sess.get_inputs()[0].name
        outs = sess.run(None, {inp: head})
        yaw_model = _decode_headpose(outs)
        if yaw_model is None:
            return None
        return round(_norm_deg(90.0 - yaw_model), 1)
    except Exception:
        return None


# ==================== 几何: 关键点 → 朝向角 ====================

def _estimate_mediapipe(pts) -> Optional[dict]:
    """MediaPipe 33 关键点 (归一化裁剪坐标 + z 深度) → 朝向。"""
    ls, rs = pts[_MP_LSHO], pts[_MP_RSHO]
    if min(ls.visibility, rs.visibility) < 0.35:
        return None

    # ---- 躯干朝向: 双肩连线的法向量。歧义消除靠肩向量本身:
    #      面向相机时人的左肩在图像右 (镜像, sx>0), 背对时在图像左 (sx<0)。
    sx = ls.x - rs.x            # 肩向量 (右肩→左肩) 图像 x 分量
    sz = ls.z - rs.z            # 深度分量 (z 越小越靠近相机)
    # 水平面 (x 朝图像右, z 朝画面深处) 内把肩向量顺时针转 90° 得躯干法向:
    #   面向相机 (sx>0, sz≈0) → (fx,fz)=(0,-1) 指向相机 ✓
    fx = sz
    fz = -sx
    norm = math.hypot(fx, fz)
    if norm < 1e-6:
        return None
    fx, fz = fx / norm, fz / norm
    fy = -fz  # 朝相机 (fz<0) => 朝图像下 (fy>0)
    yaw_deg = math.degrees(math.atan2(fy, fx))

    # ---- 头部 yaw: 鼻相对双耳中点的偏移
    head_yaw = None
    nose, le, re_ = pts[_MP_NOSE], pts[_MP_LEAR], pts[_MP_REAR]
    ear_vis = min(le.visibility, re_.visibility)
    if nose.visibility > 0.35 and ear_vis > 0.2:
        ear_mid_x = (le.x + re_.x) / 2.0
        ear_span = abs(le.x - re_.x) or 1e-6
        ratio = max(-1.5, min(1.5, (nose.x - ear_mid_x) / ear_span))
        facing_cam = nose.z < min(le.z, re_.z)  # 鼻更靠近相机 => 面向相机
        base = 90.0 if facing_cam else -90.0
        head_yaw = base - ratio * 60.0 * (1.0 if facing_cam else -1.0)

    # ---- 头部框 (归一化 → 由调用方换算像素): 鼻/耳外扩
    head_pts = [p for p in (nose, le, re_) if p.visibility > 0.2]
    head_box = None
    if head_pts:
        hx = [p.x for p in head_pts]
        hy = [p.y for p in head_pts]
        cx, cy = sum(hx) / len(hx), sum(hy) / len(hy)
        span = max(max(hx) - min(hx), 0.04) * 2.2
        head_box = (cx - span, cy - span, cx + span, cy + span)  # 归一化

    return {
        "yaw_deg": round(yaw_deg, 1),
        "conf": round(float(min(ls.visibility, rs.visibility)), 3),
        "head_yaw_deg": round(head_yaw, 1) if head_yaw is not None else None,
        "facing_camera": bool(fy > 0),
        "_head_box_norm": head_box,
    }


def _estimate_yolo(kpts: List[tuple], crop_h: float) -> Optional[dict]:
    """COCO-17 关键点 (裁剪图像素坐标, 无深度) → 朝向。

    几何 (无 z 时的替代信号, 收编自原 orientation_engine):
      - 偏转幅度: 肩宽/躯干高的透视缩短 (正/背面比值最大≈_R0, 全侧面≈0);
      - 前/背半球: 肩序 (面向相机时人左肩在画面右侧), 近侧面回退脸部可见性;
      - 偏转方向 (画面左/右): 头部相对肩中点的水平偏移。
    """
    if not kpts or len(kpts) < 13:
        return None
    lsho, rsho = kpts[_CO_LSHO], kpts[_CO_RSHO]
    if lsho[2] < _KP_CONF or rsho[2] < _KP_CONF:
        return None

    sho_mid_x = (lsho[0] + rsho[0]) / 2.0
    sho_mid_y = (lsho[1] + rsho[1]) / 2.0
    shoulder_w = abs(rsho[0] - lsho[0])

    # 躯干高 (尺度基准): 肩中点到髋中点; 髋不可见退裁剪高的 0.28 (经验比例)
    lhip, rhip = kpts[_CO_LHIP], kpts[_CO_RHIP]
    if lhip[2] >= _KP_CONF and rhip[2] >= _KP_CONF:
        torso_h = abs((lhip[1] + rhip[1]) / 2.0 - sho_mid_y)
    else:
        torso_h = crop_h * 0.28
    if torso_h <= 1e-6:
        return None

    ratio = shoulder_w / torso_h
    c = max(0.0, min(1.0, ratio / _R0))
    side_mag = math.degrees(math.acos(c))   # 0(正/背)~90(全侧面)

    face_conf = max(kpts[_CO_NOSE][2], kpts[_CO_LEYE][2], kpts[_CO_REYE][2])
    if shoulder_w >= 0.3 * torso_h:
        front = lsho[0] > rsho[0]
    else:
        front = face_conf >= 0.35

    head_pts = [p for p in (kpts[_CO_NOSE], kpts[_CO_LEYE], kpts[_CO_REYE],
                            kpts[_CO_LEAR], kpts[_CO_REAR]) if p[2] >= _KP_CONF]
    if head_pts:
        head_x = sum(p[0] for p in head_pts) / len(head_pts)
        off = head_x - sho_mid_x
        dead = _HEAD_DEAD_ZONE * torso_h
        side_sign = 1 if off > dead else (-1 if off < -dead else 0)
    else:
        side_sign = 0

    # 像素平面 phi: 0=朝画面下(朝相机), +90=右, -90=左, ±180=朝画面上(背对)
    if front:
        phi = side_sign * side_mag
    else:
        phi = side_sign * (180.0 - side_mag) if side_sign else 180.0
    # 转全链路图像平面口径 (0=右, 90=下): yaw = 90 − phi
    yaw_deg = _norm_deg(90.0 - phi)

    # 头部 yaw (COCO 无深度: 鼻相对双耳中点偏移 + 肩序给前/背)
    head_yaw = None
    nose = kpts[_CO_NOSE]
    le, re_ = kpts[_CO_LEAR], kpts[_CO_REAR]
    if nose[2] >= 0.35 and min(le[2], re_[2]) >= 0.2:
        ear_mid_x = (le[0] + re_[0]) / 2.0
        ear_span = abs(le[0] - re_[0]) or 1e-6
        r = max(-1.5, min(1.5, (nose[0] - ear_mid_x) / ear_span))
        base = 90.0 if front else -90.0
        head_yaw = base - r * 60.0 * (1.0 if front else -1.0)

    # 头部框 (裁剪图像素坐标)
    head_box = None
    if head_pts:
        hx = [p[0] for p in head_pts]
        hy = [p[1] for p in head_pts]
        cx, cy = sum(hx) / len(hx), sum(hy) / len(hy)
        span = max(max(hx) - min(hx), torso_h * 0.35) * 1.4
        head_box = (cx - span, cy - span, cx + span, cy + span)

    return {
        "yaw_deg": round(yaw_deg, 1),
        "conf": round(float(min(lsho[2], rsho[2])), 3),
        "head_yaw_deg": round(head_yaw, 1) if head_yaw is not None else None,
        "facing_camera": bool(front),
        "_head_box_px": head_box,
    }


# ==================== 主入口 ====================

def estimate_yaw(frame_bgr: np.ndarray, box_xyxy) -> Optional[dict]:
    """对单个人框估计图像平面朝向角。

    box_xyxy: 像素坐标 (x1, y1, x2, y2)。
    返回 None (估计失败/人太小/关键点不可见) 或:
      {"yaw_deg": float, "conf": float, "head_yaw_deg": float|None,
       "facing_camera": bool, "backend": str}
    """
    backend = _get_backend()
    if backend is None or frame_bgr is None:
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
    ch, cw = crop.shape[:2]

    if getattr(backend, "kind", "mediapipe_pose") == "yolo11_pose":
        with _lock:
            kpts = backend.infer_keypoints(crop)
        res = _estimate_yolo(kpts, float(ch)) if kpts else None
        head_box_px = res.pop("_head_box_px", None) if res else None
    else:
        import cv2
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        with _lock:
            pts = backend.landmarks(rgb)
        res = _estimate_mediapipe(pts) if pts is not None else None
        head_box_px = None
        if res:
            hb = res.pop("_head_box_norm", None)
            if hb:
                head_box_px = (hb[0] * cw, hb[1] * ch, hb[2] * cw, hb[3] * ch)

    if res is None:
        return None

    # 头姿精化 (可选 ONNX): 成功则覆盖关键点几何的 head_yaw。
    # 默认只在面向相机半球精化: 背对相机时头部裁剪只有后脑勺, 正脸数据训练
    # 的头姿模型输出无意义角度 (实测恒 ≈"朝向相机"), 覆盖会把正确的身体朝向
    # 盖掉, 下游 facing_dwell 夹角恒 >100° 永不确认 (六和蒸镀 2026-09-16
    # 现场: 操作员背对相机看仪表是点检常态姿势)。背面跳过顺带省一次推理。
    # 现场绑定全角度头姿模型 (6DRepNet360/WHENet) 时开 headpose_full_range
    # 开关恢复背面精化 (KV 配置, 模型仓库朝向抽屉可改)。
    if res.get("facing_camera") or headpose_full_range():
        refined = _headpose_refine(crop, head_box_px)
        if refined is not None:
            res["head_yaw_deg"] = refined

    res["backend"] = getattr(backend, "kind", "unknown")
    return res


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
