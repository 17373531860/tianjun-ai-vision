"""
PLC 连接器 API (RFC 13) — /api/v1/plc/*

连接 CRUD + 连通测试 + 实时监视 + 手动写值 + IO 日志 + 配置导入导出 + 方案模板库。
CRUD 落库后调 manager.restart_connection 热重载, 不需要重启后端。
"""
import copy
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.models.plc_models import PLCConnection
from backend.schemas.plc import (PLCConnectionCreate, PLCConnectionUpdate,
                                 PLCEnableRequest, PLCPointWrite)

logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize(row: PLCConnection, runtime: Optional[dict] = None) -> dict:
    data = {
        "id": row.id,
        "name": row.name,
        "driver": row.driver,
        "enabled": bool(row.enabled),
        "conn_params": row.conn_params or {},
        "points": row.points or [],
        "read_rules": row.read_rules or [],
        "write_rules": row.write_rules or [],
        "options": row.options or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    data["runtime"] = runtime
    return data


def _validate_config(driver: str, conn_params: dict, points: list):
    """点位/地址静态校验。驱动依赖库未装时跳过地址校验 (开发机常态),
    保存不被拦, 引擎启动时会以 config_error 状态暴露。"""
    from backend.services.plc import point_codec
    keys = set()
    for p in points or []:
        err = point_codec.validate_point(p)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if p["key"] in keys:
            raise HTTPException(status_code=400, detail=f"点位 key 重复: {p['key']}")
        keys.add(p["key"])
    try:
        from backend.services.plc.drivers import get_driver_class
        driver_cls = get_driver_class(driver)
    except RuntimeError:
        return      # 库未安装: 只做上面的通用校验
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        driver_cls(conn_params or {}, copy.deepcopy(points or []))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"配置校验失败: {e}")
    except Exception as e:
        logger.warning("[PLC] 配置校验异常 (放行保存): %s", e)


def _restart(connection_id: int):
    from backend.services.plc.manager import get_plc_manager
    try:
        get_plc_manager().restart_connection(connection_id)
    except Exception as e:
        logger.warning("[PLC] 热重载连接#%s 失败: %s", connection_id, e)


def _engine_or_404(connection_id: int):
    from backend.services.plc.manager import get_plc_manager
    eng = get_plc_manager().get_engine(connection_id)
    if eng is None:
        raise HTTPException(status_code=404,
                            detail="连接未启用或未运行 (先启用该连接)")
    return eng


# ---------------- 驱动与模板 ----------------

@router.get("/drivers")
def list_plc_drivers():
    from backend.services.plc.drivers import list_drivers
    return {"drivers": list_drivers()}


@router.get("/templates")
def list_plc_templates():
    """内置方案模板库: 载入后改地址/映射即用 (客户方案可导出后互相复制)。"""
    from backend.services.plc.presets import BUILTIN_TEMPLATES
    return {"templates": BUILTIN_TEMPLATES}


# ---------------- 连接 CRUD ----------------

@router.get("/connections")
def list_connections(db: Session = Depends(get_db)):
    from backend.services.plc.manager import get_plc_manager
    mgr = get_plc_manager()
    rows = db.query(PLCConnection).order_by(PLCConnection.id).all()
    result = []
    for row in rows:
        eng = mgr.get_engine(row.id)
        result.append(_serialize(row, eng.snapshot() if eng else None))
    return {"connections": result}


@router.post("/connections")
def create_connection(body: PLCConnectionCreate, db: Session = Depends(get_db)):
    _validate_config(body.driver, body.conn_params, body.points)
    row = PLCConnection(
        name=body.name, driver=body.driver, enabled=body.enabled,
        conn_params=body.conn_params, points=body.points,
        read_rules=body.read_rules, write_rules=body.write_rules,
        options=body.options,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _restart(row.id)
    return _serialize(row)


@router.put("/connections/{connection_id}")
def update_connection(connection_id: int, body: PLCConnectionUpdate,
                      db: Session = Depends(get_db)):
    row = db.query(PLCConnection).get(connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="连接不存在")
    payload = body.model_dump(exclude_unset=True)
    merged_driver = payload.get("driver", row.driver)
    merged_params = payload.get("conn_params", row.conn_params) or {}
    merged_points = payload.get("points", row.points) or []
    _validate_config(merged_driver, merged_params, merged_points)
    for field, value in payload.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    _restart(row.id)
    return _serialize(row)


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: int, db: Session = Depends(get_db)):
    row = db.query(PLCConnection).get(connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="连接不存在")
    from backend.services.plc.manager import get_plc_manager
    get_plc_manager().remove_connection(connection_id)
    db.delete(row)
    db.commit()
    return {"success": True}


@router.post("/connections/{connection_id}/enable")
def enable_connection(connection_id: int, body: PLCEnableRequest,
                      db: Session = Depends(get_db)):
    row = db.query(PLCConnection).get(connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="连接不存在")
    row.enabled = body.enabled
    db.commit()
    _restart(connection_id)
    return {"success": True, "enabled": body.enabled}


# ---------------- 联调诊断 ----------------

@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: int, db: Session = Depends(get_db)):
    """按已存配置做一次连通测试 (独立短连接, 不影响运行中的引擎)。"""
    row = db.query(PLCConnection).get(connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="连接不存在")
    try:
        from backend.services.plc.drivers import get_driver_class
        driver_cls = get_driver_class(row.driver)
    except (RuntimeError, ValueError) as e:
        return {"success": False, "message": str(e)}
    return driver_cls.test_connection(row.conn_params or {},
                                      copy.deepcopy(row.points or []))


@router.get("/connections/{connection_id}/live")
def live_values(connection_id: int):
    """实时监视: 点位当前值 / 变化时间 / 状态 / 计数器。前端 1s 轮询。"""
    return _engine_or_404(connection_id).snapshot()


@router.post("/connections/{connection_id}/write")
def manual_write(connection_id: int, body: PLCPointWrite):
    """手动写值 (现场联调用), 同步等待写完成。"""
    eng = _engine_or_404(connection_id)
    try:
        eng.write_sync(body.point, body.value)
    except (ValueError, RuntimeError, TimeoutError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "point": body.point, "value": body.value}


@router.get("/connections/{connection_id}/logs")
def io_logs(connection_id: int, limit: int = 100):
    eng = _engine_or_404(connection_id)
    logs = list(eng.io_log)[-max(1, min(limit, 300)):]
    return {"logs": logs[::-1]}      # 新的在前


@router.post("/connections/{connection_id}/mock-set")
def mock_set(connection_id: int, body: PLCPointWrite):
    """mock 驱动专属: 模拟 PLC 侧改点位 (置读完成/写产品号), 驱动完整规则链路。"""
    from backend.services.plc.manager import get_plc_manager
    try:
        return {"success": True,
                **get_plc_manager().mock_set(connection_id, body.point, body.value)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- 配置导入导出 ----------------

@router.get("/connections/{connection_id}/export")
def export_connection(connection_id: int, db: Session = Depends(get_db)):
    row = db.query(PLCConnection).get(connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="连接不存在")
    data = _serialize(row)
    for k in ("id", "runtime", "created_at", "updated_at", "enabled"):
        data.pop(k, None)
    return data


@router.post("/import")
def import_connection(body: PLCConnectionCreate, db: Session = Depends(get_db)):
    """导入配置 (与 create 同构, 单独留端点便于前端语义区分; 导入默认不启用)。"""
    body.enabled = False
    return create_connection(body, db)
