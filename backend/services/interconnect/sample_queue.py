# -*- coding: utf-8 -*-
"""现场帧回传的独立 SQLite 离线队列 (模式抄 sms_offline_queue, 附带图片 BLOB).

- frames 表: 待发帧 (meta JSON + JPEG BLOB), TTL + 容量上限, 租约防重复发送
- sample_log 表: 最近样本流水 (pending/sent/failed/dropped), 供互连状态页展示,
  封顶 200 行自动裁剪
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LOG_CAP = 200


@dataclass(frozen=True)
class PendingFrame:
    sample_id: str
    meta: dict[str, Any]
    image: bytes
    created_at: float
    expires_at: float
    next_attempt_at: float
    retry_count: int
    last_error: str


class InterconnectSampleQueue:
    """进程重启可恢复的帧回传队列。"""

    def __init__(self, path: str | Path, *, max_items: int = 500,
                 ttl_seconds: float = 72 * 3600.0) -> None:
        self.path = Path(path)
        self.max_items = max(1, int(max_items))
        self.ttl_seconds = max(60.0, float(ttl_seconds))
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # ------------------------------ 队列 ------------------------------
    def enqueue(self, *, sample_id: str, meta: dict[str, Any], image: bytes,
                now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        encoded = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._purge_expired(conn, current)
            exists = conn.execute(
                "SELECT 1 FROM frames WHERE sample_id = ? LIMIT 1",
                (sample_id,)).fetchone()
            if exists is not None:
                conn.commit()
                return False
            count = int(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0])
            if count >= self.max_items:
                # 满员挤掉最老的一条 (并把其流水标 dropped)
                row = conn.execute(
                    "SELECT sample_id FROM frames ORDER BY created_at, id LIMIT 1"
                ).fetchone()
                if row is not None:
                    conn.execute("DELETE FROM frames WHERE sample_id = ?", (row[0],))
                    conn.execute(
                        "UPDATE sample_log SET status = 'dropped', detail = '队列满被挤出',"
                        " updated_at = ? WHERE sample_id = ?", (current, row[0]))
            conn.execute(
                "INSERT INTO frames (sample_id, meta_json, image, created_at,"
                " expires_at, next_attempt_at, retry_count, last_error)"
                " VALUES (?, ?, ?, ?, ?, ?, 0, '')",
                (sample_id, encoded, sqlite3.Binary(image), current,
                 current + self.ttl_seconds, current))
            conn.commit()
            return True

    def next_due(self, *, now: float | None = None,
                 lease_seconds: float = 120.0) -> PendingFrame | None:
        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._purge_expired(conn, current)
            row = conn.execute(
                "SELECT sample_id, meta_json, image, created_at, expires_at,"
                " next_attempt_at, retry_count, last_error FROM frames"
                " WHERE next_attempt_at <= ? ORDER BY next_attempt_at, created_at, id"
                " LIMIT 1", (current,)).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                "UPDATE frames SET next_attempt_at = ? WHERE sample_id = ?",
                (current + max(1.0, float(lease_seconds)), row[0]))
            try:
                meta = json.loads(row[1])
            except (TypeError, json.JSONDecodeError):
                conn.execute("DELETE FROM frames WHERE sample_id = ?", (row[0],))
                conn.commit()
                return None
            conn.commit()
            return PendingFrame(
                sample_id=row[0], meta=meta, image=bytes(row[2]),
                created_at=float(row[3]), expires_at=float(row[4]),
                next_attempt_at=float(row[5]), retry_count=int(row[6]),
                last_error=row[7] or "")

    def reschedule(self, sample_id: str, *, retry_count: int,
                   next_attempt_at: float, last_error: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE frames SET retry_count = ?, next_attempt_at = ?,"
                " last_error = ? WHERE sample_id = ?",
                (int(retry_count), float(next_attempt_at),
                 (last_error or "")[:200], sample_id))
            conn.commit()

    def delete(self, sample_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM frames WHERE sample_id = ?", (sample_id,))
            conn.commit()

    def count(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as conn:
            self._purge_expired(conn, current)
            return int(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0])

    # ------------------------------ 流水 ------------------------------
    def log_add(self, *, sample_id: str, project_name: str, reason: str,
                channel_id: int, status: str = "pending",
                detail: str = "") -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sample_log (sample_id, project_name,"
                " reason, channel_id, status, detail, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sample_id, project_name, reason, int(channel_id), status,
                 detail[:200], now, now))
            # 封顶裁剪
            conn.execute(
                "DELETE FROM sample_log WHERE id NOT IN"
                " (SELECT id FROM sample_log ORDER BY created_at DESC, id DESC LIMIT ?)",
                (_LOG_CAP,))
            conn.commit()

    def log_update(self, sample_id: str, *, status: str, detail: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sample_log SET status = ?, detail = ?, updated_at = ?"
                " WHERE sample_id = ?",
                (status, detail[:200], time.time(), sample_id))
            conn.commit()

    def log_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT sample_id, project_name, reason, channel_id, status,"
                " detail, created_at, updated_at FROM sample_log"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),)).fetchall()
        return [
            {"sample_id": r[0], "project_name": r[1], "reason": r[2],
             "channel_id": r[3], "status": r[4], "detail": r[5],
             "created_at": r[6], "updated_at": r[7]}
            for r in rows
        ]

    def log_counts(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM sample_log GROUP BY status").fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    # ------------------------------ 内部 ------------------------------
    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS frames ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " sample_id TEXT NOT NULL UNIQUE,"
                " meta_json TEXT NOT NULL,"
                " image BLOB NOT NULL,"
                " created_at REAL NOT NULL,"
                " expires_at REAL NOT NULL,"
                " next_attempt_at REAL NOT NULL,"
                " retry_count INTEGER NOT NULL DEFAULT 0,"
                " last_error TEXT NOT NULL DEFAULT '')")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_frames_due"
                " ON frames(next_attempt_at, created_at)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sample_log ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " sample_id TEXT NOT NULL UNIQUE,"
                " project_name TEXT NOT NULL DEFAULT '',"
                " reason TEXT NOT NULL DEFAULT '',"
                " channel_id INTEGER NOT NULL DEFAULT 0,"
                " status TEXT NOT NULL DEFAULT 'pending',"
                " detail TEXT NOT NULL DEFAULT '',"
                " created_at REAL NOT NULL,"
                " updated_at REAL NOT NULL)")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _purge_expired(conn: sqlite3.Connection, now: float) -> None:
        conn.execute("DELETE FROM frames WHERE expires_at <= ?", (now,))
