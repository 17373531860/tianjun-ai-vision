"""
M2M API Key 管理 API — 由 system.apikey.manage 权限保护.

路由前缀: /api/v1/api-keys

端点列表:
  GET    /              列出所有 key (仅 prefix + meta, 永远不返回明文)
  POST   /              创建 key (响应一次性返回明文 plaintext, 之后不可见)
  PUT    /{id}/toggle   启停切换
  DELETE /{id}          删除

与用户系统隔离: 这里只管 "M2M 凭证", 不涉及 User/Role.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.api_key import (
    extract_prefix,
    generate_api_key,
    hash_api_key,
    invalidate_cache,
)
from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.auth_models import APIKey


router = APIRouter(
    prefix="/api-keys",
    tags=["API Keys"],
    dependencies=[Depends(require_perm("system.apikey.manage"))],
)


# ============================================================
# Schema
# ============================================================

ALLOWED_SCOPES = {"*", "cluster", "mes.receive", "license.cache"}


class APIKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    scope: str = Field(..., description="允许: * / cluster / mes.receive / license.cache")
    description: Optional[str] = Field(None, max_length=255)
    expires_at: Optional[datetime] = None


class APIKeyResponse(BaseModel):
    id: int
    key_prefix: str
    name: str
    scope: str
    description: Optional[str]
    enabled: bool
    created_at: Optional[datetime]
    last_used_at: Optional[datetime]
    use_count: int
    expires_at: Optional[datetime]


class APIKeyCreateResponse(APIKeyResponse):
    # 仅创建时一次性返回明文; 之后再也拿不到
    plaintext: str


# ============================================================
# Helper
# ============================================================

def _serialize(k: APIKey) -> dict:
    return {
        "id": k.id,
        "key_prefix": k.key_prefix,
        "name": k.name,
        "scope": k.scope,
        "description": k.description,
        "enabled": bool(k.enabled),
        "created_at": k.created_at,
        "last_used_at": k.last_used_at,
        "use_count": int(k.use_count or 0),
        "expires_at": k.expires_at,
    }


# ============================================================
# 端点
# ============================================================

@router.get("", response_model=List[APIKeyResponse])
def list_keys(db: Session = Depends(get_db)):
    rows = db.query(APIKey).order_by(APIKey.id.desc()).all()
    return [_serialize(k) for k in rows]


@router.post("", response_model=APIKeyCreateResponse)
def create_key(body: APIKeyCreate, db: Session = Depends(get_db)):
    if body.scope not in ALLOWED_SCOPES:
        raise HTTPException(status_code=400,
                            detail=f"scope 必须为 {sorted(ALLOWED_SCOPES)}")

    plaintext = generate_api_key()
    k = APIKey(
        key_prefix=extract_prefix(plaintext),
        key_hash=hash_api_key(plaintext),
        name=body.name.strip(),
        scope=body.scope,
        description=(body.description or "").strip() or None,
        enabled=True,
        expires_at=body.expires_at,
    )
    db.add(k)
    db.commit()
    db.refresh(k)
    invalidate_cache()
    return {**_serialize(k), "plaintext": plaintext}


@router.put("/{key_id}/toggle", response_model=APIKeyResponse)
def toggle_key(key_id: int, db: Session = Depends(get_db)):
    k = db.query(APIKey).filter(APIKey.id == key_id).first()
    if not k:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    k.enabled = not bool(k.enabled)
    db.commit()
    db.refresh(k)
    invalidate_cache()
    return _serialize(k)


@router.delete("/{key_id}")
def delete_key(key_id: int, db: Session = Depends(get_db)):
    k = db.query(APIKey).filter(APIKey.id == key_id).first()
    if not k:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    db.delete(k)
    db.commit()
    invalidate_cache()
    return {"ok": True, "deleted": key_id}
