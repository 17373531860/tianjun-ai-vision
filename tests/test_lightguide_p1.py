"""投影光引导 P1: /api/v1/lightguide/* 端点测试。

覆盖:
  1. config 未标定默认态 / solve 后 roundtrip / delete 清除
  2. pattern 端点出合法 PNG + 参数守门
  3. solve 无画面 400 守门
  4. 合成端到端: 用已知 投影→相机 透视变换把标定图案 warp 成假相机帧,
     monkeypatch 取帧路径喂给 solve, 验证反解出的 相机→投影 homography
     能把相机点变换回投影点 (亚像素级误差)。不依赖真相机/投影仪。
"""
import numpy as np
import pytest


PREFIX = "/api/v1/lightguide"


# ---------------- config 基础 ----------------

def test_config_default_uncalibrated(client):
    r = client.get(f"{PREFIX}/config", params={"channel": 7})
    assert r.status_code == 200
    body = r.json()
    assert body["channel"] == 7
    assert body["calibrated"] is False
    assert body["calibration"] is None
    assert "available" in body["frame"]


def test_pattern_returns_png(client):
    r = client.get(f"{PREFIX}/calibration/pattern",
                   params={"proj_w": 1280, "proj_h": 720, "cols": 4, "rows": 3})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("params", [
    {"proj_w": 100, "proj_h": 720},          # 分辨率过小
    {"cols": 1, "rows": 3},                   # 阵列过小
    {"cols": 9, "rows": 3},                   # 列数超上限
    {"cols": 8, "rows": 7},                   # 超 DICT_4X4_50 容量前先被 rows 拦
])
def test_pattern_param_guard(client, params):
    q = {"proj_w": 1280, "proj_h": 720, "cols": 4, "rows": 3}
    q.update(params)
    r = client.get(f"{PREFIX}/calibration/pattern", params=q)
    assert r.status_code == 400


def test_solve_no_frame_returns_400(client, monkeypatch):
    import backend.api.lightguide as lg
    monkeypatch.setattr(lg, "_grab_frame", lambda ch: None)
    r = client.post(f"{PREFIX}/calibration/solve",
                    json={"channel": 63, "proj_w": 1280, "proj_h": 720,
                          "cols": 4, "rows": 3, "grab_frames": 2})
    assert r.status_code == 400
    assert "无画面" in r.json()["detail"]


# ---------------- 合成端到端 ----------------

def _synthetic_camera_view(client, proj_w, proj_h, cols, rows, cam_w, cam_h):
    """取 pattern 端点的真实图案, 用已知透视变换合成"相机看到的投影台面"。

    返回 (cam_frame_bgr, H_proj2cam)。
    """
    import cv2

    r = client.get(f"{PREFIX}/calibration/pattern",
                   params={"proj_w": proj_w, "proj_h": proj_h,
                           "cols": cols, "rows": rows})
    assert r.status_code == 200
    pattern = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert pattern.shape == (proj_h, proj_w)

    # 投影四角落到相机画面里的一个带透视的四边形 (模拟斜装相机)
    src = np.float32([[0, 0], [proj_w, 0], [proj_w, proj_h], [0, proj_h]])
    dst = np.float32([
        [cam_w * 0.12, cam_h * 0.10],
        [cam_w * 0.90, cam_h * 0.16],
        [cam_w * 0.86, cam_h * 0.92],
        [cam_w * 0.08, cam_h * 0.84],
    ])
    H_proj2cam = cv2.getPerspectiveTransform(src, dst)
    cam = cv2.warpPerspective(pattern, H_proj2cam, (cam_w, cam_h),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT,
                              borderValue=170)  # 台面灰
    cam_bgr = cv2.cvtColor(cam, cv2.COLOR_GRAY2BGR)
    return cam_bgr, H_proj2cam


def test_solve_end_to_end_synthetic(client, monkeypatch):
    import cv2
    import backend.api.lightguide as lg

    proj_w, proj_h, cols, rows = 1280, 720, 4, 3
    cam_w, cam_h = 1920, 1080
    cam_frame, H_proj2cam = _synthetic_camera_view(
        client, proj_w, proj_h, cols, rows, cam_w, cam_h)

    monkeypatch.setattr(lg, "_grab_frame", lambda ch: cam_frame)

    r = client.post(f"{PREFIX}/calibration/solve",
                    json={"channel": 42, "proj_w": proj_w, "proj_h": proj_h,
                          "cols": cols, "rows": rows, "grab_frames": 2})
    assert r.status_code == 200, r.text
    calib = r.json()["calibration"]

    # 标记识别与误差指标
    assert calib["markers_matched"] >= 10          # 12 个标记至少认出 10
    assert calib["reproj_error_px"] < 2.0
    assert calib["cam_width"] == cam_w and calib["cam_height"] == cam_h

    # 求出的 H_cam2proj 应是 H_proj2cam 的逆: 随机投影点 → 相机 → 反解回投影
    H_solved = np.asarray(calib["homography"], dtype=np.float64)
    test_pts_proj = np.float32([
        [200, 150], [1100, 200], [640, 360], [300, 600], [1000, 650],
    ]).reshape(-1, 1, 2)
    pts_cam = cv2.perspectiveTransform(test_pts_proj, H_proj2cam)
    pts_back = cv2.perspectiveTransform(pts_cam.astype(np.float64), H_solved)
    err = np.linalg.norm(
        pts_back.reshape(-1, 2) - test_pts_proj.reshape(-1, 2).astype(np.float64),
        axis=1)
    assert float(err.max()) < 3.0, f"回投影误差过大: {err}"

    # roundtrip: config 现在应已标定
    r2 = client.get(f"{PREFIX}/config", params={"channel": 42})
    assert r2.status_code == 200
    assert r2.json()["calibrated"] is True
    assert r2.json()["calibration"]["reproj_error_px"] == calib["reproj_error_px"]

    # delete 清除
    r3 = client.delete(f"{PREFIX}/config", params={"channel": 42})
    assert r3.status_code == 200
    assert client.get(f"{PREFIX}/config",
                      params={"channel": 42}).json()["calibrated"] is False


def test_solve_rejects_when_markers_not_visible(client, monkeypatch):
    """相机只看到空台面 (无图案) → 422 且提示可读。"""
    import backend.api.lightguide as lg
    blank = np.full((720, 1280, 3), 170, dtype=np.uint8)
    monkeypatch.setattr(lg, "_grab_frame", lambda ch: blank)
    r = client.post(f"{PREFIX}/calibration/solve",
                    json={"channel": 41, "proj_w": 1280, "proj_h": 720,
                          "cols": 4, "rows": 3, "grab_frames": 1})
    assert r.status_code == 422
    assert "识别到" in r.json()["detail"]
