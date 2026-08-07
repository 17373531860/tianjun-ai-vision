"""LG 工时看板 — 插件全局设置 (v1.5.0 全参数可配)。

存放点: 主程序 SystemConfig KV 表, key = plugin_lg_worktime_settings (JSON)。
AGENTS.md 钦点的"客户级全局配置"扩展点, 不加自有表、不动主程序 schema。

四个参数与生效期:
  default_value_type  VA/BVA/NVA        未配置步骤的默认动作价值 — 冻结期参数
                                        (cycle_end 结算时生效, 改了不回写历史)
  wait_value_type     NVA/BVA/VA/EXCLUDE 步骤间等待归类 — 统计口径参数
                                        (查询/展示时折算, 历史数据跟随新口径)
  lean_scope          good_only/all     LEAN 累计统计范围 (LG 出厂: 仅合格轮)
  trend_days          1~60              看板趋势图天数 (出厂 7)
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

from .lean import VALUE_TYPES, WAIT_VALUE_TYPES, normalize_value_type, normalize_wait_type

log = logging.getLogger("tianjun.plugin")

SETTINGS_KEY = "plugin_lg_worktime_settings"

LEAN_SCOPES = ("good_only", "all")

DEFAULTS: Dict[str, Any] = {
    "default_value_type": "VA",
    "wait_value_type": "NVA",
    "lean_scope": "good_only",
    "trend_days": 7,
}

# hook 热路径 (cycle_end/live 轮询) 用缓存, TTL 与步骤价值缓存对齐
_TTL_SEC = 3.0
_LOCK = threading.RLock()
_CACHE: Dict[str, Any] = {"ts": 0.0, "val": None}


def normalize_settings(raw: Any) -> Dict[str, Any]:
    """任意输入规整成完整合法设置 (缺失/非法项退出厂默认)。"""
    src = raw if isinstance(raw, dict) else {}
    scope = str(src.get("lean_scope") or "").strip().lower()
    try:
        days = int(src.get("trend_days"))
    except (TypeError, ValueError):
        days = DEFAULTS["trend_days"]
    return {
        "default_value_type": normalize_value_type(src.get("default_value_type")),
        "wait_value_type": normalize_wait_type(src.get("wait_value_type")),
        "lean_scope": scope if scope in LEAN_SCOPES else DEFAULTS["lean_scope"],
        "trend_days": max(1, min(days, 60)),
    }


def invalidate_cache() -> None:
    with _LOCK:
        _CACHE["ts"] = 0.0
        _CACHE["val"] = None


def get_settings(host: Any, force: bool = False) -> Dict[str, Any]:
    """读设置 (带 TTL 缓存); host 缺失或 DB 出错时降级出厂默认。"""
    now = time.time()
    with _LOCK:
        if (not force) and _CACHE["val"] is not None and (now - _CACHE["ts"]) < _TTL_SEC:
            return dict(_CACHE["val"])
    val = dict(DEFAULTS)
    db = None
    try:
        from backend.models.models import SystemConfig
        db = host.get_db_session() if host is not None else None
        if db is not None:
            row = db.query(SystemConfig).filter(SystemConfig.key == SETTINGS_KEY).first()
            if row is not None and row.value:
                val = normalize_settings(json.loads(row.value))
    except Exception as e:
        log.warning("[Plugin][lg-worktime] 读插件设置失败 (降级出厂默认): %s", e)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    with _LOCK:
        _CACHE["ts"] = now
        _CACHE["val"] = dict(val)
    return val


def save_settings(host: Any, patch: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """部分更新设置 (未提及的键保留现值), upsert SystemConfig 后失效缓存。"""
    current = get_settings(host, force=True)
    merged = dict(current)
    if isinstance(patch, dict):
        for k in DEFAULTS:
            if k in patch:
                merged[k] = patch[k]
    merged = normalize_settings(merged)

    db = None
    try:
        from backend.models.models import SystemConfig
        db = host.get_db_session() if host is not None else None
        if db is None:
            raise RuntimeError("host DB 不可用")
        row = db.query(SystemConfig).filter(SystemConfig.key == SETTINGS_KEY).first()
        payload = json.dumps(merged, ensure_ascii=False)
        if row is None:
            db.add(SystemConfig(key=SETTINGS_KEY, value=payload,
                                description="lg-worktime 工时看板参数 (LEAN 口径)"))
        else:
            row.value = payload
        db.commit()
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    invalidate_cache()
    return merged
