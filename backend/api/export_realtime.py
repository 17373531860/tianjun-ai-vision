"""
v3.5.0 自定义导出 — 实时规则 CRUD + 测试触发 + 日志查询 API

端点全部前缀 /api/v1/export/realtime-rules：

- GET    /export/realtime-rules                 列出（带过滤）
- GET    /export/realtime-rules/{id}            获取详情
- POST   /export/realtime-rules                 创建
- PUT    /export/realtime-rules/{id}            更新
- DELETE /export/realtime-rules/{id}            删除
- POST   /export/realtime-rules/{id}/toggle     启停（enabled = !enabled）
- POST   /export/realtime-rules/{id}/test-run   手动测试触发（不影响真实统计的 source_type=manual_test）
- GET    /export/realtime-rules/{id}/logs       查询某规则的运行日志（分页）
- GET    /export/run-logs                       查询所有日志（实时 + 批量）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.export_models import (
    ExportRealtimeRule, ExportRunLog, ExportTemplate,
)
from backend.services.export_realtime import trigger_test_run


router = APIRouter()


# ============================================================
# Pydantic Schema
# ============================================================

_LATEST_FILE_STRATEGY_PATTERN = "^(mtime|mtime_stable|cycle_start_snapshot)$"


class _RuleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    enabled: bool = True
    description: Optional[str] = None
    template_id: int
    output_dir: str = Field(..., min_length=1)
    filename_template: str = "{{ cycle.id }}.txt"
    input_file_mode: str = Field("none", pattern="^(none|read_template|append)$")
    input_dir: Optional[str] = None
    trigger_event: str = Field("cycle_end", pattern="^(cycle_end|session_end|box_complete|scan_group_end)$")
    channel_filter: Optional[List[int]] = None
    project_filter: Optional[List[int]] = None
    overwrite_policy: str = Field("overwrite", pattern="^(overwrite|rename|skip)$")
    encoding: str = Field("utf-8", pattern="^(utf-8|utf-8-sig|gbk|ascii)$")
    newline: str = Field("lf", pattern="^(lf|crlf)$")
    # v3.7.2 扫码器旁路 — 取最新文件策略 + 去重重试
    latest_file_strategy: str = Field(
        "cycle_start_snapshot", pattern=_LATEST_FILE_STRATEGY_PATTERN,
    )
    latest_file_wait_stable_ms: int = Field(100, ge=0, le=10000)
    latest_file_max_age_sec: int = Field(0, ge=0, le=86400)
    dedupe_same_filename: bool = False
    dedupe_retry_max_sec: int = Field(5, ge=1, le=300)
    dedupe_retry_interval_ms: int = Field(100, ge=10, le=5000)


class RuleCreate(_RuleBase):
    pass


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    description: Optional[str] = None
    template_id: Optional[int] = None
    output_dir: Optional[str] = None
    filename_template: Optional[str] = None
    input_file_mode: Optional[str] = Field(None, pattern="^(none|read_template|append)$")
    input_dir: Optional[str] = None
    trigger_event: Optional[str] = Field(None, pattern="^(cycle_end|session_end|box_complete|scan_group_end)$")
    channel_filter: Optional[List[int]] = None
    project_filter: Optional[List[int]] = None
    overwrite_policy: Optional[str] = Field(None, pattern="^(overwrite|rename|skip)$")
    encoding: Optional[str] = Field(None, pattern="^(utf-8|utf-8-sig|gbk|ascii)$")
    newline: Optional[str] = Field(None, pattern="^(lf|crlf)$")
    latest_file_strategy: Optional[str] = Field(
        None, pattern=_LATEST_FILE_STRATEGY_PATTERN,
    )
    latest_file_wait_stable_ms: Optional[int] = Field(None, ge=0, le=10000)
    latest_file_max_age_sec: Optional[int] = Field(None, ge=0, le=86400)
    dedupe_same_filename: Optional[bool] = None
    dedupe_retry_max_sec: Optional[int] = Field(None, ge=1, le=300)
    dedupe_retry_interval_ms: Optional[int] = Field(None, ge=10, le=5000)


class TestRunRequest(BaseModel):
    cycle_id: Optional[int] = None
    session_id: Optional[int] = None
    license_payload: Optional[Dict[str, Any]] = None
    display_payload: Optional[Dict[str, Any]] = None


# ============================================================
# 序列化
# ============================================================

def _serialize_rule(r: ExportRealtimeRule, *, with_template: bool = False) -> Dict[str, Any]:
    out = {
        "id": r.id,
        "name": r.name,
        "enabled": bool(r.enabled),
        "description": r.description,
        "template_id": r.template_id,
        "output_dir": r.output_dir,
        "filename_template": r.filename_template,
        "input_file_mode": r.input_file_mode,
        "input_dir": r.input_dir,
        "trigger_event": r.trigger_event,
        "channel_filter": r.channel_filter,
        "project_filter": r.project_filter,
        "overwrite_policy": r.overwrite_policy,
        "encoding": r.encoding,
        "newline": r.newline,
        "latest_file_strategy": r.latest_file_strategy or "cycle_start_snapshot",
        "latest_file_wait_stable_ms": r.latest_file_wait_stable_ms or 0,
        "latest_file_max_age_sec": r.latest_file_max_age_sec or 0,
        "dedupe_same_filename": bool(r.dedupe_same_filename),
        "dedupe_retry_max_sec": r.dedupe_retry_max_sec or 5,
        "dedupe_retry_interval_ms": r.dedupe_retry_interval_ms or 100,
        "last_used_input_filename": r.last_used_input_filename,
        "last_run_time": r.last_run_time.isoformat() if r.last_run_time else None,
        "last_run_status": r.last_run_status,
        "last_run_error": r.last_run_error,
        "last_output_file": r.last_output_file,
        "success_count": r.success_count or 0,
        "failed_count": r.failed_count or 0,
        "skipped_count": r.skipped_count or 0,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
    if with_template and r.template:
        out["template"] = {
            "id": r.template.id,
            "name": r.template.name,
            "format": r.template.format,
            "is_system": bool(r.template.is_system),
        }
    return out


def _serialize_log(log: ExportRunLog) -> Dict[str, Any]:
    return {
        "id": log.id,
        "rule_id": log.rule_id,
        "template_id": log.template_id,
        "source_type": log.source_type,
        "cycle_id": log.cycle_id,
        "session_id": log.session_id,
        "box_serial": log.box_serial,
        "triggered_at": log.triggered_at.isoformat() if log.triggered_at else None,
        "status": log.status,
        "output_file": log.output_file,
        "file_size": log.file_size,
        "duration_ms": log.duration_ms,
        "error_msg": log.error_msg,
        "skip_reason": log.skip_reason,
    }


# ============================================================
# CRUD 端点
# ============================================================

@router.get("/realtime-rules")
def list_rules(
    enabled: Optional[bool] = Query(None),
    trigger_event: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    q = db.query(ExportRealtimeRule)
    if enabled is not None:
        q = q.filter(ExportRealtimeRule.enabled == enabled)
    if trigger_event:
        q = q.filter(ExportRealtimeRule.trigger_event == trigger_event)
    rows = q.order_by(ExportRealtimeRule.id.asc()).all()
    return {"items": [_serialize_rule(r, with_template=True) for r in rows],
            "total": len(rows)}


@router.get("/realtime-rules/{rule_id}")
def get_rule(rule_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = db.query(ExportRealtimeRule).filter(ExportRealtimeRule.id == rule_id).first()
    if not r:
        raise HTTPException(404, f"规则 {rule_id} 不存在")
    return _serialize_rule(r, with_template=True)


def _check_template_exists(db: Session, template_id: int) -> ExportTemplate:
    t = db.query(ExportTemplate).filter(ExportTemplate.id == template_id).first()
    if not t:
        raise HTTPException(400, f"template_id={template_id} 不存在")
    return t


def _validate_rule(payload: _RuleBase) -> None:
    if payload.input_file_mode != "none" and not payload.input_dir:
        raise HTTPException(400,
            f"input_file_mode={payload.input_file_mode} 时 input_dir 必填")


@router.post("/realtime-rules",
              dependencies=[Depends(require_perm("data.export"))])
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _check_template_exists(db, payload.template_id)
    _validate_rule(payload)

    row = ExportRealtimeRule(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_rule(row, with_template=True)


@router.put("/realtime-rules/{rule_id}",
             dependencies=[Depends(require_perm("data.export"))])
def update_rule(rule_id: int, payload: RuleUpdate,
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(ExportRealtimeRule).filter(
        ExportRealtimeRule.id == rule_id
    ).first()
    if not row:
        raise HTTPException(404, f"规则 {rule_id} 不存在")

    data = payload.model_dump(exclude_none=True)
    if "template_id" in data:
        _check_template_exists(db, data["template_id"])

    # input_file_mode 改了之后必须 input_dir 配套
    new_mode = data.get("input_file_mode", row.input_file_mode)
    new_dir = data.get("input_dir", row.input_dir)
    if new_mode != "none" and not new_dir:
        raise HTTPException(400,
            f"input_file_mode={new_mode} 时 input_dir 必填")

    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return _serialize_rule(row, with_template=True)


@router.delete("/realtime-rules/{rule_id}",
                dependencies=[Depends(require_perm("data.export"))])
def delete_rule(rule_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(ExportRealtimeRule).filter(
        ExportRealtimeRule.id == rule_id
    ).first()
    if not row:
        raise HTTPException(404, f"规则 {rule_id} 不存在")
    db.delete(row)
    db.commit()
    return {"deleted": rule_id}


@router.post("/realtime-rules/{rule_id}/toggle",
              dependencies=[Depends(require_perm("data.export"))])
def toggle_rule(rule_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(ExportRealtimeRule).filter(
        ExportRealtimeRule.id == rule_id
    ).first()
    if not row:
        raise HTTPException(404, f"规则 {rule_id} 不存在")
    row.enabled = not row.enabled
    db.commit()
    db.refresh(row)
    return _serialize_rule(row, with_template=True)


# ============================================================
# 测试触发 + 日志
# ============================================================

@router.post("/realtime-rules/{rule_id}/test-run",
              dependencies=[Depends(require_perm("data.export"))])
def test_run(rule_id: int, payload: TestRunRequest = TestRunRequest(),
             db: Session = Depends(get_db)) -> Dict[str, Any]:
    """手动触发一次（source_type=manual_test）— 用于调试规则配置"""
    return trigger_test_run(
        db, rule_id=rule_id,
        cycle_id=payload.cycle_id,
        session_id=payload.session_id,
        license_payload=payload.license_payload,
        display_payload=payload.display_payload,
    )


@router.get("/realtime-rules/{rule_id}/logs")
def list_rule_logs(rule_id: int,
                   limit: int = Query(50, ge=1, le=500),
                   offset: int = Query(0, ge=0),
                   status: Optional[str] = Query(None),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not db.query(ExportRealtimeRule).filter(
        ExportRealtimeRule.id == rule_id
    ).first():
        raise HTTPException(404, f"规则 {rule_id} 不存在")

    q = db.query(ExportRunLog).filter(ExportRunLog.rule_id == rule_id)
    if status:
        q = q.filter(ExportRunLog.status == status)
    total = q.count()
    rows = (q.order_by(ExportRunLog.triggered_at.desc())
              .offset(offset).limit(limit).all())
    return {"items": [_serialize_log(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


class ScannerBypassStatusOut(BaseModel):
    """旁路 SN 监控状态快照 (内存态, 只读)。"""
    running: bool = Field(..., description="监控线程是否在跑")
    polled_at: Optional[str] = Field(None, description="最近一轮扫描时间 (ISO)")
    poll_interval_sec: Optional[float] = Field(None, description="扫描间隔秒")
    error: Optional[str] = Field(None, description="监控模块异常信息 (正常为空)")
    by_channel: Dict[str, Any] = Field(default_factory=dict, description="通道号 → 当前 SN 条目")
    default: Optional[Dict[str, Any]] = Field(None, description="未绑通道规则的默认 SN 条目")
    entries: List[Dict[str, Any]] = Field(default_factory=list, description="全部监控规则的最新条目")
    channel_id: Optional[int] = Field(None, description="回显请求的通道号 (传了才有)")
    current: Optional[Dict[str, Any]] = Field(None, description="该通道当前 SN 条目 (传 channel_id 才有)")


@router.get(
    "/scanner-bypass/status",
    summary="查旁路扫码 SN 状态",
    response_model=ScannerBypassStatusOut,
)
def scanner_bypass_status(
    channel_id: Optional[int] = Query(None, description="通道号；不传返回全部通道整体状态"),
) -> Dict[str, Any]:
    """扫码器旁路当前 SN 状态 (只读后台监控线程内存, 不触发目录扫描).

    - 不传 channel_id: 返回整体状态 (by_channel + default + entries).
    - 传 channel_id: 额外返回 current = 该通道当前 entry (无专属规则回退 default).

    出错隔离: 监控模块任何异常都返回 running=false + error, 不 500.
    """
    try:
        from backend.services.scanner_bypass_monitor import (
            get_status_snapshot, get_current_for_channel,
        )
        snap = get_status_snapshot()
        if channel_id is not None:
            snap["channel_id"] = channel_id
            snap["current"] = get_current_for_channel(channel_id)
        return snap
    except Exception as e:
        return {
            "running": False,
            "error": f"{type(e).__name__}: {e}",
            "by_channel": {},
            "default": None,
            "entries": [],
        }


@router.get("/run-logs")
def list_all_logs(limit: int = Query(100, ge=1, le=1000),
                  offset: int = Query(0, ge=0),
                  status: Optional[str] = Query(None),
                  source_type: Optional[str] = Query(None),
                  cycle_id: Optional[int] = Query(None),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    """所有运行日志（含批量、实时、测试触发） — 排查问题用"""
    q = db.query(ExportRunLog)
    if status:
        q = q.filter(ExportRunLog.status == status)
    if source_type:
        q = q.filter(ExportRunLog.source_type == source_type)
    if cycle_id is not None:
        q = q.filter(ExportRunLog.cycle_id == cycle_id)
    total = q.count()
    rows = (q.order_by(ExportRunLog.triggered_at.desc())
              .offset(offset).limit(limit).all())
    return {"items": [_serialize_log(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}
