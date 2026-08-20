"""generic_http 短信的独立 SQLite 离线补发队列。

WS5(PG) 决策记录: 本队列**刻意不随主库迁 PostgreSQL**，永远是独立本地 SQLite 文件。
理由: 这是"网络断时的本地磁盘缓冲"，语义上必须独立于业务库存活
(PG 连不上时短信队列反而更要能写)；数据是临时补发件，无跨表关系，不参与备份/迁移。
interconnect/sample_queue.py 同理。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OfflineSmsRecord:
    """一条待补发短信；不保存 token/secret 等 Provider 鉴权信息。"""

    message_id: str
    dedup_key: str
    payload: dict[str, Any]
    created_at: float
    expires_at: float
    next_attempt_at: float
    retry_count: int
    last_error: str


class SmsOfflineQueue:
    """有容量与 TTL 边界的进程重启可恢复队列。"""

    def __init__(
        self,
        path: str | Path,
        *,
        max_items: int,
        ttl_seconds: float,
    ) -> None:
        self.path = Path(path)
        self.max_items = max(1, int(max_items))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def enqueue(
        self,
        *,
        message_id: str,
        dedup_key: str,
        payload: dict[str, Any],
        now: float | None = None,
        next_attempt_at: float | None = None,
    ) -> bool:
        """加入离线队列；同一去重键尚在队列时返回 False。"""

        current = time.time() if now is None else float(now)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._purge_expired_conn(conn, current)
            exists = conn.execute(
                "SELECT 1 FROM sms_offline_jobs WHERE dedup_key = ? LIMIT 1",
                (dedup_key,),
            ).fetchone()
            if exists is not None:
                conn.commit()
                return False
            count = int(
                conn.execute("SELECT COUNT(*) FROM sms_offline_jobs").fetchone()[0]
            )
            if count >= self.max_items:
                conn.execute(
                    "DELETE FROM sms_offline_jobs WHERE id = "
                    "(SELECT id FROM sms_offline_jobs ORDER BY created_at, id LIMIT 1)"
                )
            conn.execute(
                """
                INSERT INTO sms_offline_jobs (
                    message_id, dedup_key, payload_json, created_at, expires_at,
                    next_attempt_at, retry_count, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, 0, '')
                """,
                (
                    message_id,
                    dedup_key,
                    encoded,
                    current,
                    current + self.ttl_seconds,
                    current if next_attempt_at is None else float(next_attempt_at),
                ),
            )
            conn.commit()
            return True

    def next_due(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
    ) -> OfflineSmsRecord | None:
        """原子领取一条到期任务并设置租约，防热替换/多进程重复补发。"""

        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._purge_expired_conn(conn, current)
            row = conn.execute(
                """
                SELECT message_id, dedup_key, payload_json, created_at, expires_at,
                       next_attempt_at, retry_count, last_error
                FROM sms_offline_jobs
                WHERE next_attempt_at <= ?
                ORDER BY next_attempt_at, created_at, id
                LIMIT 1
                """,
                (current,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                "UPDATE sms_offline_jobs SET next_attempt_at = ? WHERE message_id = ?",
                (current + max(1.0, float(lease_seconds)), row[0]),
            )
            try:
                payload = json.loads(row[2])
            except (TypeError, json.JSONDecodeError):
                conn.execute(
                    "DELETE FROM sms_offline_jobs WHERE message_id = ?", (row[0],)
                )
                conn.commit()
                return None
            conn.commit()
            return OfflineSmsRecord(
                message_id=row[0],
                dedup_key=row[1],
                payload=payload,
                created_at=float(row[3]),
                expires_at=float(row[4]),
                next_attempt_at=float(row[5]),
                retry_count=int(row[6]),
                last_error=row[7] or "",
            )

    def reschedule(
        self,
        message_id: str,
        *,
        retry_count: int,
        next_attempt_at: float,
        last_error: str,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE sms_offline_jobs
                SET retry_count = ?, next_attempt_at = ?, last_error = ?
                WHERE message_id = ?
                """,
                (int(retry_count), float(next_attempt_at), last_error[:200], message_id),
            )
            conn.commit()

    def delete(self, message_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM sms_offline_jobs WHERE message_id = ?", (message_id,)
            )
            conn.commit()

    def count(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as conn:
            self._purge_expired_conn(conn, current)
            return int(
                conn.execute("SELECT COUNT(*) FROM sms_offline_jobs").fetchone()[0]
            )

    def list_message_ids(self, *, now: float | None = None) -> list[str]:
        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as conn:
            self._purge_expired_conn(conn, current)
            return [
                str(row[0])
                for row in conn.execute(
                    "SELECT message_id FROM sms_offline_jobs ORDER BY created_at, id"
                ).fetchall()
            ]

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sms_offline_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,
                    dedup_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    next_attempt_at REAL NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_sms_offline_due "
                "ON sms_offline_jobs(next_attempt_at, created_at)"
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _purge_expired_conn(conn: sqlite3.Connection, now: float) -> None:
        conn.execute("DELETE FROM sms_offline_jobs WHERE expires_at <= ?", (now,))
