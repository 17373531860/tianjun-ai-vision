"""
统一触发中心 API (RFC 14) — /api/v1/triggers/*

触发源实例 CRUD + 启停 + 实时状态 + 触发历史 + 手动试触发 + mock 注入
+ 像素参考帧标定 + HTTP 触发入口 + 配置导入导出 + 方案模板库。
CRUD 落库后调 manager.restart_trigger 热重载, 不需要重启后端。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.models.trigger_models import TriggerChannel
from backend.schemas.triggers import (TriggerChannelCreate, TriggerChannelUpdate,
                                      TriggerEnableRequest, TriggerMockFire,
                                      TriggerMockLevel, TriggerTestFire)

logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize(row: TriggerChannel, runtime: Optional[dict] = None) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "type": row.type,
        "enabled": bool(row.enabled),
        "params": row.params or {},
        "rules": row.rules or [],
        "options": row.options or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "runtime": runtime,
    }


def _validate(type_name: str, params: dict):
    from backend.services.triggers.sources import validate_source_params
    err = validate_source_params(type_name, params)
    if err:
        raise HTTPException(status_code=400, detail=err)


def _restart(trigger_id: int):
    from backend.services.triggers.manager import get_trigger_manager
    try:
        get_trigger_manager().restart_trigger(trigger_id)
    except Exception as e:
        logger.warning("[Trigger] 热重载触发源#%s 失败: %s", trigger_id, e)


def _engine_or_404(trigger_id: int):
    from backend.services.triggers.manager import get_trigger_manager
    eng = get_trigger_manager().get_engine(trigger_id)
    if eng is None:
        raise HTTPException(status_code=404,
                            detail="触发源未启用或未运行 (先启用该触发源)")
    return eng


# ---------------- 类型 / 动作 / 模板 ----------------

@router.get("/types")
def list_trigger_types():
    from backend.services.triggers.sources import list_source_types
    return {"types": list_source_types()}


@router.get("/actions")
def list_trigger_actions():
    """动作注册表清单 (与 PLC 规则共用一套; 插件注册的也在)。"""
    from backend.services.triggers.actions import ACTION_REGISTRY
    return {"actions": sorted(ACTION_REGISTRY.keys())}


@router.get("/templates")
def list_trigger_templates():
    from backend.services.triggers.presets import BUILTIN_TEMPLATES
    return {"templates": BUILTIN_TEMPLATES}


# ---------------- 实例 CRUD ----------------

@router.get("/channels")
def list_triggers(db: Session = Depends(get_db)):
    from backend.services.triggers.manager import get_trigger_manager
    mgr = get_trigger_manager()
    rows = db.query(TriggerChannel).order_by(TriggerChannel.id).all()
    result = []
    for row in rows:
        eng = mgr.get_engine(row.id)
        result.append(_serialize(row, eng.snapshot() if eng else None))
    return {"triggers": result}


@router.post("/channels")
def create_trigger(body: TriggerChannelCreate, db: Session = Depends(get_db)):
    _validate(body.type, body.params)
    row = TriggerChannel(name=body.name, type=body.type, enabled=body.enabled,
                         params=body.params, rules=body.rules, options=body.options)
    db.add(row)
    db.commit()
    db.refresh(row)
    _restart(row.id)
    return _serialize(row)


@router.put("/channels/{trigger_id}")
def update_trigger(trigger_id: int, body: TriggerChannelUpdate,
                   db: Session = Depends(get_db)):
    row = db.query(TriggerChannel).get(trigger_id)
    if row is None:
        raise HTTPException(status_code=404, detail="触发源不存在")
    payload = body.model_dump(exclude_unset=True)
    merged_type = payload.get("type", row.type)
    merged_params = payload.get("params", row.params) or {}
    _validate(merged_type, merged_params)
    for field, value in payload.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    _restart(row.id)
    return _serialize(row)


@router.delete("/channels/{trigger_id}")
def delete_trigger(trigger_id: int, db: Session = Depends(get_db)):
    row = db.query(TriggerChannel).get(trigger_id)
    if row is None:
        raise HTTPException(status_code=404, detail="触发源不存在")
    from backend.services.triggers.manager import get_trigger_manager
    get_trigger_manager().remove_trigger(trigger_id)
    db.delete(row)
    db.commit()
    return {"success": True}


@router.post("/channels/{trigger_id}/enable")
def enable_trigger(trigger_id: int, body: TriggerEnableRequest,
                   db: Session = Depends(get_db)):
    row = db.query(TriggerChannel).get(trigger_id)
    if row is None:
        raise HTTPException(status_code=404, detail="触发源不存在")
    row.enabled = body.enabled
    db.commit()
    _restart(trigger_id)
    return {"success": True, "enabled": body.enabled}


# ---------------- 联调诊断 ----------------

@router.get("/channels/{trigger_id}/live")
def live_status(trigger_id: int):
    """实时状态: 源指标 / 计数器 / 变量。前端 1.5s 轮询。"""
    return _engine_or_404(trigger_id).snapshot()


@router.get("/channels/{trigger_id}/history")
def trigger_history(trigger_id: int, limit: int = 50):
    eng = _engine_or_404(trigger_id)
    return {"history": list(eng.history)[:max(1, min(limit, 100))]}


@router.get("/channels/{trigger_id}/logs")
def trigger_logs(trigger_id: int, limit: int = 100):
    eng = _engine_or_404(trigger_id)
    logs = list(eng.io_log)[:max(1, min(limit, 300))]
    return {"logs": logs}


@router.post("/channels/{trigger_id}/test")
def test_fire(trigger_id: int, body: TriggerTestFire):
    """手动试触发: 绕过信号/防抖/窗口, 直接执行指定规则的动作 (现场联调)。"""
    eng = _engine_or_404(trigger_id)
    if not (0 <= body.rule_index < len(eng.rules)):
        raise HTTPException(status_code=400,
                            detail=f"rule_index 超界 (共 {len(eng.rules)} 条规则)")
    rule = eng.rules[body.rule_index]
    snapshot = {"trigger_name": eng.name, "trigger_type": eng.type, "edge": "test"}
    eng._log("event", f"[{rule.get('name') or f'规则{body.rule_index + 1}'}] 手动试触发")
    eng.manager.submit_actions(eng, rule, snapshot)
    return {"success": True, "rule": rule.get("name") or f"规则{body.rule_index + 1}"}


@router.post("/channels/{trigger_id}/mock-fire")
def mock_fire(trigger_id: int, body: TriggerMockFire):
    """mock 触发源专属: 注入一次脉冲 (走完整 规则→动作 链路)。"""
    from backend.services.triggers.manager import get_trigger_manager
    try:
        return {"success": True,
                **get_trigger_manager().mock_fire(trigger_id, body.meta)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/channels/{trigger_id}/mock-level")
def mock_level(trigger_id: int, body: TriggerMockLevel):
    """mock 触发源专属: 注入电平变化 (测防抖/边沿判定)。"""
    from backend.services.triggers.manager import get_trigger_manager
    try:
        return {"success": True,
                **get_trigger_manager().set_mock_level(trigger_id, body.value, body.meta)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/channels/{trigger_id}/calibrate")
def calibrate_pixel(trigger_id: int, db: Session = Depends(get_db)):
    """pixel_region 专属: 取当前区域均值作参考帧并持久化 (一键标定)。"""
    eng = _engine_or_404(trigger_id)
    from backend.services.triggers.sources.pixel_region import PixelRegionSource
    if not isinstance(eng.source, PixelRegionSource):
        raise HTTPException(status_code=400, detail="仅 pixel_region 触发源支持标定")
    try:
        result = eng.source.recalibrate()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    row = db.query(TriggerChannel).get(trigger_id)
    if row is not None:
        params = dict(row.params or {})
        params["ref_bgr"] = result["ref_bgr"]
        row.params = params
        db.commit()
    eng._log("event", f"参考帧已标定: {result['ref_bgr']}")
    return {"success": True, **result}


# ---------------- HTTP 触发入口 (外部系统调) ----------------

@router.post("/fire/{key}")
async def http_fire(key: str, request: Request,
                    x_trigger_secret: Optional[str] = Header(None),
                    secret: Optional[str] = None):
    """外部系统触发入口。密钥经 X-Trigger-Secret 头或 ?secret= 传入。"""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    client_ip = request.client.host if request.client else ""
    from backend.services.triggers.manager import get_trigger_manager
    try:
        result = get_trigger_manager().fire_http(
            key, payload, x_trigger_secret or secret or "", client_ip)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"success": True, **result}


# ---------------- 配置导入导出 ----------------

@router.get("/channels/{trigger_id}/export")
def export_trigger(trigger_id: int, db: Session = Depends(get_db)):
    row = db.query(TriggerChannel).get(trigger_id)
    if row is None:
        raise HTTPException(status_code=404, detail="触发源不存在")
    data = _serialize(row)
    for k in ("id", "runtime", "created_at", "updated_at", "enabled"):
        data.pop(k, None)
    return data


@router.post("/import")
def import_trigger(body: TriggerChannelCreate, db: Session = Depends(get_db)):
    """导入配置 (与 create 同构; 导入默认不启用)。"""
    body.enabled = False
    return create_trigger(body, db)
