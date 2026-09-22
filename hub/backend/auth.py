"""Fleet Hub 认证与授权 — 强制登录 (无匿名档, 与边缘 auth 默认关不同)。

角色四档 (RFC 15 §4.3 三角色 + admin):
  admin    : 全权 (含用户管理 / 节点纳管)
  director : 主任 — 看墙 + 操作 + 夺锁 + 审计
  engineer : 工程师 — 看墙 + 操作
  operator : 操作员 — 只读看墙

权限点 M1 先立四个, M2/M3 按需扩:
  wall.view / node.manage / ops.execute / audit.view / lock.steal
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hub.backend.db import get_db
from hub.backend.models import HubToken, HubUser
from hub.backend.security import generate_token, hash_token, verify_password

router = APIRouter()

ROLE_PERMS = {
    "admin": {"*"},
    "director": {"wall.view", "ops.execute", "lock.steal", "audit.view"},
    "engineer": {"wall.view", "ops.execute"},
    "operator": {"wall.view"},
}


# ============================================================
# 依赖
# ============================================================

def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def get_current_user(request: Request,
                     db: Session = Depends(get_db)) -> HubUser:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录 (需要 Bearer token)")
    row = db.query(HubToken).filter(
        HubToken.token_hash == hash_token(token)).first()
    if not row or not row.user or not row.user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 无效或用户已禁用")
    return row.user


def require_perm(perm: str):
    def _dep(user: HubUser = Depends(get_current_user)) -> HubUser:
        perms = ROLE_PERMS.get(user.role, set())
        if "*" in perms or perm in perms:
            return user
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"权限不足 (需要: {perm}; 当前角色: {user.role})")
    return _dep


# ============================================================
# 端点
# ============================================================

class LoginPayload(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    display_name: Optional[str]
    role: str


@router.post("/auth/login", response_model=LoginResponse, summary="登录")
def login(payload: LoginPayload, request: Request,
          db: Session = Depends(get_db)):
    """校验口令签发 token。成败都写审计 (登录是安全事件)。"""
    from hub.backend.audit import write_audit

    user = db.query(HubUser).filter(HubUser.username == payload.username).first()
    src = request.client.host if request.client else None
    if not user or not user.active or not verify_password(
            payload.password, user.password_hash):
        write_audit(db, username=payload.username, action="auth.login",
                    source_ip=src, result="denied")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")

    token = generate_token()
    db.add(HubToken(token_hash=hash_token(token), user_id=user.id))
    write_audit(db, username=user.username, action="auth.login",
                source_ip=src, result="ok")
    db.commit()
    return {"token": token, "username": user.username,
            "display_name": user.display_name, "role": user.role}


class MeResponse(BaseModel):
    username: str
    display_name: Optional[str]
    role: str
    permissions: list


@router.get("/auth/me", response_model=MeResponse, summary="当前用户信息")
def me(user: HubUser = Depends(get_current_user)):
    """返回当前用户身份与权限集 (枢纽前端按此渲染入口)。"""
    return {"username": user.username, "display_name": user.display_name,
            "role": user.role,
            "permissions": sorted(ROLE_PERMS.get(user.role, set()))}


class OkResponse(BaseModel):
    ok: bool


@router.post("/auth/logout", response_model=OkResponse, summary="登出")
def logout(request: Request, db: Session = Depends(get_db),
           user: HubUser = Depends(get_current_user)):
    """吊销当前 token。"""
    token = _extract_token(request)
    db.query(HubToken).filter(
        HubToken.token_hash == hash_token(token)).delete()
    db.commit()
    return {"ok": True}


# ============================================================
# 用户管理 (M4 收尾: admin CRUD + 本人改密)
# ============================================================

class UserRow(BaseModel):
    id: int
    username: str
    display_name: Optional[str]
    role: str
    active: bool

    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    items: list[UserRow]


class UserCreatePayload(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None
    role: str = "operator"


class UserUpdatePayload(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None
    password: Optional[str] = None      # admin 重置密码


class ChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str


def _validate_role(role: str):
    if role not in ROLE_PERMS:
        raise HTTPException(400, f"未知角色: {role} (可选: {sorted(ROLE_PERMS)})")


def _validate_password(pwd: str):
    if not pwd or len(pwd) < 6:
        raise HTTPException(400, "密码至少 6 位")


@router.get("/users", response_model=UserListResponse, summary="用户列表",
            dependencies=[Depends(require_perm("node.manage"))])
def list_users(db: Session = Depends(get_db)):
    """admin 专属 (node.manage 只有 admin 有)。"""
    return {"items": db.query(HubUser).order_by(HubUser.id).all()}


@router.post("/users", response_model=UserRow, summary="新建用户",
             dependencies=[Depends(require_perm("node.manage"))])
def create_user(payload: UserCreatePayload, request: Request,
                db: Session = Depends(get_db),
                user: HubUser = Depends(get_current_user)):
    """新建即审计 (账号是安全资产)。"""
    from hub.backend.audit import write_audit
    from hub.backend.security import hash_password

    uname = payload.username.strip()
    if not uname:
        raise HTTPException(400, "用户名不能为空")
    _validate_role(payload.role)
    _validate_password(payload.password)
    if db.query(HubUser).filter(HubUser.username == uname).first():
        raise HTTPException(409, f"用户名已存在: {uname}")
    row = HubUser(username=uname, password_hash=hash_password(payload.password),
                  display_name=payload.display_name or uname,
                  role=payload.role, active=True)
    db.add(row)
    write_audit(db, username=user.username, action="user.create",
                new_value={"username": uname, "role": payload.role},
                source_ip=request.client.host if request.client else None)
    db.commit()
    db.refresh(row)
    return row


@router.put("/users/{user_id}", response_model=UserRow,
            summary="改用户 (角色/禁用/重置密码)",
            dependencies=[Depends(require_perm("node.manage"))])
def update_user(user_id: int, payload: UserUpdatePayload, request: Request,
                db: Session = Depends(get_db),
                user: HubUser = Depends(get_current_user)):
    """防呆: 不许把最后一个活跃 admin 禁用/降级 (锁死枢纽无人能管)。"""
    from hub.backend.audit import write_audit
    from hub.backend.security import hash_password

    row = db.query(HubUser).filter(HubUser.id == user_id).first()
    if not row:
        raise HTTPException(404, "用户不存在")

    demoting = ((payload.role is not None and payload.role != "admin")
                or payload.active is False)
    if row.role == "admin" and demoting:
        others = db.query(HubUser).filter(
            HubUser.role == "admin", HubUser.active == True,  # noqa: E712
            HubUser.id != row.id).count()
        if others == 0:
            raise HTTPException(409, "不能禁用/降级最后一个管理员")

    old = {"role": row.role, "active": row.active}
    if payload.role is not None:
        _validate_role(payload.role)
        row.role = payload.role
    if payload.active is not None:
        row.active = payload.active
        if payload.active is False:
            # 禁用即吊销全部 token (立刻失效, 不等过期)
            db.query(HubToken).filter(HubToken.user_id == row.id).delete()
    if payload.display_name is not None:
        row.display_name = payload.display_name
    if payload.password:
        _validate_password(payload.password)
        row.password_hash = hash_password(payload.password)
        db.query(HubToken).filter(HubToken.user_id == row.id).delete()

    write_audit(db, username=user.username, action="user.update",
                old_value=old,
                new_value={"target": row.username, "role": row.role,
                           "active": row.active,
                           "password_reset": bool(payload.password)},
                source_ip=request.client.host if request.client else None)
    db.commit()
    db.refresh(row)
    return row


@router.post("/auth/change-password", response_model=OkResponse,
             summary="本人改密 (验旧密, 吊销其他会话)")
def change_password(payload: ChangePasswordPayload, request: Request,
                    db: Session = Depends(get_db),
                    user: HubUser = Depends(get_current_user)):
    """改密后吊销本人全部 token (含当前) —— 前端引导重新登录。"""
    from hub.backend.audit import write_audit
    from hub.backend.security import hash_password

    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "原密码不正确")
    _validate_password(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    db.query(HubToken).filter(HubToken.user_id == user.id).delete()
    write_audit(db, username=user.username, action="user.change_password",
                source_ip=request.client.host if request.client else None)
    db.commit()
    return {"ok": True}


def seed_admin(db: Session) -> None:
    """无任何用户时播种 admin (口令取 HUB_ADMIN_PASSWORD 环境变量, 默认 admin123)。

    默认口令仅为开箱可用; 部署手册要求首日改掉 (M4 加首登强制改密)。
    """
    import os

    from hub.backend.security import hash_password
    if db.query(HubUser).count() > 0:
        return
    pwd = os.environ.get("HUB_ADMIN_PASSWORD", "admin123")
    db.add(HubUser(username="admin", password_hash=hash_password(pwd),
                   display_name="枢纽管理员", role="admin", active=True))
    db.commit()
    print("[Hub] 已播种默认管理员 admin (请尽快修改口令)")
