"""投影光引导 P2: 自愈锚点 (anchors/marker/verify) + 悬停采样 (interaction/sample)。

合成端到端思路 (无硬件):
  1. 用 P1 同款已知透视变换 H_proj2cam 把标定图案 warp 成假相机帧 → solve 落库
  2. 再合成"引导画面只剩四角锚点"的相机帧 → verify 应报漂移 ≈ 0
  3. 篡改落库 homography 平移 12px (模拟投影仪被撞) → verify 应报漂移 ≈ 12px
"""
import json

import numpy as np
import pytest


PREFIX = "/api/v1/lightguide"


# ---------------- anchors / marker 基础 ----------------

def test_anchor_layout_four_corners(client):
    r = client.get(f"{PREFIX}/calibration/anchors",
                   params={"proj_w": 1280, "proj_h": 800})
    assert r.status_code == 200
    markers = r.json()["markers"]
    assert [m["id"] for m in markers] == [46, 47, 48, 49]
    for m in markers:
        x, y, size = m["tile"]
        assert 0 <= x and x + size <= 1280
        assert 0 <= y and y + size <= 800
        # 标记角点在 tile 内部 (白圈留白)
        for cx, cy in m["corners"]:
            assert x < cx < x + size and y < cy < y + size


def test_marker_png_and_guard(client):
    r = client.get(f"{PREFIX}/calibration/marker",
                   params={"marker_id": 46, "size": 64})
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    # id 超出 DICT_4X4_50 范围 → 参数校验 422
    r = client.get(f"{PREFIX}/calibration/marker",
                   params={"marker_id": 50, "size": 64})
    assert r.status_code == 422


def test_pattern_grid_reserves_anchor_ids(client):
    """标定阵超过 44 个标记被拒 (46~49 保留给锚点)。"""
    r = client.get(f"{PREFIX}/calibration/pattern",
                   params={"proj_w": 4000, "proj_h": 3000, "cols": 8, "rows": 6})
    assert r.status_code == 400
    assert "锚点" in r.json()["detail"]


def test_verify_requires_calibration(client):
    r = client.post(f"{PREFIX}/calibration/verify", json={"channel": 61})
    assert r.status_code == 409


# ---------------- verify 合成端到端 ----------------

CH = 43
PROJ_W, PROJ_H = 1280, 800
CAM_W, CAM_H = 1920, 1080


def _h_proj2cam():
    import cv2
    src = np.float32([[0, 0], [PROJ_W, 0], [PROJ_W, PROJ_H], [0, PROJ_H]])
    dst = np.float32([
        [CAM_W * 0.12, CAM_H * 0.10],
        [CAM_W * 0.90, CAM_H * 0.16],
        [CAM_W * 0.86, CAM_H * 0.92],
        [CAM_W * 0.08, CAM_H * 0.84],
    ])
    return cv2.getPerspectiveTransform(src, dst)


def _warp_to_cam(proj_canvas, border=170):
    import cv2
    cam = cv2.warpPerspective(proj_canvas, _h_proj2cam(), (CAM_W, CAM_H),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=border)
    return cv2.cvtColor(cam, cv2.COLOR_GRAY2BGR)


def _pattern_cam_frame(client):
    import cv2
    r = client.get(f"{PREFIX}/calibration/pattern",
                   params={"proj_w": PROJ_W, "proj_h": PROJ_H, "cols": 4, "rows": 3})
    pattern = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    return _warp_to_cam(pattern)


def _anchors_cam_frame(client):
    """合成引导态相机帧: 黑底 + 四角锚点 tile (与前端绘制同源: marker 端点 PNG)。"""
    import cv2
    layout = client.get(f"{PREFIX}/calibration/anchors",
                        params={"proj_w": PROJ_W, "proj_h": PROJ_H}).json()["markers"]
    canvas = np.zeros((PROJ_H, PROJ_W), dtype=np.uint8)
    for m in layout:
        x, y, size = m["tile"]
        png = client.get(f"{PREFIX}/calibration/marker",
                         params={"marker_id": m["id"], "size": size}).content
        tile = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
        canvas[y:y + size, x:x + size] = tile
    # 投影黑=不打光, 相机看到的是暗台面而非纯黑; 用灰 60 模拟更接近真实
    canvas[canvas == 0] = 60
    return _warp_to_cam(canvas)


def test_verify_drift_zero_then_detects_bump(client, monkeypatch, db_session):
    import backend.api.lightguide as lg
    from backend.models.models import SystemConfig

    # 1. 真实 solve 落库
    monkeypatch.setattr(lg, "_grab_frame", lambda ch: _pattern_cam_frame(client))
    r = client.post(f"{PREFIX}/calibration/solve",
                    json={"channel": CH, "proj_w": PROJ_W, "proj_h": PROJ_H,
                          "cols": 4, "rows": 3, "grab_frames": 1})
    assert r.status_code == 200, r.text

    # 2. 切到引导态锚点帧 → verify 漂移应 ≈ 0
    monkeypatch.setattr(lg, "_grab_frame", lambda ch: _anchors_cam_frame(client))
    r = client.post(f"{PREFIX}/calibration/verify",
                    json={"channel": CH, "grab_frames": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["anchors_found"] == 4, body
    assert body["drift_px"] < 2.5, body

    # 3. 篡改落库 H: 投影空间平移 12px (模拟投影仪被撞歪)
    row = db_session.query(SystemConfig).filter(
        SystemConfig.key == f"lightguide.channel.{CH}").first()
    calib = json.loads(row.value)
    H = np.asarray(calib["homography"])
    T = np.asarray([[1, 0, 12.0], [0, 1, 0], [0, 0, 1]])
    calib["homography"] = (T @ H).tolist()
    row.value = json.dumps(calib)
    db_session.commit()

    r = client.post(f"{PREFIX}/calibration/verify",
                    json={"channel": CH, "grab_frames": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["anchors_found"] == 4
    assert 9.0 < body["drift_px"] < 15.0, body   # ≈12px 被测出

    client.delete(f"{PREFIX}/config", params={"channel": CH})


# ---------------- interaction/sample ----------------

def test_sample_reads_region_brightness(client, monkeypatch):
    import backend.api.lightguide as lg
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[500:650, 400:700] = 230   # "投影按钮"亮区
    monkeypatch.setattr(lg, "_grab_frame", lambda ch: frame)

    # 按钮区多边形 → 高亮度
    on_btn = [[400 / 1280, 500 / 720], [700 / 1280, 500 / 720],
              [700 / 1280, 650 / 720], [400 / 1280, 650 / 720]]
    r = client.post(f"{PREFIX}/interaction/sample",
                    json={"channel": 0, "polygon": on_btn})
    assert r.status_code == 200
    assert r.json()["mean_gray"] > 200

    # 空白区多边形 → 低亮度 (悬停判定的对照组)
    off_btn = [[0.05, 0.05], [0.25, 0.05], [0.25, 0.25], [0.05, 0.25]]
    r = client.post(f"{PREFIX}/interaction/sample",
                    json={"channel": 0, "polygon": off_btn})
    assert r.status_code == 200
    assert r.json()["mean_gray"] < 10


@pytest.mark.parametrize("polygon", [
    [[0.1, 0.1], [0.2, 0.2]],                # 少于 3 点
    [[0.1, 0.1], [1.2, 0.2], [0.2, 0.5]],    # 超出 0~1
])
def test_sample_polygon_guard(client, monkeypatch, polygon):
    import backend.api.lightguide as lg
    monkeypatch.setattr(lg, "_grab_frame",
                        lambda ch: np.zeros((720, 1280, 3), dtype=np.uint8))
    r = client.post(f"{PREFIX}/interaction/sample",
                    json={"channel": 0, "polygon": polygon})
    assert r.status_code == 400


def test_sample_no_frame(client, monkeypatch):
    import backend.api.lightguide as lg
    monkeypatch.setattr(lg, "_grab_frame", lambda ch: None)
    r = client.post(f"{PREFIX}/interaction/sample",
                    json={"channel": 62, "polygon": [[0.1, 0.1], [0.3, 0.1], [0.2, 0.3]]})
    assert r.status_code == 400
