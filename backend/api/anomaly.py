# -*- coding: utf-8 -*-
"""异常检测 API (2026-09 全量批次) — /api/v1/anomaly/*。

只学合格品的第二条质检路径 (与"标框再训练"并列的入口):
  GET    /banks               — 记忆库列表
  POST   /banks               — 上传合格品图片建库 (multipart files[])
  DELETE /banks/{bank_id}     — 删库
  PUT    /banks/{bank_id}/threshold — 调阈值
  POST   /banks/{bank_id}/score       — 上传图片评分
  POST   /banks/{bank_id}/score-frame — 对通道当前画面评分 (现场试用零上传)

引擎实现在 services/anomaly_engine (PatchCore 风格, resnet18 特征 + 最近邻)。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.services import anomaly_engine

router = APIRouter()


def _decode_upload(raw: bytes, filename: str = ""):
    import cv2
    import numpy as np

    if not raw:
        raise HTTPException(status_code=400, detail=f"上传文件为空: {filename}")
    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400,
                            detail=f"无法解码图片 (支持 jpg/png/bmp): {filename}")
    return img


@router.get("/banks", summary="记忆库列表")
def anomaly_banks():
    return {"banks": anomaly_engine.list_banks()}


@router.post("/banks", summary="上传合格品图片建记忆库")
async def anomaly_create_bank(
    files: List[UploadFile] = File(..., description="合格品图片 (≥1 张)"),
    name: str = Form("", description="记忆库名称"),
):
    images = [_decode_upload(await f.read(), f.filename or "") for f in files]
    try:
        meta = anomaly_engine.create_bank(name, images)
    except anomaly_engine.AnomalyError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return meta


@router.delete("/banks/{bank_id}", summary="删除记忆库")
def anomaly_delete_bank(bank_id: str):
    if not anomaly_engine.delete_bank(bank_id):
        raise HTTPException(status_code=404, detail=f"记忆库不存在: {bank_id}")
    return {"deleted": bank_id}


class ThresholdRequest(BaseModel):
    threshold: float = Field(..., gt=0)


@router.put("/banks/{bank_id}/threshold", summary="调整判定阈值")
def anomaly_update_threshold(bank_id: str, req: ThresholdRequest):
    try:
        return anomaly_engine.update_threshold(bank_id, req.threshold)
    except anomaly_engine.AnomalyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/banks/{bank_id}/score", summary="上传图片评分")
async def anomaly_score(
    bank_id: str,
    file: UploadFile = File(...),
    threshold: Optional[float] = Form(None),
    with_heatmap: bool = Form(False),
):
    img = _decode_upload(await file.read(), file.filename or "")
    try:
        return anomaly_engine.score_image(bank_id, img, threshold=threshold,
                                          with_heatmap=with_heatmap)
    except anomaly_engine.AnomalyError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ScoreFrameRequest(BaseModel):
    channel_id: int = 0
    threshold: Optional[float] = Field(None, gt=0)
    with_heatmap: bool = False


@router.post("/banks/{bank_id}/score-frame", summary="对通道当前画面评分")
def anomaly_score_frame(bank_id: str, req: ScoreFrameRequest):
    from backend.api.channel_manager import get_channel_manager

    try:
        mgr = get_channel_manager().get(req.channel_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"通道 {req.channel_id} 不存在")
    frame = getattr(mgr, "current_frame", None)
    if frame is None:
        raise HTTPException(status_code=409,
                            detail=f"通道 {req.channel_id} 当前无画面 (视频源未运行)")
    try:
        return anomaly_engine.score_image(bank_id, frame.copy(),
                                          threshold=req.threshold,
                                          with_heatmap=req.with_heatmap)
    except anomaly_engine.AnomalyError as e:
        raise HTTPException(status_code=404, detail=str(e))
