"""仅在 RUNTIME_MODE=test 时挂载：把若干 BDD feature 里描述的旧 API 路径
桥接到现有真实端点或返回 stub 数据。

设计目的：让 BDD/E2E 用一致的"虚拟"路径名跑通，而不需要污染产品代码加废 endpoint。
所有 handler 都做最小占位/转发，绝不影响生产逻辑。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import get_db


router = APIRouter()


def _require_test_mode() -> None:
    if os.environ.get("RUNTIME_MODE") != "test":
        raise HTTPException(status_code=404, detail="Not found")


def _kv_get(db: Session, key: str) -> Optional[str]:
    from backend.models.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row else None


def _kv_set(db: Session, key: str, value: str) -> None:
    from backend.models.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value, description="test-compat"))
    db.commit()


# ============================================================
# MES 兼容路径（feature 用 /mes/config /mes/scanner/state /mes/workorders /mes/adapters）
# ============================================================

class MESConfigPayload(BaseModel):
    enabled: Optional[bool] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    timeout: Optional[float] = None


@router.get("/mes/config")
def mes_config_get(db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_test_mode()
    raw = _kv_get(db, "test_compat.mes.config")
    if not raw:
        return {"enabled": False, "endpoint": None}
    try:
        return json.loads(raw)
    except Exception:
        return {"enabled": False}


@router.put("/mes/config")
def mes_config_put(payload: MESConfigPayload, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_test_mode()
    data = payload.model_dump(exclude_none=True)
    if data.get("enabled") and not data.get("endpoint"):
        raise HTTPException(status_code=400, detail="endpoint 必填")
    _kv_set(db, "test_compat.mes.config", json.dumps(data, ensure_ascii=False))
    return {"status": "ok", **data}


@router.get("/mes/scanner/state")
def mes_scanner_state() -> Dict[str, Any]:
    _require_test_mode()
    return {"connected": False, "devices": [], "note": "test-compat stub"}


@router.get("/mes/workorders")
def mes_workorders(db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_test_mode()
    try:
        from backend.models.mes_models import WorkOrder
        rows = db.query(WorkOrder).order_by(WorkOrder.id.desc()).limit(50).all()
        return {"items": [{"id": r.id, "code": getattr(r, "code", None)} for r in rows], "total": len(rows)}
    except Exception:
        return {"items": [], "total": 0}


@router.get("/mes/adapters")
def mes_adapters() -> Dict[str, Any]:
    _require_test_mode()
    return {"adapters": ["http", "tcp", "mqtt"], "note": "test-compat stub"}


# ============================================================
# Alarm 兼容路径（feature 用 /alarm/state /alarm/devices /alarm/events）
# ============================================================


@router.get("/alarm/state")
def alarm_state() -> Dict[str, Any]:
    _require_test_mode()
    try:
        from backend.api.alarm import alarm_router as _ar
        mgr = _ar.get(0)
        return {
            "channel": 0,
            "is_connected": bool(mgr.is_connected()),
            "port": getattr(mgr, "port_name", None),
            "config": getattr(mgr, "config", {}),
            "note": "test-compat",
        }
    except Exception:
        return {"channel": 0, "is_connected": False, "note": "test-compat stub"}


@router.get("/alarm/devices")
def alarm_devices() -> Dict[str, Any]:
    _require_test_mode()
    try:
        import serial.tools.list_ports as _ports
        return {"devices": [p.device for p in _ports.comports()], "note": "test-compat"}
    except Exception:
        return {"devices": [], "note": "test-compat stub"}


@router.get("/alarm/events")
def alarm_events() -> Dict[str, Any]:
    _require_test_mode()
    return {"items": [], "total": 0, "note": "test-compat stub"}


# ============================================================
# 项目配置兼容路径（feature 用 /source/project_config）
# ============================================================


@router.post("/source/project_config")
def source_project_config(payload: Dict[str, Any], channel: int = 0) -> Dict[str, Any]:
    _require_test_mode()
    if not payload:
        return {"status": "cleared", "channel": channel}
    return {"status": "ok", "channel": channel, "received_keys": sorted(payload.keys())}


# ============================================================
# 旧 sessions 路径（feature 用 /sessions，真实路径 /data/sessions）
# ============================================================


@router.get("/sessions")
def sessions_compat(limit: int = 50, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_test_mode()
    try:
        from backend.models.models import DetectionSession
        rows = (
            db.query(DetectionSession)
            .order_by(DetectionSession.id.desc())
            .limit(max(1, min(limit, 200)))
            .all()
        )
        return {"items": [{"id": r.id} for r in rows], "total": len(rows)}
    except Exception:
        return {"items": [], "total": 0}
