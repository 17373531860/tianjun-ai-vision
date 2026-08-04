"""generic_http 断网队列的 SQLite 持久化、去重、上限与 TTL 回归。"""

from __future__ import annotations

from backend.services.sms_offline_queue import SmsOfflineQueue


def _payload(message: str) -> dict[str, object]:
    return {
        "recipients": ["13800138000"],
        "message": f"渲染短信：{message}",
        "event_name": "装配 NG",
        "raw_message": message,
        "context": {"device_name": "1号工位"},
    }


def test_offline_queue_survives_restart_and_deduplicates_same_key(tmp_path) -> None:
    path = tmp_path / "sms_offline_queue.db"
    queue = SmsOfflineQueue(path, max_items=10, ttl_seconds=60)

    first = queue.enqueue(
        message_id="sms-1",
        dedup_key="same-key",
        payload=_payload("缺少压板"),
        now=100.0,
    )
    duplicate = queue.enqueue(
        message_id="sms-2",
        dedup_key="same-key",
        payload=_payload("缺少压板"),
        now=101.0,
    )

    reopened = SmsOfflineQueue(path, max_items=10, ttl_seconds=60)
    record = reopened.next_due(now=102.0)

    assert first is True
    assert duplicate is False
    assert reopened.count(now=102.0) == 1
    assert record is not None
    assert record.message_id == "sms-1"
    assert record.payload == _payload("缺少压板")


def test_offline_queue_drops_oldest_at_capacity_and_purges_expired(tmp_path) -> None:
    queue = SmsOfflineQueue(
        tmp_path / "sms_offline_queue.db",
        max_items=2,
        ttl_seconds=10,
    )
    queue.enqueue(message_id="sms-1", dedup_key="k1", payload=_payload("1"), now=0)
    queue.enqueue(message_id="sms-2", dedup_key="k2", payload=_payload("2"), now=1)
    queue.enqueue(message_id="sms-3", dedup_key="k3", payload=_payload("3"), now=2)

    ids = queue.list_message_ids(now=2)

    assert ids == ["sms-2", "sms-3"]
    assert queue.count(now=12) == 0


def test_offline_queue_reschedule_and_delete(tmp_path) -> None:
    queue = SmsOfflineQueue(
        tmp_path / "sms_offline_queue.db",
        max_items=10,
        ttl_seconds=60,
    )
    queue.enqueue(message_id="sms-1", dedup_key="k1", payload=_payload("1"), now=0)

    queue.reschedule(
        "sms-1",
        retry_count=2,
        next_attempt_at=30,
        last_error="HTTP_TIMEOUT",
    )

    assert queue.next_due(now=29) is None
    assert queue.next_due(now=30).retry_count == 2
    assert queue.next_due(now=30) is None, "同一租约期内不得被第二个 worker 重复领取"
    queue.delete("sms-1")
    assert queue.count(now=30) == 0
