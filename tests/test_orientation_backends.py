# -*- coding: utf-8 -*-
"""朝向估计多后端 (2026-09-10 统一批次) 测试。

services/person_orientation 三层后端:
  - 关键点后端可插拔: yolo11_pose (COCO-17 无深度几何) / mediapipe_pose (33 点带 z)
  - 头姿 ONNX 精化层输出解码 (_decode_headpose 四种导出形态, 纯函数)
  - /api/v1/orientation/* 端点 (status / estimate / estimate-frame)

真模型不进 CI —— 关键点后端用 set_backend_for_tests 注入假实现,
几何与 API 契约全部确定性验证。
"""
from __future__ import annotations

import io
import math
from types import SimpleNamespace

import numpy as np
import pytest

from backend.services import person_orientation as po


# ============================================================
# 工具
# ============================================================

def _jpeg_bytes(img):
    import cv2
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _blank_image(h=480, w=320):
    return np.full((h, w, 3), 128, dtype=np.uint8)


def _coco_kpts_front():
    """正面朝相机的 COCO-17 假关键点 (裁剪图像素坐标)。

    面向相机 → 人左肩在画面右 (x 大); 鼻居中 → 侧偏 0 → yaw=90 (朝画面下)。
    """
    kp = [(0.0, 0.0, 0.0)] * 17
    kp[0] = (160, 60, 0.9)    # nose
    kp[1] = (170, 55, 0.9)    # left eye (画面右)
    kp[2] = (150, 55, 0.9)    # right eye
    kp[3] = (180, 60, 0.8)    # left ear
    kp[4] = (140, 60, 0.8)    # right ear
    kp[5] = (200, 150, 0.95)  # left shoulder (画面右)
    kp[6] = (120, 150, 0.95)  # right shoulder
    kp[11] = (180, 300, 0.9)  # left hip
    kp[12] = (140, 300, 0.9)  # right hip
    return kp


def _coco_kpts_back():
    """背对相机: 左肩在画面左, 脸部关键点全不可见 → yaw=-90 (朝画面上)。"""
    kp = [(0.0, 0.0, 0.0)] * 17
    kp[5] = (120, 150, 0.95)  # left shoulder (画面左)
    kp[6] = (200, 150, 0.95)  # right shoulder
    kp[11] = (140, 300, 0.9)
    kp[12] = (180, 300, 0.9)
    return kp


class _FakeYoloBackend:
    kind = "yolo11_pose"

    def __init__(self, kpts):
        self._kpts = kpts

    def infer_keypoints(self, crop_bgr):
        return self._kpts

    def close(self):
        pass


class _FakeMediapipeBackend:
    kind = "mediapipe_pose"

    def __init__(self, pts):
        self._pts = pts

    def landmarks(self, rgb):
        return self._pts

    def close(self):
        pass


def _mp_landmarks_front():
    """MediaPipe 33 点 (归一化 + z): 双肩等深、左肩在画面右 → 面向相机 yaw=90。"""
    pts = [SimpleNamespace(x=0.5, y=0.5, z=0.0, visibility=0.0)] * 33
    pts = list(pts)
    pts[0] = SimpleNamespace(x=0.5, y=0.15, z=-0.3, visibility=0.9)   # nose
    pts[7] = SimpleNamespace(x=0.56, y=0.16, z=-0.1, visibility=0.8)  # left ear
    pts[8] = SimpleNamespace(x=0.44, y=0.16, z=-0.1, visibility=0.8)  # right ear
    pts[11] = SimpleNamespace(x=0.65, y=0.35, z=0.0, visibility=0.95)  # l shoulder
    pts[12] = SimpleNamespace(x=0.35, y=0.35, z=0.0, visibility=0.95)  # r shoulder
    return pts


@pytest.fixture(autouse=True)
def _restore_backend(monkeypatch):
    # 隔离头姿槽位: 仓库里放了真 headpose.onnx (公开 6DRepNet360 占位) 时,
    # 精化层会覆盖假关键点几何的 head_yaw, 几何单测必须在空槽下跑
    monkeypatch.setenv("TIANJUN_HEADPOSE_MODEL", "/nonexistent/headpose.onnx")
    po.release()
    yield
    po.set_backend_for_tests(None)
    po.release()


# ============================================================
# 几何: YOLO COCO-17 通路
# ============================================================

def test_yolo_geometry_front_facing():
    po.set_backend_for_tests(_FakeYoloBackend(_coco_kpts_front()))
    img = _blank_image()
    r = po.estimate_yaw(img, (0, 0, img.shape[1], img.shape[0]))
    assert r is not None
    assert r["backend"] == "yolo11_pose"
    assert r["facing_camera"] is True
    assert abs(r["yaw_deg"] - 90.0) < 1e-6      # 正对相机 = 朝画面下
    assert abs(r["head_yaw_deg"] - 90.0) < 1e-6  # 鼻居中 = 头也正对
    assert r["conf"] >= 0.9


def test_yolo_geometry_back_facing():
    po.set_backend_for_tests(_FakeYoloBackend(_coco_kpts_back()))
    img = _blank_image()
    r = po.estimate_yaw(img, (0, 0, img.shape[1], img.shape[0]))
    assert r is not None
    assert r["facing_camera"] is False
    assert abs(r["yaw_deg"] - (-90.0)) < 1e-6   # 背对 = 朝画面上
    assert r["head_yaw_deg"] is None            # 脸不可见, 头部朝向不可判


def test_yolo_geometry_rejects_low_conf_shoulders():
    kp = _coco_kpts_front()
    kp[5] = (200, 150, 0.1)  # 左肩不可信
    po.set_backend_for_tests(_FakeYoloBackend(kp))
    img = _blank_image()
    assert po.estimate_yaw(img, (0, 0, img.shape[1], img.shape[0])) is None


def test_box_too_small_returns_none():
    po.set_backend_for_tests(_FakeYoloBackend(_coco_kpts_front()))
    img = _blank_image()
    assert po.estimate_yaw(img, (0, 0, 30, 30)) is None  # < _MIN_CROP_PX


# ============================================================
# 几何: MediaPipe 33 点通路
# ============================================================

def test_mediapipe_geometry_front_facing():
    po.set_backend_for_tests(_FakeMediapipeBackend(_mp_landmarks_front()))
    img = _blank_image()
    r = po.estimate_yaw(img, (0, 0, img.shape[1], img.shape[0]))
    assert r is not None
    assert r["backend"] == "mediapipe_pose"
    assert r["facing_camera"] is True
    assert abs(r["yaw_deg"] - 90.0) < 1e-6
    # 鼻在双耳中点 → 头部正对 (base 90, ratio 0)
    assert abs(r["head_yaw_deg"] - 90.0) < 1e-6


# ============================================================
# 头姿精化守门: 只在面向相机半球覆盖 (六和蒸镀 2026-09-16 现场修复)
# ============================================================

def test_headpose_refine_skipped_when_back_facing(monkeypatch):
    """背对相机时正脸头姿模型输出无意义, 不得覆盖关键点几何的朝向。

    现场事故: 操作员背对相机看仪表 (点检常态), 6DRepNet 对后脑勺恒输出
    ≈"朝向相机", 覆盖后 facing_dwell 夹角恒 >100° 永不打卡。
    """
    calls = []

    def _fake_refine(crop, head_box):
        calls.append(1)
        return 100.0  # 模拟对后脑勺输出的垃圾角度

    monkeypatch.setattr(po, "_headpose_refine", _fake_refine)
    po.set_backend_for_tests(_FakeYoloBackend(_coco_kpts_back()))
    img = _blank_image()
    r = po.estimate_yaw(img, (0, 0, img.shape[1], img.shape[0]))
    assert r is not None and r["facing_camera"] is False
    assert not calls                     # 背面连推理都不应发起
    assert r["head_yaw_deg"] is None     # 保留关键点几何结果 (背面无脸=None)
    assert abs(r["yaw_deg"] - (-90.0)) < 1e-6


def test_headpose_refine_applies_when_front_facing(monkeypatch):
    monkeypatch.setattr(po, "_headpose_refine", lambda crop, hb: 42.0)
    po.set_backend_for_tests(_FakeYoloBackend(_coco_kpts_front()))
    img = _blank_image()
    r = po.estimate_yaw(img, (0, 0, img.shape[1], img.shape[0]))
    assert r is not None and r["facing_camera"] is True
    assert abs(r["head_yaw_deg"] - 42.0) < 1e-6  # 正面才允许精化覆盖


def test_headpose_full_range_enables_backface_refine(monkeypatch):
    """现场绑全角度头姿模型 (6DRepNet360/WHENet) 时开开关恢复背面精化。"""
    monkeypatch.setattr(po, "headpose_full_range", lambda: True)
    monkeypatch.setattr(po, "_headpose_refine", lambda crop, hb: -100.0)
    po.set_backend_for_tests(_FakeYoloBackend(_coco_kpts_back()))
    img = _blank_image()
    r = po.estimate_yaw(img, (0, 0, img.shape[1], img.shape[0]))
    assert r is not None and r["facing_camera"] is False
    assert abs(r["head_yaw_deg"] - (-100.0)) < 1e-6  # 全角度模型: 背面也覆盖


# ============================================================
# 头姿 ONNX 输出解码 (纯函数, 四种导出形态)
# ============================================================

def _rot_y(deg):
    t = math.radians(deg)
    return np.array([[math.cos(t), 0, math.sin(t)],
                     [0, 1, 0],
                     [-math.sin(t), 0, math.cos(t)]], dtype=np.float64)


def test_decode_headpose_rotation_matrix():
    assert abs(po._decode_headpose([_rot_y(0.0)])) < 1e-6
    assert abs(po._decode_headpose([_rot_y(30.0)]) - 30.0) < 1e-6
    assert abs(po._decode_headpose([_rot_y(-45.0)[None]]) - (-45.0)) < 1e-6


def test_decode_headpose_6d_representation():
    R = _rot_y(30.0)
    six = np.concatenate([R[:, 0], R[:, 1]])[None]  # (1,6) 前两列
    assert abs(po._decode_headpose([six]) - 30.0) < 1e-4


def test_decode_headpose_binned_classification():
    # HopeNet/WHENet 系: (1,66) logits, one-hot bin i → 3i − 99
    logits = np.full((1, 66), -20.0)
    logits[0, 40] = 20.0
    outs = [logits, logits.copy(), logits.copy()]
    yaw = po._decode_headpose(outs)
    assert abs(yaw - (3 * 40 - 99)) < 0.5


def test_decode_headpose_euler_degrees():
    assert abs(po._decode_headpose([np.array([[25.0, 5.0, 2.0]])]) - 25.0) < 1e-6


def test_headpose_model_to_image_plane_mapping():
    """模型口径→图像平面: 0 (正对) → 90 (朝画面下); ±180 (背对) → −90。"""
    assert po._norm_deg(90.0 - 0.0) == 90.0
    assert po._norm_deg(90.0 - 180.0) == -90.0
    assert po._norm_deg(90.0 - (-180.0)) == -90.0
    assert po._norm_deg(90.0 - 30.0) == 60.0  # 头转向本人左 → 角度向画面右偏


# ============================================================
# /api/v1/orientation/* 端点
# ============================================================

def test_orientation_status_endpoint(client):
    r = client.get("/api/v1/orientation/status")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["available"], bool)
    for key in ("yolo11_pose", "mediapipe_pose", "headpose_onnx"):
        assert key in body["backends"]
    assert body["backend"] in ("yolo11_pose", "mediapipe_pose", "test")


def test_orientation_estimate_endpoint(client):
    po.set_backend_for_tests(_FakeYoloBackend(_coco_kpts_front()))
    img = _blank_image()
    r = client.post(
        "/api/v1/orientation/estimate",
        files={"file": ("p.jpg", io.BytesIO(_jpeg_bytes(img)), "image/jpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True
    assert abs(body["yaw_deg"] - 90.0) < 1e-6
    assert body["backend"] == "yolo11_pose"


def test_orientation_estimate_no_person(client):
    po.set_backend_for_tests(_FakeYoloBackend(None))  # 假后端: 检不出关键点
    img = _blank_image()
    r = client.post(
        "/api/v1/orientation/estimate",
        files={"file": ("p.jpg", io.BytesIO(_jpeg_bytes(img)), "image/jpeg")})
    assert r.status_code == 200
    assert r.json()["found"] is False


def test_orientation_estimate_rejects_bad_image(client):
    r = client.post(
        "/api/v1/orientation/estimate",
        files={"file": ("x.jpg", io.BytesIO(b"not-an-image"), "image/jpeg")})
    assert r.status_code == 400


def test_orientation_estimate_frame_channel_missing(client):
    r = client.post("/api/v1/orientation/estimate-frame",
                    json={"channel_id": 999})
    assert r.status_code in (404, 409)


def test_orientation_config_roundtrip(client):
    """headpose_full_range 配置: 默认 false → PUT true 落库 → 过期后重读仍 true。"""
    r = client.get("/api/v1/orientation/config")
    assert r.status_code == 200
    assert r.json()["headpose_full_range"] is False  # 出厂默认: 正脸模型

    r = client.put("/api/v1/orientation/config",
                   json={"headpose_full_range": True})
    assert r.status_code == 200, r.text
    # PUT 直写缓存, 推理侧立即生效
    assert po.headpose_full_range() is True
    # 缓存失效后走 DB 重读 → 验证真落库而非只写了缓存
    po.set_headpose_full_range_cache(None)
    r = client.get("/api/v1/orientation/config")
    assert r.json()["headpose_full_range"] is True

    r = client.put("/api/v1/orientation/config",
                   json={"headpose_full_range": False})
    assert r.status_code == 200
    assert po.headpose_full_range() is False


# ============================================================
# 头姿 ONNX 真权重集成 (权重在场才跑, CI 无权重自动跳过)
# ============================================================

_HEADPOSE_PATH = po._headpose_path()


@pytest.mark.skipif(not __import__("os").path.isfile(_HEADPOSE_PATH),
                    reason="headpose.onnx 权重不在场")
def test_headpose_onnx_real_weights_refine(monkeypatch):
    """真权重端到端: 头姿槽位加载成功 + 输出为有效图像平面角。

    公开 6DRepNet360 (PINTO zoo 导出, 输出 yaw_pitch_roll 度) 占位;
    授权权重到位后同名替换, 本测试契约不变。
    """
    import cv2
    monkeypatch.delenv("TIANJUN_HEADPOSE_MODEL", raising=False)  # 解除隔离
    po.release()
    # 合成头部裁剪 (灰底) — 只验证会话跑通与解码输出域, 不验角度语义
    fake_head = np.full((160, 160, 3), 120, dtype=np.uint8)
    cv2.circle(fake_head, (80, 70), 50, (150, 130, 110), -1)
    yaw = po._headpose_refine(fake_head, (10, 10, 150, 150))
    assert yaw is not None, "真权重在场时精化不应返回 None"
    assert -180.0 <= yaw <= 180.0
    st = po.engine_status()["backends"]["headpose_onnx"]
    assert st["present"] and st["loaded"] and st["error"] is None
    po.release()
