"""
外部 MES 对接 REST API

连接管理 CRUD + 测试连接 + 手动推送 + 通讯日志查询
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from backend.db.database import SessionLocal
from backend.models.mes_models import MESConnection, MESCommLog
from backend.services.mes_gateway import get_mes_gateway

router = APIRouter(prefix="/mes/gateway", tags=["MES-Gateway"])


# ============================================================
# Pydantic Schema
# ============================================================

class ConnectionCreate(BaseModel):
    name: str
    adapter_type: str = "rest"
    enabled: bool = False
    config: Optional[dict] = None
    push_events: Optional[List[str]] = None
    pull_enabled: bool = False
    pull_interval_sec: int = 60
    retry_count: int = 3
    retry_interval_sec: int = 5
    extra_fields_schema: Optional[List[dict]] = None

class ConnectionUpdate(BaseModel):
    name: Optional[str] = None
    adapter_type: Optional[str] = None
    enabled: Optional[bool] = None
    config: Optional[dict] = None
    push_events: Optional[List[str]] = None
    pull_enabled: Optional[bool] = None
    pull_interval_sec: Optional[int] = None
    retry_count: Optional[int] = None
    retry_interval_sec: Optional[int] = None
    extra_fields_schema: Optional[List[dict]] = None

class TestPayload(BaseModel):
    config: Optional[dict] = None

class ManualPush(BaseModel):
    event_type: str = "cycle_end"
    cycle_id: Optional[int] = None
    session_id: Optional[int] = None
    channel_id: int = 0

class ExtraFieldsUpdate(BaseModel):
    channel_id: int = 0
    fields: dict


def _serialize_conn(c):
    return {
        "id": c.id, "name": c.name,
        "adapter_type": c.adapter_type, "enabled": c.enabled,
        "config": c.config, "push_events": c.push_events,
        "pull_enabled": c.pull_enabled,
        "pull_interval_sec": c.pull_interval_sec,
        "retry_count": c.retry_count,
        "retry_interval_sec": c.retry_interval_sec,
        "extra_fields_schema": c.extra_fields_schema,
        "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _serialize_log(l):
    return {
        "id": l.id, "connection_id": l.connection_id,
        "direction": l.direction, "event_type": l.event_type,
        "method": l.method, "url": l.url,
        "request_body": l.request_body, "response_body": l.response_body,
        "status_code": l.status_code, "success": l.success,
        "error_msg": l.error_msg, "duration_ms": l.duration_ms,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    }


# ============================================================
# 连接 CRUD
# ============================================================

@router.get("/connections")
def list_connections():
    db = SessionLocal()
    try:
        items = db.query(MESConnection).order_by(MESConnection.id).all()
        return [_serialize_conn(c) for c in items]
    finally:
        db.close()


@router.post("/connections")
def create_connection(body: ConnectionCreate):
    db = SessionLocal()
    try:
        conn = MESConnection(
            name=body.name,
            adapter_type=body.adapter_type,
            enabled=body.enabled,
            config=body.config,
            push_events=body.push_events,
            pull_enabled=body.pull_enabled,
            pull_interval_sec=body.pull_interval_sec,
            retry_count=body.retry_count,
            retry_interval_sec=body.retry_interval_sec,
            extra_fields_schema=body.extra_fields_schema,
        )
        db.add(conn)
        db.commit()
        db.refresh(conn)
        return _serialize_conn(conn)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.get("/connections/{conn_id}")
def get_connection(conn_id: int):
    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(404, "连接不存在")
        return _serialize_conn(conn)
    finally:
        db.close()


@router.put("/connections/{conn_id}")
def update_connection(conn_id: int, body: ConnectionUpdate):
    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(404, "连接不存在")
        data = body.model_dump(exclude_unset=True)
        for field, val in data.items():
            setattr(conn, field, val)
        db.commit()
        db.refresh(conn)
        return _serialize_conn(conn)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.delete("/connections/{conn_id}")
def delete_connection(conn_id: int):
    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(404, "连接不存在")
        db.delete(conn)
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(400, str(e))
    finally:
        db.close()


# ============================================================
# 测试连接
# ============================================================

@router.post("/connections/{conn_id}/test")
def test_connection(conn_id: int, body: TestPayload = None):
    """发送测试数据到外部 MES, 验证连接+格式"""
    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(404, "连接不存在")

        config = body.config if body and body.config else conn.config or {}

        test_context = {
            "workpiece": {"id": 0, "serial_no": "TEST-001", "status": "ok", "inspection_count": 1},
            "cycle": {"id": 0, "is_good": True, "result": "OK", "duration": 5.2,
                      "event_name": "test", "ng_reason": None, "completed_steps": 3, "total_steps": 3},
            "order": {"order_no": "WO-TEST-001", "product_name": "测试产品",
                      "planned_qty": 100, "completed_qty": 50, "good_qty": 48,
                      "ng_qty": 2, "yield_rate": 96.0, "progress": 50.0},
            "project": {"id": 1, "name": "测试项目"},
            "steps": [
                {"label": "Step-A", "index": 0, "duration": 1.5, "is_good": True,
                 "confidence": 0.95, "start_time": "2026-01-01T00:00:00", "end_time": "2026-01-01T00:00:01"},
                {"label": "Step-B", "index": 1, "duration": 2.0, "is_good": True,
                 "confidence": 0.88, "start_time": "2026-01-01T00:00:02", "end_time": "2026-01-01T00:00:04"},
            ],
            "defects": [],
            "session": {"id": 0, "total_cycles": 50, "good_cycles": 48, "ng_cycles": 2},
            "extra": {},
            "timestamp": datetime.now().isoformat(),
        }

        static = config.get("static_fields", {})
        for dotted_key, val in static.items():
            parts = dotted_key.split(".", 1)
            if len(parts) == 2:
                ns, key = parts
                if ns not in test_context:
                    test_context[ns] = {}
                if isinstance(test_context[ns], dict):
                    test_context[ns][key] = val

        from backend.services.mes_adapters import get_adapter
        adapter = get_adapter(conn.adapter_type)
        payload = adapter.build_payload(test_context, config)
        result = adapter.send(payload, config)
        is_ok = adapter.check_response(result, config)

        return {
            "success": is_ok,
            "payload_preview": payload,
            "status_code": result.get("status_code"),
            "response_body": result.get("body"),
            "error": result.get("error"),
            "duration_ms": result.get("duration_ms"),
        }
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        db.close()


# ============================================================
# 手动推送
# ============================================================

@router.post("/connections/{conn_id}/push")
def manual_push(conn_id: int, body: ManualPush):
    """手动推送指定 cycle/session 的数据"""
    db = SessionLocal()
    try:
        gw = get_mes_gateway()

        if body.event_type == "cycle_end" and body.cycle_id:
            context = gw.build_context_from_cycle(
                db, body.cycle_id, project_id=None
            )
        elif body.event_type == "session_end" and body.session_id:
            context = gw.build_context_from_session(
                db, body.session_id, project_id=None
            )
        else:
            raise HTTPException(400, "请指定 cycle_id 或 session_id")

        result = gw.manual_push(conn_id, body.event_type, context, body.channel_id)
        return result
    finally:
        db.close()


# ============================================================
# 额外字段 (Monitor 页实时输入)
# ============================================================

@router.post("/extra-fields")
def set_extra_fields(body: ExtraFieldsUpdate):
    """设置当前工位的额外字段值"""
    gw = get_mes_gateway()
    gw.set_extra_fields(body.channel_id, body.fields)
    return {"success": True, "channel_id": body.channel_id, "fields": body.fields}


@router.get("/extra-fields")
def get_extra_fields(channel_id: int = 0):
    gw = get_mes_gateway()
    return {"channel_id": channel_id, "fields": gw.get_extra_fields(channel_id)}


@router.get("/extra-fields-schema")
def get_extra_fields_schema():
    """获取所有启用连接的 extra_fields_schema (用于 Monitor 页渲染输入框)"""
    db = SessionLocal()
    try:
        connections = (
            db.query(MESConnection)
            .filter(MESConnection.enabled == True)
            .all()
        )
        schemas = []
        for c in connections:
            efs = c.extra_fields_schema
            if efs:
                schemas.extend(efs)
        seen = set()
        unique = []
        for s in schemas:
            key = s.get("key")
            if key and key not in seen:
                seen.add(key)
                unique.append(s)
        return unique
    finally:
        db.close()


# ============================================================
# 通讯日志
# ============================================================

@router.get("/logs")
def list_logs(
    connection_id: Optional[int] = None,
    success: Optional[bool] = None,
    event_type: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = SessionLocal()
    try:
        q = db.query(MESCommLog)
        if connection_id is not None:
            q = q.filter(MESCommLog.connection_id == connection_id)
        if success is not None:
            q = q.filter(MESCommLog.success == success)
        if event_type:
            q = q.filter(MESCommLog.event_type == event_type)

        total = q.count()
        items = q.order_by(MESCommLog.id.desc()).offset(skip).limit(limit).all()
        return {
            "items": [_serialize_log(l) for l in items],
            "total": total, "skip": skip, "limit": limit,
        }
    finally:
        db.close()
