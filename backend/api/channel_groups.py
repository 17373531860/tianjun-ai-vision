"""Channel Groups (工位组) CRUD API — RFC 10 CG.5.

客户视角:
  双工位场景下需要"A 站 NG → B 站联动 NG". 用户在 Settings 页创建一个
  Group-A, 选 channel 0+1, 策略 synchronized_any_ng, 保存. 后台 channel_groups
  表加一行, ChannelGroupCoordinator.reload_groups() 立即生效.

端点 (前缀 /api/v1/channel-groups):
  GET    /                      列出所有工位组
  GET    /{id}                  查单个工位组配置
  POST   /                      创建工位组 (含成员校验)
  PUT    /{id}                  更新工位组 (含 reload_groups 联动)
  DELETE /{id}                  删除工位组 (仅在 enabled=False 时允许)
  GET    /{id}/state            查运行时状态 (供前端 polling)

安全:
  - 所有 CRUD 都需要 ``system.channel_group.manage`` 权限 (新加权限位)
  - 查询接口 (GET) 允许 ``system.channel_group.view`` 权限
  - 默认无 auth 时全部放行 (v3.10 兼容)

零差异默认: 没创建任何工位组时, 所有结算行为与 v3.12 完全一致.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.models import ChannelGroup


router = APIRouter()


# ============================================================
# Pydantic schemas
# ============================================================


class ChannelGroupBase(BaseModel):
    name: str
    member_channel_ids: List[int]
    settle_strategy: str = "synchronized_any_ng"
    timeout_ms: int = 5000
    timeout_action: str = "fallback_independent"
    enabled: bool = True
    # v3.51: 统一播报 — synchronized_all_ok 组聚齐全 OK 才播一次合格
    # (个体 OK 的灯/语音/toast 抑制)。存 plugin_data, 不动主 schema。默认关。
    unified_ok_report: bool = False


class ChannelGroupCreate(ChannelGroupBase):
    pass


class ChannelGroupUpdate(BaseModel):
    name: Optional[str] = None
    member_channel_ids: Optional[List[int]] = None
    settle_strategy: Optional[str] = None
    timeout_ms: Optional[int] = None
    timeout_action: Optional[str] = None
    enabled: Optional[bool] = None
    unified_ok_report: Optional[bool] = None


class ChannelGroupResponse(ChannelGroupBase):
    id: int

    class Config:
        from_attributes = True


# ============================================================
# 校验 helpers
# ============================================================


_ALLOWED_STRATEGIES = {
    "synchronized_any_ng",
    "synchronized_all_ok",
    "independent",
    "master_slave",  # v3.13.0 配置位仅, 不实现
}
_ALLOWED_TIMEOUT_ACTIONS = {"fallback_independent", "force_ng"}


def _validate_group_config(
    name: str,
    member_channel_ids: List[int],
    settle_strategy: str,
    timeout_action: str,
) -> None:
    """校验组配置. 不通过抛 HTTPException 400."""
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="工位组名称不能为空")
    if not isinstance(member_channel_ids, list) or len(member_channel_ids) < 2:
        raise HTTPException(status_code=400, detail="工位组至少需要 2 个成员通道")
    if len(set(member_channel_ids)) != len(member_channel_ids):
        raise HTTPException(status_code=400, detail="成员通道 id 不能重复")
    if any(not isinstance(c, int) or c < 0 for c in member_channel_ids):
        raise HTTPException(status_code=400, detail="成员通道 id 必须是非负整数")
    if settle_strategy not in _ALLOWED_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"settle_strategy 必须是 {sorted(_ALLOWED_STRATEGIES)} 之一",
        )
    if settle_strategy == "master_slave":
        raise HTTPException(
            status_code=400,
            detail="master_slave 策略 v3.13.0 暂未实现, 计划 v3.14",
        )
    if timeout_action not in _ALLOWED_TIMEOUT_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"timeout_action 必须是 {sorted(_ALLOWED_TIMEOUT_ACTIONS)} 之一",
        )


def _check_member_uniqueness(
    db: Session,
    member_channel_ids: List[int],
    exclude_group_id: Optional[int] = None,
) -> None:
    """跨组通道唯一性校验 (应用层做, 不用 SQLite JSON.contains).

    背景: SQLAlchemy ``JSON.contains(int)`` 在 SQLite 上退化成文本子串匹配,
    比如组 [10, 11] 用 ``contains(1)`` 会命中 (子串 "1" 在 "[10, 11]" 里).
    所以这里把所有 enabled=True 的组捞回来, 用 Python ``in`` 判断成员归属.

    参数:
        member_channel_ids: 待加入的通道 id 列表
        exclude_group_id: 排除自身 id (PUT 场景), None 表示 POST 不排除

    抛 HTTPException 400 表示有通道已属于另一个 enabled 组.
    """
    other_groups_query = db.query(ChannelGroup).filter(ChannelGroup.enabled.is_(True))
    if exclude_group_id is not None:
        other_groups_query = other_groups_query.filter(ChannelGroup.id != exclude_group_id)

    for other in other_groups_query.all():
        other_members = set(other.member_channel_ids or [])
        for cid in member_channel_ids:
            if cid in other_members:
                raise HTTPException(
                    status_code=400,
                    detail=f"channel {cid} 已属于工位组 '{other.name}' (id={other.id})",
                )


def _reload_coordinator(db: Session) -> None:
    """CRUD 后调 — 重载 Coordinator 让新配置立即生效."""
    try:
        from backend.services.channel_group_coordinator import get_coordinator
        get_coordinator().reload_groups(db)
    except Exception as e:
        print(f"[ChannelGroup] reload_coordinator 异常: {e}")


def _serialize(g: ChannelGroup) -> ChannelGroupResponse:
    _pd = g.plugin_data if isinstance(g.plugin_data, dict) else {}
    return ChannelGroupResponse(
        id=g.id,
        name=g.name,
        member_channel_ids=list(g.member_channel_ids or []),
        settle_strategy=g.settle_strategy or "synchronized_any_ng",
        timeout_ms=int(g.timeout_ms or 5000),
        timeout_action=g.timeout_action or "fallback_independent",
        enabled=bool(g.enabled),
        unified_ok_report=bool(_pd.get("unified_ok_report", False)),
    )


# ============================================================
# 端点
# ============================================================


@router.get("",
            dependencies=[Depends(require_perm("system.channel_group.view"))])
def list_channel_groups(db: Session = Depends(get_db)):
    rows = db.query(ChannelGroup).order_by(ChannelGroup.id).all()
    return {"items": [_serialize(g) for g in rows]}


@router.get("/{group_id}",
            response_model=ChannelGroupResponse,
            dependencies=[Depends(require_perm("system.channel_group.view"))])
def get_channel_group(group_id: int, db: Session = Depends(get_db)):
    row = db.query(ChannelGroup).filter(ChannelGroup.id == group_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="工位组不存在")
    return _serialize(row)


@router.post("",
             response_model=ChannelGroupResponse,
             status_code=201,
             dependencies=[Depends(require_perm("system.channel_group.manage"))])
def create_channel_group(
    payload: ChannelGroupCreate,
    db: Session = Depends(get_db),
):
    _validate_group_config(
        payload.name,
        payload.member_channel_ids,
        payload.settle_strategy,
        payload.timeout_action,
    )

    # 名字唯一
    existing = db.query(ChannelGroup).filter(ChannelGroup.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"工位组名称 '{payload.name}' 已存在")

    # 通道不能同时属于多个 enabled 组 (应用层做, 不用 JSON.contains)
    if payload.enabled:
        _check_member_uniqueness(db, payload.member_channel_ids, exclude_group_id=None)

    row = ChannelGroup(
        name=payload.name,
        member_channel_ids=payload.member_channel_ids,
        settle_strategy=payload.settle_strategy,
        timeout_ms=payload.timeout_ms,
        timeout_action=payload.timeout_action,
        enabled=payload.enabled,
        plugin_data={"unified_ok_report": bool(payload.unified_ok_report)},
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    _reload_coordinator(db)
    return _serialize(row)


@router.put("/{group_id}",
            response_model=ChannelGroupResponse,
            dependencies=[Depends(require_perm("system.channel_group.manage"))])
def update_channel_group(
    group_id: int,
    payload: ChannelGroupUpdate,
    db: Session = Depends(get_db),
):
    row = db.query(ChannelGroup).filter(ChannelGroup.id == group_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="工位组不存在")

    update_data = payload.model_dump(exclude_unset=True)

    # 部分更新场景下的校验: 取 merged 值
    new_name = update_data.get("name", row.name)
    new_members = update_data.get("member_channel_ids", row.member_channel_ids or [])
    new_strategy = update_data.get("settle_strategy", row.settle_strategy)
    new_timeout_action = update_data.get("timeout_action", row.timeout_action)

    if "name" in update_data and new_name != row.name:
        existing = db.query(ChannelGroup).filter(
            ChannelGroup.name == new_name,
            ChannelGroup.id != group_id,
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"工位组名称 '{new_name}' 已存在")

    _validate_group_config(new_name, new_members, new_strategy, new_timeout_action)

    # 跨组通道唯一性: 当 members 或 enabled 被改时, 且最终态是 enabled 才校验.
    final_enabled = update_data.get("enabled", row.enabled)
    members_or_enabled_changed = (
        "member_channel_ids" in update_data or "enabled" in update_data
    )
    if final_enabled and members_or_enabled_changed:
        _check_member_uniqueness(db, list(new_members), exclude_group_id=group_id)

    # v3.51: unified_ok_report 落 plugin_data (不是主 schema 列)
    if "unified_ok_report" in update_data:
        _uor = bool(update_data.pop("unified_ok_report"))
        _pd = dict(row.plugin_data) if isinstance(row.plugin_data, dict) else {}
        _pd["unified_ok_report"] = _uor
        row.plugin_data = _pd

    for k, v in update_data.items():
        setattr(row, k, v)

    db.commit()
    db.refresh(row)

    _reload_coordinator(db)
    return _serialize(row)


@router.delete("/{group_id}",
               status_code=204,
               dependencies=[Depends(require_perm("system.channel_group.manage"))])
def delete_channel_group(group_id: int, db: Session = Depends(get_db)):
    row = db.query(ChannelGroup).filter(ChannelGroup.id == group_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="工位组不存在")

    # 安全: enabled 组不能直接删 (防破坏正在运行的联动)
    if row.enabled:
        raise HTTPException(
            status_code=409,
            detail="enabled=True 的工位组不能直接删除, 请先 PUT enabled=false",
        )

    db.delete(row)
    db.commit()
    _reload_coordinator(db)
    return None


@router.get("/{group_id}/state",
            dependencies=[Depends(require_perm("system.channel_group.view"))])
def get_channel_group_state(group_id: int):
    """查工位组运行时状态 (供前端 polling)."""
    from backend.services.channel_group_coordinator import get_coordinator
    coord = get_coordinator()
    group = coord.get_group(group_id)
    if not group:
        raise HTTPException(
            status_code=404,
            detail="工位组不在 Coordinator 内存 (可能 disabled 或未 reload)",
        )
    # 当前简化版: 仅返回配置 + 是否在内存中
    return {
        "group": group,
        "in_memory": True,
    }
