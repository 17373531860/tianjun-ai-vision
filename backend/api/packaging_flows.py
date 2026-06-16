"""Packaging Flow (包装箱结算) CRUD API — v3.21+ 上银包装线场景.

客户视角:
  扫码驱动的"工单 → 箱 → 托盘"三层结算. 用户在 Settings 页创建一个包装结算配置,
  选检测工位 / 扫码器 / 拉单 MES 连接, 配 5 组参数 (或套「上银包装线」预设), 保存.
  packaging_flow_configs 表加一行, PackagingFlowCoordinator.reload_configs() 立即生效.

端点 (前缀 /api/v1/packaging-flows):
  GET    /                列表
  GET    /{id}            详情
  POST   /                创建
  PUT    /{id}            更新 (enabled toggle 时 reload)
  DELETE /{id}            删除 (必须 enabled=False)
  GET    /{id}/state      当前进行中的工单/箱进度快照 (供 Monitor polling)

安全:
  - CRUD 需 system.packaging_flow.manage 权限
  - 查询 (GET) 需 system.packaging_flow.view 权限
  - 默认无 auth 时全部放行 (v3.10 兼容)

零差异默认: 没创建任何包装结算配置时, 所有行为与不配置时字节级一致.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.mes_models import PackagingFlowConfig, PackagingFlowRun


router = APIRouter()


# ============================================================
# Pydantic schemas
# ============================================================


class PackagingFlowConfigBase(BaseModel):
    name: str
    enabled: bool = False
    channel_id: int = 0
    scan_device_id: Optional[int] = None
    pull_conn_id: Optional[int] = None
    # 组① 工单与箱数
    box_count_source: str = "field"
    box_count_field: str = "dispatch_qty"
    # 组② 数量规格
    tray_qty_mode: str = "fixed"
    tray_qty_fixed: int = 0
    tray_qty_table: Optional[Dict[str, int]] = None
    trays_per_box_mode: str = "fixed"
    trays_per_box_fixed: int = 4
    trays_per_box_table: Optional[Dict[str, int]] = None
    # 组③ 标签校验
    label_match: str = "strip_hyphen"
    label_len: int = 0
    hyphen_template: Optional[str] = None
    # 组④ 异常策略
    on_mes_fail: str = "block"
    on_label_mismatch: str = "warn"
    on_short_box: str = "redo"
    on_forced_stop_partial: str = "fail"
    # 组⑤ 收尾与回推 (全可选)
    on_forced_stop: str = "settle"
    forced_settle_on_standby: bool = True
    push_on_complete: bool = False
    push_event_type: str = "packaging_complete"
    # 组⑥ 异常 → 项目事件映射 (全可选, None=走默认通用报警)
    event_short_box: Optional[int] = None
    event_over_box: Optional[int] = None
    event_tray_ng: Optional[int] = None
    event_box_ng: Optional[int] = None
    event_label_mismatch: Optional[int] = None
    event_label_len: Optional[int] = None
    event_mes_fail: Optional[int] = None
    # 组⑦ 滑块口径 + 尾箱 + 自动切项目 + 塞工单 gate (v3.22, 全可选默认关)
    count_unit: str = "trays"
    items_per_box_source: str = "project"
    items_per_box_fixed: int = 0
    slider_total_field: str = "dispatch_qty"
    auto_switch_project: bool = False
    spec_to_project: Optional[Dict[str, int]] = None
    tail_paper_order_required: bool = False
    tail_paper_step_label: Optional[str] = None
    event_missing_paper: Optional[int] = None


class PackagingFlowConfigCreate(PackagingFlowConfigBase):
    pass


class PackagingFlowConfigUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    channel_id: Optional[int] = None
    scan_device_id: Optional[int] = None
    pull_conn_id: Optional[int] = None
    box_count_source: Optional[str] = None
    box_count_field: Optional[str] = None
    tray_qty_mode: Optional[str] = None
    tray_qty_fixed: Optional[int] = None
    tray_qty_table: Optional[Dict[str, int]] = None
    trays_per_box_mode: Optional[str] = None
    trays_per_box_fixed: Optional[int] = None
    trays_per_box_table: Optional[Dict[str, int]] = None
    label_match: Optional[str] = None
    label_len: Optional[int] = None
    hyphen_template: Optional[str] = None
    on_mes_fail: Optional[str] = None
    on_label_mismatch: Optional[str] = None
    on_short_box: Optional[str] = None
    on_forced_stop_partial: Optional[str] = None
    on_forced_stop: Optional[str] = None
    forced_settle_on_standby: Optional[bool] = None
    push_on_complete: Optional[bool] = None
    push_event_type: Optional[str] = None
    event_short_box: Optional[int] = None
    event_over_box: Optional[int] = None
    event_tray_ng: Optional[int] = None
    event_box_ng: Optional[int] = None
    event_label_mismatch: Optional[int] = None
    event_label_len: Optional[int] = None
    event_mes_fail: Optional[int] = None
    count_unit: Optional[str] = None
    items_per_box_source: Optional[str] = None
    items_per_box_fixed: Optional[int] = None
    slider_total_field: Optional[str] = None
    auto_switch_project: Optional[bool] = None
    spec_to_project: Optional[Dict[str, int]] = None
    tail_paper_order_required: Optional[bool] = None
    tail_paper_step_label: Optional[str] = None
    event_missing_paper: Optional[int] = None


class PackagingFlowConfigResponse(PackagingFlowConfigBase):
    id: int

    class Config:
        from_attributes = True


# ============================================================
# 校验
# ============================================================

_ENUMS = {
    "box_count_source": {"field", "formula"},
    "tray_qty_mode": {"fixed", "by_spec"},
    "trays_per_box_mode": {"fixed", "by_spec"},
    "label_match": {"exact", "strip_hyphen", "digits_only"},
    "on_mes_fail": {"block", "offline"},
    "on_label_mismatch": {"off", "block", "warn"},
    "on_short_box": {"redo", "void"},
    "on_forced_stop_partial": {"pass", "fail"},
    "on_forced_stop": {"settle", "abort", "keep"},
    "count_unit": {"trays", "sliders"},
    "items_per_box_source": {"project", "config"},
}


def _validate(values: Dict[str, Any]) -> None:
    """校验配置. 不通过抛 HTTPException 400. 只校验出现的字段 (支持部分更新)."""
    if "name" in values:
        name = values["name"]
        if not name or not str(name).strip():
            raise HTTPException(status_code=400, detail="配置名称不能为空")
    if "channel_id" in values and values["channel_id"] is not None:
        if not isinstance(values["channel_id"], int) or values["channel_id"] < 0:
            raise HTTPException(status_code=400, detail="检测工位 channel 必须是非负整数")
    for key, allowed in _ENUMS.items():
        if key in values and values[key] is not None and values[key] not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"{key} 必须是 {sorted(allowed)} 之一",
            )


def _check_channel_exclusivity(
    db: Session, channel_id: int, exclude_id: Optional[int] = None
) -> None:
    """一个检测工位不能同时属于多个启用的包装结算配置."""
    q = db.query(PackagingFlowConfig).filter(PackagingFlowConfig.enabled.is_(True))
    if exclude_id is not None:
        q = q.filter(PackagingFlowConfig.id != exclude_id)
    for other in q.all():
        if int(other.channel_id or 0) == channel_id:
            raise HTTPException(
                status_code=400,
                detail=f"工位 {channel_id} 已属于包装结算配置 '{other.name}' (id={other.id})",
            )


def _reload(db: Session) -> None:
    try:
        from backend.services.packaging_flow_coordinator import get_coordinator
        get_coordinator().reload_configs(db)
    except Exception as e:
        print(f"[PackagingFlow] reload 协调器异常: {e}")


def _serialize(row: PackagingFlowConfig) -> PackagingFlowConfigResponse:
    return PackagingFlowConfigResponse(
        id=row.id,
        name=row.name,
        enabled=bool(row.enabled),
        channel_id=int(row.channel_id or 0),
        scan_device_id=row.scan_device_id,
        pull_conn_id=row.pull_conn_id,
        box_count_source=row.box_count_source or "field",
        box_count_field=row.box_count_field or "dispatch_qty",
        tray_qty_mode=row.tray_qty_mode or "fixed",
        tray_qty_fixed=int(row.tray_qty_fixed or 0),
        tray_qty_table=row.tray_qty_table,
        trays_per_box_mode=row.trays_per_box_mode or "fixed",
        trays_per_box_fixed=int(row.trays_per_box_fixed or 4),
        trays_per_box_table=row.trays_per_box_table,
        label_match=row.label_match or "strip_hyphen",
        label_len=int(row.label_len or 0),
        hyphen_template=row.hyphen_template,
        on_mes_fail=row.on_mes_fail or "block",
        on_label_mismatch=row.on_label_mismatch or "warn",
        on_short_box=row.on_short_box or "redo",
        on_forced_stop_partial=row.on_forced_stop_partial or "fail",
        on_forced_stop=row.on_forced_stop or "settle",
        forced_settle_on_standby=bool(row.forced_settle_on_standby)
        if row.forced_settle_on_standby is not None else True,
        push_on_complete=bool(row.push_on_complete),
        push_event_type=row.push_event_type or "packaging_complete",
        event_short_box=row.event_short_box,
        event_over_box=row.event_over_box,
        event_tray_ng=row.event_tray_ng,
        event_box_ng=row.event_box_ng,
        event_label_mismatch=row.event_label_mismatch,
        event_label_len=row.event_label_len,
        event_mes_fail=row.event_mes_fail,
        count_unit=getattr(row, "count_unit", None) or "trays",
        items_per_box_source=getattr(row, "items_per_box_source", None) or "project",
        items_per_box_fixed=int(getattr(row, "items_per_box_fixed", 0) or 0),
        slider_total_field=getattr(row, "slider_total_field", None) or "dispatch_qty",
        auto_switch_project=bool(getattr(row, "auto_switch_project", False)),
        spec_to_project=getattr(row, "spec_to_project", None),
        tail_paper_order_required=bool(getattr(row, "tail_paper_order_required", False)),
        tail_paper_step_label=getattr(row, "tail_paper_step_label", None),
        event_missing_paper=getattr(row, "event_missing_paper", None),
    )


# ============================================================
# 端点
# ============================================================


@router.get("",
            dependencies=[Depends(require_perm("system.packaging_flow.view"))])
def list_packaging_flows(db: Session = Depends(get_db)):
    rows = db.query(PackagingFlowConfig).order_by(PackagingFlowConfig.id).all()
    return {"items": [_serialize(r) for r in rows]}


@router.get("/{config_id}",
            response_model=PackagingFlowConfigResponse,
            dependencies=[Depends(require_perm("system.packaging_flow.view"))])
def get_packaging_flow(config_id: int, db: Session = Depends(get_db)):
    row = db.query(PackagingFlowConfig).filter(PackagingFlowConfig.id == config_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="包装结算配置不存在")
    return _serialize(row)


@router.post("",
             response_model=PackagingFlowConfigResponse,
             status_code=201,
             dependencies=[Depends(require_perm("system.packaging_flow.manage"))])
def create_packaging_flow(
    payload: PackagingFlowConfigCreate,
    db: Session = Depends(get_db),
):
    _validate(payload.model_dump())

    existing = db.query(PackagingFlowConfig).filter(
        PackagingFlowConfig.name == payload.name
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"配置名称 '{payload.name}' 已存在")

    if payload.enabled:
        _check_channel_exclusivity(db, payload.channel_id, exclude_id=None)

    row = PackagingFlowConfig(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)

    _reload(db)
    return _serialize(row)


@router.put("/{config_id}",
            response_model=PackagingFlowConfigResponse,
            dependencies=[Depends(require_perm("system.packaging_flow.manage"))])
def update_packaging_flow(
    config_id: int,
    payload: PackagingFlowConfigUpdate,
    db: Session = Depends(get_db),
):
    row = db.query(PackagingFlowConfig).filter(PackagingFlowConfig.id == config_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="包装结算配置不存在")

    update_data = payload.model_dump(exclude_unset=True)
    _validate(update_data)

    new_name = update_data.get("name", row.name)
    if "name" in update_data and new_name != row.name:
        dup = db.query(PackagingFlowConfig).filter(
            PackagingFlowConfig.name == new_name,
            PackagingFlowConfig.id != config_id,
        ).first()
        if dup:
            raise HTTPException(status_code=400, detail=f"配置名称 '{new_name}' 已存在")

    new_channel = update_data.get("channel_id", row.channel_id)
    final_enabled = update_data.get("enabled", row.enabled)
    if final_enabled and ("channel_id" in update_data or "enabled" in update_data):
        _check_channel_exclusivity(db, int(new_channel or 0), exclude_id=config_id)

    for k, v in update_data.items():
        setattr(row, k, v)

    db.commit()
    db.refresh(row)

    _reload(db)
    return _serialize(row)


@router.delete("/{config_id}",
               status_code=204,
               dependencies=[Depends(require_perm("system.packaging_flow.manage"))])
def delete_packaging_flow(config_id: int, db: Session = Depends(get_db)):
    row = db.query(PackagingFlowConfig).filter(PackagingFlowConfig.id == config_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="包装结算配置不存在")
    if row.enabled:
        raise HTTPException(
            status_code=409,
            detail="enabled=True 的配置不能直接删除, 请先 PUT enabled=false",
        )
    db.delete(row)
    db.commit()
    _reload(db)
    return None


@router.get("/{config_id}/state",
            dependencies=[Depends(require_perm("system.packaging_flow.view"))])
def get_packaging_flow_state(config_id: int):
    """查当前进行中的工单/箱进度快照 (供 Monitor 页 polling)."""
    from backend.services.packaging_flow_coordinator import get_coordinator
    coord = get_coordinator()
    cfg = coord.get_config(config_id)
    if not cfg:
        raise HTTPException(
            status_code=404,
            detail="配置不在协调器内存 (可能 disabled 或未 reload)",
        )
    return {"config": cfg, "state": coord.get_state(config_id)}


class ScanInput(BaseModel):
    code: str
    channel_id: Optional[int] = None
    scan_device_id: Optional[int] = None


@router.post("/scan",
             dependencies=[Depends(require_perm("system.packaging_flow.view"))])
def packaging_scan(payload: ScanInput, db: Session = Depends(get_db)):
    """扫码入口: 把一次扫码喂给包装结算状态机 (工单/箱标签都走这里).

    USB 扫码枪在前端捕获原始码后, 若当前工位/设备归属某个启用的包装结算配置,
    就 POST 这条码进来驱动状态机. 没有任何启用配置时返回 handled=False,
    前端据此回退到默认的"扫码拉单 / 绑件"行为 — 与不配置时零差异.
    """
    from backend.services.packaging_flow_coordinator import get_coordinator
    coord = get_coordinator()
    if not coord.list_loaded_config_ids():
        return {"handled": False, "reason": "no_packaging_config"}
    code = (payload.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="扫码内容不能为空")
    coord.on_scan(code, db, channel_id=payload.channel_id,
                  scan_device_id=payload.scan_device_id)
    config_id = coord.resolve_config_id(payload.channel_id, payload.scan_device_id)
    if config_id is None:
        return {"handled": False, "reason": "no_matching_config"}
    return {"handled": True, "config_id": config_id, "state": coord.get_state(config_id)}
