"""监控墙读面 (RFC 15 §4.4 / §9.3) — M2 只读: 墙聚合 + 快照转发。

- GET /wall: 一次拉齐全部节点+工位+运行态+孪生+档案 properties + 异常队列
- GET/PUT /nodes/{id}/stations/{ch}: 下钻读面; PUT 只改枢纽本地显示名/分组
- GET /nodes/{id}/stations/{ch}/snapshot: 转发边缘 /snapshot, 全局信号量限并发 8
"""
import asyncio
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hub.backend.audit import write_audit
from hub.backend.auth import HubUser, require_perm
from hub.backend.config import HEALTH_INTERVAL_S, get_data_dir
from hub.backend.db import get_db
from hub.backend.edge_client import EdgeClient, EdgeError
from hub.backend.lock_manager import lock_view_for
from hub.backend.models import HubNode, HubStation, HubTwin
from hub.backend.security import decrypt_api_key

router = APIRouter()

# 快照转发全局并发上限: 保护枢纽自身与边缘 (超限请求排队而非拒绝)
_SNAPSHOT_SEMAPHORE = asyncio.Semaphore(8)

# STALE 判定: 超过 3 个轮询周期未更新 (RFC 15 §10.2)
_STALE_AFTER_S = HEALTH_INTERVAL_S * 3


class WallResponse(BaseModel):
    ts: float
    nodes: List[Dict[str, Any]]
    alerts: List[Dict[str, Any]]
    totals: Dict[str, int]


class StationUpdate(BaseModel):
    display_name: Optional[str] = None
    group_name: Optional[str] = None


class StationDetailResponse(BaseModel):
    node_id: int
    node_name: str
    hostname: Optional[str]
    app_version: Optional[str]
    license_state: Optional[str]
    status: str
    stale: bool
    channel_id: int
    display_name: Optional[str]
    group_name: Optional[str]
    reported: Dict[str, Any]
    reported_at: Optional[str]
    properties: List[Dict[str, Any]]
    actions: List[Dict[str, Any]]
    node_actions: List[Dict[str, Any]]      # M3: 节点级动作 (切项目) 也在工位操作台画
    lock: Dict[str, Any]                    # M3: 操作锁状态 {locked, holder, expires_in_s}


def _caps_for(node: HubNode, channel_id: int) -> Dict[str, list]:
    """从节点能力档案取出该工位的 property/action (缺档案则空, UI 不画按钮)。"""
    for st in (node.profile or {}).get("stations") or []:
        if st.get("channel_id") == channel_id:
            return {
                "properties": st.get("properties") or [],
                "actions": st.get("actions") or [],
            }
    return {"properties": [], "actions": []}


@router.get("/wall", response_model=WallResponse, summary="监控墙聚合状态",
            dependencies=[Depends(require_perm("wall.view"))])
def wall(request: Request, db: Session = Depends(get_db)):
    """墙页唯一数据源: 全部节点 + 工位 (孪生 reported + 展示名/分组) 一次拉齐。

    stale 字段: 节点在线但数据超 3 个轮询周期未刷新 → true (前端灰屏标注)。
    """
    poller = request.app.state.poller
    now = time.time()

    nodes_out = []
    alerts: List[Dict[str, Any]] = []
    total_stations = 0
    total_detecting = 0
    total_offline = 0
    total_stale = 0

    for node in db.query(HubNode).order_by(HubNode.id).all():
        rt = poller.runtime(node.id)
        stale = bool(rt.status == "online" and rt.last_seen
                     and now - rt.last_seen > _STALE_AFTER_S)
        if rt.status == "offline":
            total_offline += 1
            alerts.append({
                "kind": "offline", "node_id": node.id,
                "message": f"{node.name} 离线"
                           + (f"：{rt.last_error}" if rt.last_error else ""),
            })
        if stale:
            total_stale += 1
            alerts.append({
                "kind": "stale", "node_id": node.id,
                "message": f"{node.name} 数据滞后",
            })
        if node.license_state and node.license_state != "valid":
            alerts.append({
                "kind": "license", "node_id": node.id,
                "message": f"{node.name} License 异常（{node.license_state}）"
                           "，已降级只读",
            })

        twins = {t.channel_id: t for t in db.query(HubTwin).filter(
            HubTwin.node_id == node.id).all()}
        stations_out = []
        for st in sorted(node.stations, key=lambda s: s.channel_id):
            twin = twins.get(st.channel_id)
            reported = (twin.reported if twin else None) or {}
            if reported.get("detecting"):
                total_detecting += 1
            caps = _caps_for(node, st.channel_id)
            stations_out.append({
                "channel_id": st.channel_id,
                "display_name": st.display_name,
                "group_name": st.group_name,
                "reported": reported,
                "reported_at": (twin.reported_at.isoformat()
                                if twin and twin.reported_at else None),
                "properties": caps["properties"],
                "actions": caps["actions"],
            })
        total_stations += len(stations_out)

        nodes_out.append({
            "id": node.id,
            "name": node.name,
            "hostname": node.hostname,
            "app_version": node.app_version,
            "license_state": node.license_state,
            "status": rt.status,
            "stale": stale,
            "last_seen": rt.last_seen,
            "last_error": rt.last_error,
            "active_project_id": (rt.last_summary or {}).get("active_project_id"),
            "resources": (rt.last_summary or {}).get("resources"),
            "stations": stations_out,
        })

    return {
        "ts": now,
        "nodes": nodes_out,
        "alerts": alerts,
        "totals": {
            "nodes": len(nodes_out),
            "offline_nodes": total_offline,
            "stale_nodes": total_stale,
            "stations": total_stations,
            "detecting_stations": total_detecting,
        },
    }


@router.get("/nodes/{node_id}/stations/{channel_id}",
            response_model=StationDetailResponse,
            summary="单工位下钻读面",
            dependencies=[Depends(require_perm("wall.view"))])
def station_detail(node_id: int, channel_id: int, request: Request,
                   db: Session = Depends(get_db)):
    """单工位操作台读面: 节点运行态 + 孪生 reported + 能力档案 + 锁状态。

    写操作走 ops.py 网关 (M3); 本端点只供渲染 —— actions/node_actions 非空
    时前端才画对应按钮, lock 决定按钮可用态与持锁人徽章。
    """
    node = db.query(HubNode).filter(HubNode.id == node_id).first()
    if not node:
        raise HTTPException(404, "节点不存在")
    st = db.query(HubStation).filter(
        HubStation.node_id == node_id,
        HubStation.channel_id == channel_id).first()
    if not st:
        raise HTTPException(404, "工位不存在")

    poller = request.app.state.poller
    rt = poller.runtime(node.id)
    now = time.time()
    stale = bool(rt.status == "online" and rt.last_seen
                 and now - rt.last_seen > _STALE_AFTER_S)
    twin = db.query(HubTwin).filter(
        HubTwin.node_id == node_id,
        HubTwin.channel_id == channel_id).first()
    caps = _caps_for(node, channel_id)
    return {
        "node_id": node.id,
        "node_name": node.name,
        "hostname": node.hostname,
        "app_version": node.app_version,
        "license_state": node.license_state,
        "status": rt.status,
        "stale": stale,
        "channel_id": channel_id,
        "display_name": st.display_name,
        "group_name": st.group_name,
        "reported": (twin.reported if twin else None) or {},
        "reported_at": (twin.reported_at.isoformat()
                        if twin and twin.reported_at else None),
        "properties": caps["properties"],
        "actions": caps["actions"],
        "node_actions": (node.profile or {}).get("node_actions") or [],
        "lock": lock_view_for(db, node_id, channel_id),
    }


@router.put("/nodes/{node_id}/stations/{channel_id}",
            summary="改工位显示名 / 产线分组 (枢纽本地, 不下发边缘)",
            response_model=StationDetailResponse)
def update_station(node_id: int, channel_id: int, payload: StationUpdate,
                   request: Request, db: Session = Depends(get_db),
                   user: HubUser = Depends(require_perm("node.manage"))):
    """枢纽本地改名/分组, 不转发边缘 (不变量 1: 边缘生产不依赖此字段)。"""
    st = db.query(HubStation).filter(
        HubStation.node_id == node_id,
        HubStation.channel_id == channel_id).first()
    if not st:
        raise HTTPException(404, "工位不存在")
    old = {"display_name": st.display_name, "group_name": st.group_name}
    if payload.display_name is not None:
        st.display_name = payload.display_name.strip() or st.display_name
    if payload.group_name is not None:
        st.group_name = payload.group_name.strip() or None
    src = request.client.host if request.client else None
    write_audit(db, username=user.username, action="station.rename",
                node_id=node_id, channel_id=channel_id,
                old_value=old,
                new_value={"display_name": st.display_name,
                           "group_name": st.group_name},
                source_ip=src, result="ok")
    db.commit()
    return station_detail(node_id, channel_id, request, db)


@router.get("/nodes/{node_id}/stations/{channel_id}/live",
            summary="工位实时投影 (转发边缘 /hub/live, M7.5 值班读面)",
            dependencies=[Depends(require_perm("wall.view"))])
async def station_live(node_id: int, channel_id: int,
                       db: Session = Depends(get_db)):
    """按需转发: 只有下钻页开着才会打到这里 (~2s 轮询), 不进 poller。"""
    node = db.query(HubNode).filter(HubNode.id == node_id).first()
    if not node:
        raise HTTPException(404, "节点不存在")
    if not db.query(HubStation).filter(
            HubStation.node_id == node_id,
            HubStation.channel_id == channel_id).first():
        raise HTTPException(404, "工位不存在")

    api_key = (decrypt_api_key(node.api_key_enc, get_data_dir())
               if node.api_key_enc else None)
    async with EdgeClient(node.base_url, api_key=api_key) as client:
        try:
            return await client.live(channel_id)
        except EdgeError as e:
            raise HTTPException(503, f"边缘实况不可用: {e.detail}")


@router.get("/nodes/{node_id}/stations/{channel_id}/snapshot",
            summary="工位画面快照 (转发边缘)", response_class=Response,
            dependencies=[Depends(require_perm("wall.view"))])
async def station_snapshot(node_id: int, channel_id: int,
                           db: Session = Depends(get_db)):
    """转发边缘 /snapshot 单帧 JPEG。离线/失败回 503 (前端保留上一帧并灰屏)。"""
    node = db.query(HubNode).filter(HubNode.id == node_id).first()
    if not node:
        raise HTTPException(404, "节点不存在")
    if not db.query(HubStation).filter(
            HubStation.node_id == node_id,
            HubStation.channel_id == channel_id).first():
        raise HTTPException(404, "工位不存在")

    api_key = (decrypt_api_key(node.api_key_enc, get_data_dir())
               if node.api_key_enc else None)
    async with _SNAPSHOT_SEMAPHORE:
        async with EdgeClient(node.base_url, api_key=api_key) as client:
            try:
                data = await client.snapshot(channel_id)
            except EdgeError as e:
                raise HTTPException(503, f"边缘快照不可用: {e.detail}")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
