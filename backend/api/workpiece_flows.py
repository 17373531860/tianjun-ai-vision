"""Workpiece Flow (串行流水线) CRUD API — RFC 11 M4.

客户视角:
  流水线场景下需要"单台机器 + N 个摄像头沿流水线依次拍同一工件 + 全部 OK 才算合格".
  用户在 Settings 页创建一个流水线 line-A, 选 channel [0,1,2] 顺序, 触发模式
  time_window/scan/physical, 保存. 后台 workpiece_flow_configs 表加一行,
  WorkpieceFlowCoordinator.reload_flows() 立即生效.

端点 (前缀 /api/v1/workpiece-flows):
  GET    /                      列表
  GET    /{id}                  详情
  POST   /                      创建 (校验 + 与 channel_groups 互斥)
  PUT    /{id}                  更新 (enabled toggle 时 reload)
  DELETE /{id}                  删除 (必须 enabled=False)
  GET    /{id}/state            当前 in-flight 工件状态
  GET    /{id}/runs             历史 flow runs (分页)
  GET    /runs/{run_id}         单个 run 详情

安全:
  - 所有 CRUD 需 ``system.workpiece_flow.manage`` 权限
  - 查询 (GET) 需 ``system.workpiece_flow.view`` 权限
  - 默认无 auth 时全部放行 (v3.10 兼容)

零差异默认: 没创建任何流水线时, 所有行为与 v3.13 完全一致.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.mes_models import WorkpieceFlowConfig, WorkpieceFlowRun


router = APIRouter()


# ============================================================
# Pydantic schemas
# ============================================================


class WorkpieceFlowConfigBase(BaseModel):
    name: str
    station_channel_ids: List[int]
    trigger_mode: str = "time_window"
    scan_device_id: Optional[int] = None
    scan_bind_strategy: str = "entry"
    fifo_max_in_flight: int = 3
    cycle_to_cycle_window_ms: int = 15000
    physical_trigger_config: Optional[Dict[str, Any]] = None
    settle_strategy: str = "all_ok_required"
    short_circuit_on_ng: bool = True
    workpiece_timeout_ms: int = 60000
    timeout_action: str = "force_ng"
    enabled: bool = False


class WorkpieceFlowConfigCreate(WorkpieceFlowConfigBase):
    pass


class WorkpieceFlowConfigUpdate(BaseModel):
    name: Optional[str] = None
    station_channel_ids: Optional[List[int]] = None
    trigger_mode: Optional[str] = None
    scan_device_id: Optional[int] = None
    scan_bind_strategy: Optional[str] = None
    fifo_max_in_flight: Optional[int] = None
    cycle_to_cycle_window_ms: Optional[int] = None
    physical_trigger_config: Optional[Dict[str, Any]] = None
    settle_strategy: Optional[str] = None
    short_circuit_on_ng: Optional[bool] = None
    workpiece_timeout_ms: Optional[int] = None
    timeout_action: Optional[str] = None
    enabled: Optional[bool] = None


class WorkpieceFlowConfigResponse(WorkpieceFlowConfigBase):
    id: int

    class Config:
        from_attributes = True


class WorkpieceFlowRunResponse(BaseModel):
    id: int
    flow_config_id: int
    workpiece_id: Optional[int]
    flow_uuid: str
    serial_no: Optional[str]
    status: str
    station_cycle_ids: Optional[List[Optional[int]]]
    station_results: Optional[List[Optional[str]]]
    final_result: Optional[str]
    trigger_mode: Optional[str]
    trigger_source_id: Optional[int]
    started_at: Optional[str]
    completed_at: Optional[str]

    class Config:
        from_attributes = True


# ============================================================
# 校验
# ============================================================


_ALLOWED_TRIGGER_MODES = {"time_window", "scan", "physical"}
_ALLOWED_SCAN_STRATEGIES = {"entry", "each_station"}
_ALLOWED_SETTLE_STRATEGIES = {"all_ok_required"}
_ALLOWED_TIMEOUT_ACTIONS = {"force_ng", "drop", "alarm_only"}


def _validate_flow_config(
    name: str,
    station_channel_ids: List[int],
    trigger_mode: str,
    scan_bind_strategy: str,
    settle_strategy: str,
    timeout_action: str,
    fifo_max_in_flight: int,
    workpiece_timeout_ms: int,
) -> None:
    """校验流水线配置. 不通过抛 HTTPException 400."""
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="流水线名称不能为空")
    if not isinstance(station_channel_ids, list) or len(station_channel_ids) < 2:
        raise HTTPException(status_code=400, detail="流水线至少需要 2 个工位")
    if len(set(station_channel_ids)) != len(station_channel_ids):
        raise HTTPException(status_code=400, detail="工位列表不能有重复 channel")
    if any(not isinstance(c, int) or c < 0 for c in station_channel_ids):
        raise HTTPException(status_code=400, detail="工位 channel id 必须是非负整数")
    if trigger_mode not in _ALLOWED_TRIGGER_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"trigger_mode 必须是 {sorted(_ALLOWED_TRIGGER_MODES)} 之一",
        )
    if scan_bind_strategy not in _ALLOWED_SCAN_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"scan_bind_strategy 必须是 {sorted(_ALLOWED_SCAN_STRATEGIES)} 之一",
        )
    if settle_strategy not in _ALLOWED_SETTLE_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"settle_strategy 必须是 {sorted(_ALLOWED_SETTLE_STRATEGIES)} 之一",
        )
    if timeout_action not in _ALLOWED_TIMEOUT_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"timeout_action 必须是 {sorted(_ALLOWED_TIMEOUT_ACTIONS)} 之一",
        )
    if fifo_max_in_flight < 1 or fifo_max_in_flight > 20:
        raise HTTPException(
            status_code=400, detail="fifo_max_in_flight 必须在 [1, 20] 范围内"
        )
    if workpiece_timeout_ms < 100 or workpiece_timeout_ms > 600_000:
        raise HTTPException(
            status_code=400, detail="workpiece_timeout_ms 必须在 [100, 600000] 范围内"
        )


def _check_station_exclusivity(
    db: Session,
    station_channel_ids: List[int],
    exclude_flow_id: Optional[int] = None,
) -> None:
    """跨 flow 工位唯一性校验 (一个 channel 不能同时属于多个 enabled flow).

    工位组互斥也在这里查 (一个 channel 不能同时属于 enabled flow 和 enabled group).
    """
    # 1. flow 间互斥
    query = db.query(WorkpieceFlowConfig).filter(WorkpieceFlowConfig.enabled.is_(True))
    if exclude_flow_id is not None:
        query = query.filter(WorkpieceFlowConfig.id != exclude_flow_id)
    for other in query.all():
        other_stations = set(other.station_channel_ids or [])
        for cid in station_channel_ids:
            if cid in other_stations:
                raise HTTPException(
                    status_code=400,
                    detail=f"channel {cid} 已属于流水线 '{other.name}' (id={other.id})",
                )

    # 2. 与 channel_groups 互斥
    try:
        from backend.models.models import ChannelGroup
        groups = db.query(ChannelGroup).filter(ChannelGroup.enabled.is_(True)).all()
        for g in groups:
            members = set(g.member_channel_ids or [])
            for cid in station_channel_ids:
                if cid in members:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"channel {cid} 已属于工位组 '{g.name}' (id={g.id}). "
                            f"工位组 (并行) 与流水线 (串行) 互斥, 同一通道不能同时属于两者"
                        ),
                    )
    except HTTPException:
        raise
    except Exception:
        # channel_groups 模块查不到不影响 flow 创建 (向后兼容)
        pass


def _validate_scan_device_if_needed(
    db: Session,
    trigger_mode: str,
    scan_device_id: Optional[int],
) -> None:
    """scan 模式下若指定了 device_id, 校验该设备存在."""
    if trigger_mode != "scan" or scan_device_id is None:
        return
    try:
        from backend.models.mes_models import ScannerDevice
        device = db.query(ScannerDevice).filter(ScannerDevice.id == scan_device_id).first()
        if not device:
            raise HTTPException(
                status_code=400,
                detail=f"扫码器 id={scan_device_id} 不存在",
            )
    except HTTPException:
        raise
    except Exception:
        pass


def _has_in_flight(coord, flow_id: int) -> bool:
    """检查 flow 是否有 in-flight run, 防止修改时打乱状态机."""
    try:
        return len(coord.list_in_flight(flow_id)) > 0
    except Exception:
        return False


def _reload_coordinator(db: Session) -> None:
    try:
        from backend.services.workpiece_flow_coordinator import get_coordinator
        get_coordinator().reload_flows(db)
    except Exception as e:
        print(f"[WorkpieceFlow] reload_coordinator 异常: {e}")


def _serialize(row: WorkpieceFlowConfig) -> WorkpieceFlowConfigResponse:
    return WorkpieceFlowConfigResponse(
        id=row.id,
        name=row.name,
        station_channel_ids=list(row.station_channel_ids or []),
        trigger_mode=row.trigger_mode or "time_window",
        scan_device_id=row.scan_device_id,
        scan_bind_strategy=row.scan_bind_strategy or "entry",
        fifo_max_in_flight=int(row.fifo_max_in_flight or 3),
        cycle_to_cycle_window_ms=int(row.cycle_to_cycle_window_ms or 15000),
        physical_trigger_config=row.physical_trigger_config,
        settle_strategy=row.settle_strategy or "all_ok_required",
        short_circuit_on_ng=bool(row.short_circuit_on_ng),
        workpiece_timeout_ms=int(row.workpiece_timeout_ms or 60000),
        timeout_action=row.timeout_action or "force_ng",
        enabled=bool(row.enabled),
    )


def _serialize_run(row: WorkpieceFlowRun) -> WorkpieceFlowRunResponse:
    return WorkpieceFlowRunResponse(
        id=row.id,
        flow_config_id=row.flow_config_id,
        workpiece_id=row.workpiece_id,
        flow_uuid=row.flow_uuid,
        serial_no=row.serial_no,
        status=row.status,
        station_cycle_ids=row.station_cycle_ids,
        station_results=row.station_results,
        final_result=row.final_result,
        trigger_mode=row.trigger_mode,
        trigger_source_id=row.trigger_source_id,
        started_at=row.started_at.isoformat() if row.started_at else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
    )


# ============================================================
# 端点
# ============================================================


@router.get("",
            dependencies=[Depends(require_perm("system.workpiece_flow.view"))])
def list_workpiece_flows(db: Session = Depends(get_db)):
    rows = db.query(WorkpieceFlowConfig).order_by(WorkpieceFlowConfig.id).all()
    return {"items": [_serialize(r) for r in rows]}


@router.get("/{flow_id}",
            response_model=WorkpieceFlowConfigResponse,
            dependencies=[Depends(require_perm("system.workpiece_flow.view"))])
def get_workpiece_flow(flow_id: int, db: Session = Depends(get_db)):
    row = db.query(WorkpieceFlowConfig).filter(WorkpieceFlowConfig.id == flow_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="流水线不存在")
    return _serialize(row)


@router.post("",
             response_model=WorkpieceFlowConfigResponse,
             status_code=201,
             dependencies=[Depends(require_perm("system.workpiece_flow.manage"))])
def create_workpiece_flow(
    payload: WorkpieceFlowConfigCreate,
    db: Session = Depends(get_db),
):
    _validate_flow_config(
        payload.name,
        payload.station_channel_ids,
        payload.trigger_mode,
        payload.scan_bind_strategy,
        payload.settle_strategy,
        payload.timeout_action,
        payload.fifo_max_in_flight,
        payload.workpiece_timeout_ms,
    )

    existing = db.query(WorkpieceFlowConfig).filter(
        WorkpieceFlowConfig.name == payload.name
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"流水线名称 '{payload.name}' 已存在")

    if payload.enabled:
        _check_station_exclusivity(db, payload.station_channel_ids, exclude_flow_id=None)

    _validate_scan_device_if_needed(db, payload.trigger_mode, payload.scan_device_id)

    row = WorkpieceFlowConfig(
        name=payload.name,
        enabled=payload.enabled,
        station_channel_ids=payload.station_channel_ids,
        trigger_mode=payload.trigger_mode,
        scan_device_id=payload.scan_device_id,
        scan_bind_strategy=payload.scan_bind_strategy,
        fifo_max_in_flight=payload.fifo_max_in_flight,
        cycle_to_cycle_window_ms=payload.cycle_to_cycle_window_ms,
        physical_trigger_config=payload.physical_trigger_config,
        settle_strategy=payload.settle_strategy,
        short_circuit_on_ng=payload.short_circuit_on_ng,
        workpiece_timeout_ms=payload.workpiece_timeout_ms,
        timeout_action=payload.timeout_action,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    _reload_coordinator(db)
    return _serialize(row)


@router.put("/{flow_id}",
            response_model=WorkpieceFlowConfigResponse,
            dependencies=[Depends(require_perm("system.workpiece_flow.manage"))])
def update_workpiece_flow(
    flow_id: int,
    payload: WorkpieceFlowConfigUpdate,
    db: Session = Depends(get_db),
):
    row = db.query(WorkpieceFlowConfig).filter(WorkpieceFlowConfig.id == flow_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="流水线不存在")

    # 修改成员或基础参数前不能有 in-flight
    from backend.services.workpiece_flow_coordinator import get_coordinator as _get_wfc
    coord = _get_wfc()
    if _has_in_flight(coord, flow_id):
        raise HTTPException(
            status_code=409,
            detail="流水线有 in-flight 工件, 请等所有工件走完再修改",
        )

    update_data = payload.model_dump(exclude_unset=True)

    new_name = update_data.get("name", row.name)
    new_stations = update_data.get("station_channel_ids", row.station_channel_ids or [])
    new_trigger_mode = update_data.get("trigger_mode", row.trigger_mode)
    new_scan_strategy = update_data.get("scan_bind_strategy", row.scan_bind_strategy)
    new_settle = update_data.get("settle_strategy", row.settle_strategy)
    new_timeout_action = update_data.get("timeout_action", row.timeout_action)
    new_fifo = update_data.get("fifo_max_in_flight", row.fifo_max_in_flight)
    new_timeout_ms = update_data.get("workpiece_timeout_ms", row.workpiece_timeout_ms)
    new_scan_device = update_data.get("scan_device_id", row.scan_device_id)

    if "name" in update_data and new_name != row.name:
        existing = db.query(WorkpieceFlowConfig).filter(
            WorkpieceFlowConfig.name == new_name,
            WorkpieceFlowConfig.id != flow_id,
        ).first()
        if existing:
            raise HTTPException(
                status_code=400, detail=f"流水线名称 '{new_name}' 已存在"
            )

    _validate_flow_config(
        new_name, list(new_stations), new_trigger_mode, new_scan_strategy,
        new_settle, new_timeout_action, int(new_fifo), int(new_timeout_ms),
    )

    final_enabled = update_data.get("enabled", row.enabled)
    members_or_enabled_changed = (
        "station_channel_ids" in update_data or "enabled" in update_data
    )
    if final_enabled and members_or_enabled_changed:
        _check_station_exclusivity(db, list(new_stations), exclude_flow_id=flow_id)

    if "scan_device_id" in update_data or "trigger_mode" in update_data:
        _validate_scan_device_if_needed(db, new_trigger_mode, new_scan_device)

    for k, v in update_data.items():
        setattr(row, k, v)

    db.commit()
    db.refresh(row)

    _reload_coordinator(db)
    return _serialize(row)


@router.delete("/{flow_id}",
               status_code=204,
               dependencies=[Depends(require_perm("system.workpiece_flow.manage"))])
def delete_workpiece_flow(flow_id: int, db: Session = Depends(get_db)):
    row = db.query(WorkpieceFlowConfig).filter(WorkpieceFlowConfig.id == flow_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="流水线不存在")

    if row.enabled:
        raise HTTPException(
            status_code=409,
            detail="enabled=True 的流水线不能直接删除, 请先 PUT enabled=false",
        )

    db.delete(row)
    db.commit()
    _reload_coordinator(db)
    return None


@router.get("/{flow_id}/state",
            dependencies=[Depends(require_perm("system.workpiece_flow.view"))])
def get_workpiece_flow_state(flow_id: int):
    """查流水线当前 in-flight 工件快照 (供 Monitor 页 polling)."""
    from backend.services.workpiece_flow_coordinator import get_coordinator
    coord = get_coordinator()
    flow = coord.get_flow(flow_id)
    if not flow:
        raise HTTPException(
            status_code=404,
            detail="流水线不在 Coordinator 内存 (可能 disabled 或未 reload)",
        )
    in_flight = coord.list_in_flight(flow_id)
    return {
        "flow": flow,
        "in_flight": in_flight,
        "in_flight_count": len(in_flight),
    }


@router.get("/{flow_id}/runs",
            dependencies=[Depends(require_perm("system.workpiece_flow.view"))])
def list_workpiece_flow_runs(
    flow_id: int,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """流水线历史 run 列表 (分页, 按 id 倒序)."""
    flow = db.query(WorkpieceFlowConfig).filter(WorkpieceFlowConfig.id == flow_id).first()
    if not flow:
        raise HTTPException(status_code=404, detail="流水线不存在")

    query = db.query(WorkpieceFlowRun).filter(WorkpieceFlowRun.flow_config_id == flow_id)
    if status:
        query = query.filter(WorkpieceFlowRun.status == status)
    total = query.count()
    rows = query.order_by(WorkpieceFlowRun.id.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_serialize_run(r) for r in rows],
    }


@router.get("/runs/{run_id}",
            response_model=WorkpieceFlowRunResponse,
            dependencies=[Depends(require_perm("system.workpiece_flow.view"))])
def get_workpiece_flow_run(run_id: int, db: Session = Depends(get_db)):
    row = db.query(WorkpieceFlowRun).filter(WorkpieceFlowRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="run 不存在")
    return _serialize_run(row)
