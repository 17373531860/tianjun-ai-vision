# -*- coding: utf-8 -*-
"""设备身份 (多台工控机连同一训练平台时的唯一标识).

- device_id: 首次取用时生成并持久化 (SystemConfig KV 'interconnect.device_id'),
  优先复用 License 缓存里的 machineId (与授权体系同源, 换机即变),
  没有 License 时退化为随机串。生成后永不再变。
- device_name: 取互连配置 device_name, 空则用主机名 (可读标识, 允许现场改)。

所有出站请求 (帧回传 meta / 模型拉取 query) 都携带这对身份,
训练平台按 device_id 做设备台账、去重与配额。
"""
from __future__ import annotations

import json
import socket
import threading
import uuid

KV_DEVICE_ID = "interconnect.device_id"

_lock = threading.Lock()
_device_id_cache: str | None = None


def _machine_id_from_license(db) -> str | None:
    """从 License 缓存 (SystemConfig KV 'license.cache') 取 machineId。"""
    from backend.models.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == "license.cache").first()
    if row is None or not row.value:
        return None
    try:
        data = json.loads(row.value)
    except (TypeError, json.JSONDecodeError):
        return None
    mid = (data or {}).get("machine_id") or (data or {}).get("machineId")
    return str(mid) if mid else None


def get_device_id() -> str:
    """稳定设备 ID (进程内缓存, 首次生成落库)。"""
    global _device_id_cache
    if _device_id_cache:
        return _device_id_cache
    with _lock:
        if _device_id_cache:
            return _device_id_cache
        from backend.db.database import SessionLocal
        from backend.models.models import SystemConfig
        db = SessionLocal()
        try:
            row = db.query(SystemConfig).filter(
                SystemConfig.key == KV_DEVICE_ID).first()
            if row is not None and (row.value or "").strip():
                _device_id_cache = row.value.strip()
                return _device_id_cache
            machine_id = _machine_id_from_license(db)
            device_id = (f"tj-{machine_id}" if machine_id
                         else f"tj-{uuid.uuid4().hex[:16]}")
            if row is None:
                db.add(SystemConfig(key=KV_DEVICE_ID, value=device_id,
                                    description="训练平台互连设备 ID (生成后不变)"))
            else:
                row.value = device_id
            db.commit()
            _device_id_cache = device_id
            return device_id
        finally:
            db.close()


def get_device_name() -> str:
    from backend.services.interconnect import config as icfg
    name = (icfg.get_config().get("device_name") or "").strip()
    if name:
        return name
    try:
        return socket.gethostname()
    except OSError:
        return "unknown-host"


def get_identity() -> dict:
    return {"device_id": get_device_id(), "device_name": get_device_name()}
