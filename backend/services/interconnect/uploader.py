# -*- coding: utf-8 -*-
"""帧回传后台 worker + 对端 (YoloVision) HTTP 客户端.

- worker: daemon 线程, 从磁盘队列逐条取到期帧 POST 到训练平台 ingest 端点;
  失败指数退避, 对端离线不丢帧 (队列 TTL/容量兜底), 绝不阻塞推理/结算热路径
- 对端契约: interconnect-contract 1.0 (训练平台仓库 TIANJUN_INTERCONNECT_SPEC.md)
"""
from __future__ import annotations

import json
import os
import threading
import time

INGEST_PATH = "/api/v1/interconnect/frames/ingest"
HEALTH_PATH = "/api/v1/interconnect/health"

_queue = None
_queue_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()

_status_lock = threading.Lock()
_status = {
    "worker_running": False,
    "last_success_at": None,   # epoch 秒
    "last_error": "",
    "last_error_at": None,
    "sent_total": 0,
    "failed_total": 0,
}


def get_queue():
    """帧回传队列单例 (容量/TTL 取当前配置)。"""
    global _queue
    with _queue_lock:
        if _queue is None:
            from backend.core.config import DATA_DIR
            from backend.services.interconnect import config as icfg
            from backend.services.interconnect.sample_queue import (
                InterconnectSampleQueue,
            )
            qcfg = (icfg.get_config().get("queue") or {})
            _queue = InterconnectSampleQueue(
                os.path.join(DATA_DIR, "interconnect_queue.db"),
                max_items=int(qcfg.get("max_items", 500) or 500),
                ttl_seconds=float(qcfg.get("ttl_hours", 72) or 72) * 3600.0,
            )
        return _queue


def ensure_worker() -> None:
    """幂等启动上传 worker (daemon)。"""
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(
            target=_worker_loop, name="interconnect-uploader", daemon=True)
        _worker_thread.start()
        with _status_lock:
            _status["worker_running"] = True


def _worker_loop() -> None:
    from backend.services.interconnect import config as icfg
    print("[Interconnect] 帧回传 worker 启动")
    idle_sleep = 2.0
    while True:
        try:
            cfg = icfg.get_config()
            if not cfg.get("enabled") or not (cfg.get("platform_url") or "").strip():
                time.sleep(idle_sleep)
                continue
            rec = get_queue().next_due(lease_seconds=120.0)
            if rec is None:
                time.sleep(idle_sleep)
                continue
            _push_record(cfg, rec)
        except Exception as e:  # noqa: BLE001 — worker 永不因单条异常退出
            _note_error(f"worker 异常: {e}")
            time.sleep(5.0)


def _push_record(cfg: dict, rec) -> None:
    import requests

    q = get_queue()
    url = cfg["platform_url"].rstrip("/") + INGEST_PATH
    try:
        resp = requests.post(
            url,
            headers={"X-Interconnect-Token": cfg.get("token") or ""},
            data={"meta": json.dumps(rec.meta, ensure_ascii=False)},
            files={"image": (f"{rec.sample_id}.jpg", rec.image, "image/jpeg")},
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001 — 网络异常走退避重试
        _reschedule(q, rec, f"网络异常: {e}")
        return

    if resp.status_code == 200:
        q.delete(rec.sample_id)
        q.log_update(rec.sample_id, status="sent")
        with _status_lock:
            _status["sent_total"] += 1
            _status["last_success_at"] = time.time()
            _status["last_error"] = ""
        return
    if resp.status_code == 404:
        # 对端项目不存在 = 永久失败, 丢弃防止堵队列 (项目名对不齐要人来修)
        q.delete(rec.sample_id)
        detail = _short_body(resp)
        q.log_update(rec.sample_id, status="failed",
                     detail=f"对端项目不存在: {detail}")
        _note_error(f"样本 {rec.sample_id} 对端项目不存在 ({rec.meta.get('project_name')})")
        with _status_lock:
            _status["failed_total"] += 1
        return
    if resp.status_code == 401:
        # 令牌配置错误, 长间隔重试等人修配置
        _reschedule(q, rec, "对端令牌校验失败 (检查双方 token 是否一致)",
                    fixed_delay=300.0)
        return
    _reschedule(q, rec, f"HTTP {resp.status_code}: {_short_body(resp)}")


def _reschedule(q, rec, error: str, *, fixed_delay: float | None = None) -> None:
    retry = rec.retry_count + 1
    delay = fixed_delay if fixed_delay is not None else min(600.0, 5.0 * (2 ** min(retry, 7)))
    q.reschedule(rec.sample_id, retry_count=retry,
                 next_attempt_at=time.time() + delay, last_error=error)
    q.log_update(rec.sample_id, status="pending", detail=error)
    _note_error(error)
    with _status_lock:
        _status["failed_total"] += 1


def _short_body(resp) -> str:
    try:
        return (resp.text or "")[:120]
    except Exception:  # noqa: BLE001
        return ""


def _note_error(msg: str) -> None:
    print(f"[Interconnect] {msg}")
    with _status_lock:
        _status["last_error"] = msg[:200]
        _status["last_error_at"] = time.time()


def test_peer(platform_url: str, timeout: float = 5.0) -> dict:
    """探活对端 health 端点。"""
    import requests

    url = (platform_url or "").rstrip("/") + HEALTH_PATH
    if not url.startswith(("http://", "https://")):
        return {"reachable": False, "error": "平台地址必须以 http:// 或 https:// 开头"}
    try:
        resp = requests.get(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "error": f"连接失败: {e}"}
    if resp.status_code != 200:
        return {"reachable": False,
                "error": f"HTTP {resp.status_code}: {_short_body(resp)}"}
    try:
        body = resp.json()
    except ValueError:
        return {"reachable": False, "error": "对端返回非 JSON"}
    return {"reachable": True,
            "product": body.get("product"),
            "version": body.get("version"),
            "contract": body.get("contract")}


def get_status() -> dict:
    with _status_lock:
        snap = dict(_status)
    try:
        q = get_queue()
        snap["queue_pending"] = q.count()
        snap["log_counts"] = q.log_counts()
    except Exception as e:  # noqa: BLE001
        snap["queue_pending"] = -1
        snap["log_counts"] = {}
        snap["queue_error"] = str(e)
    return snap
