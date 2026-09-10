# -*- coding: utf-8 -*-
"""VLM 坐诊 API (2026-09 全量批次) — /api/v1/vlm/*。

"站岗检测 + VLM 坐诊" 的坐诊侧: 按需对单图/通道当前帧问答,
不进实时帧循环, 默认关闭零开销。端点:

  GET  /config      — 当前配置 (api_key 打码回显)
  PUT  /config      — 保存配置 (SystemConfig KV vlm_config)
  GET  /status      — 端点探活 (GET {endpoint}/models)
  POST /ask         — 上传图片 + 问题
  POST /ask-frame   — 通道当前画面 + 问题

服务实现在 services/vlm_service (OpenAI 兼容 chat/completions 协议,
本地 Ollama / vLLM / LM Studio 通用; 推荐 Qwen2.5-VL-7B, Apache 2.0)。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.services import vlm_service

router = APIRouter()


def _masked(cfg: dict) -> dict:
    out = dict(cfg)
    key = out.get("api_key") or ""
    out["api_key"] = (key[:3] + "****") if len(key) > 3 else ("****" if key else "")
    return out


@router.get("/config", summary="VLM 坐诊配置")
def get_config():
    return _masked(vlm_service.get_config(force=True))


class VlmConfigPatch(BaseModel):
    enabled: Optional[bool] = None
    endpoint: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    timeout_s: Optional[int] = Field(None, ge=5, le=600)
    max_tokens: Optional[int] = Field(None, ge=16, le=4096)
    system_prompt: Optional[str] = None


@router.put("/config", summary="保存 VLM 坐诊配置")
def put_config(patch: VlmConfigPatch):
    data = patch.model_dump(exclude_none=True)
    # 打码回显值不覆盖真实 key
    if "api_key" in data and "****" in (data["api_key"] or ""):
        data.pop("api_key")
    return _masked(vlm_service.save_config(data))


@router.get("/status", summary="VLM 端点探活")
def status():
    return vlm_service.probe()


def _do_ask(image_bgr, question: str) -> dict:
    try:
        return vlm_service.ask(image_bgr, question)
    except vlm_service.VlmError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/ask", summary="上传图片问答")
async def ask(
    file: UploadFile = File(..., description="图片文件 (jpg/png/bmp)"),
    question: str = Form(..., description="要问的问题"),
):
    import cv2
    import numpy as np

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="无法解码图片 (支持 jpg/png/bmp)")
    return _do_ask(img, question)


class AskFrameRequest(BaseModel):
    channel_id: int = 0
    question: str


@router.post("/ask-frame", summary="通道当前画面问答")
def ask_frame(req: AskFrameRequest):
    from backend.api.channel_manager import get_channel_manager

    try:
        mgr = get_channel_manager().get(req.channel_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"通道 {req.channel_id} 不存在")
    frame = getattr(mgr, "current_frame", None)
    if frame is None:
        raise HTTPException(status_code=409,
                            detail=f"通道 {req.channel_id} 当前无画面 (视频源未运行)")
    return _do_ask(frame.copy(), req.question)
