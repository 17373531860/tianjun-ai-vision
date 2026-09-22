"""Fleet Hub 审计 — 写入 helper + 查询端点 (只读, 只增不改)。

纪律 (RFC 15 §4.3): 每条写操作必有审计行; M3 op_gateway 收口后
"审计被内部调用绕过"的风险靠单一写出口治理。
"""
import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hub.backend.auth import require_perm
from hub.backend.db import get_db
from hub.backend.models import HubAuditLog

router = APIRouter()


def write_audit(db: Session, *, username: Optional[str], action: str,
                node_id: Optional[int] = None, channel_id: Optional[int] = None,
                old_value: Any = None, new_value: Any = None,
                source_ip: Optional[str] = None, result: str = "ok") -> None:
    """追加一条审计 (不 commit, 跟调用方事务走; 值非字符串时 JSON 序列化)"""
    def _s(v):
        if v is None or isinstance(v, str):
            return v
        return json.dumps(v, ensure_ascii=False, default=str)

    db.add(HubAuditLog(
        username=username, action=action, node_id=node_id,
        channel_id=channel_id, old_value=_s(old_value), new_value=_s(new_value),
        source_ip=source_ip, result=result, created_at=datetime.now(),
    ))


class AuditRow(BaseModel):
    id: int
    username: Optional[str]
    node_id: Optional[int]
    channel_id: Optional[int]
    action: str
    old_value: Optional[str]
    new_value: Optional[str]
    source_ip: Optional[str]
    result: str
    created_at: datetime

    class Config:
        from_attributes = True


class AuditListResponse(BaseModel):
    total: int
    items: list[AuditRow]


@router.get("/audit", response_model=AuditListResponse, summary="审计查询",
            dependencies=[Depends(require_perm("audit.view"))])
def list_audit(node_id: Optional[int] = None,
               action: Optional[str] = None,
               limit: int = Query(100, le=1000),
               offset: int = 0,
               db: Session = Depends(get_db)):
    """按 节点/动作 筛选, 时间倒序分页。"""
    q = db.query(HubAuditLog)
    if node_id is not None:
        q = q.filter(HubAuditLog.node_id == node_id)
    if action:
        q = q.filter(HubAuditLog.action == action)
    total = q.count()
    rows = q.order_by(HubAuditLog.id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": rows}
