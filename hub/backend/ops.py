"""操作网关 (RFC 15 §4.3 / M3) — 枢纽全部写操作的唯一出口。

流水线 (每一步失败都终止且留痕):
  权限 (ops.execute) → 锁裁决 (ensure_lock: 自动获取/续期, 他人持锁 409)
  → 白名单校验 (只认能力档案登记过的 action) → 写孪生 desired (意图先落库)
  → 快路径转发边缘 POST /hub/ops → 审计 (成功/失败/拒绝都写) → 返回。

收敛语义 (M3 最小闭环):
  - desired 是"最后一次已接受的意图"; 转发成功即视为达成, 下一轮 poll 的
    reported 自然对齐 (2s 内)。
  - 边缘业务拒绝 (409: 检测中/模型未就绪) 不重试不排队 —— 原样透传给操作者,
    人来决策 (RFC 15 不变量: 枢纽不自作主张)。
  - 网络失败回 502, desired 保留 —— 前端可见"意图未达成"(desired≠reported)。

节点级操作 (activate_project) 也从工位路径进 (UI 在工位操作台上), 锁按该工位裁决;
影响全节点这一点由边缘 409 守门 + 前端 danger 确认双重明示。
"""
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hub.backend.audit import write_audit
from hub.backend.auth import HubUser, require_perm
from hub.backend.config import get_data_dir
from hub.backend.db import get_db
from hub.backend.edge_client import EdgeClient, EdgeError
from hub.backend.lock_manager import ensure_lock
from hub.backend.models import HubNode, HubStation
from hub.backend.security import decrypt_api_key
from hub.backend.twin_store import set_desired

router = APIRouter()

# action → desired patch 生成器 (白名单本体; 档案里没登记的 action 一律 400)
_STATION_ACTIONS = {"start_detection", "stop_detection", "ack_alarm"}
_NODE_ACTIONS = {"activate_project"}


class OpsPayload(BaseModel):
    action: str
    project_id: Optional[int] = None


class OpsResponse(BaseModel):
    ok: bool
    action: str
    message: str
    desired: Dict[str, Any]


class ProjectsResponse(BaseModel):
    items: list


def _load_node_station(db: Session, node_id: int, channel_id: int):
    node = db.query(HubNode).filter(HubNode.id == node_id).first()
    if not node:
        raise HTTPException(404, "节点不存在")
    st = db.query(HubStation).filter(
        HubStation.node_id == node_id,
        HubStation.channel_id == channel_id).first()
    if not st:
        raise HTTPException(404, "工位不存在")
    return node, st


def _action_registered(node: HubNode, channel_id: int, action: str) -> bool:
    """能力档案白名单: 只有边缘档案登记过的 action 才可执行 (RFC 15 §4.1)。"""
    profile = node.profile or {}
    if action in _NODE_ACTIONS:
        return any(a.get("id") == action
                   for a in profile.get("node_actions") or [])
    for st in profile.get("stations") or []:
        if st.get("channel_id") == channel_id:
            return any(a.get("id") == action
                       for a in st.get("actions") or [])
    return False


@router.post("/nodes/{node_id}/stations/{channel_id}/ops",
             response_model=OpsResponse, summary="执行工位写操作 (M3 唯一写出口)")
async def execute_op(node_id: int, channel_id: int, payload: OpsPayload,
                     request: Request, db: Session = Depends(get_db),
                     user: HubUser = Depends(require_perm("ops.execute"))):
    """锁 + 白名单 + desired + 转发 + 审计, 一次走完。

    响应约定: 边缘业务拒绝按原 status_code 透传 (409 等), 网络失败 502;
    两者都已写入审计 (result=failed), desired 保留供前端画"意图未达成"。
    """
    node, _st = _load_node_station(db, node_id, channel_id)
    action = payload.action
    src = request.client.host if request.client else None

    if action not in (_STATION_ACTIONS | _NODE_ACTIONS):
        raise HTTPException(400, f"不支持的 action: {action}")
    if not _action_registered(node, channel_id, action):
        raise HTTPException(
            409, f"该工位能力档案未登记 {action} (边缘版本过低或档案未刷新)")
    if action == "activate_project" and not payload.project_id:
        raise HTTPException(400, "activate_project 需要 project_id")

    # 锁裁决 (他人持有效锁 → 409, 本函数直接抛出)
    ensure_lock(db, node_id, channel_id, user)

    # 意图先落 desired (转发失败也保留, 暴露 desired≠reported 差异)。
    # ack_alarm 例外: 一次性 RPC 动作不进 desired (RFC 15 §10.3-5 孪生放配置
    # 意图、消息放动作 —— 消警丢进期望态队列慢慢收敛是不可接受的)。
    desired_patch: Dict[str, Any] = {}
    if action == "start_detection":
        desired_patch = {"detecting": True}
    elif action == "stop_detection":
        desired_patch = {"detecting": False}
    elif action == "activate_project":
        desired_patch = {"active_project_id": payload.project_id}
    if desired_patch:
        set_desired(db, node_id, channel_id, desired_patch)

    api_key = (decrypt_api_key(node.api_key_enc, get_data_dir())
               if node.api_key_enc else None)

    def _audit(result: str, message: str):
        write_audit(db, username=user.username, node_id=node_id,
                    channel_id=channel_id, action=f"ops.{action}",
                    new_value=json.dumps(
                        {"params": payload.dict(exclude_none=True),
                         "message": message}, ensure_ascii=False),
                    source_ip=src, result=result)

    try:
        async with EdgeClient(node.base_url, api_key=api_key) as client:
            res = await client.ops(action, channel=channel_id,
                                   project_id=payload.project_id)
    except EdgeError as e:
        _audit("failed", e.detail)
        db.commit()  # desired + 锁 + 审计都保留 (失败也是事实)
        raise HTTPException(e.status_code or 502, e.detail)

    _audit("ok", res.get("message", ""))
    db.commit()

    # 转发成功后立即触发一轮 poll, 让 reported 尽快对齐 (best-effort)
    try:
        poller = request.app.state.poller
        await poller.poll_node_once(node_id)
    except Exception:
        pass

    return {"ok": True, "action": action,
            "message": res.get("message", "已执行"),
            "desired": desired_patch}


@router.get("/nodes/{node_id}/projects", response_model=ProjectsResponse,
            summary="节点项目列表 (转发边缘, 切项目下拉用)",
            dependencies=[Depends(require_perm("ops.execute"))])
async def node_projects(node_id: int, db: Session = Depends(get_db)):
    """实时转发不缓存: 项目增删在边缘发生, 枢纽任何缓存都会陈旧误导。"""
    node = db.query(HubNode).filter(HubNode.id == node_id).first()
    if not node:
        raise HTTPException(404, "节点不存在")
    api_key = (decrypt_api_key(node.api_key_enc, get_data_dir())
               if node.api_key_enc else None)
    try:
        async with EdgeClient(node.base_url, api_key=api_key) as client:
            return await client.list_projects()
    except EdgeError as e:
        raise HTTPException(e.status_code or 502, e.detail)
