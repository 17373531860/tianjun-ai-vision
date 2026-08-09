# -*- coding: utf-8 -*-
"""训练平台互连 API (/api/v1/interconnect/*, interconnect-contract 1.0).

- health / models/push: 契约端点, 供 YoloVision 训练平台调用
  (鉴权: X-Interconnect-Token 与本机配置的共享令牌恒定时间比较)
- config / status / test-connection / samples/recent: 本机管理端点, 供前端互连页
契约文档: 训练平台仓库 TIANJUN_INTERCONNECT_SPEC.md
"""
from __future__ import annotations

import hmac
import os
import shutil
import tempfile
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.services.interconnect import config as icfg
from backend.services.interconnect import uploader

router = APIRouter()

# 1.1: 多设备身份 + 模型拉取分发 + 漏检语义加固 (纯追加, 兼容 1.0 对端)
CONTRACT_VERSION = "1.1"
PRODUCT = "tianjun-ai-vision"


# ------------------------------ 鉴权 ------------------------------
def _require_token(
    x_interconnect_token: Optional[str] = Header(None, alias="X-Interconnect-Token"),
):
    cfg = icfg.get_config()
    if not cfg.get("enabled"):
        raise HTTPException(status_code=403, detail="互连未启用 (先在互连设置页开启)")
    token = (cfg.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=403, detail="互连令牌未配置")
    if not x_interconnect_token or not hmac.compare_digest(token, x_interconnect_token):
        raise HTTPException(status_code=401, detail="互连令牌校验失败")


# ------------------------------ 契约端点 ------------------------------
@router.get("/health", summary="互连健康检查 (契约端点, 免鉴权)")
def interconnect_health():
    from backend.services.interconnect.identity import get_identity
    return {
        "status": "ok",
        "product": PRODUCT,
        "version": os.environ.get("TIANJUN_APP_VERSION", ""),
        "contract": CONTRACT_VERSION,
        **get_identity(),
    }


@router.post("/models/push", summary="接收训练平台推送的 .yvmodel 模型包 (契约端点)",
             dependencies=[Depends(_require_token)])
async def push_model_package(
    package: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    from backend.services.interconnect.package_ingest import (
        PackageConflict, PackageError, ingest_package,
    )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".yvmodel", delete=False) as tmp:
            tmp_path = tmp.name
            shutil.copyfileobj(package.file, tmp)
        try:
            result = ingest_package(tmp_path, db)
        except PackageError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except PackageConflict as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(e),
                    "model_id": e.model_id,
                    "hint": "同名同版本已存在, 请升版本号后重推 (不覆盖)",
                },
            ) from e
        print(f"[Interconnect] 收到训练平台模型: {result['model_name']} "
              f"v{result['version']} (model_id={result['model_id']}, "
              f"project_matched={result['project_matched']})")
        return result
    finally:
        package.file.close()
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ------------------------------ 管理端点 ------------------------------
class InterconnectConfigBody(BaseModel):
    enabled: Optional[bool] = None
    platform_url: Optional[str] = None
    token: Optional[str] = None
    device_name: Optional[str] = None
    sampling: Optional[Dict[str, Any]] = None
    queue: Optional[Dict[str, Any]] = None
    model_pull: Optional[Dict[str, Any]] = None


@router.get("/config", summary="读取互连配置")
def get_interconnect_config():
    return icfg.get_config()


@router.put("/config", summary="保存互连配置")
def save_interconnect_config(body: InterconnectConfigBody):
    data = body.model_dump(exclude_unset=True, exclude_none=True)
    sampling = data.get("sampling") or {}
    if sampling:
        try:
            lo = float(sampling.get("conf_min", 0.0))
            hi = float(sampling.get("conf_max", 1.0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="置信度带必须是数字") from None
        if not (0.0 <= lo < hi <= 1.0):
            raise HTTPException(
                status_code=400,
                detail="置信度带无效: 需要 0 ≤ conf_min < conf_max ≤ 1")
    url = (data.get("platform_url") or "").strip()
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400, detail="平台地址必须以 http:// 或 https:// 开头")
    return icfg.save_config(data)


@router.get("/status", summary="互连运行状态 (队列/上传/采样/拉取/设备身份)")
def interconnect_status():
    from backend.services.interconnect import puller
    from backend.services.interconnect.identity import get_identity
    cfg = icfg.get_config()
    return {
        "enabled": cfg.get("enabled"),
        "platform_url": cfg.get("platform_url"),
        "token_configured": bool((cfg.get("token") or "").strip()),
        "sampling_active": icfg.is_sampling_active(),
        **get_identity(),
        "uploader": uploader.get_status(),
        "puller": puller.get_status(),
    }


@router.post("/test-connection", summary="探活训练平台 health 端点")
def test_connection(body: Optional[Dict[str, Any]] = None):
    url = ((body or {}).get("platform_url")
           or icfg.get_config().get("platform_url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="未配置训练平台地址")
    return uploader.test_peer(url)


@router.get("/samples/recent", summary="最近采样回传流水")
def recent_samples(limit: int = 50):
    return {"items": uploader.get_queue().log_recent(limit=min(limit, 200))}


@router.post("/pull-now", summary="立即向训练平台拉取一轮新模型包")
def pull_now():
    from backend.services.interconnect import puller
    cfg = icfg.get_config()
    if not cfg.get("enabled"):
        raise HTTPException(status_code=403, detail="互连未启用")
    if not (cfg.get("platform_url") or "").strip():
        raise HTTPException(status_code=400, detail="未配置训练平台地址")
    return puller.pull_now()
