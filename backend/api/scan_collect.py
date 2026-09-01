"""
周期多码采集 REST API — v3.56

前缀 /api/v1/scan-collect（router_manifest 登记）：

- GET  /scan-collect/config?project_id=N        取某项目配置（无则回默认骨架）
- PUT  /scan-collect/config?project_id=N        保存配置（mes.scanner.edit 权限）
- GET  /scan-collect/state?channel=N            当前码组实况（Monitor 轮询兜底）
- POST /scan-collect/remove-code                纠错：删当前组内一个码
- POST /scan-collect/clear                      纠错：清空当前组重扫
- POST /scan-collect/resolve-ng                 NG 挂起人工放行（v3.56.1）
- GET  /scan-collect/records                    追溯：按 workpiece_id / group_id 查逐码记录
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.models import Project
from backend.models.scan_collect_models import ScanCollectConfig, ScanCollectRecord
from backend.services.scan_collect import DEFAULT_CONFIG, get_scan_collect_engine

router = APIRouter(prefix="/scan-collect", tags=["ScanCollect"])


# ============================================================
# Schema
# ============================================================

class SlotDef(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    label: str = ""
    count: int = Field(1, ge=1, le=99)
    regex: str = ""
    role: str = Field("", pattern="^(|closing)$")
    # v3.56.1 槽位级策略覆盖 (inherit=跟随全局):
    # 循环治具(工装码)跨工件必然重复 → 该槽 dedup_cross_group=off 豁免
    dedup_cross_group: str = Field(
        "inherit", pattern="^(inherit|off|reject|ng_alarm)$")
    # 芯子多扫直接判 NG、多扫母排(=忘收尾开新件)只拦提示 → 槽级 on_overflow
    on_overflow: str = Field("inherit", pattern="^(inherit|reject|ng_alarm)$")


class ConfigPayload(BaseModel):
    enabled: bool = False
    slots: List[SlotDef] = []
    sequence_fallback: bool = True
    dedup_in_group: str = Field("ng_alarm", pattern="^(ng_alarm|reject)$")
    dedup_cross_group: str = Field("off", pattern="^(off|reject|ng_alarm)$")
    settle_on: str = Field("closing", pattern="^(closing|all_filled)$")
    on_overflow: str = Field("reject", pattern="^(reject|ng_alarm)$")
    on_unmatched: str = Field("reject", pattern="^(reject|ng_alarm)$")
    timeout_sec: float = Field(0, ge=0, le=86400)
    event_ok_id: Optional[int] = 1
    event_ng_id: Optional[int] = 2
    # v3.56.1 (默认全关 = 存量行为):
    ng_pending: bool = False          # 少扫收尾挂起等补扫/人工放行
    vision_gate: bool = False         # 视觉+扫码双重验证
    vision_window_sec: float = Field(300, ge=0, le=86400)
    vision_missing: str = Field("ignore", pattern="^(ignore|ng)$")


class RemoveCodeRequest(BaseModel):
    channel_id: int = 0
    record_id: int


class ClearRequest(BaseModel):
    channel_id: int = 0


# ============================================================
# 配置
# ============================================================

def _serialize_config(row: Optional[ScanCollectConfig],
                      project_id: int) -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    enabled = False
    if row is not None:
        cfg.update(row.config or {})
        enabled = bool(row.enabled)
    return {"project_id": project_id, "enabled": enabled, **cfg}


@router.get("/config")
def get_config(project_id: int = Query(...), db: Session = Depends(get_db)):
    row = (db.query(ScanCollectConfig)
           .filter(ScanCollectConfig.project_id == project_id).first())
    return _serialize_config(row, project_id)


@router.put("/config",
            dependencies=[Depends(require_perm("mes.scanner.edit"))])
def put_config(payload: ConfigPayload,
               project_id: int = Query(...),
               db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    slots = [s.model_dump() for s in payload.slots]
    if payload.enabled:
        if not slots:
            raise HTTPException(status_code=400, detail="启用前至少配置一个码类别")
        keys = [s["key"] for s in slots]
        if len(keys) != len(set(keys)):
            raise HTTPException(status_code=400, detail="码类别 key 不能重复")
        closing = [s for s in slots if s.get("role") == "closing"]
        if payload.settle_on == "closing" and len(closing) != 1:
            raise HTTPException(
                status_code=400,
                detail="「扫收尾码结算」模式必须且只能有一个收尾码类别")
        import re as _re
        for s in slots:
            if s.get("regex"):
                try:
                    _re.compile(s["regex"])
                except _re.error as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"类别「{s.get('label') or s['key']}」正则无效: {e}")

    config = {
        "slots": slots,
        "sequence_fallback": payload.sequence_fallback,
        "dedup_in_group": payload.dedup_in_group,
        "dedup_cross_group": payload.dedup_cross_group,
        "settle_on": payload.settle_on,
        "on_overflow": payload.on_overflow,
        "on_unmatched": payload.on_unmatched,
        "timeout_sec": payload.timeout_sec,
        "event_ok_id": payload.event_ok_id,
        "event_ng_id": payload.event_ng_id,
        "ng_pending": payload.ng_pending,
        "vision_gate": payload.vision_gate,
        "vision_window_sec": payload.vision_window_sec,
        "vision_missing": payload.vision_missing,
    }

    row = (db.query(ScanCollectConfig)
           .filter(ScanCollectConfig.project_id == project_id).first())
    if row is None:
        row = ScanCollectConfig(project_id=project_id)
        db.add(row)
    row.enabled = payload.enabled
    row.config = config
    row.updated_at = datetime.now()
    db.commit()

    get_scan_collect_engine().invalidate_config(project_id)
    return _serialize_config(row, project_id)


# ============================================================
# 实况 / 纠错
# ============================================================

def _active_project_id(db: Session) -> Optional[int]:
    row = db.query(Project.id).filter(Project.is_active == True).first()  # noqa: E712
    return row[0] if row else None


@router.get("/state")
def get_state(channel: int = Query(0), db: Session = Depends(get_db)):
    state = get_scan_collect_engine().get_state(
        db, channel, _active_project_id(db))
    return state or {"enabled": False}


@router.post("/remove-code",
             dependencies=[Depends(require_perm("monitor.detection.ack"))])
def remove_code(req: RemoveCodeRequest, db: Session = Depends(get_db)):
    ok, msg = get_scan_collect_engine().remove_code(
        db, req.channel_id, req.record_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"success": True, "message": msg}


@router.post("/clear",
             dependencies=[Depends(require_perm("monitor.detection.ack"))])
def clear_group(req: ClearRequest, db: Session = Depends(get_db)):
    ok, msg = get_scan_collect_engine().clear_group(db, req.channel_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"success": True, "message": msg}


@router.post("/resolve-ng",
             dependencies=[Depends(require_perm("monitor.detection.ack"))])
def resolve_ng(req: ClearRequest, db: Session = Depends(get_db)):
    """v3.56.1 NG 挂起人工放行: 挂起组按 NG 结算导出, 开放下一工件。"""
    ok, msg = get_scan_collect_engine().resolve_ng(db, req.channel_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"success": True, "message": msg}


# ============================================================
# 追溯
# ============================================================

@router.get("/records")
def list_records(workpiece_id: Optional[int] = Query(None),
                 group_id: Optional[str] = Query(None),
                 include_deleted: bool = Query(False),
                 limit: int = Query(200, ge=1, le=1000),
                 db: Session = Depends(get_db)):
    q = db.query(ScanCollectRecord)
    if workpiece_id is not None:
        q = q.filter(ScanCollectRecord.workpiece_id == workpiece_id)
    elif group_id:
        q = q.filter(ScanCollectRecord.group_id == group_id)
    else:
        raise HTTPException(status_code=400,
                            detail="需要 workpiece_id 或 group_id 参数")
    # 纠错删掉的码默认不进追溯列表 (status=deleted 只作审计留痕)
    if not include_deleted:
        q = q.filter(ScanCollectRecord.status != "deleted")
    rows = q.order_by(ScanCollectRecord.seq.asc()).limit(limit).all()
    return [{
        "id": r.id, "group_id": r.group_id, "channel_id": r.channel_id,
        "project_id": r.project_id, "slot_key": r.slot_key,
        "slot_label": r.slot_label, "code": r.code, "seq": r.seq,
        "status": r.status, "group_result": r.group_result,
        "workpiece_id": r.workpiece_id,
        "scanned_at": r.scanned_at.isoformat() if r.scanned_at else None,
        "settled_at": r.settled_at.isoformat() if r.settled_at else None,
    } for r in rows]
