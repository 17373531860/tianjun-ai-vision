"""操作锁协议 (RFC 15 §10.1) — 写操作互斥的唯一裁决点。

协议要点:
  - 一工位一锁, TTL 默认 120s (config.LOCK_TTL_S), 过期锁视同无锁。
  - 写操作时无锁 → 自动 acquire (不打断操作流, 与主程序"点了就干"的现场习惯一致);
    持锁人再操作 → 自动续期; 他人持有效锁 → 409 (带持有人/剩余秒数, 前端明示)。
  - 夺锁 (steal) 是显式仲裁: 需要 lock.steal 权限 (director/admin), 必写审计。
  - 锁只管"枢纽侧多人互斥"; 边缘本机 UI 不感知锁 (边缘自治不变量) —— 本机操作员
    永远优先, 冲突由孪生 reported 差异暴露给枢纽用户。

对外两用法:
  1. ensure_lock(): op_gateway 每次写操作前调用 (自动 acquire/续期/409)。
  2. router 端点: 前端显式查看/获取/释放/夺锁。
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hub.backend.auth import HubUser, get_current_user, require_perm
from hub.backend.config import LOCK_TTL_S
from hub.backend.db import get_db
from hub.backend.models import HubStationLock

router = APIRouter()


def _now() -> datetime:
    return datetime.now()


def _get_lock(db: Session, node_id: int, channel_id: int) -> Optional[HubStationLock]:
    return db.query(HubStationLock).filter(
        HubStationLock.node_id == node_id,
        HubStationLock.channel_id == channel_id).first()


def _lock_view(lock: Optional[HubStationLock]) -> dict:
    """锁的对外形态; 过期锁按"无锁"报告 (协议语义, 不暴露尸体行)。"""
    if lock is None or lock.expires_at <= _now():
        return {"locked": False, "holder": None, "expires_in_s": None}
    return {
        "locked": True,
        "holder": lock.username,
        "expires_in_s": int((lock.expires_at - _now()).total_seconds()),
    }


def lock_view_for(db: Session, node_id: int, channel_id: int) -> dict:
    """给其他读面 (station_detail 等) 复用的锁状态视图。"""
    return _lock_view(_get_lock(db, node_id, channel_id))


def ensure_lock(db: Session, node_id: int, channel_id: int,
                user: HubUser, steal: bool = False) -> HubStationLock:
    """写操作前的锁裁决: 无锁/过期 → acquire; 本人 → 续期; 他人 → 409 (或夺锁)。

    steal=True 时抢占他人有效锁 —— 调用方必须先验 lock.steal 权限并写审计,
    本函数不重复验权 (单一职责: 只管锁状态机)。
    不 commit; 由调用方与业务写入同事务提交。
    """
    lock = _get_lock(db, node_id, channel_id)
    now = _now()
    expires = now + timedelta(seconds=LOCK_TTL_S)

    if lock is None:
        lock = HubStationLock(node_id=node_id, channel_id=channel_id,
                              username=user.username,
                              acquired_at=now, expires_at=expires)
        db.add(lock)
        return lock

    if lock.expires_at <= now or lock.username == user.username or steal:
        if lock.username != user.username:
            lock.acquired_at = now
        lock.username = user.username
        lock.expires_at = expires
        return lock

    remain = int((lock.expires_at - now).total_seconds())
    raise HTTPException(
        status_code=409,
        detail=f"工位已被 {lock.username} 锁定操作 (剩余 {remain}s); "
               f"可等待释放, 或由主任/管理员夺锁")


# ============================================================
# 端点 (前端显式锁操作)
# ============================================================

class LockResponse(BaseModel):
    locked: bool
    holder: Optional[str]
    expires_in_s: Optional[int]
    mine: bool = False


def _respond(lock_dict: dict, user: HubUser) -> dict:
    lock_dict["mine"] = lock_dict.get("holder") == user.username
    return lock_dict


@router.get("/nodes/{node_id}/stations/{channel_id}/lock",
            response_model=LockResponse, summary="查看工位操作锁状态")
def get_lock_status(node_id: int, channel_id: int,
                    db: Session = Depends(get_db),
                    user: HubUser = Depends(get_current_user)):
    """任何登录用户可查 (锁状态是墙面公共信息)。"""
    return _respond(_lock_view(_get_lock(db, node_id, channel_id)), user)


class AcquirePayload(BaseModel):
    steal: bool = False


@router.post("/nodes/{node_id}/stations/{channel_id}/lock",
             response_model=LockResponse, summary="获取/续期/夺取工位操作锁",
             dependencies=[Depends(require_perm("ops.execute"))])
def acquire_lock(node_id: int, channel_id: int, request: Request,
                 payload: AcquirePayload = None,
                 db: Session = Depends(get_db),
                 user: HubUser = Depends(get_current_user)):
    """steal=true 时需要 lock.steal 权限, 夺锁写审计 (RFC 15 §10.1 显式仲裁)。"""
    from hub.backend.audit import write_audit
    steal = bool(payload and payload.steal)
    if steal:
        from hub.backend.auth import ROLE_PERMS
        perms = ROLE_PERMS.get(user.role, set())
        if "*" not in perms and "lock.steal" not in perms:
            raise HTTPException(403, "夺锁需要 lock.steal 权限 (主任/管理员)")
        old = _get_lock(db, node_id, channel_id)
        old_holder = old.username if old and old.expires_at > _now() else None
        if old_holder and old_holder != user.username:
            write_audit(db, username=user.username, node_id=node_id,
                        channel_id=channel_id, action="lock.steal",
                        old_value=old_holder, new_value=user.username,
                        source_ip=request.client.host if request.client else None)
    lock = ensure_lock(db, node_id, channel_id, user, steal=steal)
    db.commit()
    return _respond(_lock_view(lock), user)


@router.delete("/nodes/{node_id}/stations/{channel_id}/lock",
               response_model=LockResponse, summary="释放工位操作锁 (仅本人)",
               dependencies=[Depends(require_perm("ops.execute"))])
def release_lock(node_id: int, channel_id: int,
                 db: Session = Depends(get_db),
                 user: HubUser = Depends(get_current_user)):
    """只放本人的锁; 他人的锁走夺锁端点 (语义分离, 防误触)。"""
    lock = _get_lock(db, node_id, channel_id)
    if lock and lock.username == user.username:
        db.delete(lock)
        db.commit()
    return _respond({"locked": False, "holder": None, "expires_in_s": None}, user)
