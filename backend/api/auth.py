"""
鉴权 API — 登录 / 登出 / 当前用户 / 启用-关闭 / 修改密码

路由前缀: /api/v1/auth

端点列表:
  GET  /status                  前端启动调, 看账号鉴权是否启用 + 已有几个用户
  GET  /me                      当前身份 + 角色 + 权限 (用于前端渲染菜单)
  POST /login                   用户名密码登录, 返回 token
  POST /logout                  登出, 删 token
  POST /enable-auth             启用账号鉴权 (同时强制创建首个超级管理员)
  POST /disable-auth            关闭账号鉴权 (需 system.auth_toggle 权限)
  POST /change-password         改自己的密码
  GET  /permissions/catalog     权限目录 (给账号管理页渲染权限树)
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.auth import (
    cache_token,
    delete_all_tokens_for_user,
    delete_token,
    generate_token,
    hash_password,
    persist_token,
    set_current_active_user,
    verify_password,
)
from backend.core.auth_deps import (
    CurrentUser,
    get_current_user,
    is_anonymous_operator_allowed,
    is_auth_enabled,
    is_session_persist_enabled,
    require_login,
    require_perm,
    set_allow_anonymous_operator,
    set_auth_enabled,
    set_session_persist,
)
from backend.core.permissions import get_permission_catalog
from backend.db.database import get_db
from backend.models.auth_models import Role, User, UserRole


router = APIRouter(prefix="/auth", tags=["Auth"])


# ============================================================
# Pydantic Schema
# ============================================================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)


class EnableAuthRequest(BaseModel):
    """启用账号鉴权 — 必须同时创建第一个超级管理员"""
    admin_username: str = Field(..., min_length=3, max_length=64)
    admin_password: str = Field(..., min_length=6)
    admin_display_name: Optional[str] = Field(None, max_length=64)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6)


# ============================================================
# Serializer
# ============================================================

def _user_to_dict(user: User) -> dict:
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
    }


def _current_user_to_dict(user: CurrentUser) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "roles": user.roles,
        "permissions": user.permissions,
        "is_anonymous": user.is_anonymous,
        "is_superuser": user.is_superuser,
    }


# ============================================================
# 端点
# ============================================================

@router.get("/status")
def get_auth_status(db: Session = Depends(get_db)):
    """前端启动调: 看账号鉴权是否启用, 已有几个用户, 是否允许匿名 operator.

    根据返回决定:
      auth_enabled=false                       → 旧体验 (任何人最高权限)
      auth_enabled=true + allow_anon=true      → 未登录 = 匿名 operator (默认)
      auth_enabled=true + allow_anon=false     → 未登录 = 必须先去登录页
      session_persist                          → 前端 token 持久化策略 (true=localStorage)
    """
    enabled = is_auth_enabled(db)
    user_count = db.query(User).count() if enabled else 0
    return {
        "auth_enabled": enabled,
        "user_count": user_count,
        "allow_anonymous_operator": is_anonymous_operator_allowed(db),
        "session_persist": is_session_persist_enabled(db),
    }


@router.get("/me")
def get_me(user: CurrentUser = Depends(get_current_user),
           db: Session = Depends(get_db)) -> dict:
    """当前身份 + 权限 (前端按 user.permissions 决定渲染哪些菜单)"""
    return {
        "auth_enabled": is_auth_enabled(db),
        "user": _current_user_to_dict(user),
    }


# ============================================================
# /config — 鉴权高级配置 (目前仅 1 项: 匿名 operator 兜底开关)
# ============================================================

class AuthConfigUpdate(BaseModel):
    """鉴权高级配置 - 字段全 Optional, 仅更新提供的字段"""
    allow_anonymous_operator: Optional[bool] = None
    session_persist: Optional[bool] = None


@router.get("/config")
def get_auth_config(db: Session = Depends(get_db)) -> dict:
    """读鉴权高级配置 - 任何人可读 (用于前端 AuthPanel 显示当前状态).

    返回:
      auth_enabled                  : 总开关
      allow_anonymous_operator      : 匿名兜底开关 (默认 true)
      session_persist               : 重启后保持登录开关 (默认 true)
    """
    return {
        "auth_enabled": is_auth_enabled(db),
        "allow_anonymous_operator": is_anonymous_operator_allowed(db),
        "session_persist": is_session_persist_enabled(db),
    }


@router.put("/config",
            dependencies=[Depends(require_perm("system.auth_toggle"))])
def put_auth_config(body: AuthConfigUpdate, db: Session = Depends(get_db)) -> dict:
    """写鉴权高级配置 - 需 system.auth_toggle 权限 (admin only).

    业务约束:
      - auth_enabled=false 时也可改这些字段 (预先配置)
      - 一次只改提供的字段, 不动其他
    """
    data = body.model_dump(exclude_unset=True)
    changed = []
    if "allow_anonymous_operator" in data:
        set_allow_anonymous_operator(db, bool(data["allow_anonymous_operator"]))
        changed.append("allow_anonymous_operator")
    if "session_persist" in data:
        set_session_persist(db, bool(data["session_persist"]))
        changed.append("session_persist")
    if changed:
        db.commit()
    return {
        "success": True,
        "changed": changed,
        "auth_enabled": is_auth_enabled(db),
        "allow_anonymous_operator": is_anonymous_operator_allowed(db),
        "session_persist": is_session_persist_enabled(db),
    }


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """用户名密码登录 — 仅 auth_enabled=true 时有效"""
    if not is_auth_enabled(db):
        raise HTTPException(400, "账号鉴权系统未启用, 无需登录")

    user = db.query(User).filter(
        User.username == body.username,
        User.active == True,  # noqa: E712
    ).first()
    if not user or not verify_password(body.password, user.password_hash):
        # 故意统一报错信息, 不泄露"用户名是否存在"
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")

    token = generate_token()
    cache_token(token, user.id)
    # v3.10+ 阶段 7+: 按 session_persist 决定是否把 token 落盘
    # True (默认) → 落盘, 重启自动恢复
    # False         → 仅内存, 关后端进程即失效, 需要重新登录
    persist_enabled = is_session_persist_enabled(db)
    if persist_enabled:
        persist_token(token, user.id)

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    # v3.10+ 阶段 4: 记录"当前活跃用户" (落盘), 给检测主循环写 cycle.operator_id 用
    set_current_active_user(user.id)

    user_dict = _user_to_dict(user)
    return {
        "token": token,
        "user": user_dict,
        "permissions": user_dict["permissions"],
        # 给前端用: 是否要把 token 存 localStorage (true) 还是 sessionStorage (false)
        "session_persist": persist_enabled,
    }


@router.post("/logout")
def logout(request: Request):
    """登出 — 删当前 token (匿名调也无害, 静默成功)"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            delete_token(token)
    # v3.10+ 阶段 4: 清空"当前活跃用户" 落盘 (任何 logout 都清, 防遗留)
    set_current_active_user(None)
    return {"success": True}


@router.post("/enable-auth")
def enable_auth(body: EnableAuthRequest, db: Session = Depends(get_db)):
    """启用账号鉴权系统 — 同时强制创建第一个超级管理员.

    重要约束:
    - 此端点不能加 require_perm("system.auth_toggle"), 因为启用前
      auth_enabled=false 任何人都是 SUPERUSER, 已经全放行;
      但启用后再开关需要走 disable-auth (那里需要权限)
    - 已启用时拒绝重复启用 (防误操作)
    """
    if is_auth_enabled(db):
        raise HTTPException(400, "账号鉴权系统已启用; 如需重新创建首个管理员, 请先关闭再启用")

    if db.query(User).filter(User.username == body.admin_username).first():
        raise HTTPException(400, f"用户名 {body.admin_username} 已存在")

    admin_role = db.query(Role).filter(Role.code == "admin").first()
    if not admin_role:
        raise HTTPException(500, "内置 admin 角色未初始化; 检查后端启动种子流程")

    new_admin = User(
        username=body.admin_username,
        password_hash=hash_password(body.admin_password),
        display_name=body.admin_display_name or body.admin_username,
        active=True,
        # 客户自己设的密码, 不强制改
        must_change_password=False,
    )
    db.add(new_admin)
    db.flush()  # 拿到 id

    db.add(UserRole(user_id=new_admin.id, role_id=admin_role.id))
    set_auth_enabled(db, True)
    db.commit()
    db.refresh(new_admin)

    return {
        "success": True,
        "auth_enabled": True,
        "admin": _user_to_dict(new_admin),
    }


@router.post("/disable-auth",
             dependencies=[Depends(require_perm("system.auth_toggle"))])
def disable_auth(db: Session = Depends(get_db)):
    """关闭账号鉴权系统 — 关后所有人变回最高管理员; 账号数据保留.

    幂等: 已关闭再调返回 noop=True.
    """
    if not is_auth_enabled(db):
        return {"success": True, "auth_enabled": False, "noop": True}
    set_auth_enabled(db, False)
    db.commit()
    # v3.10+ 阶段 4: 关闭鉴权 = 所有人变最高管理员; 当前活跃用户语义失效, 落盘清空
    set_current_active_user(None)
    return {"success": True, "auth_enabled": False}


@router.get("/permissions/catalog")
def list_permission_catalog():
    """权限目录 — 给账号管理页渲染勾选树用"""
    return get_permission_catalog()


@router.post("/change-password")
def change_password(body: ChangePasswordRequest,
                    db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_login)):
    """改自己的密码 — 必须是真实登录用户, 匿名拒绝.

    成功后会清掉本用户的所有 token (强制其他设备重新登录).
    """
    if user.id is None:
        raise HTTPException(400, "系统超级管理员无密码")

    db_user = db.query(User).filter(User.id == user.id).first()
    if not db_user:
        raise HTTPException(404, "用户不存在")

    if not verify_password(body.old_password, db_user.password_hash):
        raise HTTPException(401, "原密码错误")

    db_user.password_hash = hash_password(body.new_password)
    db_user.must_change_password = False
    db.commit()

    delete_all_tokens_for_user(user.id)
    return {"success": True, "message": "密码已修改, 请重新登录"}
