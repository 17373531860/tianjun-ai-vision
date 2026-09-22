"""设备孪生存储 (RFC 15 §4.2) — M1 只做 reported 侧。

运行态以内存 (poller.NodeRuntime) 为准; 本模块负责 DB 落底:
reported 有实际变化才写库 + version++ (2s 轮询高频, 无变化不打 DB)。
desired 写入与收敛循环随 M3 op_gateway 落地。
"""
from datetime import datetime
from typing import Any, Dict

from sqlalchemy.orm import Session

from hub.backend.models import HubTwin


def update_reported(db: Session, node_id: int, channel_id: int,
                    reported: Dict[str, Any]) -> bool:
    """upsert 工位孪生 reported。返回是否发生了变化 (调用方据此决定 commit)。"""
    twin = db.query(HubTwin).filter(
        HubTwin.node_id == node_id,
        HubTwin.channel_id == channel_id).first()
    if twin is None:
        db.add(HubTwin(node_id=node_id, channel_id=channel_id,
                       desired={}, reported=reported,
                       version=1, reported_at=datetime.now()))
        return True
    if twin.reported == reported:
        return False
    twin.reported = reported
    twin.version = (twin.version or 0) + 1
    twin.reported_at = datetime.now()
    return True


def set_desired(db: Session, node_id: int, channel_id: int,
                patch: Dict[str, Any]) -> None:
    """merge 写 desired (M3 op_gateway 专用; 白名单校验在网关, 这里只管落库)。

    不 commit —— 与操作审计同事务, 由网关统一提交。
    """
    twin = db.query(HubTwin).filter(
        HubTwin.node_id == node_id,
        HubTwin.channel_id == channel_id).first()
    if twin is None:
        twin = HubTwin(node_id=node_id, channel_id=channel_id,
                       desired={}, reported={}, version=0)
        db.add(twin)
    merged = dict(twin.desired or {})
    merged.update(patch)
    twin.desired = merged


def get_twins(db: Session, node_id: int) -> list:
    return (db.query(HubTwin)
            .filter(HubTwin.node_id == node_id)
            .order_by(HubTwin.channel_id).all())
