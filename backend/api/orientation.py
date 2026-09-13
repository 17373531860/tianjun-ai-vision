# -*- coding: utf-8 -*-
"""朝向估计 API (2026-09 朝向驻留批次) — /api/v1/orientation/*。

三个端点 (模型仓库「试一试」抽屉消费, 与 /ocr /anomaly 同范式):
  GET  /status          — 引擎与三层后端可用性探针 (yolo11/mediapipe/headpose)
  POST /estimate        — 上传图片估计朝向 (整幅当人框; 谈单演示/装机标定)
  POST /estimate-frame  — 对指定通道当前画面估计 (现场零上传试用)

生产路径 (facing_dwell 规则) 不走本 API —— 推理线程内直调
services/person_orientation 同一单例, 本 API 只是试用/标定入口。
装机标定用法: 工程师站到点检位分别面向各仪表, 用 /estimate-frame 实测
角度, 直接抄进 facing_dwell 规则的仪表点/容差配置。
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from backend.services import person_orientation

router = APIRouter()


def _estimate(image_bgr) -> dict:
    if not person_orientation.is_available():
        st = person_orientation.engine_status()
        reason = st.get("error") or ("缺关键点后端 (yolo11n-pose.pt 权重"
                                     "或 mediapipe 依赖至少要有一个)")
        raise HTTPException(status_code=503, detail=f"朝向估计引擎不可用: {reason}")
    h, w = image_bgr.shape[:2]
    res = person_orientation.estimate_yaw(image_bgr, (0, 0, w, h))
    if res is None:
        # 画面里没有可判定的人不是错误 —— 试用页要能拿到"未检出"结果
        return {"found": False, "backend":
                person_orientation.engine_status().get("backend")}
    return {"found": True, **res}


@router.get("/status", summary="朝向估计引擎状态")
def orientation_status():
    return person_orientation.engine_status()


@router.post("/estimate", summary="上传图片估计人体朝向")
async def orientation_estimate(
    file: UploadFile = File(..., description="图片文件 (jpg/png/bmp), 整幅当人框"),
):
    import cv2
    import numpy as np

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="无法解码图片 (支持 jpg/png/bmp)")
    return _estimate(img)


class EstimateFrameRequest(BaseModel):
    channel_id: int = 0


@router.post("/estimate-frame", summary="估计通道当前画面的人体朝向")
def orientation_estimate_frame(req: EstimateFrameRequest):
    from backend.api.channel_manager import get_channel_manager

    try:
        mgr = get_channel_manager().get(req.channel_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"通道 {req.channel_id} 不存在")
    frame = getattr(mgr, "current_frame", None)
    if frame is None:
        raise HTTPException(status_code=409,
                            detail=f"通道 {req.channel_id} 当前无画面 (视频源未运行)")
    return _estimate(frame.copy())
