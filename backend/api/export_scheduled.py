"""
v3.8.x 定时导出 — CRUD + toggle + test-run + logs API

端点前缀 /api/v1/export/scheduled-rules:

- GET    /export/scheduled-rules                列出 (带 enabled 过滤)
- GET    /export/scheduled-rules/{id}           获取详情
- POST   /export/scheduled-rules                创建
- PUT    /export/scheduled-rules/{id}           更新
- DELETE /export/scheduled-rules/{id}           删除
- POST   /export/scheduled-rules/{id}/toggle    启停
- POST   /export/scheduled-rules/{id}/test-run  立即测试触发一次
- GET    /export/scheduled-rules/{id}/logs      查询日志 (复用 ExportRunLog 表)

辅助端点:
- GET    /export/scheduled-rules/_default-output-dir   读全局默认输出目录
- PUT    /export/scheduled-rules/_default-output-dir   设全局默认输出目录
- POST   /export/scheduled-rules/_cron-preview         给前端预览下次触发时间
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.models.export_models import ExportScheduledRule, ExportRunLog
from backend.services.export_scheduled import (
    DEFAULT_OUTPUT_DIR_KEY, get_default_output_dir,
    reload_rule, remove_rule_job, trigger_now,
)


router = APIRouter()


_WINDOW_TYPES = "^(yesterday|today|last_n_hours|last_n_days|shift_day_yesterday|shift_night_yesterday|custom_offset)$"
_OUTPUT_FORMATS = "^(csv|txt|xlsx|docx|pdf)$"
_OVERWRITE_POLICIES = "^(overwrite|rename|skip)$"
_ENCODINGS = "^(utf-8|utf-8-sig|gbk|ascii)$"


# ============================================================
# Pydantic
# ============================================================

class _RuleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    enabled: bool = True
    description: Optional[str] = None

    cron_expression: str = Field("0 0 * * *", min_length=1, max_length=64)

    data_window_type: str = Field("yesterday", pattern=_WINDOW_TYPES)
    data_window_config: Optional[Dict[str, Any]] = None

    project_id: Optional[int] = None
    channel_id: Optional[int] = None

    output_format: str = Field("csv", pattern=_OUTPUT_FORMATS)
    output_dir: Optional[str] = None
    filename_template: str = Field("{rule_name}_{date}.{format}", min_length=1, max_length=256)

    use_standard_daily_report: bool = True
    template_id: Optional[int] = None

    encoding: str = Field("utf-8-sig", pattern=_ENCODINGS)
    newline: str = Field("lf", pattern="^(lf|crlf)$")
    overwrite_policy: str = Field("overwrite", pattern=_OVERWRITE_POLICIES)

    pt_mode: Optional[str] = Field(None, pattern="^(avg|last|current)$")
    ct_mode: Optional[str] = Field(None, pattern="^(avg|last|current)$")


class RuleCreate(_RuleBase):
    pass


class RuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    enabled: Optional[bool] = None
    description: Optional[str] = None
    cron_expression: Optional[str] = Field(None, min_length=1, max_length=64)
    data_window_type: Optional[str] = Field(None, pattern=_WINDOW_TYPES)
    data_window_config: Optional[Dict[str, Any]] = None
    project_id: Optional[int] = None
    channel_id: Optional[int] = None
    output_format: Optional[str] = Field(None, pattern=_OUTPUT_FORMATS)
    output_dir: Optional[str] = None
    filename_template: Optional[str] = Field(None, min_length=1, max_length=256)
    use_standard_daily_report: Optional[bool] = None
    template_id: Optional[int] = None
    encoding: Optional[str] = Field(None, pattern=_ENCODINGS)
    newline: Optional[str] = Field(None, pattern="^(lf|crlf)$")
    overwrite_policy: Optional[str] = Field(None, pattern=_OVERWRITE_POLICIES)
    pt_mode: Optional[str] = Field(None, pattern="^(avg|last|current)$")
    ct_mode: Optional[str] = Field(None, pattern="^(avg|last|current)$")


def _serialize(rule: ExportScheduledRule) -> Dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "enabled": rule.enabled,
        "description": rule.description,
        "cron_expression": rule.cron_expression,
        "data_window_type": rule.data_window_type,
        "data_window_config": rule.data_window_config or {},
        "project_id": rule.project_id,
        "channel_id": rule.channel_id,
        "output_format": rule.output_format,
        "output_dir": rule.output_dir,
        "filename_template": rule.filename_template,
        "use_standard_daily_report": rule.use_standard_daily_report,
        "template_id": rule.template_id,
        "encoding": rule.encoding,
        "newline": rule.newline,
        "overwrite_policy": rule.overwrite_policy,
        "pt_mode": rule.pt_mode,
        "ct_mode": rule.ct_mode,
        "last_run_time": rule.last_run_time.isoformat() if rule.last_run_time else None,
        "last_run_status": rule.last_run_status,
        "last_run_error": rule.last_run_error,
        "last_output_file": rule.last_output_file,
        "next_run_time": rule.next_run_time.isoformat() if rule.next_run_time else None,
        "success_count": rule.success_count or 0,
        "failed_count": rule.failed_count or 0,
        "skipped_count": rule.skipped_count or 0,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
    }


# ============================================================
# CRUD
# ============================================================

@router.get("/scheduled-rules")
def list_rules(enabled: Optional[bool] = None, db: Session = Depends(get_db)):
    q = db.query(ExportScheduledRule)
    if enabled is not None:
        q = q.filter(ExportScheduledRule.enabled.is_(enabled))
    rules = q.order_by(ExportScheduledRule.id.desc()).all()
    return [_serialize(r) for r in rules]


@router.get("/scheduled-rules/_default-output-dir")
def get_default_dir():
    return {"key": DEFAULT_OUTPUT_DIR_KEY, "value": get_default_output_dir()}


@router.put("/scheduled-rules/_default-output-dir")
def set_default_dir(payload: dict = Body(...), db: Session = Depends(get_db)):
    from backend.models.models import SystemConfig
    value = (payload.get("value") or "").strip()
    cfg = db.query(SystemConfig).filter(SystemConfig.key == DEFAULT_OUTPUT_DIR_KEY).first()
    if cfg:
        cfg.value = value
    else:
        cfg = SystemConfig(key=DEFAULT_OUTPUT_DIR_KEY, value=value,
                           description="定时导出全局默认输出目录")
        db.add(cfg)
    db.commit()
    return {"key": DEFAULT_OUTPUT_DIR_KEY, "value": get_default_output_dir(db)}


@router.post("/scheduled-rules/_cron-preview")
def cron_preview(payload: dict = Body(...)):
    """前端实时预览: 给一个 cron 字符串, 返回下 N 次触发时间."""
    expr = (payload.get("cron_expression") or "").strip()
    n = int(payload.get("count", 5))
    n = max(1, min(n, 20))
    if not expr:
        raise HTTPException(status_code=400, detail="cron_expression 不能为空")
    try:
        from croniter import croniter
        it = croniter(expr, datetime.now())
        runs = [it.get_next(datetime).isoformat() for _ in range(n)]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"cron 表达式不合法: {e}")
    return {"cron_expression": expr, "next_runs": runs}


@router.get("/scheduled-rules/{rule_id}")
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(ExportScheduledRule).filter(ExportScheduledRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    return _serialize(rule)


@router.post("/scheduled-rules")
def create_rule(body: RuleCreate, db: Session = Depends(get_db)):
    _validate_cron(body.cron_expression)
    rule = ExportScheduledRule(**body.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    reload_rule(rule.id)
    return _serialize(rule)


@router.put("/scheduled-rules/{rule_id}")
def update_rule(rule_id: int, body: RuleUpdate, db: Session = Depends(get_db)):
    rule = db.query(ExportScheduledRule).filter(ExportScheduledRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    data = body.model_dump(exclude_unset=True)
    if "cron_expression" in data:
        _validate_cron(data["cron_expression"])
    for k, v in data.items():
        setattr(rule, k, v)
    db.commit()
    db.refresh(rule)
    reload_rule(rule.id)
    return _serialize(rule)


@router.delete("/scheduled-rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(ExportScheduledRule).filter(ExportScheduledRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    db.delete(rule)
    db.commit()
    remove_rule_job(rule_id)
    return {"ok": True}


@router.post("/scheduled-rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(ExportScheduledRule).filter(ExportScheduledRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    rule.enabled = not rule.enabled
    db.commit()
    db.refresh(rule)
    if rule.enabled:
        reload_rule(rule.id)
    else:
        remove_rule_job(rule.id)
    return _serialize(rule)


@router.post("/scheduled-rules/{rule_id}/test-run")
def test_run(rule_id: int, db: Session = Depends(get_db)):
    """立即同步触发一次。返回结果 + 刷新 last_run_*."""
    rule = db.query(ExportScheduledRule).filter(ExportScheduledRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    result = trigger_now(rule_id)
    db.refresh(rule)
    return {"result": result, "rule": _serialize(rule)}


@router.get("/scheduled-rules/{rule_id}/logs")
def rule_logs(rule_id: int,
              limit: int = Query(50, ge=1, le=500),
              offset: int = Query(0, ge=0),
              db: Session = Depends(get_db)):
    """查日志: ExportRunLog 表借用, 通过 skip_reason 含 scheduled_rule_id={id} 识别."""
    tag = f"scheduled_rule_id={rule_id}"
    q = db.query(ExportRunLog).filter(ExportRunLog.skip_reason.like(f"%{tag}%"))
    total = q.count()
    rows = q.order_by(ExportRunLog.triggered_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [{
            "id": r.id,
            "triggered_at": r.triggered_at.isoformat() if r.triggered_at else None,
            "status": r.status,
            "output_file": r.output_file,
            "file_size": r.file_size,
            "duration_ms": r.duration_ms,
            "error_msg": r.error_msg,
            "source_type": r.source_type,
        } for r in rows],
    }


def _validate_cron(expr: str) -> None:
    try:
        from croniter import croniter
        if not croniter.is_valid(expr):
            raise ValueError("croniter.is_valid returned False")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"cron 表达式不合法: {e}")
