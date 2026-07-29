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

from backend.core.auth_deps import require_perm, get_current_user, CurrentUser
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
    hyphen_pos: int = 0
    # 复合条码取段 (v3.30.1, 默认关)
    composite_label_enabled: bool = False
    composite_delimiter: str = "|"
    composite_pick_mode: str = "prefix"
    composite_prefix: Optional[str] = None
    composite_index: int = 1
    # 工单号识别规则 (v3.30.1, 默认空=不过滤)
    order_code_pattern: Optional[str] = None
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
    match_project_by_name: bool = False
    name_match_strict_boundary: bool = False
    tail_paper_order_required: bool = False
    tail_paper_step_label: Optional[str] = None
    # v3.34.1 放工单=尾箱收尾动作, 默认关=老行为; v3.43 语义: 箱归周期结算, 放工单归工单收尾
    tail_paper_as_close_action: bool = False
    event_missing_paper: Optional[int] = None
    # v3.43 缺工单判定方式二选一 (仅 as_close_action 模式生效):
    # scan_alarm=True → 扫新单判定(默认); False 且 timeout_s>0 → 时限判定
    tail_paper_scan_alarm: bool = True
    tail_paper_timeout_s: int = 0
    # 缺油嘴 gate (v3.23, 每箱查, 默认关)
    oil_nozzle_required: bool = False
    oil_nozzle_step_label: Optional[str] = None
    event_missing_nozzle: Optional[int] = None
    # 已完成(OK)工单重扫拦截 (v3.42.1, 默认关): 报警提示且不重新录入
    block_completed_order_rescan: bool = False
    event_completed_order_rescan: Optional[int] = None
    # 包装工单镜像进工单管理 (v3.45, 默认开): 开工/收尾/中止同步 work_orders
    sync_work_orders: bool = True
    # 组⑧ 箱标签扫码授权 + 标签取本箱数量 (v3.45, 全可选默认关)
    box_label_scan_required: bool = False
    label_qty_enabled: bool = False
    label_qty_segment: int = 3
    label_qty_pattern: Optional[str] = None
    label_rescan_action: str = "ignore"
    unauthorized_cycle_action: str = "hold"
    label_total_check: bool = False
    event_box_not_scanned: Optional[int] = None
    event_label_qty_missing: Optional[int] = None
    event_label_total_mismatch: Optional[int] = None


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
    hyphen_pos: Optional[int] = None
    composite_label_enabled: Optional[bool] = None
    composite_delimiter: Optional[str] = None
    composite_pick_mode: Optional[str] = None
    composite_prefix: Optional[str] = None
    composite_index: Optional[int] = None
    order_code_pattern: Optional[str] = None
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
    match_project_by_name: Optional[bool] = None
    name_match_strict_boundary: Optional[bool] = None
    tail_paper_order_required: Optional[bool] = None
    tail_paper_step_label: Optional[str] = None
    tail_paper_as_close_action: Optional[bool] = None
    event_missing_paper: Optional[int] = None
    tail_paper_scan_alarm: Optional[bool] = None
    tail_paper_timeout_s: Optional[int] = None
    oil_nozzle_required: Optional[bool] = None
    oil_nozzle_step_label: Optional[str] = None
    event_missing_nozzle: Optional[int] = None
    block_completed_order_rescan: Optional[bool] = None
    event_completed_order_rescan: Optional[int] = None
    sync_work_orders: Optional[bool] = None
    box_label_scan_required: Optional[bool] = None
    label_qty_enabled: Optional[bool] = None
    label_qty_segment: Optional[int] = None
    label_qty_pattern: Optional[str] = None
    label_rescan_action: Optional[str] = None
    unauthorized_cycle_action: Optional[str] = None
    label_total_check: Optional[bool] = None
    event_box_not_scanned: Optional[int] = None
    event_label_qty_missing: Optional[int] = None
    event_label_total_mismatch: Optional[int] = None


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
    "label_match": {"exact", "strip_hyphen", "digits_only", "insert_char"},
    "on_mes_fail": {"block", "offline"},
    "on_label_mismatch": {"off", "block", "warn"},
    "on_short_box": {"redo", "void"},
    "on_forced_stop_partial": {"pass", "fail"},
    "on_forced_stop": {"settle", "abort", "keep"},
    "count_unit": {"trays", "sliders"},
    "items_per_box_source": {"project", "config"},
    "label_rescan_action": {"ignore", "update"},
    "unauthorized_cycle_action": {"hold", "book"},
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
        hyphen_pos=int(getattr(row, "hyphen_pos", 0) or 0),
        composite_label_enabled=bool(getattr(row, "composite_label_enabled", False)),
        composite_delimiter=getattr(row, "composite_delimiter", None) or "|",
        composite_pick_mode=getattr(row, "composite_pick_mode", None) or "prefix",
        composite_prefix=getattr(row, "composite_prefix", None),
        composite_index=int(getattr(row, "composite_index", 1) or 1),
        order_code_pattern=getattr(row, "order_code_pattern", None),
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
        match_project_by_name=bool(getattr(row, "match_project_by_name", False)),
        name_match_strict_boundary=bool(getattr(row, "name_match_strict_boundary", False)),
        tail_paper_order_required=bool(getattr(row, "tail_paper_order_required", False)),
        tail_paper_step_label=getattr(row, "tail_paper_step_label", None),
        tail_paper_as_close_action=bool(getattr(row, "tail_paper_as_close_action", False)),
        event_missing_paper=getattr(row, "event_missing_paper", None),
        tail_paper_scan_alarm=(
            bool(row.tail_paper_scan_alarm)
            if getattr(row, "tail_paper_scan_alarm", None) is not None else True
        ),
        tail_paper_timeout_s=int(getattr(row, "tail_paper_timeout_s", 0) or 0),
        oil_nozzle_required=bool(getattr(row, "oil_nozzle_required", False)),
        oil_nozzle_step_label=getattr(row, "oil_nozzle_step_label", None),
        event_missing_nozzle=getattr(row, "event_missing_nozzle", None),
        block_completed_order_rescan=bool(getattr(row, "block_completed_order_rescan", False)),
        event_completed_order_rescan=getattr(row, "event_completed_order_rescan", None),
        sync_work_orders=getattr(row, "sync_work_orders", None) not in (False, 0),
        box_label_scan_required=bool(getattr(row, "box_label_scan_required", False)),
        label_qty_enabled=bool(getattr(row, "label_qty_enabled", False)),
        label_qty_segment=int(getattr(row, "label_qty_segment", 3) or 3),
        label_qty_pattern=getattr(row, "label_qty_pattern", None),
        label_rescan_action=getattr(row, "label_rescan_action", None) or "ignore",
        unauthorized_cycle_action=getattr(row, "unauthorized_cycle_action", None) or "hold",
        label_total_check=bool(getattr(row, "label_total_check", False)),
        event_box_not_scanned=getattr(row, "event_box_not_scanned", None),
        event_label_qty_missing=getattr(row, "event_label_qty_missing", None),
        event_label_total_mismatch=getattr(row, "event_label_total_mismatch", None),
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
    """查当前进行中的工单/箱进度快照 (供 Monitor 页 polling).

    v3.44: 走展示态 — 工单收尾后仍返回收尾快照 (status=completed/aborted),
    前端保留工单号/最终结果/箱明细直到下一张工单开工, 不再一片空白.
    """
    from backend.services.packaging_flow_coordinator import get_coordinator
    coord = get_coordinator()
    cfg = coord.get_config(config_id)
    if not cfg:
        raise HTTPException(
            status_code=404,
            detail="配置不在协调器内存 (可能 disabled 或未 reload)",
        )
    return {"config": cfg, "state": coord.get_display_state(config_id)}


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
    state = coord.get_state(config_id)
    try:
        from backend.core import debug_center
        if debug_center.is_on("backend.packaging"):
            st = state or {}
            debug_center.dbg(
                "backend.packaging", "HTTP扫码入口",
                f"code={code!r} ch={payload.channel_id} cfg_id={config_id} "
                f"order={st.get('order_no')} box_done={st.get('box_done')}/{st.get('box_total')}")
    except Exception:
        pass
    return {"handled": True, "config_id": config_id, "state": state}


class ForceSettleInput(BaseModel):
    reason: str
    channel_id: Optional[int] = None


@router.post("/{config_id}/force-settle",
             dependencies=[Depends(require_perm("system.packaging_flow.force_settle"))])
def packaging_force_settle(config_id: int, payload: ForceSettleInput,
                           db: Session = Depends(get_db),
                           user: CurrentUser = Depends(get_current_user)):
    """管理员 / 主管「强制结案」当前进行中工单 (必填理由, 审计留痕).

    现场异常 (卡单 / 工单提前收尾 / 漏箱要硬收) 时由有权限者手动收尾, 不靠停止/待机.
    强制走 settle 收尾 (忽略配置 keep/abort), 未满箱按 on_forced_stop_partial 判合格性.
    需 system.packaging_flow.force_settle 权限 (admin / engineer; 操作员无 → 403).
    """
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="强制结案必须填写理由")
    from backend.services.packaging_flow_coordinator import get_coordinator
    coord = get_coordinator()
    if config_id not in coord.list_loaded_config_ids():
        raise HTTPException(status_code=404, detail="包装结算配置不存在或未启用")
    operator = (getattr(user, "username", None) or getattr(user, "display_name", None) or "未知")
    settled = coord.force_settle_manual(config_id, db, reason=reason, operator=operator)
    if not settled:
        raise HTTPException(status_code=409, detail="当前没有进行中的工单可结案")

    last_run = None
    row = (db.query(PackagingFlowRun)
           .filter(PackagingFlowRun.flow_config_id == config_id)
           .order_by(PackagingFlowRun.id.desc()).first())
    if row is not None:
        last_run = {
            "order_no": row.order_no, "status": row.status,
            "final_result": row.final_result, "box_total": row.box_total,
            "box_done": row.box_done, "box_ng": row.box_ng,
            "forced_reason": row.forced_reason, "forced_by": row.forced_by,
        }
    try:
        from backend.core import debug_center
        debug_center.dbg("backend.packaging", "强制结案",
                         f"cfg_id={config_id} by={operator} reason={reason!r} "
                         f"final={last_run.get('final_result') if last_run else '?'}")
    except Exception:
        pass
    return {"success": True, "config_id": config_id, "forced_by": operator,
            "reason": reason, "last_run": last_run}


class SupplementInput(BaseModel):
    target_count: Optional[int] = None   # None=自动补齐到目标; 传值=手动指定最终数
    reason: Optional[str] = None
    channel_id: Optional[int] = None


@router.post("/{config_id}/supplement-sliders",
             dependencies=[Depends(require_perm("monitor.detection.ack"))])
def packaging_supplement_sliders(config_id: int, payload: SupplementInput,
                                 db: Session = Depends(get_db),
                                 user: CurrentUser = Depends(get_current_user)):
    """对挂起中的「少装」箱补滑块: 补齐数量直接落账, 不重置周期 (需 monitor.detection.ack 权限).

    需项目开启 NG 补做策略(补数量); 少装时本箱会进入 pending_remediation 挂起态, 由此接口推进.
    """
    from backend.services.packaging_flow_coordinator import get_coordinator
    coord = get_coordinator()
    if config_id not in coord.list_loaded_config_ids():
        raise HTTPException(status_code=404, detail="包装结算配置不存在或未启用")
    operator = (getattr(user, "username", None) or getattr(user, "display_name", None) or "未知")
    ok = coord.supplement_sliders(config_id, db, target_count=payload.target_count,
                                  operator=operator, reason=payload.reason)
    if not ok:
        raise HTTPException(status_code=409, detail="当前没有等待补做的少装箱")
    try:
        from backend.core import debug_center
        debug_center.dbg("backend.packaging", "补滑块",
                         f"cfg_id={config_id} by={operator} target={payload.target_count}")
    except Exception:
        pass
    return {"success": True, "config_id": config_id, "supplemented_by": operator,
            "state": coord.get_state(config_id)}


class RemediationRedoInput(BaseModel):
    channel_id: Optional[int] = None


@router.post("/{config_id}/remediation-redo",
             dependencies=[Depends(require_perm("monitor.detection.ack"))])
def packaging_remediation_redo(config_id: int, payload: RemediationRedoInput,
                               db: Session = Depends(get_db),
                               user: CurrentUser = Depends(get_current_user)):
    """对挂起中的少装箱「重做」: 丢弃本箱, 等下一检测周期重新结算 (需 monitor.detection.ack 权限)."""
    from backend.services.packaging_flow_coordinator import get_coordinator
    coord = get_coordinator()
    if config_id not in coord.list_loaded_config_ids():
        raise HTTPException(status_code=404, detail="包装结算配置不存在或未启用")
    operator = (getattr(user, "username", None) or getattr(user, "display_name", None) or "未知")
    ok = coord.redo_pending(config_id, db, operator=operator)
    if not ok:
        raise HTTPException(status_code=409, detail="当前没有等待补做的少装箱")
    return {"success": True, "config_id": config_id, "state": coord.get_state(config_id)}
