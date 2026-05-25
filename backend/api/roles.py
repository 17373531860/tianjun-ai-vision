"""
角色管理 API — 由 system.roles.manage 权限保护

路由前缀: /api/v1/roles

端点列表:
  GET    /             列出所有角色
  POST   /             新建自定义角色
  GET    /{id}         查角色详情
  PUT    /{id}         更新角色 (name / description / permissions)
  DELETE /{id}         删除角色 (内置三角色禁删)

约束:
  - is_builtin=True 的三个内置角色 (admin/engineer/operator) 不可删, 不可改 code
  - 内置角色的 permissions 可以改 (客户可调整 engineer 默认权限)
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.auth_models import Role, UserRole


router = APIRouter(
    prefix="/roles",
    tags=["Roles"],
    dependencies=[Depends(require_perm("system.roles.manage"))],
)


# ============================================================
# Schema
# ============================================================

class RoleCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=255)
    permissions: List[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=255)
    permissions: Optional[List[str]] = None
    # 注意: code 与 is_builtin 一旦创建不允许改


# ============================================================
# Helper
# ============================================================

def _serialize(role: Role) -> dict:
    return {
        "id": role.id,
        "code": role.code,
        "name": role.name,
        "description": role.description,
        "permissions": role.permissions or [],
        "is_builtin": role.is_builtin,
        "created_at": role.created_at.isoformat() if role.created_at else None,
        "updated_at": role.updated_at.isoformat() if role.updated_at else None,
    }


# ============================================================
# 端点
# ============================================================

@router.get("")
def list_roles(db: Session = Depends(get_db)):
    roles = db.query(Role).order_by(Role.id).all()
    return [_serialize(r) for r in roles]


@router.get("/{role_id}")
def get_role(role_id: int, db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(404, "角色不存在")
    return _serialize(role)


@router.post("")
def create_role(body: RoleCreate, db: Session = Depends(get_db)):
    if db.query(Role).filter(Role.code == body.code).first():
        raise HTTPException(400, f"角色代码 {body.code} 已存在")
    role = Role(
        code=body.code,
        name=body.name,
        description=body.description,
        permissions=body.permissions or [],
        is_builtin=False,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return _serialize(role)


@router.put("/{role_id}")
def update_role(role_id: int, body: RoleUpdate, db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(404, "角色不存在")

    data = body.model_dump(exclude_unset=True)
    for field, val in data.items():
        if hasattr(role, field):
            setattr(role, field, val)

    db.commit()
    db.refresh(role)
    return _serialize(role)


@router.delete("/{role_id}")
def delete_role(role_id: int, db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(404, "角色不存在")
    if role.is_builtin:
        raise HTTPException(400, f"内置角色 {role.code} 不可删除")

    # 删之前看看有没有用户挂在这个角色上
    bound = db.query(UserRole).filter(UserRole.role_id == role.id).count()
    if bound > 0:
        raise HTTPException(400, f"该角色还有 {bound} 个用户在用, 请先把他们的角色换掉")

    db.delete(role)
    db.commit()
    return {"success": True}
