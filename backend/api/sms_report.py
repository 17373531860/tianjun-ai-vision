"""
每日短信日报 — 规则 CRUD + 试发 + 预览 + 日志 API (v3.46)

端点前缀 /api/v1/sms-report:

- GET    /sms-report/rules                 列出规则 (带 enabled 过滤)
- GET    /sms-report/rules/{id}            规则详情
- POST   /sms-report/rules                 创建
- PUT    /sms-report/rules/{id}            更新
- DELETE /sms-report/rules/{id}            删除
- POST   /sms-report/rules/{id}/toggle     启停
- POST   /sms-report/rules/{id}/test-send  立即执行一次 (可 ?use_mock=true 不发真短信)
- POST   /sms-report/rules/{id}/preview    渲染当前窗口的模板变量, 不发送
- GET    /sms-report/rules/{id}/logs       发送记录
- GET    /sms-report/providers             可用通道清单 (含当前生效通道)

通道选择与凭据在共享短信配置 GET/PUT /api/v1/sms/config (与 NG 短信通知同一份),
本路由不再有自己的服务商配置端点。
字段勾选清单复用 GET /api/v1/export/fields (前端按组过滤 stats/aggregations/counters_daily)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_perm
from backend.db.database import get_db
from backend.models.notify_models import SmsReportRule, SmsSendLog
from backend.services.sms_report import (
    build_preview, load_channel_config, reload_rule, remove_rule_job, trigger_now,
)

router = APIRouter()

_WINDOW_TYPES = "^(today|yesterday)$"


# ============================================================
# Pydantic
# ============================================================

class _RuleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    enabled: bool = True
    description: Optional[str] = None

    cron_expression: str = Field("0 20 * * *", min_length=1, max_length=64)
    data_window_type: str = Field("today", pattern=_WINDOW_TYPES)

    group_by_channel: bool = False
    channel_ids: Optional[List[int]] = None
    project_id: Optional[int] = None

    metrics: Optional[List[str]] = None
    template_param_mapping: Optional[Dict[str, str]] = None
    extra_params: Optional[Dict[str, str]] = None
    phone_numbers: Optional[List[str]] = None
    template_code: Optional[str] = Field(None, max_length=64)
    content_template: Optional[str] = Field(None, max_length=2000)


class RuleCreate(_RuleBase):
    pass


class RuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    enabled: Optional[bool] = None
    description: Optional[str] = None
    cron_expression: Optional[str] = Field(None, min_length=1, max_length=64)
    data_window_type: Optional[str] = Field(None, pattern=_WINDOW_TYPES)
    group_by_channel: Optional[bool] = None
    channel_ids: Optional[List[int]] = None
    project_id: Optional[int] = None
    metrics: Optional[List[str]] = None
    template_param_mapping: Optional[Dict[str, str]] = None
    extra_params: Optional[Dict[str, str]] = None
    phone_numbers: Optional[List[str]] = None
    template_code: Optional[str] = Field(None, max_length=64)
    content_template: Optional[str] = Field(None, max_length=2000)


def _serialize(rule: SmsReportRule) -> Dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "enabled": rule.enabled,
        "description": rule.description,
        "cron_expression": rule.cron_expression,
        "data_window_type": rule.data_window_type,
        "group_by_channel": rule.group_by_channel,
        "channel_ids": rule.channel_ids or [],
        "project_id": rule.project_id,
        "metrics": rule.metrics or [],
        "template_param_mapping": rule.template_param_mapping or {},
        "extra_params": rule.extra_params or {},
        "phone_numbers": rule.phone_numbers or [],
        "template_code": rule.template_code,
        "content_template": rule.content_template,
        "last_run_time": rule.last_run_time.isoformat() if rule.last_run_time else None,
        "last_run_status": rule.last_run_status,
        "last_run_error": rule.last_run_error,
        "next_run_time": rule.next_run_time.isoformat() if rule.next_run_time else None,
        "success_count": rule.success_count or 0,
        "failed_count": rule.failed_count or 0,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
    }


def _validate_cron(expr: str) -> None:
    try:
        from croniter import croniter
        if not croniter.is_valid(expr):
            raise ValueError("croniter.is_valid returned False")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"cron 表达式不合法: {e}")


def _get_rule_or_404(db: Session, rule_id: int) -> SmsReportRule:
    rule = db.query(SmsReportRule).filter(SmsReportRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail=f"短信日报规则 #{rule_id} 不存在")
    return rule


# ============================================================
# 通道信息 (只读; 配置编辑走共享 /api/v1/sms/config)
# ============================================================

@router.get("/providers")
def read_providers():
    """统一通道清单 + 当前生效通道 (来自共享 sms_config.json)。"""
    from backend.services.sms_providers import PROVIDER_LABELS
    channel_config = load_channel_config()
    return {
        "providers": [
            {"name": name, "label": label}
            for name, label in PROVIDER_LABELS.items()
        ],
        "active_provider": channel_config.provider,
        "template_based": channel_config.provider in {"aliyun", "tencent"},
    }


# ============================================================
# 规则 CRUD
# ============================================================

@router.get("/rules")
def list_rules(enabled: Optional[bool] = Query(None), db: Session = Depends(get_db)):
    q = db.query(SmsReportRule)
    if enabled is not None:
        q = q.filter(SmsReportRule.enabled.is_(enabled))
    rules = q.order_by(SmsReportRule.id.asc()).all()
    return {"total": len(rules), "rules": [_serialize(r) for r in rules]}


@router.get("/rules/{rule_id}")
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    return _serialize(_get_rule_or_404(db, rule_id))


@router.post("/rules",
             dependencies=[Depends(require_perm("data.export"))])
def create_rule(body: RuleCreate, db: Session = Depends(get_db)):
    _validate_cron(body.cron_expression)
    rule = SmsReportRule(**body.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    reload_rule(rule.id)
    return _serialize(rule)


@router.put("/rules/{rule_id}",
            dependencies=[Depends(require_perm("data.export"))])
def update_rule(rule_id: int, body: RuleUpdate, db: Session = Depends(get_db)):
    rule = _get_rule_or_404(db, rule_id)
    data = body.model_dump(exclude_unset=True)
    if "cron_expression" in data:
        _validate_cron(data["cron_expression"])
    for k, v in data.items():
        setattr(rule, k, v)
    db.commit()
    db.refresh(rule)
    reload_rule(rule.id)
    return _serialize(rule)


@router.delete("/rules/{rule_id}",
               dependencies=[Depends(require_perm("data.export"))])
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = _get_rule_or_404(db, rule_id)
    db.delete(rule)
    db.commit()
    remove_rule_job(rule_id)
    return {"deleted": rule_id}


@router.post("/rules/{rule_id}/toggle",
             dependencies=[Depends(require_perm("data.export"))])
def toggle_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = _get_rule_or_404(db, rule_id)
    rule.enabled = not rule.enabled
    db.commit()
    db.refresh(rule)
    reload_rule(rule.id)
    return _serialize(rule)


# ============================================================
# 试发 / 预览 / 日志
# ============================================================

@router.post("/rules/{rule_id}/test-send",
             dependencies=[Depends(require_perm("data.export"))])
def test_send(rule_id: int,
              use_mock: bool = Query(False, description="true = 走 mock 适配器不发真短信"),
              db: Session = Depends(get_db)):
    _get_rule_or_404(db, rule_id)
    return trigger_now(rule_id, force_provider="mock" if use_mock else None)


@router.post("/rules/{rule_id}/preview",
             dependencies=[Depends(require_perm("data.export"))])
def preview_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = _get_rule_or_404(db, rule_id)
    return {"rule_id": rule_id, "scopes": build_preview(db, rule)}


@router.get("/rules/{rule_id}/logs")
def rule_logs(rule_id: int, limit: int = Query(50, ge=1, le=500),
              db: Session = Depends(get_db)):
    logs = (db.query(SmsSendLog)
            .filter(SmsSendLog.rule_id == rule_id)
            .order_by(SmsSendLog.triggered_at.desc())
            .limit(limit).all())
    return {"total": len(logs), "logs": [{
        "id": lg.id,
        "rule_id": lg.rule_id,
        "rule_name": lg.rule_name,
        "triggered_at": lg.triggered_at.isoformat() if lg.triggered_at else None,
        "source_type": lg.source_type,
        "provider": lg.provider,
        "channel_id": lg.channel_id,
        "phone_numbers": lg.phone_numbers or [],
        "template_code": lg.template_code,
        "template_params": lg.template_params or {},
        "success": lg.success,
        "retry_count": lg.retry_count or 0,
        "error_msg": lg.error_msg,
    } for lg in logs]}
