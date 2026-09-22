"""节点纳管 (RFC 15 §11.1 node_registry) — 节点 CRUD + 纳管门槛 + 状态读。

纳管流程 (M1 手动纳管; mDNS 发现候选是 P1 加速器):
  管理员在边缘机 API Key 页签发 scope=hub 的 key → 枢纽 POST /nodes 带
  base_url + api_key → 枢纽 handshake 验身份 → License/契约双门槛 →
  拉全量档案建工位映射 → Fernet 加密留存 key → 启动该节点轮询循环。
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hub.backend.audit import write_audit
from hub.backend.auth import HubUser, require_perm
from hub.backend.config import SUPPORTED_EDGE_CONTRACT, get_data_dir
from hub.backend.db import get_db
from hub.backend.edge_client import EdgeClient, EdgeError
from hub.backend.models import HubNode, HubStation, HubTwin
from hub.backend.security import encrypt_api_key
from hub.backend.twin_store import get_twins

router = APIRouter()


# ============================================================
# Schema
# ============================================================

class NodeCreate(BaseModel):
    name: str
    base_url: str            # http://192.168.1.10:8001
    api_key: str             # 边缘签发的 scope=hub M2M key (只在本请求出现明文)


class NodeResponse(BaseModel):
    id: int
    name: str
    base_url: str
    node_uid: Optional[str]
    machine_id: Optional[str]
    hostname: Optional[str]
    app_version: Optional[str]
    api_contract: Optional[int]
    license_state: Optional[str]
    profile_hash: Optional[str]
    enabled: bool
    station_count: int
    status: str              # 运行态 (poller 内存): unknown / online / offline


class NodeStatusResponse(BaseModel):
    node: NodeResponse
    runtime: Dict[str, Any]
    twins: List[Dict[str, Any]]


class NodeDeleteResponse(BaseModel):
    ok: bool
    deleted: int


def _serialize(node: HubNode, status: str) -> dict:
    return {
        "id": node.id, "name": node.name, "base_url": node.base_url,
        "node_uid": node.node_uid, "machine_id": node.machine_id,
        "hostname": node.hostname, "app_version": node.app_version,
        "api_contract": node.api_contract, "license_state": node.license_state,
        "profile_hash": node.profile_hash, "enabled": bool(node.enabled),
        "station_count": len(node.stations), "status": status,
    }


# ============================================================
# 端点
# ============================================================

@router.get("/nodes", response_model=List[NodeResponse], summary="节点列表",
            dependencies=[Depends(require_perm("wall.view"))])
def list_nodes(request: Request, db: Session = Depends(get_db)):
    """全部纳管节点 + 内存运行态 (墙页首屏数据源之一)。"""
    poller = request.app.state.poller
    return [_serialize(n, poller.runtime(n.id).status)
            for n in db.query(HubNode).order_by(HubNode.id).all()]


@router.post("/nodes", response_model=NodeResponse, summary="纳管边缘节点")
async def enroll_node(payload: NodeCreate, request: Request,
                      db: Session = Depends(get_db),
                      user: HubUser = Depends(require_perm("node.manage"))):
    """纳管: handshake 验身份 → License/契约门槛 → 拉档案建工位 → 加密存 key。

    门槛 (RFC 15 §3.4):
      - License 状态非 valid → 403 拒绝纳管 (unknown 亦拒: 让边缘先跑起来把
        license.cache 写进 KV, 避免纳管了一台没授权的机器)
      - 边缘 api_contract 高于枢纽支持上限 → 409 提示升级枢纽
    """
    base_url = payload.base_url.rstrip("/")
    if db.query(HubNode).filter(HubNode.base_url == base_url).first():
        raise HTTPException(409, f"该地址已纳管: {base_url}")

    src = request.client.host if request.client else None

    async with EdgeClient(base_url, api_key=payload.api_key) as client:
        try:
            hs = await client.handshake()
        except EdgeError as e:
            write_audit(db, username=user.username, action="node.enroll",
                        new_value={"base_url": base_url, "error": e.detail},
                        source_ip=src, result="failed")
            db.commit()
            raise HTTPException(400, f"纳管失败: {e.detail}")

        ident = hs.get("identity", {})
        lic_state = (ident.get("license") or {}).get("state", "unknown")
        if lic_state != "valid":
            write_audit(db, username=user.username, action="node.enroll",
                        new_value={"base_url": base_url,
                                   "license_state": lic_state},
                        source_ip=src, result="denied")
            db.commit()
            raise HTTPException(
                403, f"拒绝纳管: 边缘机 License 状态为 {lic_state} "
                     f"(需在边缘机上完成激活并至少启动一次)")

        contract = int(ident.get("api_contract") or 0)
        if contract > SUPPORTED_EDGE_CONTRACT:
            raise HTTPException(
                409, f"边缘 API 契约 v{contract} 高于枢纽支持上限 "
                     f"v{SUPPORTED_EDGE_CONTRACT}, 请升级枢纽")

        # 用带 key 的 profile 调用同时验证 key 有效性 (边缘开鉴权时 401/403 会在此暴露)
        try:
            profile = await client.profile()
        except EdgeError as e:
            raise HTTPException(400, f"能力档案拉取失败: {e.detail}")

    node = HubNode(
        name=payload.name.strip(),
        base_url=base_url,
        api_key_enc=encrypt_api_key(payload.api_key, get_data_dir()),
        node_uid=ident.get("node_id"),
        machine_id=ident.get("machine_id"),
        hostname=ident.get("hostname"),
        app_version=ident.get("app_version"),
        api_contract=contract,
        license_state=lic_state,
        profile=profile,
        profile_hash=profile.get("profile_hash"),
        enabled=True,
    )
    db.add(node)
    db.flush()  # 拿 node.id

    for st in profile.get("stations", []):
        db.add(HubStation(node_id=node.id, channel_id=st["channel_id"],
                          display_name=f"{node.name}-工位{st['channel_id']}"))

    write_audit(db, username=user.username, action="node.enroll",
                node_id=node.id,
                new_value={"base_url": base_url, "name": node.name,
                           "node_uid": node.node_uid,
                           "stations": len(profile.get("stations", []))},
                source_ip=src, result="ok")
    db.commit()
    db.refresh(node)

    request.app.state.poller.add_node(node.id)
    return _serialize(node, "unknown")


@router.delete("/nodes/{node_id}", response_model=NodeDeleteResponse,
               summary="移除纳管节点")
def remove_node(node_id: int, request: Request,
                db: Session = Depends(get_db),
                user: HubUser = Depends(require_perm("node.manage"))):
    """移除节点 (级联删工位映射与孪生); 边缘机自身不受影响 (不变量 1)。"""
    node = db.query(HubNode).filter(HubNode.id == node_id).first()
    if not node:
        raise HTTPException(404, "节点不存在")
    src = request.client.host if request.client else None
    write_audit(db, username=user.username, action="node.remove",
                node_id=node_id,
                old_value={"base_url": node.base_url, "name": node.name},
                source_ip=src, result="ok")
    db.query(HubTwin).filter(HubTwin.node_id == node_id).delete()
    db.delete(node)   # stations 级联
    db.commit()
    request.app.state.poller.remove_node(node_id)
    return {"ok": True, "deleted": node_id}


@router.post("/nodes/{node_id}/poll", response_model=Dict[str, Any],
             summary="立即轮询一次 (手动刷新)",
             dependencies=[Depends(require_perm("wall.view"))])
async def poll_now(node_id: int, request: Request,
                   db: Session = Depends(get_db)):
    """绕过轮询周期立即拉一次 health-summary (前端"刷新"按钮 / 测试驱动)。"""
    if not db.query(HubNode).filter(HubNode.id == node_id).first():
        raise HTTPException(404, "节点不存在")
    rt = await request.app.state.poller.poll_node_once(node_id)
    return rt.snapshot()


@router.get("/nodes/{node_id}/status", response_model=NodeStatusResponse,
            summary="节点运行态 + 工位孪生",
            dependencies=[Depends(require_perm("wall.view"))])
def node_status(node_id: int, request: Request,
                db: Session = Depends(get_db)):
    """单节点下钻读面: 节点信息 + poller 运行态 + 各工位孪生 (desired/reported)。"""
    node = db.query(HubNode).filter(HubNode.id == node_id).first()
    if not node:
        raise HTTPException(404, "节点不存在")
    rt = request.app.state.poller.runtime(node_id)
    twins = [{
        "channel_id": t.channel_id,
        "desired": t.desired or {},
        "reported": t.reported or {},
        "version": t.version,
        "reported_at": t.reported_at.isoformat() if t.reported_at else None,
    } for t in get_twins(db, node_id)]
    return {
        "node": _serialize(node, rt.status),
        "runtime": rt.snapshot(),
        "twins": twins,
    }
