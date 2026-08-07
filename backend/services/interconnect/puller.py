# -*- coding: utf-8 -*-
"""模型分发拉取 worker (设备主动拉, 多机/NAT 友好).

拓扑说明: push 模式 (训练平台 POST 到每台设备的 models/push) 要求平台能反向
直连每台工控机 — 单机局域网可行, 多台设备 + 工厂 NAT/防火墙下不成立。
pull 模式让每台设备定期问平台"我有没有新模型包", 平台只需要一个对外地址。

对端契约 (interconnect-contract 1.1, 训练平台侧实现):
- GET  {platform}/api/v1/interconnect/packages
       ?cursor=<上次游标>&device_id=...&device_name=...
       → {"contract":"1.1","items":[{"package_id","model_name","version",
          "project_name","size","sha256"}...],"next_cursor":"..."}
       items 按发布先后有序; cursor 为对端定义的不透明字符串, 首拉传空。
- GET  {platform}/api/v1/interconnect/packages/{package_id}/download
       → .yvmodel 字节流

下载后走 package_ingest.ingest_package 同一入库路径 (复用全部安全校验);
同名同版本冲突 (409 语义) 视为已入库, 照常推进游标。
游标持久化在 SystemConfig KV 'interconnect.pull_cursor'。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time

PACKAGES_PATH = "/api/v1/interconnect/packages"
KV_PULL_CURSOR = "interconnect.pull_cursor"

_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_wakeup = threading.Event()

_status_lock = threading.Lock()
_status = {
    "worker_running": False,
    "last_check_at": None,      # epoch 秒
    "last_error": "",
    "last_error_at": None,
    "pulled_total": 0,
    "skipped_total": 0,         # 重复包 (已入库) 数
}


def ensure_puller() -> None:
    """幂等启动拉取 worker (daemon)。"""
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(
            target=_loop, name="interconnect-puller", daemon=True)
        _thread.start()
        with _status_lock:
            _status["worker_running"] = True


def pull_now() -> dict:
    """立即执行一轮拉取 (前端「立即拉取」按钮, 同步返回本轮结果)。"""
    return _check_once()


def _loop() -> None:
    from backend.services.interconnect import config as icfg
    print("[Interconnect] 模型拉取 worker 启动")
    while True:
        try:
            cfg = icfg.get_config()
            pull_cfg = cfg.get("model_pull") or {}
            interval = max(30.0, float(pull_cfg.get("interval_s", 300) or 300))
            if (cfg.get("enabled") and pull_cfg.get("enabled")
                    and (cfg.get("platform_url") or "").strip()):
                _check_once()
        except Exception as e:  # noqa: BLE001 — worker 永不因单轮异常退出
            _note_error(f"拉取轮异常: {e}")
            interval = 60.0
        _wakeup.wait(timeout=interval)
        _wakeup.clear()


def _check_once() -> dict:
    """一轮拉取: 列出新包 → 逐个下载入库 → 推进游标。"""
    import requests

    from backend.services.interconnect import config as icfg
    from backend.services.interconnect.identity import get_identity

    cfg = icfg.get_config()
    base = (cfg.get("platform_url") or "").strip().rstrip("/")
    if not base:
        return {"checked": False, "error": "未配置训练平台地址"}
    headers = {"X-Interconnect-Token": cfg.get("token") or ""}
    with _status_lock:
        _status["last_check_at"] = time.time()

    try:
        resp = requests.get(
            base + PACKAGES_PATH,
            params={"cursor": _load_cursor(), **get_identity()},
            headers=headers, timeout=15)
    except Exception as e:  # noqa: BLE001
        _note_error(f"列包失败: {e}")
        return {"checked": True, "error": str(e), "pulled": 0}
    if resp.status_code == 404:
        # 对端还没实现 1.1 拉取端点 — 不算错误, 等对方升级
        _note_error("对端未实现模型拉取端点 (契约 1.1), 等训练平台升级")
        return {"checked": True, "error": "对端未实现拉取端点", "pulled": 0}
    if resp.status_code != 200:
        _note_error(f"列包 HTTP {resp.status_code}")
        return {"checked": True, "error": f"HTTP {resp.status_code}", "pulled": 0}

    try:
        body = resp.json()
        items = list(body.get("items") or [])
        next_cursor = body.get("next_cursor")
    except (ValueError, AttributeError) as e:
        _note_error(f"列包响应非法: {e}")
        return {"checked": True, "error": "响应非法", "pulled": 0}

    pulled = skipped = 0
    for item in items:
        result = _download_and_ingest(base, headers, item)
        if result == "pulled":
            pulled += 1
        elif result == "skipped":
            skipped += 1
        elif result.startswith("bad:"):
            # 包内容永久非法 (校验失败) — 记错误但跳过, 不许一个坏包
            # 卡死游标堵住后续所有模型分发
            _note_error(result[4:])
            skipped += 1
        else:
            # 网络/传输类失败即停, 游标不推进, 下轮从头重试 (保序, 不丢包)
            _note_error(result)
            return {"checked": True, "error": result,
                    "pulled": pulled, "skipped": skipped}
    if next_cursor:
        _save_cursor(str(next_cursor))
    with _status_lock:
        _status["pulled_total"] += pulled
        _status["skipped_total"] += skipped
        if pulled or not items:
            _status["last_error"] = ""
    if pulled:
        print(f"[Interconnect] 本轮拉取入库 {pulled} 个模型包 (跳过重复 {skipped})")
    return {"checked": True, "error": "", "pulled": pulled,
            "skipped": skipped, "listed": len(items)}


def _download_and_ingest(base: str, headers: dict, item: dict) -> str:
    """下载单个包并入库。返回 'pulled' / 'skipped' / 错误描述。"""
    import requests

    package_id = item.get("package_id")
    if not package_id:
        return "bad:包条目缺 package_id"
    tmp_path = None
    try:
        try:
            resp = requests.get(
                f"{base}{PACKAGES_PATH}/{package_id}/download",
                headers=headers, timeout=120)
        except Exception as e:  # noqa: BLE001
            return f"下载失败: {e}"
        if resp.status_code != 200:
            return f"下载 HTTP {resp.status_code}"
        data = resp.content
        want_sha = (item.get("sha256") or "").lower()
        if want_sha and hashlib.sha256(data).hexdigest() != want_sha:
            return f"包 {package_id} SHA-256 不符 (传输损坏或对端异常)"

        with tempfile.NamedTemporaryFile(suffix=".yvmodel", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(data)

        from backend.db.database import SessionLocal
        from backend.services.interconnect.package_ingest import (
            PackageConflict, PackageError, ingest_package,
        )
        db = SessionLocal()
        try:
            result = ingest_package(tmp_path, db)
        except PackageConflict:
            return "skipped"  # 已入库 (push 模式或上轮已拉), 照常推进
        except PackageError as e:
            return f"bad:包 {package_id} 校验失败: {e}"
        finally:
            db.close()
        print(f"[Interconnect] 拉取入库模型: {result['model_name']} "
              f"v{result['version']} (model_id={result['model_id']})")
        return "pulled"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ------------------------------ 游标持久化 ------------------------------
def _load_cursor() -> str:
    from backend.db.database import SessionLocal
    from backend.models.models import SystemConfig
    db = SessionLocal()
    try:
        row = db.query(SystemConfig).filter(
            SystemConfig.key == KV_PULL_CURSOR).first()
        return (row.value or "") if row is not None else ""
    finally:
        db.close()


def _save_cursor(cursor: str) -> None:
    from backend.db.database import SessionLocal
    from backend.models.models import SystemConfig
    db = SessionLocal()
    try:
        row = db.query(SystemConfig).filter(
            SystemConfig.key == KV_PULL_CURSOR).first()
        if row is None:
            db.add(SystemConfig(key=KV_PULL_CURSOR, value=cursor,
                                description="训练平台模型拉取游标"))
        else:
            row.value = cursor
        db.commit()
    finally:
        db.close()


def _note_error(msg: str) -> None:
    print(f"[Interconnect][pull] {msg}")
    with _status_lock:
        _status["last_error"] = msg[:200]
        _status["last_error_at"] = time.time()


def get_status() -> dict:
    with _status_lock:
        return dict(_status)
