"""
FastAPI 鉴权依赖 — get_current_user / require_perm / require_role

三种身份:
  SUPERUSER         auth_enabled=false 时全员伪装最高管理员 (出厂默认)
  匿名 operator     auth_enabled=true 但请求无 token → 操作员权限
  真实登录用户      auth_enabled=true 且 Bearer token 有效 → 装载真实角色 + 权限并集

关键不变量:
  - auth_enabled=false 时 → 不读 Authorization header, 直接放行最高权限
  - auth_enabled=true 时 → 默认权限 = operator 角色 (不是全拒绝)
  - 任何端点不标 require_perm → 任何人 (含匿名 operator) 都能调
"""
from dataclasses import dataclass
from typing import List, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.core.auth import resolve_token_cached
from backend.core.permissions import BUILTIN_ROLES, match_permission
from backend.db.database import get_db
from backend.models.auth_models import User
from backend.models.models import SystemConfig


# ============================================================
# auth_enabled 总开关 + allow_anonymous_operator 匿名兜底开关
# (SystemConfig KV 缓存)
# ============================================================

_AUTH_ENABLED_KEY = "auth.enabled"
_ALLOW_ANON_KEY = "auth.allow_anonymous_operator"
_SESSION_PERSIST_KEY = "auth.session_persist"
_auth_enabled_cache: Optional[bool] = None
_allow_anon_cache: Optional[bool] = None
_session_persist_cache: Optional[bool] = None


def _read_bool_config(db: Session, key: str, default: bool) -> bool:
    """从 system_configs 读 bool 配置, 未配置时返回 default"""
    cfg = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if not cfg or cfg.value is None:
        return default
    return str(cfg.value).strip().lower() in ("1", "true", "yes", "on")


def _read_auth_enabled(db: Session) -> bool:
    return _read_bool_config(db, _AUTH_ENABLED_KEY, default=False)


def _read_allow_anon(db: Session) -> bool:
    # 默认 true: 保持 v3.10.0 老行为, 启用鉴权后未登录仍可受限只读
    return _read_bool_config(db, _ALLOW_ANON_KEY, default=True)


def _read_session_persist(db: Session) -> bool:
    # 默认 true: 重启后保持登录状态 (token 落盘 + 前端用 localStorage)
    return _read_bool_config(db, _SESSION_PERSIST_KEY, default=True)


def is_auth_enabled(db: Session) -> bool:
    """获取 auth_enabled, 带模块级缓存避免每个请求都查表.

    写入开关时必须调 invalidate_auth_enabled_cache() 让缓存失效.
    """
    global _auth_enabled_cache
    if _auth_enabled_cache is None:
        _auth_enabled_cache = _read_auth_enabled(db)
    return _auth_enabled_cache


def is_anonymous_operator_allowed(db: Session) -> bool:
    """获取 allow_anonymous_operator (匿名兜底开关), 带模块级缓存.

    True (默认) → 未登录请求被当作 operator 受限处理
    False        → 未登录请求被 401 拒绝, 严格强制登录
    """
    global _allow_anon_cache
    if _allow_anon_cache is None:
        _allow_anon_cache = _read_allow_anon(db)
    return _allow_anon_cache


def is_session_persist_enabled(db: Session) -> bool:
    """获取 session_persist (重启后保持登录开关), 带模块级缓存.

    True (默认) → 登录后 token 落盘 + 前端 localStorage, 重启自动恢复
    False        → token 仅内存 + 前端 sessionStorage, 关 tab/重启即失效
    """
    global _session_persist_cache
    if _session_persist_cache is None:
        _session_persist_cache = _read_session_persist(db)
    return _session_persist_cache


def invalidate_auth_enabled_cache() -> None:
    """让 auth_enabled 缓存失效, 下次重新读 DB"""
    global _auth_enabled_cache
    _auth_enabled_cache = None


def invalidate_allow_anon_cache() -> None:
    """让 allow_anonymous_operator 缓存失效, 下次重新读 DB"""
    global _allow_anon_cache
    _allow_anon_cache = None


def invalidate_session_persist_cache() -> None:
    """让 session_persist 缓存失效, 下次重新读 DB"""
    global _session_persist_cache
    _session_persist_cache = None


def _upsert_bool_config(db: Session, key: str, value: bool, desc: str) -> None:
    cfg = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    value_str = "true" if value else "false"
    if cfg:
        cfg.value = value_str
    else:
        db.add(SystemConfig(key=key, value=value_str, description=desc))


def set_auth_enabled(db: Session, enabled: bool) -> None:
    """写入 auth_enabled (调用方负责 commit + 缓存失效)"""
    _upsert_bool_config(
        db, _AUTH_ENABLED_KEY, enabled,
        desc="用户系统总开关; 关闭时所有 API 视为最高管理员可调",
    )
    invalidate_auth_enabled_cache()


def set_allow_anonymous_operator(db: Session, allowed: bool) -> None:
    """写入 allow_anonymous_operator (调用方负责 commit + 缓存失效)"""
    _upsert_bool_config(
        db, _ALLOW_ANON_KEY, allowed,
        desc="匿名 operator 兜底; True=未登录视为受限 operator, False=未登录直接 401",
    )
    invalidate_allow_anon_cache()


def set_session_persist(db: Session, enabled: bool) -> None:
    """写入 session_persist (调用方负责 commit + 缓存失效)"""
    _upsert_bool_config(
        db, _SESSION_PERSIST_KEY, enabled,
        desc="重启后保持登录; True=token 落盘自动恢复, False=关闭即失效需重登",
    )
    invalidate_session_persist_cache()


# ============================================================
# 当前用户结构
# ============================================================

@dataclass
class CurrentUser:
    """FastAPI 依赖返回的"当前用户"结构, 兼容三种身份:

    身份                          id    username        roles          is_anonymous  is_superuser
    --------------------------    --    ------------    -----------    ------------  ------------
    SUPERUSER (开关关)            None  __system__      [admin]        False         True
    匿名 operator (开关开/未登录) None  __anonymous__   [operator]     True          False
    真实登录用户                  int   <username>      [<codes>]      False         <由 perm 判定>
    """
    id: Optional[int]
    username: str
    display_name: str
    roles: List[str]
    permissions: List[str]
    is_anonymous: bool
    is_superuser: bool


_SUPERUSER = CurrentUser(
    id=None,
    username="__system__",
    display_name="系统超级管理员 (鉴权未启用)",
    roles=["admin"],
    permissions=["*"],
    is_anonymous=False,
    is_superuser=True,
)


def _anonymous_operator() -> CurrentUser:
    """每次新建, 避免共享 list 被外部修改"""
    return CurrentUser(
        id=None,
        username="__anonymous__",
        display_name="操作员 (未登录)",
        roles=["operator"],
        permissions=list(BUILTIN_ROLES["operator"]["permissions"]),
        is_anonymous=True,
        is_superuser=False,
    )


def _resolve_real_user(db: Session, user_id: int) -> Optional[CurrentUser]:
    """根据 user_id 查 DB, 装载真实用户的 roles + permissions 合集"""
    user = db.query(User).filter(
        User.id == user_id,
        User.active == True,  # noqa: E712
    ).first()
    if not user:
        return None

    role_objs = [ur.role for ur in user.user_roles if ur.role]
    role_codes = [r.code for r in role_objs]
    permissions: List[str] = []
    for r in role_objs:
        for p in (r.permissions or []):
            if p and p not in permissions:
                permissions.append(p)

    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        roles=role_codes,
        permissions=permissions,
        is_anonymous=False,
        is_superuser=("*" in permissions),
    )


# ============================================================
# 主依赖: get_current_user
# ============================================================

def _extract_bearer_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return None


def get_current_user(request: Request,
                     db: Session = Depends(get_db)) -> CurrentUser:
    """FastAPI 依赖: 解析当前用户身份.

    流程:
      1. auth_enabled=false → SUPERUSER (全放行, 与现有客户体验零差异)
      2. 提取 Authorization: Bearer <token>
      3. token 命中内存缓存 + DB 用户活跃 → 真实用户
      4. 无 token / token 无效 / 用户已禁用:
         - allow_anonymous_operator=true  → 匿名 operator (受限只读 + 3 按钮)
         - allow_anonymous_operator=false → 401 强制登录
    """
    if not is_auth_enabled(db):
        return _SUPERUSER

    token = _extract_bearer_token(request)
    if token:
        user_id = resolve_token_cached(token)
        if user_id is not None:
            real = _resolve_real_user(db, user_id)
            if real:
                return real

    if is_anonymous_operator_allowed(db):
        return _anonymous_operator()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录; 系统已关闭匿名 operator 兜底, 请先登录",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ============================================================
# 权限依赖工厂
# ============================================================

def require_perm(*required_perms: str):
    """生成一个 FastAPI 依赖: 检查当前用户是否拥有 required_perms 中的任一项 (OR).

    用法:
      @router.post("/projects",
                   dependencies=[Depends(require_perm("project.create"))])
      def create_project(...): ...

    auth_enabled=false 时 SUPERUSER permissions=['*'] 永远 match, 等同关闭鉴权.
    """
    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not required_perms:
            return user
        for perm in required_perms:
            if match_permission(perm, user.permissions):
                return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"权限不足 (需要: {', '.join(required_perms)}; "
                f"当前角色: {', '.join(user.roles) or '匿名'})"
            ),
        )
    return _dep


def require_role(*required_roles: str):
    """生成一个 FastAPI 依赖: 检查当前用户是否拥有 required_roles 中的任一角色 (OR).

    is_superuser=True 视为永远 match (admin 角色或开关关闭时).
    """
    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.is_superuser:
            return user
        for role in required_roles:
            if role in user.roles:
                return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"角色不足 (需要: {', '.join(required_roles)}; "
                f"当前角色: {', '.join(user.roles) or '匿名'})"
            ),
        )
    return _dep


def require_login(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """要求"非匿名"身份 (登录用户或 SUPERUSER); 匿名 operator 被拒.

    用法: 改自己密码、查 me 详情等需要"真实身份"的端点.
    """
    if user.is_anonymous:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="此操作需要登录",
        )
    return user
