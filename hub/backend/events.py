"""事件中心读写面 (P0-10) — 全厂 NG 事件聚合列表 + 处理登记。

数据源: poller._pull_events 按游标从各边缘拉 NG 周期落 hub_events。
"处理" (ack) 是枢纽侧的工作流登记 (谁看过/处理了这条 NG), 不下发边缘 ——
边缘工位报警器的物理消警走 ops 网关的 ack_alarm 动作, 两者语义不同。
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hub.backend.audit import write_audit
from hub.backend.auth import HubUser, require_perm
from hub.backend.db import get_db
from hub.backend.models import HubEvent, HubNode

router = APIRouter()


class EventRow(BaseModel):
    id: int
    node_id: int
    node_name: Optional[str] = None
    channel_id: int
    kind: str
    result: Optional[str]
    event_name: Optional[str]
    reason: Optional[str]
    ts: Optional[str]
    acked_by: Optional[str]
    acked_at: Optional[datetime]

    class Config:
        from_attributes = True


class EventListResponse(BaseModel):
    total: int
    unacked: int
    items: list[EventRow]


class EventAckResponse(BaseModel):
    ok: bool
    acked_by: str


@router.get("/events", response_model=EventListResponse,
            summary="全厂 NG 事件聚合查询 (报警中心)",
            dependencies=[Depends(require_perm("wall.view"))])
def list_events(node_id: Optional[int] = None,
                channel_id: Optional[int] = None,
                unacked_only: bool = False,
                limit: int = 50, offset: int = 0,
                db: Session = Depends(get_db)):
    """时间倒序分页; unacked 计数供墙顶栏徽标轮询。"""
    limit = max(1, min(int(limit), 500))
    q = db.query(HubEvent)
    if node_id is not None:
        q = q.filter(HubEvent.node_id == node_id)
    if channel_id is not None:
        q = q.filter(HubEvent.channel_id == channel_id)
    unacked = q.filter(HubEvent.acked_by.is_(None)).count()
    if unacked_only:
        q = q.filter(HubEvent.acked_by.is_(None))
    total = q.count()
    rows = q.order_by(HubEvent.id.desc()).offset(offset).limit(limit).all()

    names = {n.id: n.name for n in db.query(HubNode).all()}
    items = []
    for r in rows:
        row = EventRow.model_validate(r)
        row.node_name = names.get(r.node_id)
        items.append(row)
    return {"total": total, "unacked": unacked, "items": items}


@router.post("/events/{event_id}/ack", response_model=EventAckResponse,
             summary="登记 NG 事件已处理 (枢纽工作流, 不下发边缘)")
def ack_event(event_id: int, db: Session = Depends(get_db),
              user: HubUser = Depends(require_perm("ops.execute"))):
    """幂等: 已处理的事件重复 ack 保留首个处理人 (先到先记, 审计可追)。"""
    ev = db.query(HubEvent).filter(HubEvent.id == event_id).first()
    if not ev:
        raise HTTPException(404, "事件不存在")
    if ev.acked_by:
        return {"ok": True, "acked_by": ev.acked_by}
    ev.acked_by = user.username
    ev.acked_at = datetime.now()
    write_audit(db, username=user.username, action="event.ack",
                node_id=ev.node_id, channel_id=ev.channel_id,
                new_value={"event_id": ev.id, "edge_event_id": ev.edge_event_id,
                           "event_name": ev.event_name})
    db.commit()
    return {"ok": True, "acked_by": user.username}