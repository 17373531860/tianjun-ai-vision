# -*- coding: utf-8 -*-
"""OCR 读字引擎 + 异常检测引擎 (2026-09 全量批次) 测试。

OCR (services/ocr_engine + /api/v1/ocr):
  - 状态探针 / 生成含文字的图片真识别 / roi 裁剪坐标换算 / 参数校验
异常检测 (services/anomaly_engine + /api/v1/anomaly):
  - 建库→评分闭环: 同分布图得分低于缺陷图 (黑块注入), 阈值判定方向正确
  - bank CRUD / 阈值调整 / 不存在的库 404
  - TJ_ANOMALY_PRETRAINED=0 强制随机权重 (无网络环境确定性, 不下载 45MB)

rapidocr 依赖缺失的环境自动跳过 OCR 真识别用例 (状态探针仍验证降级路径)。
"""
from __future__ import annotations

import io
import os

import numpy as np
import pytest

os.environ.setdefault("TJ_ANOMALY_PRETRAINED", "0")

_HAS_OCR = True
try:
    import rapidocr_onnxruntime  # noqa: F401
except Exception:
    _HAS_OCR = False


def _text_image(text="SN 12345", size=(320, 640)):
    """生成白底黑字测试图 (cv2 putText, OCR 可稳定识别的印刷体数字)。"""
    import cv2
    img = np.full((size[0], size[1], 3), 255, dtype=np.uint8)
    cv2.putText(img, text, (40, size[0] // 2), cv2.FONT_HERSHEY_SIMPLEX,
                2.0, (0, 0, 0), 5, cv2.LINE_AA)
    return img


def _jpeg_bytes(img):
    import cv2
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


# ============================================================
# OCR
# ============================================================

def test_ocr_status_endpoint(client):
    r = client.get("/api/v1/ocr/status")
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "rapidocr_onnxruntime"
    assert isinstance(body["available"], bool)


@pytest.mark.skipif(not _HAS_OCR, reason="rapidocr_onnxruntime 未安装")
def test_ocr_read_recognizes_text(client):
    img = _text_image("SN 12345")
    r = client.post("/api/v1/ocr/read",
                    files={"file": ("sn.jpg", io.BytesIO(_jpeg_bytes(img)),
                                    "image/jpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 1
    assert "12345" in body["text"].replace(" ", "")
    # box 归一化坐标 + 分数
    b = body["results"][0]
    assert 0 <= b["box"][0][0] <= 1 and 0 <= b["box"][0][1] <= 1
    assert b["score"] > 0.5


@pytest.mark.skipif(not _HAS_OCR, reason="rapidocr_onnxruntime 未安装")
def test_ocr_read_roi_crop(client):
    """文字在画面左半区: 右半 roi 读不到, 左半 roi 读得到且 box 换算回整图坐标。"""
    img = _text_image("777888")
    right = client.post(
        "/api/v1/ocr/read",
        files={"file": ("a.jpg", io.BytesIO(_jpeg_bytes(img)), "image/jpeg")},
        data={"roi": "[0.75, 0, 0.25, 1]"})
    assert right.status_code == 200 and right.json()["count"] == 0
    left = client.post(
        "/api/v1/ocr/read",
        files={"file": ("a.jpg", io.BytesIO(_jpeg_bytes(img)), "image/jpeg")},
        data={"roi": "[0, 0, 0.8, 1]"})
    assert left.status_code == 200, left.text
    body = left.json()
    assert "777888" in body["text"].replace(" ", "")
    # box 应落在整图坐标系 (文字起点 x≈40/640)
    assert body["results"][0]["box"][0][0] < 0.5


def test_ocr_read_rejects_bad_inputs(client):
    r = client.post("/api/v1/ocr/read",
                    files={"file": ("x.jpg", io.BytesIO(b"not-an-image"),
                                    "image/jpeg")})
    assert r.status_code == 400
    r2 = client.post("/api/v1/ocr/read",
                     files={"file": ("x.jpg", io.BytesIO(_jpeg_bytes(
                         _text_image())), "image/jpeg")},
                     data={"roi": "[2, 0, 1, 1]"})
    assert r2.status_code == 400


def test_ocr_read_frame_no_source(client):
    r = client.post("/api/v1/ocr/read-frame", json={"channel_id": 0})
    assert r.status_code in (404, 409)  # 测试环境通道无画面


# ============================================================
# 异常检测
# ============================================================

def _textured_ok_image(seed):
    """合格品: 固定纹理底 + 轻噪声 (同分布可复现)。"""
    rng = np.random.default_rng(seed)
    base = np.full((224, 224, 3), 128, dtype=np.uint8)
    noise = rng.integers(-10, 10, size=base.shape, dtype=np.int16)
    return np.clip(base.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def _defect_image(seed):
    """缺陷品: 同底纹理 + 大黑块 (划伤/脏污模拟)。"""
    img = _textured_ok_image(seed)
    img[60:140, 60:140] = 0
    return img


@pytest.fixture()
def bank_id(client):
    files = [("files", (f"ok{i}.png", io.BytesIO(_jpeg_bytes(_textured_ok_image(i))),
                        "image/png")) for i in range(4)]
    r = client.post("/api/v1/anomaly/banks", files=files,
                    data={"name": "测试合格品库"})
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["num_images"] == 4 and meta["num_vectors"] > 0
    assert meta["threshold"] > 0
    yield meta["id"]
    client.delete(f"/api/v1/anomaly/banks/{meta['id']}")


def test_anomaly_enroll_and_score_direction(client, bank_id):
    """核心断言: 缺陷图分数显著高于同分布合格图 (方向正确性)。"""
    ok = client.post(
        f"/api/v1/anomaly/banks/{bank_id}/score",
        files={"file": ("t.png", io.BytesIO(_jpeg_bytes(_textured_ok_image(99))),
                        "image/png")})
    ng = client.post(
        f"/api/v1/anomaly/banks/{bank_id}/score",
        files={"file": ("d.png", io.BytesIO(_jpeg_bytes(_defect_image(99))),
                        "image/png")},
        data={"with_heatmap": "true"})
    assert ok.status_code == 200 and ng.status_code == 200
    ok_score, ng_score = ok.json()["score"], ng.json()["score"]
    assert ng_score > ok_score * 1.5, \
        f"缺陷图应显著高分: ok={ok_score} ng={ng_score}"
    assert ng.json()["heatmap"], "with_heatmap 应返回异常热力图"
    # 阈值判定方向: 合格图不应报异常
    assert ok.json()["is_anomaly"] is False


def test_anomaly_bank_crud_and_threshold(client, bank_id):
    banks = client.get("/api/v1/anomaly/banks").json()["banks"]
    assert any(b["id"] == bank_id for b in banks)
    r = client.put(f"/api/v1/anomaly/banks/{bank_id}/threshold",
                   json={"threshold": 123.45})
    assert r.status_code == 200 and r.json()["threshold"] == 123.45
    # 阈值可控判定: 极高阈值下缺陷图也判合格
    ng = client.post(
        f"/api/v1/anomaly/banks/{bank_id}/score",
        files={"file": ("d.png", io.BytesIO(_jpeg_bytes(_defect_image(1))),
                        "image/png")})
    assert ng.json()["is_anomaly"] is False


def test_anomaly_missing_bank_404(client):
    r = client.post(
        "/api/v1/anomaly/banks/no-such-bank/score",
        files={"file": ("t.png", io.BytesIO(_jpeg_bytes(_textured_ok_image(0))),
                        "image/png")})
    assert r.status_code == 404
    assert client.delete("/api/v1/anomaly/banks/no-such-bank").status_code == 404


def test_anomaly_empty_files_rejected(client):
    r = client.post("/api/v1/anomaly/banks",
                    files={"files": ("x.png", io.BytesIO(b""), "image/png")})
    assert r.status_code == 400
