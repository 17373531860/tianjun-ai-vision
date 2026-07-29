"""原生称重投料模式 控制/查询 API。

挂在 /api/v1/weighing/* 下, 配合 logic_mode='weighing' 项目使用。
- 前置选择 (人员/型号, 6.5) / 扫码开始一件 / 手动去皮置零 (6.4) / 复位
- 视觉料别标签喂入 (6.3, 真模型或虚拟测试都走这)
- 逐件记录查询 (6.1, 数据页)
- 虚拟喂重量 (无真秤时直接注入读数验逻辑)
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from typing import List, Optional

from backend.services.weighing_engine import get_weighing_engine

router = APIRouter()


class ContextBody(BaseModel):
    channel_id: int = 0
    operator: Optional[str] = None
    model_name: Optional[str] = None


class ScanBody(BaseModel):
    channel_id: int = 0
    serial_no: str


class ChannelBody(BaseModel):
    channel_id: int = 0


class LabelBody(BaseModel):
    channel_id: int = 0
    label: Optional[str] = None


class FeedBody(BaseModel):
    channel_id: int = 0
    weight: float


@router.get("/weighing/state")
def weighing_state(channel: Optional[int] = Query(None)):
    """工位实时状态 (相位/型号/料别进度/本件结果)。不传 channel 返回所有。"""
    return get_weighing_engine().snapshot(channel)


class OperatorsOut(BaseModel):
    operators: List[str] = Field(
        default_factory=list,
        description="启用状态账号的显示名列表 (display_name 优先, 缺省回退 username)")


@router.get("/weighing/operators",
            summary="作业员候选名单",
            response_model=OperatorsOut)
def weighing_operators():
    """作业员候选名单: 用户系统中「启用」状态的账号显示名。

    称重配置开启「作业员从用户名单选择」后, 监控页人员下拉从这里取数。
    只暴露显示名 (无敏感字段), 故不挂用户管理权限——操作员工位也要能拉取。
    - 查询失败 (如用户表不可用) 时返回空名单, 不抛错 (前端下拉退化为空列表)。
    """
    try:
        from backend.db.database import SessionLocal
        from backend.models.auth_models import User
        db = SessionLocal()
        try:
            rows = (db.query(User).filter(User.active == True)  # noqa: E712
                    .order_by(User.id).all())
            return {"operators": [u.display_name or u.username for u in rows]}
        finally:
            db.close()
    except Exception:
        return {"operators": []}


@router.post("/weighing/context")
def weighing_context(body: ContextBody):
    """6.5 前置选择: 设置某通道当前人员/型号。"""
    snap = get_weighing_engine().set_context(
        body.channel_id, operator=body.operator, model_name=body.model_name)
    if snap is None:
        return {"ok": False, "message": "该通道未启用称重模式"}
    return {"ok": True, "snapshot": snap}


@router.post("/weighing/scan")
def weighing_scan(body: ScanBody):
    """扫码开始一件 (含 6.5 前置校验拦截)。"""
    snap = get_weighing_engine().start_product(body.channel_id, body.serial_no)
    if snap is None:
        return {"ok": False, "message": "该通道未启用称重模式"}
    return {"ok": True, "snapshot": snap}


@router.post("/weighing/tare")
def weighing_tare(body: ChannelBody):
    """6.4 手动去皮: 给工位称重器发 T + 推进状态机。"""
    res = get_weighing_engine().manual_tare(body.channel_id)
    if res is None:
        return {"ok": False, "message": "该通道未启用称重模式"}
    return {"ok": True, **res}


@router.post("/weighing/zero")
def weighing_zero(body: ChannelBody):
    """手动置零: 给工位称重器发 Z。"""
    res = get_weighing_engine().manual_zero(body.channel_id)
    if res is None:
        return {"ok": False, "message": "该通道未启用称重模式"}
    return {"ok": True, **res}


@router.post("/weighing/reset")
def weighing_reset(body: ChannelBody):
    """复位某工位当前件 (放弃重来)。"""
    snap = get_weighing_engine().reset_station(body.channel_id)
    if snap is None:
        return {"ok": False, "message": "该通道未启用称重模式"}
    return {"ok": True, "snapshot": snap}


@router.post("/weighing/material-label")
def weighing_material_label(body: LabelBody):
    """6.3 喂入视觉识别的料别标签 (真模型 or 虚拟测试)。"""
    snap = get_weighing_engine().set_material_label(body.channel_id, body.label)
    if snap is None:
        return {"ok": False, "message": "该通道未启用称重模式"}
    return {"ok": True, "snapshot": snap}


@router.get("/weighing/records")
def weighing_records(channel: int = Query(0), limit: int = Query(200)):
    """6.1 逐件逐料记录 (数据页)。"""
    return {"records": get_weighing_engine().get_records(channel, limit)}


@router.post("/weighing/feed")
def weighing_feed(body: FeedBody):
    """虚拟喂一帧重量 (无真秤时验逻辑用; 等价真秤一帧读数)。"""
    eng = get_weighing_engine()
    if not eng.is_weighing_channel(body.channel_id):
        return {"ok": False, "message": "该通道未启用称重模式"}
    eng.feed_weight(body.channel_id, body.weight)
    return {"ok": True, "snapshot": eng.snapshot(body.channel_id)}
