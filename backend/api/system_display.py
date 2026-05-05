"""
v3.5.0 系统级品牌/显示字段持久化 API

把前端 useSystemStore.display 中的几个字段（之前只在 localStorage）落库到
SystemConfig KV 表（key='display.xxx'），让自定义导出系统的 build_*_context()
能跨进程读取（实时规则在后端线程触发，前端 localStorage 不可达）。

字段：brand_name / app_name / inspector_name / device_number /
     factory_name / line_name

设计：
- GET  /api/v1/system/display      读全部 display 字段
- PUT  /api/v1/system/display      批量更新（前端保存时调用）
- GET  /api/v1/system/license-cache 读 License 缓存
- PUT  /api/v1/system/license-cache  写 License 缓存（前端 IPC 解析后调用）
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.models.models import SystemConfig


router = APIRouter()

DISPLAY_FIELDS = [
    "brand_name", "app_name",
    "inspector_name", "device_number",
    "factory_name", "line_name",
]


class DisplayPayload(BaseModel):
    brand_name: Optional[str] = None
    app_name: Optional[str] = None
    inspector_name: Optional[str] = None
    device_number: Optional[str] = None
    factory_name: Optional[str] = None
    line_name: Optional[str] = None


def _set_kv(db: Session, key: str, value: Optional[Any], desc: str = "") -> None:
    """upsert SystemConfig 单条 KV"""
    if value is None:
        v = ""
    elif isinstance(value, (str, int, float, bool)):
        v = str(value)
    else:
        import json as _json
        v = _json.dumps(value, ensure_ascii=False)

    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = v
        if desc and not row.description:
            row.description = desc
    else:
        db.add(SystemConfig(key=key, value=v, description=desc or None))


def _get_kv(db: Session, key: str) -> Optional[str]:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row else None


@router.get("/display")
def get_display(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """读 display 全部字段 — 前端启动时调用补全 useSystemStore.display"""
    out = {f: None for f in DISPLAY_FIELDS}
    rows = db.query(SystemConfig).filter(SystemConfig.key.like("display.%")).all()
    for r in rows:
        short_key = r.key.split(".", 1)[1] if "." in r.key else r.key
        if short_key in out:
            out[short_key] = r.value
    return out


@router.put("/display")
def put_display(payload: DisplayPayload,
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    """更新 display 字段（前端 useSystemStore 保存时调用）"""
    updated = []
    data = payload.model_dump(exclude_unset=True)
    for k in DISPLAY_FIELDS:
        if k in data:
            _set_kv(db, f"display.{k}", data[k],
                    desc=f"display field for export context")
            updated.append(k)
    db.commit()
    return {"status": "ok", "updated": updated}


@router.get("/license-cache")
def get_license_cache(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """读后端缓存的 License 信息（实时规则触发导出时使用）

    返回 {} 表示尚未缓存（前端启动后调 PUT 写入一次）
    """
    raw = _get_kv(db, "license.cache")
    if not raw:
        return {}
    try:
        import json as _json
        return _json.loads(raw)
    except Exception:
        return {}


class LicenseCachePayload(BaseModel):
    customer: Optional[str] = None
    machine_id: Optional[str] = None
    expires_at: Optional[str] = None
    is_perpetual: Optional[bool] = None
    days_remaining: Optional[int] = None
    features: Optional[list] = None


@router.put("/license-cache")
def put_license_cache(payload: LicenseCachePayload,
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    """前端通过 Electron IPC 拿到 License 信息后，PUT 一次到后端缓存

    实时规则触发导出时无前端在场，build_cycle_context 走 SystemConfig 读取。
    """
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "至少需要一个字段")
    _set_kv(db, "license.cache",
            {k: v for k, v in data.items() if v is not None},
            desc="License cache from frontend IPC")
    db.commit()
    return {"status": "ok"}
