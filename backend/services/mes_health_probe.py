"""
出站 MES 连接「主动健康探测」— APScheduler 后台周期探活 + 状态缓存

设计原则 (对齐 export_scheduled):
1. 独立 BackgroundScheduler (守护线程, 不阻塞 uvicorn), 单个 tick job 周期触发
2. 每次 tick 扫描所有 enabled 连接, 仅对开了健康探测且到点的连接发探测请求
3. 探测结果写进程内缓存 _STATUS, 供 API / 前端轮询 (不落库, 重启即清零)
4. 默认惰性: 连接 config.health_probe_enabled 未开则完全不探测, 零开销

每连接 config 可配 (全部带默认, 不配即用默认):
- health_probe_enabled: bool   是否开启主动探测 (默认 False)
- health_probe_interval_sec:   探测间隔秒 (默认 60, 最小 5)
- health_probe_url:            探测地址 (默认用连接的推送 url)
- health_probe_method:         GET (默认) / POST / HEAD
- health_probe_timeout_sec:    超时秒 (默认 5)
- health_probe_expect_status:  期望 HTTP 状态码 (默认空 = 2xx 即健康)
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Optional

from backend.db.database import SessionLocal
from backend.models.mes_models import MESConnection
from backend.core import debug_center


_SCHEDULER = None
_LOCK = threading.Lock()
_JOB_ID = "mes_health_probe_tick"
_TICK_SEC = 15  # 调度器扫描节拍 (实际探测频率由每连接 interval 控制)

# 进程内状态缓存: {conn_id: {ok, checked_at, latency_ms, status_code, error, url, _ts}}
_STATUS: dict = {}
_STATUS_LOCK = threading.Lock()


def _probe_params(config: dict) -> dict:
    cfg = config or {}
    interval = cfg.get("health_probe_interval_sec")
    try:
        interval = max(5, int(interval)) if interval is not None else 60
    except Exception:
        interval = 60
    timeout = cfg.get("health_probe_timeout_sec")
    try:
        timeout = max(1.0, float(timeout)) if timeout is not None else 5.0
    except Exception:
        timeout = 5.0
    url = (cfg.get("health_probe_url") or cfg.get("url") or "").strip()
    method = (cfg.get("health_probe_method") or "GET").upper()
    expect = cfg.get("health_probe_expect_status")
    try:
        expect = int(expect) if expect not in (None, "") else None
    except Exception:
        expect = None
    return {"interval": interval, "timeout": timeout, "url": url,
            "method": method, "expect": expect}


def probe_connection(conn_id: int, name: str, config: dict) -> dict:
    """对单个连接发一次健康探测, 写入状态缓存并返回该状态。"""
    p = _probe_params(config)
    now_iso = datetime.now().isoformat()
    if not p["url"]:
        st = {"ok": False, "checked_at": now_iso, "latency_ms": 0,
              "status_code": 0, "error": "未配置探测地址", "url": "",
              "name": name, "_ts": time.time()}
        _save_status(conn_id, st)
        return st

    start = time.time()
    try:
        import requests
        resp = requests.request(p["method"], p["url"], timeout=p["timeout"])
        latency = int((time.time() - start) * 1000)
        if p["expect"] is not None:
            ok = (resp.status_code == p["expect"])
        else:
            ok = (200 <= resp.status_code < 300)
        st = {"ok": ok, "checked_at": now_iso, "latency_ms": latency,
              "status_code": resp.status_code,
              "error": None if ok else f"状态码 {resp.status_code}",
              "url": p["url"], "name": name, "_ts": time.time()}
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        st = {"ok": False, "checked_at": now_iso, "latency_ms": latency,
              "status_code": 0, "error": str(e)[:200],
              "url": p["url"], "name": name, "_ts": time.time()}
    _save_status(conn_id, st)
    return st


def _save_status(conn_id: int, st: dict):
    with _STATUS_LOCK:
        prev = _STATUS.get(conn_id)
        _STATUS[conn_id] = st
    # 只在"在线↔离线"翻转时打日志, 避免每次探测刷屏 (首次也算翻转)
    if prev is None or bool(prev.get("ok")) != bool(st.get("ok")):
        if st.get("ok"):
            debug_center.dbg("backend.gateway", "外部系统恢复在线",
                             f"conn={conn_id}({st.get('name')}) {st.get('latency_ms')}ms")
        else:
            debug_center.dbg("backend.gateway", "外部系统探测离线",
                             f"conn={conn_id}({st.get('name')}) err={st.get('error')}")


def get_health_status() -> dict:
    """返回所有连接的探测状态 (去掉内部 _ts 字段)。"""
    with _STATUS_LOCK:
        return {cid: {k: v for k, v in s.items() if k != "_ts"}
                for cid, s in _STATUS.items()}


def probe_now(conn_id: int) -> dict:
    """立即探测指定连接 (API /probe-now 用)。"""
    db = SessionLocal()
    try:
        conn = db.query(MESConnection).filter(MESConnection.id == conn_id).first()
        if not conn:
            return {"ok": False, "error": "连接不存在"}
        return probe_connection(conn.id, conn.name, conn.config or {})
    finally:
        db.close()


def probe_due_connections():
    """调度器每个 tick 调: 探测所有 enabled + 开了健康探测 + 到点的连接。"""
    db = SessionLocal()
    try:
        conns = db.query(MESConnection).filter(MESConnection.enabled == True).all()
    except Exception as e:
        debug_center.dbg("backend.gateway", "健康探测查连接失败", str(e))
        db.close()
        return
    # 先取出需要的字段再关库, 探测请求本身不持库
    targets = []
    for c in conns:
        cfg = c.config or {}
        if cfg.get("health_probe_enabled"):
            targets.append((c.id, c.name, cfg))
    db.close()

    now = time.time()
    # 清理已不再启用探测的连接的旧状态
    with _STATUS_LOCK:
        live_ids = {t[0] for t in targets}
        for cid in list(_STATUS.keys()):
            if cid not in live_ids:
                _STATUS.pop(cid, None)

    for conn_id, name, cfg in targets:
        p = _probe_params(cfg)
        with _STATUS_LOCK:
            last = _STATUS.get(conn_id, {}).get("_ts", 0)
        if now - last >= p["interval"]:
            try:
                probe_connection(conn_id, name, cfg)
            except Exception as e:
                debug_center.dbg("backend.gateway", "健康探测异常", f"conn={conn_id} {e}")


def start_health_probe() -> None:
    """启动后台健康探测调度器。幂等。"""
    global _SCHEDULER
    with _LOCK:
        if _SCHEDULER is not None:
            return
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            print("[MES-Health] APScheduler 未安装, 主动健康探测关闭")
            return
        _SCHEDULER = BackgroundScheduler(daemon=True)
        _SCHEDULER.add_job(probe_due_connections, "interval", seconds=_TICK_SEC,
                           id=_JOB_ID, max_instances=1, coalesce=True)
        _SCHEDULER.start()
        print("[MES-Health] 出站健康探测调度器启动")


def stop_health_probe() -> None:
    global _SCHEDULER
    with _LOCK:
        if _SCHEDULER is None:
            return
        try:
            _SCHEDULER.shutdown(wait=False)
        except Exception:
            pass
        _SCHEDULER = None
        print("[MES-Health] 出站健康探测调度器已停止")
