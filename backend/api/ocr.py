# -*- coding: utf-8 -*-
"""OCR 读字 API (2026-09 全量批次) — /api/v1/ocr/*。

三个端点:
  GET  /status      — 引擎可用性探针 (依赖是否安装/是否已加载/错误原因)
  POST /read        — 上传图片识别 (multipart; 可选 roi 归一化矩形)
  POST /read-frame  — 对指定通道当前画面识别 (谈单演示/现场试读零上传)

引擎实现在 services/ocr_engine (rapidocr_onnxruntime, 懒加载单例)。
依赖缺失 → 503 + 安装指引, 不拖垮主程序。
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.services import ocr_engine

router = APIRouter()


def _parse_roi(roi) -> Optional[list]:
    if roi in (None, "", "null"):
        return None
    if isinstance(roi, str):
        try:
            roi = json.loads(roi)
        except Exception:
            raise HTTPException(status_code=400, detail="roi 必须是 JSON 数组 [x,y,w,h]")
    if (not isinstance(roi, (list, tuple)) or len(roi) < 4
            or not all(isinstance(v, (int, float)) for v in roi[:4])):
        raise HTTPException(status_code=400, detail="roi 必须是 [x,y,w,h] 归一化数值")
    x, y, w, h = [float(v) for v in roi[:4]]
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
        raise HTTPException(status_code=400, detail="roi 取值需在 0~1 归一化范围内")
    return [x, y, w, h]


def _run_ocr(image_bgr, roi, min_score: float) -> dict:
    try:
        results = ocr_engine.read_text(image_bgr, roi=roi, min_score=min_score)
    except ocr_engine.OcrUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"results": results, "count": len(results),
            "text": " ".join(r["text"] for r in results)}


@router.get("/status", summary="OCR 引擎状态")
def ocr_status():
    return ocr_engine.engine_status()


@router.post("/read", summary="上传图片识别文字")
async def ocr_read(
    file: UploadFile = File(..., description="图片文件 (jpg/png/bmp)"),
    roi: Optional[str] = Form(None, description="可选 [x,y,w,h] 归一化矩形 JSON"),
    min_score: float = Form(0.5, ge=0.0, le=1.0),
):
    import cv2
    import numpy as np

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="无法解码图片 (支持 jpg/png/bmp)")
    return _run_ocr(img, _parse_roi(roi), min_score)


class ReadFrameRequest(BaseModel):
    channel_id: int = 0
    roi: Optional[list] = Field(None, description="可选 [x,y,w,h] 归一化矩形")
    min_score: float = Field(0.5, ge=0.0, le=1.0)


@router.post("/read-frame", summary="识别通道当前画面文字")
def ocr_read_frame(req: ReadFrameRequest):
    from backend.api.channel_manager import get_channel_manager

    try:
        mgr = get_channel_manager().get(req.channel_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"通道 {req.channel_id} 不存在")
    frame = getattr(mgr, "current_frame", None)
    if frame is None:
        raise HTTPException(status_code=409,
                            detail=f"通道 {req.channel_id} 当前无画面 (视频源未运行)")
    return _run_ocr(frame.copy(), _parse_roi(req.roi), req.min_score)
