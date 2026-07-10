"""扫码器旁路 SN 监控服务 (主程序原生基础设施)

场景 (v3.7.2 起的"扫码器旁路"能力延伸):
  客户扫码器不接入软件, 只会在固定目录里生成 `SN123456.txt`.
  v3.7.2 已实现: cycle_start 锁定该目录最新 txt -> external_meta,
                 cycle_end 导出沿用锁定文件名 (export_snapshot.py + export_realtime.py).
  本模块补齐"实时当前 SN"这一块 —— 一个轻量后台守护线程, 定期扫描配置目录,
  把每个通道当前最新 txt 的 SN (文件名去扩展名) 缓存在内存里, 供:
    1. 前端 Monitor 显示"当前 SN"(GET /export/scanner-bypass/status)
    2. cycle_start 锁快照时"内存优先"(export_snapshot 读本缓存, 无则回退 glob),
       满足"cycle_start 只读内存、不扫目录"的诉求.

设计约束 (对齐需求):
  • 不阻塞检测热路径: 目录扫描全在本守护线程里做, API / cycle_start 只读内存.
  • 出错必须隔离: 目录不存在 / 权限 / 文件名非法 / 无 SN 都只记状态和日志,
    线程本身永不因单目录异常而退出.
  • 多工位 channel 隔离: 配置来源复用 ExportRealtimeRule.channel_filter, 每个
    channel 解析各自 input_dir; channel_filter 为空的规则作为 default 兜底.

配置来源: 复用 ExportRealtimeRule (enabled + input_dir 非空), 不新造表/字段.
轮询间隔: SystemConfig['export.scanner_bypass.poll_interval_sec'] (默认 1.0s, 夹在 [0.2, 60]).
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.services.export_snapshot import _normalize_dir


_POLL_INTERVAL_KEY = "export.scanner_bypass.poll_interval_sec"
_DEFAULT_POLL_INTERVAL = 1.0
_MIN_POLL_INTERVAL = 0.2
_MAX_POLL_INTERVAL = 60.0

# 内存状态 (读多写少, 用锁保护整体替换). 结构见 _build_state.
_STATE_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "running": False,
    "polled_at": None,
    "poll_interval_sec": _DEFAULT_POLL_INTERVAL,
    "by_dir": {},        # normalized_dir -> entry dict
    "by_channel": {},    # str(channel_id) -> entry dict
    "default": None,     # channel_filter 为空的规则对应 entry (兜底)
    "entries": [],       # 供 API 平铺展示
    "error": None,       # 整轮扫描级异常
}

_STOP_EVENT = threading.Event()
_THREAD: Optional[threading.Thread] = None
_THREAD_LOCK = threading.Lock()


def _read_poll_interval() -> float:
    """从 SystemConfig 读轮询间隔; 任何异常回默认值. 每轮读一次以便热改生效."""
    try:
        from backend.db.database import SessionLocal
        from backend.models.models import SystemConfig
        db = SessionLocal()
        try:
            cfg = db.query(SystemConfig).filter(
                SystemConfig.key == _POLL_INTERVAL_KEY
            ).first()
            if cfg and cfg.value and str(cfg.value).strip():
                val = float(str(cfg.value).strip())
                return max(_MIN_POLL_INTERVAL, min(_MAX_POLL_INTERVAL, val))
        finally:
            db.close()
    except Exception:
        pass
    return _DEFAULT_POLL_INTERVAL


def _collect_rule_targets() -> List[Dict[str, Any]]:
    """扫所有启用且带 input_dir 的实时规则, 按规范化目录去重.

    返回 [{"input_dir", "normalized_dir", "wait_stable_ms", "max_age_sec",
           "channels": set()|None, "rule_ids": [...]}]
    channels=None 表示存在 channel_filter 为空的规则 (适用所有通道 / 兜底).
    """
    from backend.db.database import SessionLocal
    from backend.models.export_models import ExportRealtimeRule

    targets: Dict[str, Dict[str, Any]] = {}
    db = SessionLocal()
    try:
        rules = (
            db.query(ExportRealtimeRule)
            .filter(
                ExportRealtimeRule.enabled == True,  # noqa: E712
                ExportRealtimeRule.input_dir.isnot(None),
                ExportRealtimeRule.input_dir != "",
            )
            .all()
        )
        for r in rules:
            key = _normalize_dir(r.input_dir or "")
            if not key:
                continue
            # mtime 策略不加保险, 其余取最严 (宁严勿松), 与 export_snapshot 口径一致
            strategy = r.latest_file_strategy or "cycle_start_snapshot"
            wait_ms = 0 if strategy == "mtime" else int(r.latest_file_wait_stable_ms or 0)
            max_age = 0 if strategy == "mtime" else int(r.latest_file_max_age_sec or 0)

            entry = targets.get(key)
            if entry is None:
                entry = {
                    "input_dir": r.input_dir,
                    "normalized_dir": key,
                    "wait_stable_ms": wait_ms,
                    "max_age_sec": max_age,
                    "channels": set(),
                    "has_default": False,
                    "rule_ids": [],
                }
                targets[key] = entry
            entry["wait_stable_ms"] = max(entry["wait_stable_ms"], wait_ms)
            entry["max_age_sec"] = max(entry["max_age_sec"], max_age)
            entry["rule_ids"].append(r.id)
            if r.channel_filter:
                for ch in r.channel_filter:
                    try:
                        entry["channels"].add(int(ch))
                    except (TypeError, ValueError):
                        continue
            else:
                # channel_filter 为空 = 适用所有通道 (兜底)
                entry["has_default"] = True
        return list(targets.values())
    except Exception as e:
        print(f"[ScannerBypassMonitor] 查询规则异常: {e}", flush=True)
        return []
    finally:
        db.close()


def _scan_dir(target: Dict[str, Any]) -> Dict[str, Any]:
    """扫单个目录取最新 txt, 组装 entry. 任何异常都归到 status=error, 不外抛."""
    from backend.services.export_renderer import (
        latest_input_filename, latest_input_text,
    )

    input_dir = target["input_dir"]
    entry: Dict[str, Any] = {
        "input_dir": input_dir,
        "normalized_dir": target["normalized_dir"],
        "rule_ids": sorted(target["rule_ids"]),
        "channels": sorted(target["channels"]) if target["channels"] else [],
        "serial_no": None,
        "filename": None,
        "text": None,
        "mtime": None,
        "updated_at": datetime.now().isoformat(),
        "status": "no_file",
        "error": None,
        "source": "scanner_bypass",
    }
    try:
        if not input_dir or not os.path.isdir(input_dir):
            entry["status"] = "dir_missing"
            return entry
        filename = latest_input_filename(
            input_dir,
            wait_stable_ms=int(target.get("wait_stable_ms") or 0),
            max_age_sec=int(target.get("max_age_sec") or 0),
        )
        if not filename:
            entry["status"] = "no_file"
            return entry
        entry["filename"] = filename
        entry["serial_no"] = os.path.splitext(filename)[0]
        try:
            entry["text"] = latest_input_text(
                input_dir,
                wait_stable_ms=int(target.get("wait_stable_ms") or 0),
                max_age_sec=int(target.get("max_age_sec") or 0),
            )
        except Exception:
            entry["text"] = None
        try:
            entry["mtime"] = os.path.getmtime(os.path.join(input_dir, filename))
        except Exception:
            entry["mtime"] = None
        entry["status"] = "ok"
        return entry
    except Exception as e:
        entry["status"] = "error"
        entry["error"] = f"{type(e).__name__}: {e}"
        print(f"[ScannerBypassMonitor] 扫描目录异常 {input_dir}: {e}", flush=True)
        return entry


def _build_state(interval: float) -> Dict[str, Any]:
    """跑一轮扫描, 组装完整状态 dict (不写全局, 交给 poll loop 原子替换)."""
    targets = _collect_rule_targets()
    by_dir: Dict[str, Dict[str, Any]] = {}
    by_channel: Dict[str, Dict[str, Any]] = {}
    default_entry: Optional[Dict[str, Any]] = None
    entries: List[Dict[str, Any]] = []

    for t in targets:
        entry = _scan_dir(t)
        by_dir[t["normalized_dir"]] = entry
        entries.append(entry)
        for ch in (t["channels"] or set()):
            # 若同一通道命中多个目录, 后者覆盖前者 (罕见配置, 取扫描顺序最后一个)
            by_channel[str(ch)] = entry
        if t.get("has_default"):
            default_entry = entry

    return {
        "running": True,
        "polled_at": datetime.now().isoformat(),
        "poll_interval_sec": interval,
        "by_dir": by_dir,
        "by_channel": by_channel,
        "default": default_entry,
        "entries": entries,
        "error": None,
    }


def _poll_loop() -> None:
    print("[ScannerBypassMonitor] 旁路 SN 监控线程启动", flush=True)
    while not _STOP_EVENT.is_set():
        interval = _read_poll_interval()
        try:
            new_state = _build_state(interval)
            with _STATE_LOCK:
                _STATE.update(new_state)
        except Exception as e:
            # 整轮级异常: 记录但绝不让线程退出
            with _STATE_LOCK:
                _STATE["error"] = f"{type(e).__name__}: {e}"
                _STATE["polled_at"] = datetime.now().isoformat()
            print(f"[ScannerBypassMonitor] 轮询异常 (已隔离, 线程继续): {e}", flush=True)
        _STOP_EVENT.wait(interval)
    with _STATE_LOCK:
        _STATE["running"] = False
    print("[ScannerBypassMonitor] 旁路 SN 监控线程已停止", flush=True)


# ============================================================
# 生命周期
# ============================================================

def start_monitor() -> None:
    """启动后台守护线程. 幂等."""
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP_EVENT.clear()
        _THREAD = threading.Thread(
            target=_poll_loop, daemon=True, name="scanner-bypass-monitor",
        )
        _THREAD.start()


def stop_monitor() -> None:
    global _THREAD
    with _THREAD_LOCK:
        _STOP_EVENT.set()
        t = _THREAD
        _THREAD = None
    if t is not None:
        try:
            t.join(timeout=2.0)
        except Exception:
            pass


# ============================================================
# 只读查询 (内存, 供 API + cycle_start 内存优先)
# ============================================================

def get_status_snapshot() -> Dict[str, Any]:
    """给 API 用: 返回整体状态的浅拷贝 (只读内存, 不触发扫描)."""
    with _STATE_LOCK:
        return {
            "running": bool(_STATE.get("running")),
            "polled_at": _STATE.get("polled_at"),
            "poll_interval_sec": _STATE.get("poll_interval_sec"),
            "by_channel": dict(_STATE.get("by_channel") or {}),
            "default": _STATE.get("default"),
            "entries": list(_STATE.get("entries") or []),
            "error": _STATE.get("error"),
        }


def get_current_for_channel(channel_id: int) -> Optional[Dict[str, Any]]:
    """指定通道当前 SN entry; 无专属规则时回退 default. 只读内存."""
    with _STATE_LOCK:
        by_ch = _STATE.get("by_channel") or {}
        entry = by_ch.get(str(channel_id))
        if entry is not None:
            return entry
        return _STATE.get("default")


def get_current_for_dir(normalized_dir: str) -> Optional[Dict[str, Any]]:
    """按规范化目录取当前 entry (供 export_snapshot 内存优先). 只读内存."""
    if not normalized_dir:
        return None
    with _STATE_LOCK:
        by_dir = _STATE.get("by_dir") or {}
        return by_dir.get(normalized_dir)
