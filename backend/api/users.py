"""
账号管理 API — 由 system.users.manage 权限保护

路由前缀: /api/v1/users

端点列表:
  GET    /             列出所有账号
  POST   /             创建账号 (用户名 + 密码 + 角色)
  GET    /{id}         查账号详情
  PUT    /{id}         更新账号 (display_name / active / 重置密码 / 改角色)
  DELETE /{id}         删除账号 (硬删, 同时清掉 user_roles 和 session_tokens)
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.auth import delete_all_tokens_for_user, hash_password
from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.auth_models import Role, User, UserRole


router = APIRouter(
    prefix="/users",
    tags=["Users"],
    dependencies=[Depends(require_perm("system.users.manage"))],
)


# ============================================================
# Schema
# ============================================================

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6)
    display_name: Optional[str] = Field(None, max_length=64)
    role_codes: List[str] = Field(default_factory=list)
    active: bool = True
    must_change_password: bool = False


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=64)
    active: Optional[bool] = None
    must_change_password: Optional[bool] = None
    # 重置密码: 非空时覆盖
    new_password: Optional[str] = Field(None, min_length=6)
    # 完整替换角色列表 (None = 不动)
    role_codes: Optional[List[str]] = None


# ============================================================
# Helper
# ============================================================

def _serialize(user: User) -> dict:
    role_codes: List[str] = []
    permissions: List[str] = []
    for ur in user.user_roles:
        if not ur.role:
            continue
        if ur.role.code not in role_codes:
            role_codes.append(ur.role.code)
        for p in (ur.role.permissions or []):
            if p and p not in permissions:
                permissions.append(p)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "active": user.active,
        "must_change_password": user.must_change_password,
        "roles": role_codes,
        "permissions": permissions,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


def _resolve_roles(db: Session, role_codes: List[str]) -> List[Role]:
    """role_codes → Role 对象列表; 未知 code 抛 400"""
    if not role_codes:
        return []
    roles = db.query(Role).filter(Role.code.in_(role_codes)).all()
    found_codes = {r.code for r in roles}
    missing = [c for c in role_codes if c not in found_codes]
    if missing:
        raise HTTPException(400, f"未知角色代码: {', '.join(missing)}")
    return roles


# ============================================================
# 端点
# ============================================================

@router.get("")
def list_users(db: Session = Depends(get_db),
               active: Optional[bool] = None):
    q = db.query(User)
    if active is not None:
        q = q.filter(User.active == active)
    users = q.order_by(User.id).all()
    return [_serialize(u) for u in users]


@router.get("/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "账号不存在")
    return _serialize(user)


@router.post("")
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, f"用户名 {body.username} 已存在")

    roles = _resolve_roles(db, body.role_codes)

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.display_name or body.username,
        active=body.active,
        must_change_password=body.must_change_password,
    )
    db.add(user)
    db.flush()
    for r in roles:
        db.add(UserRole(user_id=user.id, role_id=r.id))
    db.commit()
    db.refresh(user)
    return _serialize(user)


@router.put("/{user_id}")
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "账号不存在")

    data = body.model_dump(exclude_unset=True)

    # 改密码
    if "new_password" in data and data["new_password"]:
        user.password_hash = hash_password(data["new_password"])
        # 改密后清光所有 token, 强制全部设备重新登录
        delete_all_tokens_for_user(user.id)
    data.pop("new_password", None)

    # 改角色 (完整替换)
    if "role_codes" in data and data["role_codes"] is not None:
        new_roles = _resolve_roles(db, data["role_codes"])
        db.query(UserRole).filter(UserRole.user_id == user.id).delete()
        for r in new_roles:
            db.add(UserRole(user_id=user.id, role_id=r.id))
    data.pop("role_codes", None)

    # 改普通字段
    for field, val in data.items():
        if hasattr(user, field):
            setattr(user, field, val)

    # 被禁用时同步清光 token
    if data.get("active") is False:
        delete_all_tokens_for_user(user.id)

    db.commit()
    db.refresh(user)
    return _serialize(user)


@router.delete("/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "账号不存在")
    delete_all_tokens_for_user(user.id)
    db.delete(user)
    db.commit()
    return {"success": True}
